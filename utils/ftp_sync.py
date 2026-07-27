"""
FTP sync for MT5 published account HTML reports.

Runs as a daemon thread — starts once per server process, polls every N seconds.
Connects to FTP, downloads the HTML report for each configured account,
parses it with the MT5 parser, and upserts trades into the DB.

Sync status is written to app_settings (ftp_last_sync, ftp_last_result)
so the Settings UI can read it without touching Streamlit from the thread.
"""
import ftplib
import json
import logging
import threading
from datetime import datetime

from database import fetch_all, execute as _db_exec

log = logging.getLogger(__name__)

_sync_thread: threading.Thread | None = None
_stop_event = threading.Event()
DEFAULT_INTERVAL = 300  # 5 minutes


# ── Settings helpers ──────────────────────────────────────────────────────────

def get_ftp_config() -> dict:
    rows = fetch_all(
        "SELECT key, value FROM app_settings "
        "WHERE key IN ('ftp_host','ftp_port','ftp_user','ftp_password','ftp_enabled')"
    )
    return {r["key"]: r["value"] for r in rows}


def _save_setting(key: str, value: str):
    existing = fetch_all("SELECT key FROM app_settings WHERE key=?", (key,))
    if existing:
        _db_exec(
            "UPDATE app_settings SET value=?, updated_at=datetime('now') WHERE key=?",
            (value, key),
        )
    else:
        _db_exec(
            "INSERT INTO app_settings (key, value) VALUES (?,?)",
            (key, value),
        )


def _save_sync_status(results: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_setting("ftp_last_sync", now)
    _save_setting("ftp_last_result", json.dumps(results))


# ── FTP helpers ───────────────────────────────────────────────────────────────

def connect_ftp(cfg: dict) -> ftplib.FTP:
    ftp = ftplib.FTP()
    ftp.connect(cfg["ftp_host"], int(cfg.get("ftp_port", 21)), timeout=10)
    ftp.login(cfg["ftp_user"], cfg["ftp_password"])
    ftp.set_pasv(True)
    return ftp


def list_ftp_folders(ftp: ftplib.FTP) -> list[str]:
    items: list[str] = []
    ftp.retrlines("LIST", items.append)
    folders = []
    for item in items:
        if item.startswith("d"):
            parts = item.split()
            if parts:
                folders.append(parts[-1])
    return folders


def find_report_file(ftp: ftplib.FTP, folder: str) -> str | None:
    """Navigate into folder and return the first .htm/.html filename found."""
    try:
        ftp.cwd(f"/{folder}")
    except ftplib.error_perm:
        try:
            ftp.cwd(folder)
        except ftplib.error_perm:
            return None
    files: list[str] = []
    ftp.retrlines("NLST", files.append)
    for f in files:
        if f.lower().endswith((".htm", ".html")):
            return f
    return None


def download_report(ftp: ftplib.FTP, filename: str) -> bytes:
    buf: list[bytes] = []
    ftp.retrbinary(f"RETR {filename}", buf.append)
    return b"".join(buf)


# ── Trade conversion ──────────────────────────────────────────────────────────

def _row_to_trade(row, account_id: int, broker: str) -> dict:
    """Map a mt5_parser DataFrame row to the upsert_trade_from_broker schema."""
    import pandas as pd

    def _iso(val):
        if val is None:
            return None
        try:
            ts = pd.to_datetime(val)
            return None if pd.isna(ts) else ts.isoformat()
        except Exception:
            return None

    def _f(val, default: float = 0.0) -> float:
        try:
            v = float(val)
            return default if v != v else v  # NaN guard
        except Exception:
            return default

    direction = "LONG" if str(row.get("type", "")).lower() == "buy" else "SHORT"
    open_t  = _iso(row.get("open_time"))
    close_t = _iso(row.get("close_time"))

    return {
        "account_id":      account_id,
        "broker":          broker,
        "broker_trade_id": str(row.get("position", "")).strip() or None,
        "symbol":          str(row.get("symbol", "")).strip().upper(),
        "direction":       direction,
        "entry_price":     _f(row.get("open_price")),
        "exit_price":      _f(row.get("close_price")) or None,
        "entry_time":      open_t,
        "exit_time":       close_t,
        "quantity":        _f(row.get("volume")),
        "pnl":             _f(row.get("net_profit", row.get("profit", 0))),
        "commission":      _f(row.get("commission")),
        "swap":            _f(row.get("swap")),
        "status":          "closed" if close_t else "open",
        "raw_data":        None,
    }


# ── Core sync logic ───────────────────────────────────────────────────────────

def sync_all_accounts() -> dict:
    """
    Connect to FTP, download and parse the HTML report for every account
    that has ftp_folder set, and upsert the resulting trades.

    Returns a result dict keyed by account name.
    """
    from brokers.mt5_parser import parse_mt5_report
    from utils.trade_ops import upsert_trade_from_broker

    cfg = get_ftp_config()
    if not (cfg.get("ftp_host") and cfg.get("ftp_user") and cfg.get("ftp_password")):
        return {"_error": "FTP not configured — set host/user/password in Settings → MT5 FTP"}

    if cfg.get("ftp_enabled", "true") == "false":
        return {"_skipped": "FTP sync is disabled"}

    accounts = fetch_all(
        "SELECT id, name, broker, ftp_folder FROM accounts "
        "WHERE ftp_folder IS NOT NULL AND TRIM(ftp_folder) != ''"
    )
    if not accounts:
        return {"_skipped": "No accounts have an FTP folder configured"}

    results: dict = {}
    ftp = None
    try:
        ftp = connect_ftp(cfg)
        for acc in accounts:
            r: dict = {"new": 0, "updated": 0, "error": None}
            try:
                report_file = find_report_file(ftp, acc["ftp_folder"])
                if not report_file:
                    r["error"] = f"No HTML report found in folder '{acc['ftp_folder']}'"
                    results[acc["name"]] = r
                    continue

                raw = download_report(ftp, report_file)
                df = parse_mt5_report(raw)
                if df is None or df.empty:
                    r["error"] = "Report parsed 0 trades"
                    results[acc["name"]] = r
                    continue

                for _, row in df.iterrows():
                    trade = _row_to_trade(row, acc["id"], acc["broker"])
                    if not trade["symbol"]:
                        continue
                    _, is_new = upsert_trade_from_broker(trade)
                    if is_new:
                        r["new"] += 1
                    else:
                        r["updated"] += 1

            except Exception as exc:
                r["error"] = str(exc)
                log.exception("FTP sync error for account %s", acc["name"])

            results[acc["name"]] = r

    except Exception as exc:
        log.exception("FTP connection failed")
        return {"_error": f"FTP connection failed: {exc}"}
    finally:
        if ftp:
            try:
                ftp.quit()
            except Exception:
                pass

    return results


# ── Background thread ─────────────────────────────────────────────────────────

def _poll_loop(interval: int):
    # Sync immediately on startup, then every `interval` seconds
    for _ in range(1):  # initial run
        try:
            results = sync_all_accounts()
            _save_sync_status(results)
        except Exception as exc:
            _save_sync_status({"_error": str(exc)})

    while not _stop_event.wait(timeout=interval):
        try:
            results = sync_all_accounts()
            _save_sync_status(results)
        except Exception as exc:
            _save_sync_status({"_error": str(exc)})


def start_sync_thread(interval: int = DEFAULT_INTERVAL):
    """Start the FTP polling thread. Safe to call multiple times — only starts once."""
    global _sync_thread
    if _sync_thread is not None and _sync_thread.is_alive():
        return
    _stop_event.clear()
    _sync_thread = threading.Thread(
        target=_poll_loop, args=(interval,), daemon=True, name="FTPSyncThread"
    )
    _sync_thread.start()
    log.info("FTP sync thread started (interval=%ds)", interval)


def stop_sync_thread():
    _stop_event.set()


def is_running() -> bool:
    return _sync_thread is not None and _sync_thread.is_alive()
