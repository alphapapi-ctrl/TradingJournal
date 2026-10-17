"""On-demand imports, exports, settings, market analysis, and replay/reference tools."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import database as db
from journal_mcp import records
from journal_mcp.reporting import generate_report, markdown_report

MAX_IMPORT_BYTES = 8 * 1024 * 1024


def output_dir(kind: str) -> Path:
    path = Path(db.DB_PATH).parent / "mcp" / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_path(filename: str) -> Path:
    if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename or ":" in filename:
        raise ValueError("Use the export's filename only")
    root = output_dir("exports").resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError("Export does not exist")
    return path


def _write_export(content: str, suffix: str, prefix: str) -> dict:
    name = f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.{suffix}"
    path = output_dir("exports") / name
    path.write_text(content, encoding="utf-8-sig" if suffix == "csv" else "utf-8")
    return {"filename": name, "path": str(path.resolve()), "bytes": path.stat().st_size,
            "download_path": f"/exports/{name}", "resource_uri": f"journal://exports/{name}"}


def export_records(entity: str, filters: dict | None = None, start_date: str | None = None,
                   end_date: str | None = None, format: str = "csv", limit: int = 500, offset: int = 0) -> dict:
    if format not in ("csv", "json"):
        raise ValueError("format must be csv or json")
    with db.transaction(write=False):
        result = records.list_records(entity, filters, start_date, end_date, limit=limit, offset=offset)
    if format == "json":
        content = json.dumps(result["records"], indent=2, ensure_ascii=False, allow_nan=False)
    else:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(records.columns(entity)))
        writer.writeheader()
        writer.writerows(result["records"])
        content = buffer.getvalue()
    return {**_write_export(content, format, entity), "exported": len(result["records"]),
            "total": result["total"], "next_offset": result["next_offset"]}


def save_report(kind: str = "all", account_id: int | None = None, start_date: str | None = None,
                end_date: str | None = None, symbol: str | None = None, playbook_id: int | None = None,
                format: str = "markdown") -> dict:
    if format not in ("markdown", "json"):
        raise ValueError("format must be markdown or json")
    report = generate_report(kind, account_id, start_date, end_date, symbol, playbook_id)
    content = markdown_report(report) if format == "markdown" else json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    return _write_export(content, "md" if format == "markdown" else "json", "report")


def read_export(filename: str, offset: int = 0, max_bytes: int = 65536) -> dict:
    if offset < 0 or not 1 <= max_bytes <= 262144:
        raise ValueError("offset must be nonnegative and max_bytes must be 1–262144")
    path = export_path(filename)
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read(max_bytes)
    end = offset + len(chunk)
    return {"filename": filename, "encoding": "base64", "content": base64.b64encode(chunk).decode(),
            "bytes": path.stat().st_size, "next_offset": end if end < path.stat().st_size else None}


def backup_database() -> dict:
    """SQLite online backup includes committed WAL data without copying live files."""
    name = f"journal-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.db"
    path = output_dir("backups") / name
    source = db.get_connection()
    target = sqlite3.connect(str(path))
    try:
        source.backup(target, pages=128, sleep=0.01)
    finally:
        target.close()
        source.close()
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "scope": "Journal database; external market caches and files are separate"}


def import_trades(filename: str, content: str, account_id: int, encoding: str = "utf-8",
                  dry_run: bool = True, include_open: bool = True, include_dividends: bool = False) -> dict:
    records.get_record("accounts", account_id)
    if encoding not in ("utf-8", "base64"):
        raise ValueError("encoding must be utf-8 or base64")
    if len(content) > MAX_IMPORT_BYTES * 4 // 3 + 8:
        raise ValueError("Import exceeds 8 MiB")
    raw = base64.b64decode(content, validate=True) if encoding == "base64" else content.encode("utf-8")
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("Import exceeds 8 MiB")
    from brokers.importers import auto_detect_and_parse
    parsed, broker = auto_detect_and_parse(filename, raw)
    parsed = [r for r in parsed if (include_open or r.get("status") != "open") and (include_dividends or r.get("trade_type") != "dividend")]
    if len(parsed) > 5000:
        raise ValueError("Import exceeds 5,000 rows; split the export into smaller date ranges")
    if not parsed:
        raise ValueError("No importable trades detected")
    prepared = []
    for row in parsed:
        data = {k: v for k, v in row.items() if k in records.writable("trades")}
        # Broker parsers can return numpy scalars / NaN; normalise before validation.
        for key, value in list(data.items()):
            if hasattr(value, "item"):
                value = value.item()
            data[key] = None if isinstance(value, float) and not math.isfinite(value) else value
        data.update(account_id=account_id, broker=broker)
        if not data.get("broker_trade_id"):
            identity = {k: data.get(k) for k in ("symbol", "direction", "entry_time", "exit_time", "quantity", "entry_price", "exit_price", "trade_type")}
            data["broker_trade_id"] = "mcp-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        records._validate("trades", data)
        prepared.append(data)
    if dry_run:
        return {"dry_run": True, "broker": broker, "trades": len(prepared), "preview": prepared[:20],
                "next_step": "Call again with dry_run=false to save these trades"}
    created = updated = 0
    ids = []
    with db.transaction():
        for data in prepared:
            existing = db.fetch_all("SELECT id FROM trades WHERE account_id=? AND broker=? AND broker_trade_id=?",
                                    (account_id, broker, data["broker_trade_id"]))
            if len(existing) > 1:
                raise ValueError("Duplicate broker IDs already exist in this account; resolve them before importing")
            if existing:
                old = records.get_record("trades", existing[0]["id"])
                # Broker imports may close or correct a previously imported record.
                # Avoid touching user-maintained playbooks/journal fields.
                patch = records._validate("trades", {k: v for k, v in data.items() if k != "status"}, {**old, "status": data["status"], "exit_time": data.get("exit_time"), "exit_price": data.get("exit_price")})
                patch["status"] = data["status"]
                db.execute(f'UPDATE trades SET {",".join(k + "=?" for k in patch)},updated_at=datetime(\'now\') WHERE id=?', [*patch.values(), old["id"]])
                records._refresh_position(old.get("position_id"))
                tid = old["id"]
                updated += 1
            else:
                tid = records.save_record("trades", data)["id"]
                created += 1
            ids.append(tid)
        db.execute("INSERT INTO import_history(broker,filename,trades_imported) VALUES (?,?,?)", (broker, Path(filename).name, len(ids)))
    return {"dry_run": False, "broker": broker, "created": created, "updated": updated, "trade_ids": ids[:100], "ids_truncated": len(ids) > 100}


def settings(action: str = "get", values: dict | None = None, include_secrets: bool = False) -> dict:
    from utils.app_settings import get_settings_dict, set_many_settings
    if action not in ("get", "update"):
        raise ValueError("action must be get or update")
    if action == "update":
        if not values or len(values) > 200:
            raise ValueError("Provide 1–200 settings")
        if any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", k) for k in values):
            raise ValueError("Setting keys must be simple names")
        if any(not isinstance(v, (str, int, float, bool)) or (isinstance(v, float) and not math.isfinite(v)) for v in values.values()):
            raise ValueError("Setting values must be finite scalar values")
        if "theme" in values and values["theme"] not in ("dark", "mid", "light"):
            raise ValueError("theme must be dark, mid, or light")
        if "theme" in values:
            from utils.theme import set_theme
            set_theme(values["theme"])
        with db.transaction():
            set_many_settings({key: str(value).lower() if isinstance(value, bool) else value for key, value in values.items()})
        # The network/theme UI stores additional files; synchronise these too.
        if any(k.startswith("server_") or k == "open_browser" for k in values):
            from utils.network_config import save_network_config
            from utils.app_settings import load_network_settings
            save_network_config(load_network_settings().to_dict())
    result = get_settings_dict()
    # Read theme without get_theme(), whose UI helper rewrites Streamlit config.
    from utils.theme import THEME_FILE
    try:
        result["theme"] = json.loads(THEME_FILE.read_text(encoding="utf-8")).get("theme", "dark")
    except (OSError, ValueError):
        result["theme"] = "dark"
    if not include_secrets:
        result = {k: "[redacted]" if any(s in k.lower() for s in ("password", "secret", "token", "api_key")) else v for k, v in result.items()}
    return {"settings": result, "note": "Journal network changes take effect after restarting its browser server; MCP startup options are separate"}


def risk_calculator(entry_price: float, stop_price: float, balance: float, risk_pct: float = 1,
                    direction: str = "LONG", point_value: float = 1, quantity_step: float = 1,
                    playbook_id: int | None = None, rules_met: dict[str, bool] | None = None) -> dict:
    for key, value in locals().copy().items():
        if key in ("entry_price", "stop_price", "balance", "risk_pct", "point_value", "quantity_step"):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be finite and positive")
    if risk_pct > 100 or direction not in ("LONG", "SHORT"):
        raise ValueError("risk_pct must be at most 100 and direction must be LONG or SHORT")
    if (direction == "LONG" and stop_price >= entry_price) or (direction == "SHORT" and stop_price <= entry_price):
        raise ValueError("Stop must be below a long entry or above a short entry")
    assessment = records.evaluate_playbook(playbook_id, rules_met or {}) if playbook_id is not None else {}
    multiplier = assessment.get("risk_multiplier", 1)
    if assessment.get("risk_level") == "no_trade":
        multiplier = 0
    risk = balance * risk_pct / 100 * multiplier
    distance = abs(entry_price - stop_price)
    qty = math.floor(risk / (distance * point_value) / quantity_step + 1e-10) * quantity_step
    return {"risk_budget": round(risk, 2), "quantity": round(qty, 8), "actual_risk": round(qty * distance * point_value, 2),
            "position_value": round(qty * entry_price * point_value, 2), "assessment": assessment,
            "note": "Sizing only; no broker order is placed. Supply the instrument's account-currency point value."}


def market_analysis(action: str, symbol: str = "", exchange_hint: str = "", limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1–500")
    from utils import stock_data as sd
    if action == "holders":
        data = sd.load_substantial_holders()
        if data is None:
            return {"notices": []}
        if symbol:
            data = data[data["ticker"].str.upper() == symbol.upper()]
        return {"notices": json.loads(data.head(limit).to_json(orient="records", date_format="iso"))}
    if action == "backfill_holders":
        if not re.fullmatch(r"[A-Za-z0-9]{2,8}", symbol):
            raise ValueError("Supply an ASX ticker")
        ok, message = sd.backfill_substantial_holders(symbol)
        return {"success": ok, "message": message}
    if not re.fullmatch(r"[A-Za-z0-9.^=_:\-]{1,40}", symbol):
        raise ValueError("Supply a market symbol")
    ticker = sd.resolve_yf_symbol(symbol, exchange_hint)
    if action == "quote":
        bars = sd.fetch_history(ticker, "5d", "1d")
        return {"symbol": ticker, "price": float(bars["Close"].iloc[-1]), "as_of": str(bars.index[-1]), "source": "yfinance; may be delayed"}
    if action == "history":
        bars = sd.fetch_history(ticker, "2y", "1d")
        return {"symbol": ticker, "bars": json.loads(bars.tail(limit).reset_index().to_json(orient="records", date_format="iso"))}
    if action == "analysis":
        daily = sd.fetch_history(ticker, "2y", "1d")
        weekly = sd.fetch_history(ticker, "5y", "1wk")
        info = sd.fetch_info(ticker)
        technicals = sd.compute_technicals(daily, weekly)
        technicals.pop("df", None)  # UI-only dataframe; don't send/cache the entire chart in model context.
        return {"symbol": ticker, "technicals": technicals, "fundamentals": sd.fundamental_checks(info)}
    raise ValueError("action must be quote, history, analysis, holders, or backfill_holders")


def reference_data(action: str = "days", instrument: str = "DEUIDXEUR", day: str | None = None,
                    filters: dict | None = None, limit: int = 100, offset: int = 0) -> dict:
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("limit must be 1–500; offset must be nonnegative")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,50}", instrument):
        raise ValueError("Invalid instrument code")
    if day:
        date.fromisoformat(day)
    from utils import market_data as md
    if action == "instruments":
        return {"instruments": md.list_instruments()}
    if action == "days":
        from utils.day_index import query_days
        return {"days": query_days(filters or {}, instrument, limit=limit)}
    if action == "bars":
        if not day:
            raise ValueError("day is required")
        bars = md.get_day_bars(day, instrument=instrument)
        rows = md.bars_to_lwc(bars, instrument)
        return {"bars": rows[offset:offset + limit], "total": len(rows), "next_offset": offset + limit if offset + limit < len(rows) else None}
    if action == "rebuild_index":
        from utils.day_index import rebuild_index
        return {"result": rebuild_index(instrument=instrument)}
    if action == "update_caches":
        args = filters or {}
        for key in ("start", "end"):
            if args.get(key):
                date.fromisoformat(args[key])
        # Existing data updater runs independently; do not allow overlapping jobs.
        global _cache_update_process
        if _cache_update_process is not None and _cache_update_process.poll() is None:
            raise ValueError("A cache update is already running")
        _cache_update_process = md.run_data_update(instrument, start=args.get("start"), end=args.get("end"))
        return {"pid": _cache_update_process.pid, "status": "started; use action=update_log"}
    if action == "update_log":
        return {"log": md.read_update_log(min(limit, 100)), "running": _cache_update_process is not None and _cache_update_process.poll() is None}
    raise ValueError("action must be instruments, days, bars, rebuild_index, update_caches, or update_log")


_cache_update_process = None


def replay_action(action: str, session_id: int | None = None, data: dict | None = None) -> dict:
    from utils import replay_engine as engine
    values = data or {}
    if action == "create_session":
        required = {"name", "instrument", "day", "balance"}
        if not required <= values.keys() or set(values) - required - {"currency"}:
            raise ValueError("Supply name, instrument, day, balance, and optional currency")
        date.fromisoformat(values["day"])
        if not isinstance(values["balance"], (int, float)) or not math.isfinite(values["balance"]) or values["balance"] <= 0:
            raise ValueError("balance must be positive")
        with db.transaction():
            sid = engine.create_replay_session(**values)
            return records.get_record("replay_sessions", sid)
    session = records.get_record("replay_sessions", session_id)
    if action == "stats":
        return engine.day_stats(session["account_id"], session["day"])
    if action == "open_trade":
        allowed = {"side", "price", "qty", "sl", "tp", "bar_time", "symbol"}
        if set(values) - allowed or not {"side", "price", "qty", "bar_time"} <= values.keys():
            raise ValueError("Supply side, price, qty, bar_time and optional sl, tp, symbol")
        if values["side"] not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        for key in ("price", "qty", "bar_time"):
            if not isinstance(values[key], (int, float)) or not math.isfinite(values[key]) or values[key] <= 0:
                raise ValueError(f"{key} must be positive")
        if not engine.validate_fill(session["day"], "market", values["side"], values["price"], values["bar_time"], session["instrument"]):
            raise ValueError("Replay fill is outside the historical bar range or cache is missing")
        if not session.get("account_id") or records.get_record("accounts", session["account_id"])["account_type"] != "Demo":
            raise ValueError("Replay trading requires a Demo account")
        with db.transaction():
            tid = engine.open_replay_trade(session["account_id"], session["day"], **{"sl": None, "tp": None, **values})
            return records.get_record("trades", tid)
    if action == "delete_session":
        return records.delete_record("replay_sessions", session_id)
    raise ValueError("action must be create_session, stats, open_trade, or delete_session; use save_record to update replay state and trade_action to close trades")


def ftp_sync() -> dict:
    from utils.ftp_sync import sync_all_accounts
    return sync_all_accounts()


def diagnostics() -> dict:
    memory_mb = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            memory_mb = round(counters.WorkingSetSize / 1024 ** 2, 1)
    return {"pid": os.getpid(), "database": str(Path(db.DB_PATH).resolve()), "working_set_mb": memory_mb,
            "cpu_seconds": round(sum(os.times()[:2]), 3), "tool_concurrency": 1,
            "authentication": False, "model_runtime": False, "background_polling": False,
            "data_libraries_in_main_process": any(name in sys.modules for name in ("pandas", "streamlit", "pyarrow")),
            "heavy_operations": "Short-lived helpers; cache-update job tracking stays in the parent"}
