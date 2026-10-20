"""Validated journal operations; imports only the standard library while idle."""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any

import database as db
from utils import trade_ops as trades
from utils import playbook_logic as playbooks
from journal_mcp import guards
from journal_mcp.neo_schema import TABLES
from journal_mcp.pattern_schema import TABLES as PATTERN_TABLES
from journal_mcp.pattern_models import SCHEMA_VERSION

ENTITIES = (
    "accounts", "trades", "positions", "journal_entries", "journal_templates",
    "playbooks", "playbook_rules", "playbook_risk_rules", "risk_setups",
    "replay_sessions", "import_history", "day_features", *TABLES, *PATTERN_TABLES,
)
READ_ONLY = {"import_history", "day_features", *TABLES, *PATTERN_TABLES}
JSON_FIELDS = {"raw_data", "playbook_rules_met", "custom_fields", "pending_orders", "sizing_assumptions"}
REFERENCES = {
    "account_id": "accounts", "trade_id": "trades", "position_id": "positions",
    "playbook_id": "playbooks", "trigger_rule_id": "playbook_rules",
    "session_id": "trading_sessions", "candidate_id": "candidates",
    "instrument_spec_id": "instrument_specs", "template_id": "journal_templates",
}
ENUMS = {
    "direction": {"LONG", "SHORT"}, "entry_type": {"daily", "weekly", "trade"},
    "template_type": {"daily", "weekly", "trade"}, "stage": {"pre", "post"},
    "rule_type": {"required", "optional", "bonus"},
    "risk_level": {"normal", "reduced", "no_trade"},
    "account_type": {"Personal", "Prop Firm", "EA", "Demo"},
}
DEFAULTS = {
    "trades": {"broker": "Manual", "direction": "LONG", "status": "open", "pnl": 0,
               "commission": 0, "swap": 0},
    "journal_entries": {"stage": "post"},
    "playbooks": {"symbol_filter": "*", "direction": "BOTH"},
    "playbook_rules": {"weight": 1, "sort_order": 0},
    "accounts": {"currency": "AUD", "broker": "Manual", "account_type": "Personal"},
}


def field_enum(entity, key):
    specific = {
        ('journal_entries', 'trade_category'): {1, 2, 3, 4},
        ("playbooks", "direction"): {"LONG", "SHORT", "BOTH"},
        ("evidence", "stage"): {"pre", "management", "post"},
        ("candidates", "status"): {"draft", "assessed", "blocked", "pending", "cancelled", "open", "closed"},
        ("trading_sessions", "status"): {"active", "completed"},
        ("risk_regimes", "mode"): {"demo", "live", "prop"},
        ("management_events", "action_status"): {"observed", "proposed", "executed", "cancelled"},
        ("fill_events", "kind"): {"entry", "exit"},
        ("instrument_specs", "sizing_method"): {"point_value", "contract_size"},
    }
    return specific.get((entity, key), ENUMS.get(key))


def columns(entity: str) -> dict:
    if entity not in ENTITIES:
        raise ValueError(f"Unknown entity. Choose one of: {', '.join(ENTITIES)}")
    return {r["name"]: r for r in db.fetch_all(f'PRAGMA table_info("{entity}")')}


def writable(entity: str) -> set:
    fields = set(columns(entity)) - {"id", "created_at", "updated_at", "revision"}
    if entity in READ_ONLY:
        return set()
    if entity == "positions":
        fields &= {"notes", "playbook_id"}
    if entity == "trades":
        fields -= {"risk_score", "playbook_rules_met", "position_id"}
    return fields


def schema(entity: str | None = None) -> dict:
    result = {}
    for name in [entity] if entity else ENTITIES:
        fields = columns(name)
        result[name] = {
            "read_only": name in READ_ONLY,
            "create": name not in READ_ONLY and name != "positions",
            "fields": {key: {"type": col["type"], "required": bool(col["notnull"] and col["dflt_value"] is None),
                             "default_sql": col["dflt_value"], "writable": key in writable(name),
                             **({'description': 'Menaker category: 1 = on-plan profit; 2 = on-plan predefined loss; 3 = off-plan loss; 4 = off-plan profit. Null means unrated. Set only from the recorded plan and review.'} if name == 'journal_entries' and key == 'trade_category' else {}),
                             **({'description': 'Review grade, separate from the Menaker category and calculated playbook risk score.'} if name == 'journal_entries' and key == 'grade' else {}),
                             **({"enum": sorted(field_enum(name, key))} if field_enum(name, key) else {})}
                       for key, col in fields.items()},
        }
    from journal_mcp.patterns import schema_contracts
    for name, contract in schema_contracts().items():
        if name in result:
            result[name]["contract"] = contract
    return {"schema_version": SCHEMA_VERSION, "capabilities": {"native_patterns": True, "pattern_reports": True, "pattern_evidence_owner": True, "legacy_pattern_migration": True, "contract_size_sizing": True}, "entities": result, "notes": [
        "IDs refer to this journal database; inspect existing accounts before creating trades.",
        "Use trade_action for close/partial_close/reopen and position_action to merge/unmerge trades.",
        "Use assign_playbook to store rule checks and calculated risk scores.",
        "Trade Detail pre/post notes, Menaker trade_category, grade, mood and pre-trade psychology are journal_entries fields linked by trade_id, position_id or a confirmed candidate fill. Read them with list_records/get_record and patch with save_record. Screenshots, management history and native psychology patterns use their dedicated entities/tools.",
        "Updates are patches: omitted fields stay unchanged; null clears a nullable field.",
        "save_record/trade_action accept idempotency_key and expected_revision. A conflicting retry fails; read and merge after a revision conflict. UI updates also advance revisions.",
        "Neo entities use dedicated workflow tools; list_records/get_record read their history. candidate_plans, events, evidence, snapshots and instrument specs are append-only.",
        "Playbook thresholds are minimum guards: their consequence applies on failure. Passing all minima defaults to normal / multiplier 1. A score is checklist composition, never probability or a risk-percentage recommendation.",
        "JSON fields accept objects/arrays; raw_data also accepts plain notes text.",
        "Amounts use the account currency. No FX conversion is performed.",
        "CFD sizing_method=contract_size derives point_value from contract_multiplier; tick_size/tick_value may be omitted. Cash sizing uses exact price distance, lot step/minimum and optional maximum. Known tick pairs still validate prices; unknown ticks do not create a sizing blocker.",
        "These tools manage journal records only; they do not send orders to MT5 or brokers.",
    ]}


def get_record(entity: str, record_id: int) -> dict:
    fields = columns(entity)
    if "id" not in fields:
        raise ValueError("day_features uses date and instrument; use list_records filters")
    row = db.fetch_one(f'SELECT * FROM "{entity}" WHERE id=?', (record_id,))
    if row is None:
        raise ValueError(f"{entity} #{record_id} does not exist")
    return row


def details(entity: str, record_id: int) -> dict:
    if entity in PATTERN_TABLES:
        from journal_mcp.patterns import details as pattern_details
        return pattern_details(entity, record_id)
    row = get_record(entity, record_id)
    if entity == "positions":
        row["trades"] = db.fetch_all("SELECT * FROM trades WHERE position_id=? ORDER BY id", (record_id,))
    if entity == "playbooks":
        row = playbooks.get_playbook(record_id)
    if entity == "candidates":
        row["plans"] = db.fetch_all("SELECT * FROM candidate_plans WHERE candidate_id=? ORDER BY version", (record_id,))
        row["fills"] = db.fetch_all("SELECT * FROM fill_events WHERE candidate_id=? ORDER BY id", (record_id,))
    if entity == "trading_sessions":
        row["candidates"] = db.fetch_all("SELECT * FROM candidates WHERE session_id=? ORDER BY id", (record_id,))
    return row


def where_clause(entity: str, filters: dict | None = None, start_date: str | None = None,
                 end_date: str | None = None, search: str | None = None) -> tuple[str, list]:
    fields = columns(entity)
    clauses, params = [], []
    for key, value in (filters or {}).items():
        if key not in fields:
            raise ValueError(f"Unknown {entity} filter: {key}")
        if isinstance(value, (dict, list)):
            raise ValueError("Filters accept scalar values or null")
        clauses.append(f'"{key}" IS NULL' if value is None else f'"{key}"=?')
        if value is not None:
            params.append(value)
    time_field = next((x for x in ("entry_time", "entry_date", "date", "day", "observed_at", "captured_at", "filled_at", "exchange_date", "effective_at", "verified_at", "created_at", "imported_at") if x in fields), None)
    if start_date or end_date:
        if not time_field:
            raise ValueError("This entity has no date field")
        for value, operator in ((start_date, ">="), (end_date, "<=")):
            if value:
                date.fromisoformat(value)
                clauses.append(f'substr("{time_field}",1,10){operator}?')
                params.append(value)
        if start_date and end_date and start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
    if search:
        text_fields = [key for key, col in fields.items() if col["type"] == "TEXT"]
        clauses.append("(" + " OR ".join(f'"{key}" LIKE ? ESCAPE \'\\\'' for key in text_fields) + ")")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%"] * len(text_fields))
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def list_records(entity: str, filters: dict | None = None, start_date: str | None = None,
                 end_date: str | None = None, search: str | None = None,
                 limit: int = 100, offset: int = 0) -> dict:
    if entity in PATTERN_TABLES:
        from journal_mcp.patterns import list_records as pattern_list
        return pattern_list(entity, filters, start_date, end_date, search, limit, offset)
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("limit must be 1–500 and offset must be nonnegative")
    where, params = where_clause(entity, filters, start_date, end_date, search)
    fields = columns(entity)
    order = '"id" DESC' if "id" in fields else '"date" DESC, "instrument"'
    total = db.fetch_one(f'SELECT COUNT(*) n FROM "{entity}"{where}', params)["n"]
    rows = db.fetch_all(f'SELECT * FROM "{entity}"{where} ORDER BY {order} LIMIT ? OFFSET ?', params + [limit, offset])
    return {"records": rows, "total": total, "offset": offset,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None}


def _validate(entity: str, patch: dict, old: dict | None = None) -> dict:
    fields = columns(entity)
    unknown = set(patch) - writable(entity)
    if unknown:
        raise ValueError(f"Unknown or calculated fields: {', '.join(sorted(unknown))}. Use journal_schema.")
    data = dict(patch)
    for key, value in data.items():
        if value is None:
            if fields[key]["notnull"]:
                raise ValueError(f"{key} cannot be null")
            continue
        if key in JSON_FIELDS:
            if isinstance(value, (dict, list)):
                data[key] = json.dumps(value, allow_nan=False)
            elif not isinstance(value, str):
                raise ValueError(f"{key} must be JSON or text")
            elif key != "raw_data":
                parsed = json.loads(value)
                if not isinstance(parsed, (dict, list)):
                    raise ValueError(f"{key} must encode an object or array")
            if key != "raw_data":
                parsed = json.loads(data[key])
                expected = list if key == "pending_orders" or entity == "journal_templates" else dict
                if not isinstance(parsed, expected):
                    raise ValueError(f"{key} must be a JSON {'array' if expected is list else 'object'}")
        elif fields[key]["type"] in ("REAL", "INTEGER"):
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number")
            if fields[key]["type"] == "INTEGER" and int(value) != value:
                raise ValueError(f"{key} must be an integer")
        elif not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        if key in REFERENCES and value is not None:
            get_record(REFERENCES[key], value)
        if key in ENUMS:
            allowed = {"LONG", "SHORT", "BOTH"} if key == "direction" and entity == "playbooks" else ENUMS[key]
            if value not in allowed:
                raise ValueError(f"{key} must be one of {sorted(allowed)}")
        if key in ("entry_date", "day", "replay_day"):
            date.fromisoformat(value)
        if key in ("entry_time", "exit_time"):
            datetime.fromisoformat(value)
        if key in ("is_default", "is_replay", "trigger_rule_must_be") and value not in (0, 1):
            raise ValueError(f"{key} must be 0 or 1")
    full = {**DEFAULTS.get(entity, {}), **(old or {}), **data}
    if entity in ("journal_entries", "trades"):
        for key, version_key, table in (("template_id", "template_version", "journal_templates"), ("playbook_id", "playbook_version", "playbooks")):
            if key in data and version_key not in data:
                linked = get_record(table, data[key]) if data[key] is not None else None
                data[version_key] = f"{linked.get('version') or '1'}@{linked['revision']}" if linked else None
    for key, col in fields.items():
        if col["notnull"] and not col["pk"] and col["dflt_value"] is None:
            if full.get(key) is None or (isinstance(full.get(key), str) and not full[key].strip()):
                raise ValueError(f"{key} is required")
    if entity == "trades":
        if full.get("instrument_spec_id") is not None:
            from journal_mcp.neo import validate_trade_economics
            assumptions = full.get("sizing_assumptions") or {}
            if isinstance(assumptions, str):
                assumptions = json.loads(assumptions)
            data["sizing_assumptions"] = json.dumps({**assumptions, **validate_trade_economics(full)}, allow_nan=False)
        if full.get("status") not in ("open", "closed"):
            raise ValueError("Trade status must be open or closed")
        dividend = full.get("trade_type") == "dividend"
        if full.get("quantity") is None or full["quantity"] < 0 or (full["quantity"] == 0 and not dividend):
            raise ValueError("quantity must be positive")
        if full.get("entry_price") is None or full["entry_price"] < 0:
            raise ValueError("entry_price must be nonnegative")
        if not full.get("entry_time"):
            raise ValueError("entry_time is required")
        if full["status"] == "closed" and not full.get("exit_time"):
            raise ValueError("Closed trades require exit_time")
        if old and "status" in data and old["status"] != data["status"]:
            raise ValueError("Use trade_action to close or reopen an existing trade")
        if full["status"] == "open" and (full.get("exit_price") is not None or full.get("exit_time") is not None):
            raise ValueError("Open trades cannot have exit fields")
        if old and old.get("position_id") and any(k in data for k in ("symbol", "direction")):
            position = get_record("positions", old["position_id"])
            if any(full[k] != position[k] for k in ("symbol", "direction")):
                raise ValueError("Unmerge the position before changing symbol/direction")
        if old and old.get("position_id") and "playbook_id" in data and data["playbook_id"] != old.get("playbook_id"):
            raise ValueError("Assign the playbook to the entire merged position")
        if full.get("entry_time") and full.get("exit_time"):
            validate_exit_time(full["entry_time"], full["exit_time"])
    if entity == "journal_entries":
        for link, table in (("candidate_id", "candidates"), ("session_id", "trading_sessions")):
            if full.get(link):
                linked = get_record(table, full[link])
                if full.get("account_id") != linked["account_id"]:
                    raise ValueError("Journal entry account does not match its candidate/session")
        if full.get("trade_id") and full.get("position_id"):
            raise ValueError("Link an entry to a trade OR a position")
        target = None
        if full.get("trade_id"):
            target = get_record("trades", full["trade_id"]).get("account_id")
        if full.get("position_id"):
            linked = db.fetch_all("SELECT DISTINCT account_id FROM trades WHERE position_id=?", (full["position_id"],))
            target = linked[0]["account_id"] if len(linked) == 1 else None
        if target and full.get("account_id") not in (None, target):
            raise ValueError("Journal entry account does not match its trade/position")
        if target:
            data["account_id"] = target
        if full.get("trade_category") is not None and full["trade_category"] not in (1, 2, 3, 4):
            raise ValueError("trade_category must be 1–4")
        if full.get("mood") is not None and not 1 <= full["mood"] <= 10:
            raise ValueError("mood must be 1–10")
    if entity == "replay_sessions" and full.get("status", "active") not in ("active", "archived"):
        raise ValueError("Replay status must be active or archived")
    if entity in ("playbook_rules", "playbook_risk_rules"):
        if not full.get("playbook_id"):
            raise ValueError("playbook_id is required")
        if full.get("trigger_rule_id"):
            if get_record("playbook_rules", full["trigger_rule_id"])["playbook_id"] != full["playbook_id"]:
                raise ValueError("Trigger rule belongs to a different playbook")
        if old and full["playbook_id"] != old["playbook_id"]:
            raise ValueError("Create a new rule instead of moving rules between playbooks")
        for key in ("min_required_pct", "min_optional_pct"):
            if full.get(key) is not None and not 0 <= full[key] <= 100:
                raise ValueError(f"{key} must be 0–100")
        for key in ("weight", "risk_multiplier"):
            if full.get(key) is not None and full[key] < 0:
                raise ValueError(f"{key} cannot be negative")
    if entity in ("risk_setups", "replay_sessions") and full.get("account_id"):
        if entity == "replay_sessions" and get_record("accounts", full["account_id"])["account_type"] != "Demo":
            raise ValueError("Replay sessions require a Demo account")
    return data


def _refresh_position(position_id: int | None):
    if not position_id:
        return
    trades.recompute_position(position_id)
    if not db.fetch_one("SELECT id FROM positions WHERE id=?", (position_id,)):
        db.execute("UPDATE journal_entries SET position_id=NULL WHERE position_id=?", (position_id,))


def _refresh_rules(playbook_id: int):
    pb = playbooks.get_playbook(playbook_id)
    valid = {str(r["id"]) for r in pb["rules"]}
    for row in db.fetch_all("SELECT id,playbook_rules_met FROM trades WHERE playbook_id=?", (playbook_id,)):
        if row["playbook_rules_met"] is None:
            continue  # Preserve the distinction between unassessed and failed checks.
        try:
            checks = json.loads(row["playbook_rules_met"] or "{}")
        except (ValueError, TypeError):
            checks = {}
        checks = {k: bool(v) for k, v in checks.items() if k in valid} if isinstance(checks, dict) else {}
        result = playbooks.evaluate_playbook_rules(pb, checks)
        trades.update_trade_playbook(row["id"], playbook_id, checks, result["risk_score"])


def save_record(entity: str, data: dict, record_id: int | None = None,
                expected_revision: int | None = None, idempotency_key: str | None = None) -> dict:
    with db.transaction():
        fingerprint, cached = guards.replay(idempotency_key, "save_record", [entity, data, record_id, expected_revision])
        if cached is not None:
            return cached
        guards.check_revision(get_record(entity, record_id) if record_id is not None else None, expected_revision)
        return guards.remember(idempotency_key, fingerprint, _save_record(entity, data, record_id))


def _save_record(entity: str, data: dict, record_id: int | None = None) -> dict:
    if entity in PATTERN_TABLES:
        from journal_mcp.patterns import PatternError
        raise PatternError("validation_failure", "Use dedicated pattern save/follow-up tools to preserve audit and relationship validation")
    if entity in READ_ONLY or (entity == "positions" and record_id is None):
        raise ValueError("This entity is generated; use import_trades, reference_data, or position_action")
    with db.transaction():
        old = get_record(entity, record_id) if record_id is not None else None
        patch = _validate(entity, data, old)
        if not old:
            patch = {**DEFAULTS.get(entity, {}), **patch}
        if entity == "trades":
            full = {**(old or {}), **patch}
            # A manual closed trade can provide broker P&L explicitly for CFDs/options.
            if full["status"] == "closed" and "pnl" not in data and full.get("exit_price") is not None and (not old or set(data) & {"entry_price", "exit_price", "quantity", "direction"}):
                patch["pnl"] = trades.price_pnl(full, full["exit_price"], full["quantity"])
            if old and old.get("position_id") and "account_id" in patch and patch["account_id"] != old["account_id"]:
                raise ValueError("Unmerge before moving a constituent trade to another account")
        if entity in ("accounts", "journal_templates") and (patch.get("is_default") or (old and old.get("is_default") and "template_type" in patch)):
            scope = "" if entity == "accounts" else " WHERE template_type=?"
            params = () if entity == "accounts" else ((patch.get("template_type") or old["template_type"]),)
            db.execute(f'UPDATE "{entity}" SET is_default=0{scope}', params)
        if old:
            if patch:
                assignments = [f'"{key}"=?' for key in patch]
                if "updated_at" in columns(entity):
                    assignments.append("updated_at=datetime('now')")
                db.execute(f'UPDATE "{entity}" SET {", ".join(assignments)} WHERE id=?', [*patch.values(), record_id])
        else:
            if not patch:
                raise ValueError("Record data cannot be empty")
            names = ",".join(f'"{key}"' for key in patch)
            record_id = db.execute(f'INSERT INTO "{entity}" ({names}) VALUES ({",".join("?" for _ in patch)})', list(patch.values()))
            if "created_at" in columns(entity):
                db.execute(f'UPDATE "{entity}" SET created_at=datetime(\'now\') WHERE id=? AND created_at IS NULL', (record_id,))
        if entity == "positions" and "playbook_id" in patch:
            trades.set_position_playbook(record_id, patch["playbook_id"])
            db.execute("UPDATE trades SET playbook_rules_met=NULL,risk_score=NULL WHERE position_id=?", (record_id,))
        if entity == "trades":
            if "account_id" in patch:
                db.execute("UPDATE journal_entries SET account_id=? WHERE trade_id=?", (patch["account_id"], record_id))
            saved = get_record("trades", record_id)
            if saved["status"] == "closed" and set(patch) & {"pnl", "planned_risk"}:
                risk = saved.get("planned_risk")
                db.execute("UPDATE trades SET r_multiple=? WHERE id=?", (round(saved["pnl"] / risk, 4) if risk and risk > 0 else None, record_id))
            _refresh_position((old or {}).get("position_id"))
            if "playbook_id" in patch:
                db.execute("UPDATE trades SET playbook_rules_met=NULL,risk_score=NULL WHERE id=?", (record_id,))
        if entity in ("playbook_rules", "playbook_risk_rules"):
            _refresh_rules(get_record(entity, record_id)["playbook_id"])
        return details(entity, record_id)


def delete_record(entity: str, record_id: int) -> dict:
    if entity in PATTERN_TABLES:
        from journal_mcp.patterns import PatternError
        raise PatternError("validation_failure", "Pattern history is retained; archive definitions/occurrences instead of deleting")
    if entity in READ_ONLY:
        raise ValueError("Generated records cannot be deleted with this tool")
    with db.transaction():
        old = get_record(entity, record_id)
        if entity == "accounts":
            for table in ("trades", "journal_entries", "risk_setups", "replay_sessions"):
                db.execute(f'UPDATE "{table}" SET account_id=NULL WHERE account_id=?', (record_id,))
        elif entity == "trades":
            db.execute("UPDATE journal_entries SET trade_id=NULL WHERE trade_id=?", (record_id,))
        elif entity == "positions":
            # Keep the individual trades and their playbooks.
            db.execute("UPDATE trades SET position_id=NULL WHERE position_id=?", (record_id,))
            db.execute("UPDATE journal_entries SET position_id=NULL WHERE position_id=?", (record_id,))
        elif entity == "playbooks":
            db.execute("UPDATE trades SET playbook_id=NULL,playbook_rules_met=NULL,risk_score=NULL WHERE playbook_id=?", (record_id,))
            for table in ("positions", "journal_entries"):
                db.execute(f'UPDATE "{table}" SET playbook_id=NULL WHERE playbook_id=?', (record_id,))
            db.execute("DELETE FROM playbook_risk_rules WHERE playbook_id=?", (record_id,))
            db.execute("DELETE FROM playbook_rules WHERE playbook_id=?", (record_id,))
        elif entity == "playbook_rules":
            # A threshold whose trigger disappeared must not silently become percentage-based.
            db.execute("DELETE FROM playbook_risk_rules WHERE trigger_rule_id=?", (record_id,))
        db.execute(f'DELETE FROM "{entity}" WHERE id=?', (record_id,))
        if entity == "trades":
            _refresh_position(old.get("position_id"))
        if entity in ("playbook_rules", "playbook_risk_rules"):
            _refresh_rules(old["playbook_id"])
        return {"deleted": True, "entity": entity, "id": record_id}


def trade_action(action: str, trade_id: int, exit_price: float | None = None,
                 exit_time: str | None = None, close_quantity: float | None = None,
                 pnl: float | None = None, extra_commission: float = 0,
                 expected_revision: int | None = None, idempotency_key: str | None = None) -> dict:
    with db.transaction():
        fingerprint, cached = guards.replay(idempotency_key, "trade_action", [action, trade_id, exit_price, exit_time, close_quantity, pnl, extra_commission, expected_revision])
        if cached is not None:
            return cached
        guards.check_revision(get_record("trades", trade_id), expected_revision)
        result = _trade_action(action, trade_id, exit_price, exit_time, close_quantity, pnl, extra_commission)
        return guards.remember(idempotency_key, fingerprint, result)


def _trade_action(action, trade_id, exit_price=None, exit_time=None, close_quantity=None, pnl=None, extra_commission=0):
    with db.transaction():
        old = get_record("trades", trade_id)
        if action == "reopen":
            trades.reopen_trade(trade_id)
            db.execute("UPDATE trades SET r_multiple=NULL WHERE id=?", (trade_id,))
            return details("trades", trade_id)
        if action not in ("close", "partial_close"):
            raise ValueError("action must be close, partial_close, or reopen")
        if old["status"] != "open":
            raise ValueError("Only an open trade can be closed")
        for key, val in (("exit_price", exit_price), ("extra_commission", extra_commission)):
            if val is None or not math.isfinite(val) or val < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        if pnl is not None and not math.isfinite(pnl):
            raise ValueError("pnl must be finite")
        if old.get("instrument_spec_id"):
            from journal_mcp.neo import validate_trade_economics
            validate_trade_economics({**old, "exit_price": exit_price,
                                      "quantity": close_quantity if action == "partial_close" else old["quantity"]})
        entry_zone = datetime.fromisoformat(old["entry_time"]).tzinfo if old.get("entry_time") else None
        exit_time = exit_time or datetime.now(tz=entry_zone).isoformat(timespec="seconds")
        datetime.fromisoformat(exit_time)
        if old.get("entry_time"):
            validate_exit_time(old["entry_time"], exit_time)
        result_id = trade_id
        if action == "partial_close":
            if close_quantity is None or not math.isfinite(close_quantity) or not 0 < close_quantity < old["quantity"]:
                raise ValueError("close_quantity must be positive and below the open quantity")
            result_id = trades.partial_close_trade(trade_id, close_quantity, exit_price, exit_time, extra_commission)
            ratio = close_quantity / old["quantity"]
            # Preserve replay/risk/rule metadata and split carrying costs proportionally.
            copied = {k: old.get(k) for k in ("trade_type", "stop_price", "take_profit", "is_replay", "replay_day", "playbook_rules_met", "risk_score")}
            copied["planned_risk"] = (old["planned_risk"] * ratio) if old.get("planned_risk") else None
            copied["swap"] = (old.get("swap") or 0) * ratio
            db.execute(f'UPDATE trades SET {",".join(k + "=?" for k in copied)} WHERE id=?', [*copied.values(), result_id])
            db.execute("UPDATE trades SET swap=?,planned_risk=? WHERE id=?",
                       ((old.get("swap") or 0) * (1 - ratio), old["planned_risk"] * (1 - ratio) if old.get("planned_risk") else None, trade_id))
            if pnl is not None:
                db.execute("UPDATE trades SET pnl=? WHERE id=?", (pnl, result_id))
        else:
            trades.close_trade(trade_id, exit_price, exit_time, pnl, extra_commission)
        row = get_record("trades", result_id)
        if row.get("planned_risk") and row["planned_risk"] > 0:
            db.execute("UPDATE trades SET r_multiple=? WHERE id=?", (round(row["pnl"] / row["planned_risk"], 4), result_id))
        _refresh_position(old.get("position_id"))
        return {"trade": get_record("trades", trade_id), "closed_trade": get_record("trades", result_id)}


def position_action(action: str, trade_ids: list[int] | None = None, position_id: int | None = None,
                    notes: str = "", playbook_id: int | None = None) -> dict:
    if action == "unmerge":
        if position_id is None:
            raise ValueError("position_id is required")
        return delete_record("positions", position_id)
    if action != "merge":
        raise ValueError("action must be merge or unmerge")
    with db.transaction():
        ids = list(dict.fromkeys(trade_ids or []))
        if len(ids) < 2 or len(ids) > 500:
            raise ValueError("Merge requires 2–500 distinct trade IDs")
        rows = [get_record("trades", tid) for tid in ids]
        if len({row.get("account_id") for row in rows}) != 1:
            raise ValueError("Cannot merge trades from different accounts")
        if any(row.get("position_id") for row in rows):
            raise ValueError("Unmerge existing positions before merging their trades")
        if playbook_id is not None:
            get_record("playbooks", playbook_id)
        position_id = trades.merge_trades_into_position(ids, notes, playbook_id)
        db.execute("UPDATE trades SET playbook_rules_met=NULL,risk_score=NULL WHERE position_id=?", (position_id,))
        return details("positions", position_id)


def assign_playbook(playbook_id: int | None, rules_met: dict[str, bool] | None = None,
                    trade_id: int | None = None, position_id: int | None = None) -> dict:
    if (trade_id is None) == (position_id is None):
        raise ValueError("Provide exactly one of trade_id or position_id")
    with db.transaction():
        if trade_id is not None:
            trade = get_record("trades", trade_id)
            if trade.get("position_id"):
                raise ValueError("Assign the playbook to the entire merged position")
            ids = [trade_id]
        else:
            get_record("positions", position_id)
            ids = [row["id"] for row in db.fetch_all("SELECT id FROM trades WHERE position_id=?", (position_id,))]
            trades.set_position_playbook(position_id, playbook_id)
        result = evaluate_playbook(playbook_id, rules_met or {}) if playbook_id is not None else {}
        for tid in ids:
            if playbook_id is None:
                db.execute("UPDATE trades SET playbook_id=NULL,playbook_rules_met=NULL,risk_score=NULL WHERE id=?", (tid,))
            else:
                trades.update_trade_playbook(tid, playbook_id, rules_met or {}, result["risk_score"])
        return {"trade_ids": ids, "assessment": result}


def evaluate_playbook(playbook_id: int, rules_met: dict[str, bool] | None = None,
                      evidence: dict | None = None) -> dict:
    pb = details("playbooks", playbook_id)
    if evidence is not None:
        if rules_met is not None:
            raise ValueError("Supply rules_met or four-state evidence, not both")
        from journal_mcp.neo import assess_evidence
        return assess_evidence(pb, evidence)
    rules_met = rules_met or {}
    valid = {str(rule["id"]) for rule in pb["rules"]}
    if set(rules_met) - valid or any(not isinstance(v, bool) for v in rules_met.values()):
        raise ValueError("rules_met must map this playbook's rule IDs to true/false")
    result = playbooks.evaluate_trade_risk(playbook_id, rules_met)
    from journal_mcp.neo import assess_evidence
    # Preserve the existing boolean interface: omitted booleans mean unmet.
    # Four-state callers use evidence, where omissions remain unknown.
    result.update(assess_evidence(pb, {key: {"status": "pass" if rules_met.get(key, False) else "fail"} for key in valid}))
    return result


def validate_exit_time(entry_time: str, exit_time: str):
    entry, exit = datetime.fromisoformat(entry_time), datetime.fromisoformat(exit_time)
    if bool(entry.tzinfo) != bool(exit.tzinfo):
        raise ValueError("Entry and exit times must both use timezone offsets or both use local time")
    if exit < entry:
        raise ValueError("exit_time cannot precede entry_time")
