"""Native psychology records, explicit links and deterministic account-scoped reports."""
from __future__ import annotations

import functools
import inspect
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError
import database as db
from journal_mcp import guards, neo, records as r
from journal_mcp.pattern_models import (
    REFLECTIONS, SCHEMA_VERSION, PatternData, PatternEntryData, FollowupData,
    PatternFilters, MigrationSelection,
)

ENTITIES = {"psychology_patterns", "pattern_entries", "pattern_followups", "pattern_revisions"}
ARRAYS = {"tags", "trade_ids", "trigger_tags", "emotion_tags", "behaviour_tags", "related_pattern_entry_ids", "management_event_ids", "evidence_ids"}
OBJECTS = {"field_provenance", "field_states"}
OBSERVATIONS = {*REFLECTIONS, "observed_result", "next_session_intention", "setup_description", "legacy_reflection_text"}


class PatternError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.payload = {"code": code, "message": message, **details}


def validated(model, value):
    try:
        obj = value if isinstance(value, model) else model.model_validate(value)
        return obj.model_dump(exclude_unset=True)
    except ValidationError as exc:
        raise PatternError("validation_failure", "Input does not match the pattern schema", fields=[{"path": list(error["loc"]), "message": error["msg"]} for error in exc.errors()]) from exc


def atomic(function):
    signature = inspect.signature(function, eval_str=True)
    @functools.wraps(function)
    def call(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        for key, value in list(bound.arguments.items()):
            annotation = signature.parameters[key].annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                bound.arguments[key] = validated(annotation, value)
        values = dict(bound.arguments)
        request_key = values.pop("idempotency_key")
        if not isinstance(request_key, str) or not request_key.strip():
            raise PatternError("validation_failure", "A stable idempotency_key is required")
        try:
            with db.transaction():
                try:
                    fingerprint, cached = guards.replay(request_key, function.__name__, values)
                except ValueError as exc:
                    code = "idempotency_conflict" if "different input" in str(exc) else "validation_failure"
                    raise PatternError(code, str(exc)) from exc
                if cached is not None:
                    return cached
                result = function(**bound.arguments)
                return guards.remember(request_key, fingerprint, result)
        except PatternError:
            raise
        except (ValueError, TypeError) as exc:
            raise PatternError("validation_failure", str(exc)) from exc
    return call


def get(entity, record_id):
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
        raise PatternError("missing_link", f"Invalid {entity} ID", entity=entity, record_id=record_id)
    row = db.fetch_one(f"SELECT * FROM {entity} WHERE id=?", (record_id,))
    if row is None:
        raise PatternError("missing_link", f"{entity} #{record_id} does not exist", entity=entity, record_id=record_id)
    return decode(row)


def decode(row):
    result = {key: json.loads(value) if key in ARRAYS | OBJECTS and isinstance(value, str) else value for key, value in row.items()}
    for key in ("response_practised", "response_helpful"):
        if result.get(key) is not None:
            result[key] = bool(result[key])
    return result


def serialise(values):
    result = dict(values)
    for field in ARRAYS | OBJECTS:
        if field in result:
            result[field] = neo.encode(result[field] if result[field] is not None else {} if field in OBJECTS else [])
    return result


def revision(old, expected):
    if old is None:
        if expected is not None:
            raise PatternError("revision_conflict", "Creation has no expected revision", current_revision=None)
    elif isinstance(expected, bool) or not isinstance(expected, int) or expected != old["revision"]:
        raise PatternError("revision_conflict", "Read and merge the current record before retrying", current_revision=old["revision"])


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise PatternError("validation_failure", f"{field} requires nonempty text", field=field)


def iso_date(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("Use YYYY-MM-DD")
        return parsed
    except (ValueError, TypeError) as exc:
        raise PatternError("validation_failure", "Use a valid YYYY-MM-DD date") from exc


def arrays(values):
    for field in ARRAYS:
        if values.get(field) is None:
            continue
        if len(values[field]) != len(set(values[field])):
            raise PatternError("validation_failure", f"{field} contains duplicate values", field=field)
        if field.endswith("_ids"):
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in values[field]):
                raise PatternError("validation_failure", f"{field} requires positive integer IDs")
        elif any(not isinstance(v, str) or not v.strip() for v in values[field]):
            raise PatternError("validation_failure", f"{field} requires nonempty confirmed labels")


def details(entity, record_id):
    row = get(entity, record_id)
    if entity in {"psychology_patterns", "pattern_entries"}:
        row["history"] = [{**item, "snapshot": decode(json.loads(item["snapshot"]))} for item in db.fetch_all("SELECT * FROM pattern_revisions WHERE entity=? AND record_id=? ORDER BY revision", (entity, record_id))]
    if entity == "pattern_entries":
        row["followups"] = [decode(item) for item in db.fetch_all("SELECT * FROM pattern_followups WHERE pattern_entry_id=? ORDER BY id", (record_id,))]
        row["owned_evidence_ids"] = [item["id"] for item in db.fetch_all("SELECT id FROM evidence WHERE pattern_entry_id=? ORDER BY id", (record_id,))]
        row["definition"] = get("psychology_patterns", row["pattern_id"]) if row["pattern_id"] else None
    return row


def _save(entity, values, old=None):
    values = serialise(values)
    if old:
        values.update(revision=old["revision"] + 1)
        setters = ','.join(key + "=?" for key in values)
        db.execute(f"UPDATE {entity} SET {setters},updated_at=datetime('now') WHERE id=?", [*values.values(), old["id"]])
        record_id = old["id"]
    else:
        record_id = neo.insert(entity, values)
    return details(entity, record_id)


@atomic
def save_psychology_pattern(data: PatternData, idempotency_key: str, pattern_id: int | None = None,
                            expected_revision: int | None = None) -> dict:
    old = get("psychology_patterns", pattern_id) if pattern_id is not None else None
    revision(old, expected_revision)
    if old and "pattern_key" in data and data["pattern_key"] != old["pattern_key"]:
        raise PatternError("validation_failure", "pattern_key is immutable")
    full = {**(old or {}), **data}
    for field in ("pattern_key", "label"):
        text(full.get(field), field)
    if full.get("status", "active") is None:
        raise PatternError("validation_failure", "status cannot be null")
    arrays(data)
    existing = db.fetch_one("SELECT id FROM psychology_patterns WHERE pattern_key=?", (full["pattern_key"],))
    if existing and (not old or existing["id"] != old["id"]):
        raise PatternError("idempotency_conflict", "pattern_key already exists; update its existing ID", record_id=existing["id"])
    return _save("psychology_patterns", data, old)


def validate_context(full, record_id=None):
    """Validate every relationship against the explicit account and selected context."""
    account_id = full["account_id"]
    get("accounts", account_id)
    candidate = get("candidates", full["candidate_id"]) if full.get("candidate_id") else None
    session_id = full.get("session_id")
    if candidate and session_id and candidate["session_id"] != session_id:
        raise PatternError("account_mismatch", "Candidate belongs to a different session")
    if candidate:
        if candidate["account_id"] != account_id:
            raise PatternError("account_mismatch", "Candidate belongs to a different account")
        if full.get("symbol") and full["symbol"] != candidate["symbol"]:
            raise PatternError("account_mismatch", "Candidate and pattern exact symbols differ")
    if session_id and get("trading_sessions", session_id)["account_id"] != account_id:
        raise PatternError("account_mismatch", "Session belongs to a different account")
    trades = [get("trades", key) for key in full.get("trade_ids") or []]
    if full.get("position_id"):
        get("positions", full["position_id"])
        parts = db.fetch_all("SELECT * FROM trades WHERE position_id=?", (full["position_id"],))
        if not parts:
            raise PatternError("missing_link", "Position has no trades")
        if trades and any(trade["position_id"] != full["position_id"] for trade in trades):
            raise PatternError("account_mismatch", "Trade is outside the selected position")
        trades += parts
    for trade in trades:
        if trade["account_id"] != account_id or (full.get("symbol") and full["symbol"] != trade["symbol"]):
            raise PatternError("account_mismatch", "Trade account or exact symbol conflicts with the occurrence")
        if candidate and not db.fetch_one("SELECT id FROM fill_events WHERE candidate_id=? AND trade_id=?", (candidate["id"], trade["id"])):
            raise PatternError("account_mismatch", "Trade has no fill link to the selected candidate")
    journal_ids = {key for key in (full.get("parent_journal_entry_id"), full.get("context_journal_entry_id")) if key}
    def journal_context(entry):
        if entry.get("account_id") != account_id:
            raise PatternError("account_mismatch", "Linked journal/evidence account differs")
        if session_id and entry.get("session_id") and session_id != entry["session_id"]:
            raise PatternError("account_mismatch", "Journal/evidence belongs to a different session")
        if candidate and entry.get("candidate_id") and entry["candidate_id"] != candidate["id"]:
            raise PatternError("account_mismatch", "Journal/evidence belongs to a different candidate")
        if entry.get("trade_id") and full.get("trade_ids") and entry["trade_id"] not in full["trade_ids"]:
            raise PatternError("account_mismatch", "Journal/evidence belongs to a different linked trade")
        if candidate and entry.get("trade_id") and not db.fetch_one("SELECT id FROM fill_events WHERE candidate_id=? AND trade_id=?", (candidate["id"], entry["trade_id"])):
            raise PatternError("account_mismatch", "Journal trade has no fill link to the selected candidate")
        if full.get("symbol") and entry.get("symbol") and entry["symbol"] != full["symbol"]:
            raise PatternError("account_mismatch", "Journal/evidence symbol differs")
    for key in journal_ids:
        journal_context(get("journal_entries", key))
    for key in full.get("related_pattern_entry_ids") or []:
        if key == record_id:
            raise PatternError("validation_failure", "An occurrence cannot be related to itself")
        if get("pattern_entries", key)["account_id"] != account_id:
            raise PatternError("account_mismatch", "Related occurrence belongs to a different account")
    for key in full.get("management_event_ids") or []:
        event = get("management_events", key)
        journal_context(get("journal_entries", event["journal_entry_id"]))
    for key in full.get("evidence_ids") or []:
        evidence = get("evidence", key)
        if evidence.get("journal_entry_id"):
            journal_context(get("journal_entries", evidence["journal_entry_id"]))
        if evidence.get("pattern_entry_id"):
            owner = get("pattern_entries", evidence["pattern_entry_id"])
            if owner["account_id"] != account_id:
                raise PatternError("account_mismatch", "Pattern evidence belongs to a different account")
            if session_id and owner.get("session_id") and session_id != owner["session_id"]:
                raise PatternError("account_mismatch", "Pattern evidence belongs to a different session")
            if candidate and owner.get("candidate_id") and candidate["id"] != owner["candidate_id"]:
                raise PatternError("account_mismatch", "Pattern evidence belongs to a different candidate")
        if full.get("symbol") and evidence["symbol"] != full["symbol"]:
            raise PatternError("account_mismatch", "Evidence exact symbol differs")


def validate_entry(data, old=None, record_id=None):
    full = {**(old or {}), **data}
    for field in ("entry_key", "event_date", "timezone"):
        text(full.get(field), field)
    if old and any(field in data and data[field] != old[field] for field in ("entry_key", "account_id")):
        raise PatternError("validation_failure", "Occurrence key and account are immutable")
    if full.get("account_id") is None:
        raise PatternError("validation_failure", "account_id is required")
    try:
        iso_date(full["event_date"])
        neo.timezone(full["timezone"])
        if full.get("occurred_at"):
            neo.timestamp(full["occurred_at"])
            local = datetime.fromisoformat(full["occurred_at"]).astimezone(ZoneInfo(full["timezone"])).date().isoformat()
            if local != full["event_date"]:
                raise PatternError("validation_failure", "occurred_at does not match event_date in the stored timezone")
    except (ValueError, TypeError) as exc:
        raise PatternError("validation_failure", str(exc)) from exc
    if not any(isinstance(full.get(field), str) and full[field].strip() for field in OBSERVATIONS):
        raise PatternError("validation_failure", "At least one substantive observation is required; partial drafts are allowed")
    for field in ("urge_response", "plan_adherence", "rule_breach", "status", "follow_up_status"):
        if field in data and data[field] is None:
            raise PatternError("validation_failure", f"{field} cannot be null; use its explicit unknown state where supported")
    arrays(full)
    if full.get("pattern_id"):
        get("psychology_patterns", full["pattern_id"])
    if full.get("template_id"):
        template = get("journal_templates", full["template_id"])
        if "template_id" in data and "template_version" not in data:
            data["template_version"] = f"{template.get('version') or '1'}@{template['revision']}"
    states = full.get("field_states") or {}
    if set(states) - OBSERVATIONS:
        raise PatternError("validation_failure", "field_states keys must name reflection/observation fields")
    for field, state in states.items():
        if state == "provided" and not full.get(field):
            raise PatternError("validation_failure", f"{field} is marked provided without a value")
        if state != "provided" and full.get(field):
            raise PatternError("validation_failure", f"{field} has text but is marked {state}")
    provenance = full.get("field_provenance") or {}
    for field, value in provenance.items():
        if field not in PatternEntryData.model_fields:
            raise PatternError("validation_failure", "Unknown field in field_provenance", field=field)
        if value.get("observed_at"):
            neo.timestamp(value["observed_at"])
        if value["kind"] == "assistant_interpretation" and field in REFLECTIONS:
            raise PatternError("validation_failure", "Store assistant interpretation in interpretation, not user reflection fields", field=field)
    if old:
        for field in REFLECTIONS:
            previous = (old.get("field_provenance") or {}).get(field, {})
            current = provenance.get(field, {})
            if field in data and previous.get("kind") == "user_report" and current.get("kind") == "assistant_interpretation":
                raise PatternError("validation_failure", "An interpretation cannot overwrite a user report", field=field)
    if full.get("proposed_rule_status") == "adopted":
        for field in ("proposed_rule", "adoption_decision", "adoption_reference", "adopted_at"):
            text(full.get(field), field)
        neo.timestamp(full["adopted_at"])
    validate_context(full, record_id)
    return full


@atomic
def save_pattern_entry(data: PatternEntryData, idempotency_key: str, pattern_entry_id: int | None = None,
                        expected_revision: int | None = None) -> dict:
    old = get("pattern_entries", pattern_entry_id) if pattern_entry_id is not None else None
    revision(old, expected_revision)
    full = validate_entry(data, old, pattern_entry_id)
    duplicate = db.fetch_one("SELECT id FROM pattern_entries WHERE entry_key=?", (full["entry_key"],))
    if duplicate and (not old or duplicate["id"] != old["id"]):
        raise PatternError("idempotency_conflict", "entry_key already exists; update its ID", record_id=duplicate["id"])
    return _save("pattern_entries", data, old)


@atomic
def append_pattern_followup(data: FollowupData, idempotency_key: str,
                            expected_revision: int | None = None) -> dict:
    for field in ("followup_key", "note"):
        text(data[field], field)
    parent = get("pattern_entries", data["pattern_entry_id"])
    update = data.pop("parent_update", None)
    if update:
        revision(parent, expected_revision)
        validate_entry(update, parent, parent["id"])
    elif expected_revision is not None:
        revision(parent, expected_revision)
    if data.get("observed_at"):
        neo.timestamp(data["observed_at"])
    arrays(data)
    validate_context({**parent, "evidence_ids": data.get("evidence_ids") or []}, parent["id"])
    if data.get("corrects_followup_id"):
        corrected = get("pattern_followups", data["corrects_followup_id"])
        if corrected["pattern_entry_id"] != parent["id"]:
            raise PatternError("account_mismatch", "Correction belongs to another occurrence")
        if db.fetch_one("SELECT id FROM pattern_followups WHERE corrects_followup_id=?", (corrected["id"],)):
            raise PatternError("revision_conflict", "Correct the latest correction in this chain")
    if db.fetch_one("SELECT id FROM pattern_followups WHERE followup_key=?", (data["followup_key"],)):
        raise PatternError("idempotency_conflict", "followup_key already exists; reuse the original idempotency key")
    record_id = neo.insert("pattern_followups", serialise(data))
    if update:
        _save("pattern_entries", update, parent)
    return {"id": record_id, "revision": 1, "followup": details("pattern_followups", record_id),
            "parent": details("pattern_entries", parent["id"])}


def query_entries(filters=None, start_date=None, end_date=None, include_archived=False):
    values = validated(PatternFilters, filters or {})
    clauses, params = [], []
    if not include_archived and values.get("status") != "archived":
        clauses.append("status<>'archived'")
    for field, value in values.items():
        if field in {"trigger_tags", "emotion_tags", "behaviour_tags"}:
            for tag in value or []:
                text(tag, field)
                clauses.append(f"EXISTS(SELECT 1 FROM json_each({field}) WHERE value=?)")
                params.append(tag)
        else:
            clauses.append(field + (" IS NULL" if value is None else "=?"))
            if value is not None:
                params.append(value)
    for value, operator in ((start_date, ">="), (end_date, "<=")):
        if value:
            iso_date(value)
            clauses.append("event_date" + operator + "?")
            params.append(value)
    if start_date and end_date and start_date > end_date:
        raise PatternError("validation_failure", "start_date must be on or before end_date")
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def event_order(row):
    # Date-only events remain date-only. They sort before known times on that date.
    time_key = datetime.fromisoformat(row["occurred_at"]).timestamp() if row.get("occurred_at") else float('-inf')
    return row["event_date"], time_key, row["id"]


def list_records(entity, filters=None, start_date=None, end_date=None, search=None, limit=100, offset=0):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500 or not isinstance(offset, int) or offset < 0:
        raise PatternError("validation_failure", "limit must be 1–500; offset must be nonnegative")
    filters = dict(filters or {})
    include_archived = filters.pop("include_archived", False)
    if not isinstance(include_archived, bool):
        raise PatternError("validation_failure", "include_archived must be boolean")
    if entity == "pattern_entries":
        # Identity filters complement the report's typed context filters.
        identity = {key: filters.pop(key) for key in ("id", "entry_key", "legacy_journal_entry_id") if key in filters}
        where, params = query_entries(filters, start_date, end_date, include_archived)
        for key, value in identity.items():
            if key == "entry_key":
                text(value, key)
            elif value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise PatternError("validation_failure", f"{key} must be a positive integer or null")
            where += (" AND " if where else " WHERE ") + key + (" IS NULL" if value is None else "=?")
            if value is not None:
                params.append(value)
    else:
        allowed = set(r.columns(entity))
        if entity == "pattern_followups":
            allowed.add("account_id")
        if set(filters) - allowed:
            raise PatternError("validation_failure", "Unknown pattern filter", fields=sorted(set(filters) - allowed))
        clauses, params = [], []
        for field, value in filters.items():
            if field == "account_id" and entity == "pattern_followups":
                clauses.append("pattern_entry_id IN (SELECT id FROM pattern_entries WHERE account_id=?)")
                params.append(value)
            elif field in ARRAYS:
                if not isinstance(value, list):
                    raise PatternError("validation_failure", "Tag filters require arrays")
                for item in value:
                    clauses.append(f"EXISTS(SELECT 1 FROM json_each({field}) WHERE value=?)")
                    params.append(item)
            else:
                if isinstance(value, (dict, list)):
                    raise PatternError("validation_failure", "Scalar filter expected")
                clauses.append(field + (" IS NULL" if value is None else "=?"))
                if value is not None:
                    params.append(value)
        if entity == "psychology_patterns" and "status" not in filters and not include_archived:
            clauses.append("status='active'")
        if start_date or end_date:
            if entity != "pattern_followups":
                raise PatternError("validation_failure", "Event-date filters apply to entries; follow-up dates use observed_at only")
            for value, operator in ((start_date, ">="), (end_date, "<=")):
                if value:
                    iso_date(value)
                    clauses.append("substr(observed_at,1,10)" + operator + "?")
                    params.append(value)
            if start_date and end_date and start_date > end_date:
                raise PatternError("validation_failure", "Invalid date interval")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
    if search:
        # Plain substring search across text columns; no SQL interpolation of user text.
        columns = [key for key, info in r.columns(entity).items() if info["type"] == "TEXT"]
        where += (" AND " if where else " WHERE ") + '(' + ' OR '.join(f"instr(lower(COALESCE({key},'')),lower(?))>0" for key in columns) + ')'
        params += [search] * len(columns)
    with db.transaction(write=False):
        total = db.fetch_one(f"SELECT COUNT(*) n FROM {entity}{where}", params)["n"]
        if entity == "pattern_entries":
            order = "event_date,CASE WHEN occurred_at IS NULL THEN 0 ELSE 1 END,julianday(occurred_at),id"
        elif entity == "pattern_followups":
            order = "CASE WHEN observed_at IS NULL THEN 0 ELSE 1 END,julianday(observed_at),id"
        else:
            order = "id"
        rows = db.fetch_all(f"SELECT * FROM {entity}{where} ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"records": [decode(row) for row in rows], "total": total, "offset": offset,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "sort": order, "schema_version": SCHEMA_VERSION}


def _metrics(rows):
    def rate(field, positive, negative, states):
        counts = {state: 0 for state in states}
        counts.update(Counter(row[field] for row in rows))
        denominator = counts[positive] + counts[negative]
        return {"counts": counts, "numerator": counts[positive], "denominator": denominator,
                "rate": counts[positive] / denominator if denominator else None}
    trades = {key for row in rows for key in row["trade_ids"]}
    positions = {row["position_id"] for row in rows if row.get("position_id")}
    for position_id in positions:
        trades.update(item["id"] for item in db.fetch_all("SELECT id FROM trades WHERE position_id=?", (position_id,)))
    return {"occurrences": len(rows), "entry_ids": [row["id"] for row in rows],
            "session_ids": sorted({row["session_id"] for row in rows if row.get("session_id")}),
            "trade_ids": sorted(trades), "distinct_sessions": len({row["session_id"] for row in rows if row.get("session_id")}),
            "distinct_trades": len(trades),
            "urge_response": rate("urge_response", "resisted", "acted_on", ["resisted", "acted_on", "no_urge", "unknown"]),
            "plan_adherence": rate("plan_adherence", "followed", "deviated", ["followed", "deviated", "not_applicable", "unknown"]),
            "rule_breach": dict(Counter(row["rule_breach"] for row in rows)),
            "sample_size": len(rows),
            "missing_classifications": {field: sum(row[field] == "unknown" for row in rows) for field in ("urge_response", "plan_adherence", "rule_breach")}}


def generate_pattern_report(account_ids: list[int], start_date: str, end_date: str,
                            group_by: str = "pattern", filters: PatternFilters | None = None,
                            date_basis: str = "event_date", include_examples: bool = False,
                            limit: int = 100, offset: int = 0, include_archived: bool = False) -> dict:
    """Aggregate logged occurrences; classifications are supplied facts, never inferred."""
    if not account_ids or any(isinstance(key, bool) or not isinstance(key, int) or key < 1 for key in account_ids):
        raise PatternError("validation_failure", "An explicit nonempty account_ids scope is required")
    accounts = sorted(set(account_ids))
    for key in accounts:
        get("accounts", key)
    start, end = iso_date(start_date), iso_date(end_date)
    if start > end or date_basis not in {"event_date", "session_exchange_date"}:
        raise PatternError("validation_failure", "Use an ordered inclusive date range and event_date or session_exchange_date")
    if group_by not in {"pattern", "day", "week", "month", "trigger_tags", "emotion_tags", "behaviour_tags"}:
        raise PatternError("validation_failure", "Unknown group_by")
    if not 1 <= limit <= 500 or offset < 0:
        raise PatternError("validation_failure", "limit must be 1–500 and offset nonnegative")
    selected = validated(PatternFilters, filters or {})
    if selected.get("account_id") is not None and selected["account_id"] not in accounts:
        raise PatternError("account_mismatch", "Filter account is outside account_ids scope")
    with db.transaction(write=False):
        where, params = query_entries(selected, include_archived=include_archived)
        where += (" AND " if where else " WHERE ") + f"account_id IN ({','.join('?' for _ in accounts)})"
        rows = db.fetch_all(f"SELECT * FROM pattern_entries{where} ORDER BY event_date,id LIMIT 20001", [*params, *accounts])
        if len(rows) > 20000:
            raise PatternError("validation_failure", "Report exceeds 20,000 occurrences; narrow filters")
        rows = [decode(row) for row in rows]
        missing_context = []
        sessions = {row["id"]: row for row in db.fetch_all("SELECT id,exchange_date FROM trading_sessions")}
        def basis(row):
            return row["event_date"] if date_basis == "event_date" else sessions.get(row.get("session_id"), {}).get("exchange_date")
        eligible = []
        for row in rows:
            row["report_date"] = basis(row)
            if row["report_date"] is None:
                missing_context.append(row["id"])
            else:
                eligible.append(row)
        span = (end - start).days + 1
        prior_start, prior_end = (start - timedelta(days=span)).isoformat(), (start - timedelta(days=1)).isoformat()
        current = sorted([row for row in eligible if start_date <= row["report_date"] <= end_date], key=lambda row: (row["report_date"], *event_order(row)))
        prior = [row for row in eligible if prior_start <= row["report_date"] <= prior_end]
        totals = _metrics(current)
        definitions = {row["id"]: decode(row) for row in db.fetch_all("SELECT * FROM psychology_patterns")}
        recurrence_groups, groups, tags = defaultdict(list), defaultdict(list), {}
        for row in current:
            recurrence_groups[row["pattern_id"]].append(row)
            if group_by == "pattern":
                keys = [str(row["pattern_id"]) if row["pattern_id"] else "unclassified"]
            elif group_by.endswith("_tags"):
                keys = row[group_by] or ["untagged"]
            elif group_by == "week":
                day = date.fromisoformat(row["report_date"])
                keys = [(day - timedelta(days=day.weekday())).isoformat()]
            else:
                keys = [row["report_date"][:7] if group_by == "month" else row["report_date"]]
            for key in keys:
                groups[key].append(row)
        for field in ("trigger_tags", "emotion_tags", "behaviour_tags"):
            counter = defaultdict(list)
            for row in current:
                for tag in row[field]:
                    counter[tag].append(row["id"])
            tags[field] = [{"tag": key, "count": len(ids), "entry_ids": ids} for key, ids in sorted(counter.items())]
        followup_rows = [decode(row) for row in db.fetch_all("SELECT * FROM pattern_followups ORDER BY id")]
        superseded = {row["corrects_followup_id"] for row in followup_rows if row["corrects_followup_id"]}
        active_followups = [row for row in followup_rows if row["id"] not in superseded]
        page = current[offset:offset + limit]
        page_ids = {row["id"] for row in page}
        queue = [{"pattern_entry_id": row["id"], "pattern_id": row["pattern_id"], "session_id": row["session_id"],
                  "candidate_id": row["candidate_id"], "followup_ids": [f["id"] for f in active_followups if f["pattern_entry_id"] == row["id"]],
                  "next_session_intention": row["next_session_intention"],
                  "practice_response": definitions.get(row["pattern_id"], {}).get("practice_response")}
                 for row in page if row["follow_up_status"] == "pending"]
        useful = [{"followup_id": followup["id"], "pattern_entry_id": followup["pattern_entry_id"],
                   "response_practised": followup["response_practised"], "response_helpful": followup["response_helpful"],
                   **({"note": followup["note"][:500], "observed_result": (followup["observed_result"] or '')[:500]} if include_examples else {})}
                  for followup in active_followups if followup["pattern_entry_id"] in page_ids and (followup["response_practised"] == 1 or followup["response_helpful"] == 1)][:limit]
        recurrence = []
        for pattern_id, items in sorted(recurrence_groups.items(), key=lambda pair: pair[0] or 0):
            definition = definitions.get(pattern_id, {})
            recurrence.append({"pattern_id": pattern_id, "pattern_key": definition.get("pattern_key"), "label": definition.get("label"),
                               "practice_response": definition.get("practice_response"), "first_date": min(row["report_date"] for row in items),
                               "last_date": max(row["report_date"] for row in items), "period_counts": dict(sorted(Counter(row["report_date"][:7] for row in items).items())),
                               "count": len(items), "entry_ids": [row["id"] for row in items]})
        logged_days = len({row["report_date"] for row in current})
        return {"schema_version": SCHEMA_VERSION, "account_ids": accounts, "start_date": start_date, "end_date": end_date,
                "date_basis": date_basis, "timezone_handling": "Stored local event_date; session basis uses linked exchange_date. Date-only events are never shifted or assigned a time.",
                "totals": totals, "recurrence": recurrence, "tag_breakdown": tags,
                "archived": _metrics([row for row in current if row["status"] == "archived"]),
                "groups": [{"key": key, **_metrics(value)} for key, value in sorted(groups.items())],
                "groups_overlap": group_by.endswith("_tags"), "tag_rows_overlap": True,
                "follow_up_queue": queue, "useful_responses": useful,
                "comparison": {"current": totals, "prior_start_date": prior_start, "prior_end_date": prior_end, "prior": _metrics(prior),
                               "current_logged_days": logged_days, "current_days_without_logs": span - logged_days,
                               "prior_logged_days": len({row["report_date"] for row in prior}),
                               "prior_days_without_logs": span - len({row["report_date"] for row in prior}), "absence_means_resolved": False},
                "data_quality": {"drafts": sum(row["status"] == "draft" for row in current),
                                 "partial": sum(any(not row.get(field) for field in REFLECTIONS) for row in current),
                                 "unclassified": sum(row["pattern_id"] is None for row in current),
                                 "legacy": sum(row["legacy_journal_entry_id"] is not None for row in current),
                                 "provenance_present": sum(bool(row["source"] or row["recorded_by"] or row["field_provenance"]) for row in current),
                                 "missing_classifications": totals["missing_classifications"],
                                 "excluded_missing_date_context_ids": missing_context,
                                 "excluded_missing_date_context_count": len(missing_context),
                                 "archived_included": sum(row["status"] == "archived" for row in current)},
                "entries": [{key: row[key] for key in ("id", "event_date", "occurred_at", "timezone", "report_date", "pattern_id", "session_id", "candidate_id", "trade_ids", "follow_up_status")}
                            | ({"situation": (row["situation"] or '')[:500], "better_response": (row["better_response"] or '')[:500]} if include_examples else {}) for row in page],
                "total": len(current), "offset": offset, "next_offset": offset + len(page) if offset + len(page) < len(current) else None,
                "methodology": {"population": "Logged pattern occurrences, not all trades/days; selective logging is not population incidence",
                                "urge_rate": "resisted / (resisted + acted_on); no_urge and unknown excluded",
                                "adherence_rate": "followed / (followed + deviated); not_applicable and unknown excluded",
                                "causal_inference": False, "pnl_attribution": False, "progression_decisions": False}}


def _legacy_mapping(source, metadata, selected_timezone):
    reflection = source.get("psychology") or ''
    labels = {field.replace('_', ' '): field for field in REFLECTIONS}
    labels.update({"emotion/body state": "emotion_body_state", "impulse/temptation": "impulse_temptation",
                   "cost/risk if repeated": "hypothetical_cost_if_repeated", "root belief or fear": "root_belief_or_fear",
                   "future regret/greed check": "future_regret_or_reward", "better response/proposed rule": "better_response"})
    mapped, active, pieces, unknown = {}, None, [], []
    def finish():
        if active and ''.join(pieces).strip():
            mapped[active] = ''.join(pieces).strip()
    for line in reflection.splitlines(keepends=True):
        clean = re.sub(r'^\s*(?:#{1,6}\s*)?(?:\d+[.)]\s*)?', '', line).strip()
        heading, separator, inline = clean.partition(':')
        field = labels.get(heading.strip().lower())
        if field:
            finish()
            active, pieces = field, [inline + '\n'] if separator and inline.strip() else []
        elif re.match(r'^\s*(?:#{1,6}\s|\d+[.)]\s)', line):
            finish()
            active, pieces = None, []
            unknown.append(line.strip())
        elif active:
            pieces.append(line)
        elif line.strip():
            unknown.append(line.strip())
    finish()
    raw = {key: source.get(key) for key in ("analysis", "psychology", "execution", "lessons", "custom_fields", "entry_date", "stage", "template_id", "template_version", "created_at", "updated_at")}
    values = {"entry_key": metadata.get("pattern_key") or f"legacy-pattern-journal-{source['id']}",
              "account_id": source.get("account_id"), "event_date": source["entry_date"],
              "timezone": metadata.get("timezone") or selected_timezone,
              "title": metadata.get("pattern_label"), "occurred_at": metadata.get("observed_at"),
              "source": f"legacy journal_entries #{source['id']}", "field_provenance": {key: {"kind": "imported", "source": f"journal_entries #{source['id']} psychology"} for key in mapped},
              "follow_up_status": metadata.get("follow_up_status", "pending"),
              "legacy_reflection_text": neo.encode(raw), "migration_review_required": int(bool(unknown) or not mapped), **mapped}
    for key in ("session_id", "candidate_id", "position_id", "symbol", "template_id", "template_version", "baseline_version", "playbook_version"):
        if source.get(key) is not None:
            values[key] = source[key]
    if source.get("trade_id"):
        values["trade_ids"] = [source["trade_id"]]
    aliases = {"parent_daily_entry_id": "parent_journal_entry_id", "context_entry_id": "context_journal_entry_id", "evidence_ids": "evidence_ids"}
    for old_key, new_key in aliases.items():
        if metadata.get(old_key) is not None:
            values[new_key] = metadata[old_key]
    # Legacy related IDs are journal IDs; resolve known mappings without inventing native links.
    related, unresolved = [], []
    for key in metadata.get("related_pattern_entry_ids") or []:
        target = db.fetch_one("SELECT id FROM pattern_entries WHERE legacy_journal_entry_id=?", (key,))
        (related if target else unresolved).append(target["id"] if target else key)
    if related:
        values["related_pattern_entry_ids"] = related
    if unresolved:
        values["migration_review_required"] = 1
    known_metadata = {"schema_version", "pattern_key", "timezone", "observed_at", "pattern_label", "follow_up_status", "parent_daily_entry_id", "context_entry_id", "evidence_ids", "related_pattern_entry_ids", "next_session_intention", "source", "recorded_by"}
    unmapped_metadata = sorted(set(metadata) - known_metadata)
    if unmapped_metadata:
        values["migration_review_required"] = 1
    for field in ("next_session_intention", "recorded_by"):
        if metadata.get(field) is not None:
            values[field] = metadata[field]
    return values, {"unmapped_headings_or_text": unknown, "unresolved_legacy_related_ids": unresolved,
                    "unmapped_metadata_fields": unmapped_metadata,
                    "preserved_source_fields": list(raw), "legacy_reflection_preserved": True}


def migrate_pattern_entries(selection: MigrationSelection, dry_run: bool = True,
                            idempotency_key: str | None = None) -> dict:
    """Explicit legacy selection, lossless dry run and repeatable additive migration."""
    selected = validated(MigrationSelection, selection)
    neo.timezone(selected["timezone"])
    ids, template_ids = selected.get("source_ids") or [], selected.get("template_ids") or []
    if not ids and not template_ids and not selected.get("namespace"):
        raise PatternError("validation_failure", "Select source_ids, explicit template_ids or namespace=true")
    for key in ids:
        get("journal_entries", key)
    for key in template_ids:
        get("journal_templates", key)
    if not dry_run and (not isinstance(idempotency_key, str) or not idempotency_key.strip()):
        raise PatternError("validation_failure", "Committed migration requires idempotency_key")
    with db.transaction(write=not dry_run):
        fingerprint = None
        if not dry_run:
            try:
                fingerprint, cached = guards.replay(idempotency_key, "migrate_pattern_entries", [selected, dry_run])
            except ValueError as exc:
                raise PatternError("idempotency_conflict", str(exc)) from exc
            if cached is not None:
                return cached
        if ids:
            sources = db.fetch_all(f"SELECT * FROM journal_entries WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY id", ids)
        else:
            sources = db.fetch_all("SELECT * FROM journal_entries ORDER BY id LIMIT 20001")
            if len(sources) > 20000:
                raise PatternError("validation_failure", "Select explicit source_ids for migrations over 20,000 journal entries")
        results = []
        for source in sources:
            invalid_metadata = False
            try:
                metadata = json.loads(source.get("custom_fields") or '{}')
                if not isinstance(metadata, dict):
                    invalid_metadata, metadata = True, {}
            except ValueError:
                invalid_metadata, metadata = True, {}
            if invalid_metadata and (source["id"] in ids or source.get("template_id") in template_ids):
                results.append({"source_id": source["id"], "status": "conflict", "reason": "Source custom_fields is not a JSON object; repair explicitly before migration so existing metadata is preserved"})
                continue
            namespace = metadata.get("neo_pattern")
            confirmed = isinstance(namespace, dict) or source.get("template_id") in template_ids
            if not confirmed:
                if source["id"] in ids:
                    results.append({"source_id": source["id"], "status": "conflict", "reason": "Explicit source has no known namespace or selected pattern-template binding"})
                continue
            if not ids and not (source.get("template_id") in template_ids or (selected.get("namespace") and isinstance(namespace, dict))):
                continue
            namespace = namespace if isinstance(namespace, dict) else {}
            existing = db.fetch_one("SELECT * FROM pattern_entries WHERE legacy_journal_entry_id=?", (source["id"],))
            if existing:
                marker = namespace.get("native_pattern_entry_id")
                baseline_revision = existing["legacy_source_revision"]
                expected_source_revision = baseline_revision + 1 if marker == existing["id"] else baseline_revision
                status = "already_migrated" if source["revision"] == expected_source_revision else "reconciliation_required"
                results.append({"source_id": source["id"], "pattern_entry_id": existing["id"], "status": status,
                                "source_revision": source["revision"], "migrated_source_revision": baseline_revision})
                continue
            try:
                values, quality = _legacy_mapping(source, namespace, selected["timezone"])
                validate_entry(values)
                if db.fetch_one("SELECT id FROM pattern_entries WHERE entry_key=?", (values["entry_key"],)):
                    raise PatternError("idempotency_conflict", "Stable entry key is already used by another occurrence")
                item = {"source_id": source["id"], "source_revision": source["revision"], "status": "proposed" if dry_run else "migrated",
                        "mapping": values, "data_quality": quality}
                if not dry_run:
                    with db.transaction():
                        values.update(legacy_journal_entry_id=source["id"], legacy_source_revision=source["revision"], migration_id=idempotency_key)
                        target = _save("pattern_entries", values)
                        # Change only the marker; preserve source text and other namespaces.
                        updated_metadata = {**metadata, "neo_pattern": {**namespace, "native_pattern_entry_id": target["id"], "migration_id": idempotency_key}}
                        r.save_record("journal_entries", {"custom_fields": updated_metadata}, source["id"], expected_revision=source["revision"])
                        item["pattern_entry_id"] = target["id"]
                results.append(item)
            except (PatternError, ValueError, TypeError) as exc:
                results.append({"source_id": source["id"], "status": "conflict", "error": exc.payload if isinstance(exc, PatternError) else {"code": "validation_failure", "message": str(exc)}})
        response = {"schema_version": SCHEMA_VERSION, "dry_run": dry_run, "items": results,
                    "counts": dict(Counter(item["status"] for item in results)),
                    "mappings": [{"legacy_journal_entry_id": item["source_id"], "pattern_entry_id": item["pattern_entry_id"]} for item in results if item.get("pattern_entry_id")],
                    "source_records_preserved": True}
        return response if dry_run else guards.remember(idempotency_key, fingerprint, response)


def attach_evidence(pattern_entry_id, evidence_id):
    parent = get("pattern_entries", pattern_entry_id)
    if evidence_id not in parent["evidence_ids"]:
        values = {"evidence_ids": [*parent["evidence_ids"], evidence_id]}
        validate_entry(values, parent, parent["id"])
        _save("pattern_entries", values, parent)


def schema_contracts():
    result = {}
    for entity, model, required in (
        ("psychology_patterns", PatternData, ["pattern_key", "label"]),
        ("pattern_entries", PatternEntryData, ["entry_key", "account_id", "event_date", "timezone"]),
        ("pattern_followups", FollowupData, ["pattern_entry_id", "followup_key", "note"]),
    ):
        patch_schema = model.model_json_schema()
        create_schema = model.model_json_schema()
        create_schema["required"] = required
        for key in required:
            prop = create_schema["properties"][key]
            choices = prop.get("anyOf", [])
            non_null = [choice for choice in choices if choice.get("type") != "null"]
            if len(non_null) == 1:
                create_schema["properties"][key] = {**{name: value for name, value in prop.items() if name not in {"anyOf", "default"}}, **non_null[0]}
        if entity == "pattern_entries":
            create_schema["anyOf"] = [{"required": [key], "properties": {key: {"type": "string", "minLength": 1}}} for key in sorted(OBSERVATIONS - {"legacy_reflection_text"})]
            filter_schema = PatternFilters.model_json_schema()
            filter_schema["properties"].update({"id": {"type": "integer"}, "entry_key": {"type": "string"}, "legacy_journal_entry_id": {"type": ["integer", "null"]}, "include_archived": {"type": "boolean"}})
        else:
            properties = {key: {"type": ["integer" if info["type"] == "INTEGER" else "string", "null"]} for key, info in r.columns(entity).items()}
            properties["include_archived"] = {"type": "boolean"}
            for key in ARRAYS & properties.keys():
                properties[key] = {"type": "array", "items": {"type": "integer" if key.endswith("_ids") else "string"}, "uniqueItems": True}
            if entity == "pattern_followups":
                properties["account_id"] = {"type": "integer"}
            filter_schema = {"type": "object", "additionalProperties": False, "properties": properties}
        result[entity] = {"create_schema": create_schema, "patch_schema": patch_schema,
                          "write_tool": {"psychology_patterns": "save_psychology_pattern", "pattern_entries": "save_pattern_entry", "pattern_followups": "append_pattern_followup"}[entity],
                          "generic_crud_writes": False, "arrays": "Atomic replacement; null clears to []", "objects": "Atomic replacement; null clears to {}",
                          "relationship_rules": {"account": "Explicit existing account; all linked context must match", "trades": "Multiple same-account trades count as one occurrence", "candidate": "When selected, linked trades must have confirmed candidate fill links", "evidence": "Existing evidence ownership/context must match; direct pattern ownership needs no journal entry"},
                          "date_semantics": {"default": "event_date" if entity == "pattern_entries" else "observed_at" if entity == "pattern_followups" else None, "event_timezone": "Stored IANA timezone", "unknown_time": "null; never creation time or invented midnight"},
                          "sort": "event_date, known event UTC time, id (date-only first)" if entity == "pattern_entries" else "observed_at UTC, id (unknown first)" if entity == "pattern_followups" else "id",
                          "filters_schema": filter_schema,
                          "archive": "Excluded by default; status=archived or include_archived=true includes archived entries"}
    return result
