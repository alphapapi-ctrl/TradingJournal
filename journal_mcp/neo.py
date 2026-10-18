"""Durable session, plan, evidence and execution records. No broker operations."""
from __future__ import annotations

import base64
import functools
import hashlib
import inspect
import io
import json
import math
import secrets
import time
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import database as db
from journal_mcp import guards, records as r
from utils import playbook_logic

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def write(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = dict(bound.arguments)
        key = arguments.pop("idempotency_key", None)
        with db.transaction():
            fingerprint, cached = guards.replay(key, function.__name__, arguments)
            if cached is not None:
                return cached
            response = function(*args, **kwargs)
            return guards.remember(key, fingerprint, response)
    return call


def fields(data, allowed, required=()):
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise ValueError(f"Supported fields: {', '.join(sorted(allowed))}")
    missing = [key for key in required if data.get(key) is None or data[key] == ""]
    if missing:
        raise ValueError(f"Required fields: {', '.join(missing)}")


def text_value(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def number(value, name, minimum=0, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum or (positive and value == minimum):
        raise ValueError(f"{name} must be a finite number {'above' if positive else 'at least'} {minimum}")
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be ISO text with timezone offset")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include timezone offset")
    return value


def timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("Use an IANA timezone such as Australia/Brisbane") from exc


def insert(table, data):
    names = ','.join(data)
    return db.execute(f"INSERT INTO {table} ({names}) VALUES ({','.join('?' for _ in data)})", list(data.values()))


def patch(table, record_id, data):
    db.execute(f"UPDATE {table} SET {','.join(key+'=?' for key in data)} WHERE id=?", [*data.values(), record_id])


def encode(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


@write
def save_session(data: dict, session_id: int | None = None, expected_revision: int | None = None,
                 idempotency_key: str | None = None) -> dict:
    """Create/resume a session. Clearing a review block requires a restart decision."""
    allowed = {"session_key", "account_id", "exchange_date", "local_date", "timezone", "label", "status", "review_block", "review_reason", "handover", "restart_decision", "baseline_version"}
    old = r.get_record("trading_sessions", session_id) if session_id else None
    guards.check_revision(old, expected_revision)
    if old:
        if expected_revision is None:
            raise ValueError("Session updates require expected_revision")
        if set(data) & {"session_key", "account_id", "exchange_date", "local_date", "timezone", "baseline_version"}:
            raise ValueError("Session identity and original baseline are immutable; create a new session")
    fields(data, allowed)
    full = {**(old or {}), **data}
    fields({k: v for k, v in full.items() if k in allowed}, allowed, {"session_key", "account_id", "exchange_date", "local_date", "timezone", "label", "baseline_version"})
    r.get_record("accounts", full["account_id"])
    for key in ("session_key", "label", "baseline_version"):
        text_value(full[key], key)
    for key in ("exchange_date", "local_date"):
        date.fromisoformat(full[key])
    timezone(full["timezone"])
    if full.get("status", "active") not in {"active", "completed"}:
        raise ValueError("Session status must be active or completed")
    if full.get("review_block", 0) not in (0, 1):
        raise ValueError("review_block must be 0 or 1")
    if full.get("review_block"):
        text_value(full.get("review_reason"), "review_reason")
    if old and old["review_block"] and not full.get("review_block"):
        text_value(data.get("restart_decision"), "restart_decision to clear the review block")
    values = dict(data)
    if "handover" in values:
        if not isinstance(values["handover"], dict):
            raise ValueError("handover must be an object")
        values["handover"] = encode(values["handover"])
    if old:
        if values:
            patch("trading_sessions", session_id, values)
    else:
        # A new date/chat cannot silently reset a previous unresolved block.
        previous = db.fetch_one("SELECT * FROM trading_sessions WHERE account_id=? ORDER BY id DESC LIMIT 1", (full["account_id"],))
        if previous and previous["review_block"]:
            values.update(review_block=1, review_reason=previous["review_reason"])
        entry = r.save_record("journal_entries", {"entry_type": "daily", "entry_date": full["local_date"], "account_id": full["account_id"], "stage": "pre", "baseline_version": full["baseline_version"]})
        values["journal_entry_id"] = entry["id"]
        session_id = insert("trading_sessions", values)
        r.save_record("journal_entries", {"session_id": session_id}, entry["id"])
    return r.details("trading_sessions", session_id)


def provenance(plan):
    result = {"baseline_version": text_value(plan.get("baseline_version"), "baseline_version")}
    for entity, key in (("playbooks", "playbook_id"), ("journal_templates", "template_id")):
        if plan.get(key) is not None:
            linked = r.get_record(entity, plan[key])
            version = key.replace("_id", "_version")
            # Version is the declared baseline; revisions also pin subsequent edits.
            result.update({key: linked["id"], version: f"{linked.get('version') or '1'}@{linked['revision']}"})
    return result


def plan_history(candidate_id, version, plan, reason):
    values = {"candidate_id": candidate_id, "version": version, "plan": encode(plan), "reason": reason, **provenance(plan)}
    for entity, key, snapshot_field in (("playbooks", "playbook_id", "playbook_snapshot"), ("journal_templates", "template_id", "template_snapshot")):
        if plan.get(key):
            values[snapshot_field] = encode(r.details(entity, plan[key]))
    return insert("candidate_plans", values)


def validate_plan(plan):
    allowed = {"entry_price", "stop_price", "target_price", "risk_pct", "planned_cash_risk", "baseline_version", "playbook_id", "template_id", "checklist", "notes", "sizing"}
    fields(plan, allowed, {"baseline_version"})
    for key in ("entry_price", "stop_price", "target_price", "risk_pct", "planned_cash_risk"):
        if plan.get(key) is not None:
            number(plan[key], key, positive=True)
    if plan.get("risk_pct", 0) > 100:
        raise ValueError("risk_pct must be at most 100")
    if "checklist" in plan:
        if not plan.get("playbook_id"):
            raise ValueError("checklist requires playbook_id")
        assess_evidence(r.details("playbooks", plan["playbook_id"]), plan["checklist"])
    return provenance(plan)


@write
def save_candidate(data: dict, candidate_id: int | None = None, expected_revision: int | None = None,
                   idempotency_key: str | None = None) -> dict:
    """Create or append a plan revision; order intent never creates an executed trade."""
    fields(data, {"candidate_key", "session_id", "symbol", "direction", "plan", "reason", "status"})
    old = r.get_record("candidates", candidate_id) if candidate_id else None
    guards.check_revision(old, expected_revision)
    if old and expected_revision is None:
        raise ValueError("Candidate updates require expected_revision")
    if old and set(data) & {"candidate_key", "session_id", "symbol", "direction"}:
        raise ValueError("Candidate identity is immutable")
    full = {**(old or {}), **data}
    if not old:
        fields(data, set(data), {"candidate_key", "session_id", "symbol", "direction", "plan", "reason"})
        text_value(data["candidate_key"], "candidate_key")
        text_value(data["symbol"], "symbol")
        if data["direction"] not in ("LONG", "SHORT"):
            raise ValueError("direction must be LONG or SHORT")
    session = r.get_record("trading_sessions", full["session_id"])
    status = full.get("status", "draft")
    transitions = {
        "draft": {"draft", "assessed", "blocked", "cancelled"},
        "assessed": {"assessed", "draft", "blocked", "pending", "cancelled"},
        "blocked": {"blocked", "draft", "assessed", "cancelled"},
        "pending": {"pending", "cancelled", "blocked"},
        "open": {"open", "closed"}, "closed": {"closed"}, "cancelled": {"cancelled"},
    }
    if status not in transitions or (old and status not in transitions[old["status"]]):
        raise ValueError("Invalid lifecycle transition; actual fills establish open status")
    if not old and status not in {"draft", "blocked"}:
        raise ValueError("A candidate starts as draft or blocked")
    if session["review_block"] and status in {"assessed", "pending"}:
        raise ValueError("Session has an unresolved stop-and-review block")
    if session["status"] != "active" and status in {"draft", "assessed", "pending"}:
        raise ValueError("Session is completed")
    if old and status == "closed":
        reconciliation = reconcile_candidate(candidate_id)
        if reconciliation["open_quantity"] != 0 or not reconciliation["complete"]:
            raise ValueError("Confirmed entry/exit fills and their costs must reconcile before closing a candidate")
    if "plan" in data:
        plan = data["plan"]
        versions = validate_plan(plan)
        text_value(data.get("reason"), "reason for this plan revision")
        if plan.get("entry_price") and plan.get("stop_price"):
            sign = 1 if full["direction"] == "LONG" else -1
            if (plan["entry_price"] - plan["stop_price"]) * sign <= 0:
                raise ValueError("Structural stop is on the wrong side of entry")
            if plan.get("target_price") and (plan["target_price"] - plan["entry_price"]) * sign <= 0:
                raise ValueError("Target is on the wrong side of entry")
        if "sizing" in plan:
            raise ValueError("Sizing is generated by size_candidate, not supplied in a plan")
    else:
        if not old:
            raise ValueError("plan is required")
        plan = json.loads(db.fetch_one("SELECT plan FROM candidate_plans WHERE candidate_id=? ORDER BY version DESC LIMIT 1", (candidate_id,))["plan"])
        versions = provenance(plan)
    if status in {"assessed", "pending"}:
        assessment = assess_evidence(r.details("playbooks", plan["playbook_id"]), plan.get("checklist", {})) if plan.get("playbook_id") else {}
        if assessment.get("readiness") != "ready" or assessment.get("risk_level") in {None, "no_trade"}:
            raise ValueError("Required checklist evidence is not ready")
        if not plan.get("sizing", {}).get("quantity"):
            raise ValueError("Persist sizing with size_candidate before assessment/order intent")
    if old:
        values = {"status": status}
        if "plan" in data:
            values["plan_version"] = old["plan_version"] + 1
        patch("candidates", candidate_id, values)
    else:
        entry = r.save_record("journal_entries", {
            "entry_type": "trade", "entry_date": session["local_date"], "account_id": session["account_id"],
            "symbol": full["symbol"], "session_id": session["id"], "stage": "pre", "pre_plan": encode(plan), **versions,
        })
        candidate_id = insert("candidates", {key: full[key] for key in ("candidate_key", "session_id", "symbol", "direction")} | {"account_id": session["account_id"], "journal_entry_id": entry["id"], "status": status})
        r.save_record("journal_entries", {"candidate_id": candidate_id}, entry["id"])
    if "plan" in data:
        version = r.get_record("candidates", candidate_id)["plan_version"]
        plan_history(candidate_id, version, plan, data["reason"])
    return r.details("candidates", candidate_id)


@write
def save_account_snapshot(data: dict, idempotency_key: str | None = None) -> dict:
    required = {"account_id", "balance", "currency", "source", "observed_at", "trading_day_timezone", "trading_day_boundary"}
    fields(data, required | {"equity"}, required)
    account = r.get_record("accounts", data["account_id"])
    if data["currency"] != account["currency"]:
        raise ValueError("Snapshot must use the account currency")
    number(data["balance"], "balance", minimum=-float('inf'))
    if data.get("equity") is not None:
        number(data["equity"], "equity", minimum=-float('inf'))
    timestamp(data["observed_at"])
    timezone(data["trading_day_timezone"])
    boundary = data["trading_day_boundary"]
    if not isinstance(boundary, str) or len(boundary) != 5:
        raise ValueError("trading_day_boundary must be HH:MM")
    datetime.strptime(boundary, "%H:%M")
    text_value(data["source"], "source")
    return r.get_record("account_snapshots", insert("account_snapshots", data))


@write
def save_risk_regime(data: dict, idempotency_key: str | None = None) -> dict:
    required = {"account_id", "mode", "risk_pct", "source", "effective_at"}
    fields(data, required | {"max_trade_risk", "max_combined_risk", "max_daily_loss", "notes"}, required)
    account = r.get_record("accounts", data["account_id"])
    if data["mode"] not in {"demo", "live", "prop"}:
        raise ValueError("mode must be demo, live or prop")
    if (account["account_type"] == "Demo") != (data["mode"] == "demo"):
        raise ValueError("Demo regime requires a Demo account; live/prop must be explicitly configured separately")
    if data["mode"] == "prop" and account["account_type"] != "Prop Firm":
        raise ValueError("prop regime requires a Prop Firm account")
    if account["account_type"] == "Prop Firm" and data["mode"] != "prop":
        raise ValueError("Prop Firm accounts require a prop regime")
    number(data["risk_pct"], "risk_pct", positive=True)
    if data["risk_pct"] > 100:
        raise ValueError("risk_pct must be at most 100")
    for key in ("max_trade_risk", "max_combined_risk", "max_daily_loss"):
        if data.get(key) is not None:
            number(data[key], key, positive=True)
    if data["mode"] == "prop" and any(data.get(k) is None for k in ("max_trade_risk", "max_combined_risk", "max_daily_loss")):
        raise ValueError("A prop regime needs explicit account-currency trade, combined-risk and daily-loss limits")
    timestamp(data["effective_at"])
    text_value(data["source"], "source")
    return r.get_record("risk_regimes", insert("risk_regimes", data))


@write
def save_instrument_spec(data: dict, idempotency_key: str | None = None) -> dict:
    required = {"symbol", "version", "quote_currency", "contract_multiplier", "point_value", "tick_size", "tick_value", "quantity_step", "min_quantity", "source", "verified_at"}
    fields(data, required, required)
    for key in ("contract_multiplier", "point_value", "tick_size", "tick_value", "quantity_step", "min_quantity"):
        number(data[key], key, positive=True)
    for key in ("symbol", "version", "quote_currency", "source"):
        text_value(data[key], key)
    if not math.isclose(data["tick_value"], data["point_value"] * data["tick_size"], rel_tol=1e-8):
        raise ValueError("tick_value must equal point_value × tick_size; point_value already includes the contract multiplier")
    if not math.isclose(data["min_quantity"] / data["quantity_step"], round(data["min_quantity"] / data["quantity_step"]), abs_tol=1e-8):
        raise ValueError("min_quantity must be a multiple of quantity_step")
    timestamp(data["verified_at"])
    return r.get_record("instrument_specs", insert("instrument_specs", data))


def economics(spec, account, fx_rate=None, fx_source=None, fx_observed_at=None):
    if spec["quote_currency"] == account["currency"]:
        if fx_rate not in (None, 1):
            raise ValueError("Same-currency FX rate must be 1")
        fx_rate = 1
    else:
        number(fx_rate, "FX account-currency units per quote-currency unit", positive=True)
        text_value(fx_source, "fx_source")
        timestamp(fx_observed_at)
    return {"instrument_spec_id": spec["id"], "point_value": spec["point_value"], "quote_currency": spec["quote_currency"],
            "account_currency": account["currency"], "fx_rate": fx_rate, "fx_source": fx_source, "fx_observed_at": fx_observed_at,
            "account_point_value": spec["point_value"] * fx_rate}


def validate_trade_economics(trade):
    spec = r.get_record("instrument_specs", trade["instrument_spec_id"])
    account = r.get_record("accounts", trade.get("account_id"))
    if spec["symbol"] != trade["symbol"]:
        raise ValueError("Instrument symbol must match the exact trade contract")
    assumptions = trade.get("sizing_assumptions") or {}
    if isinstance(assumptions, str):
        assumptions = json.loads(assumptions)
    if not isinstance(assumptions, dict):
        raise ValueError("sizing_assumptions must be an object")
    result = economics(spec, account, assumptions.get("fx_rate"), assumptions.get("fx_source"), assumptions.get("fx_observed_at"))
    if assumptions.get("account_currency", account["currency"]) != account["currency"]:
        raise ValueError("Persisted account currency differs from the current account")
    for key in ("entry_price", "exit_price", "stop_price", "take_profit"):
        if trade.get(key) is not None:
            ticks = trade[key] / spec["tick_size"]
            if not math.isclose(ticks, round(ticks), abs_tol=1e-6):
                raise ValueError(f"{key} must align to instrument tick size")
    quantity = trade.get("quantity", 0)
    if quantity < spec["min_quantity"] or not math.isclose(quantity / spec["quantity_step"], round(quantity / spec["quantity_step"]), abs_tol=1e-7):
        raise ValueError("Trade quantity must meet instrument minimum and step")
    return result


@write
def size_candidate(candidate_id: int, snapshot_id: int, regime_id: int, instrument_spec_id: int,
                   expected_revision: int, observed_at: str, risk_pct_override: float | None = None,
                   override_reason: str | None = None, fx_rate: float | None = None,
                   fx_source: str | None = None, fx_observed_at: str | None = None,
                   costs_per_contract: float = 0, existing_open_risk: float | None = None,
                   daily_loss: float | None = None, idempotency_key: str | None = None) -> dict:
    """Persist account-currency sizing assumptions as a new immutable plan revision."""
    candidate = r.get_record("candidates", candidate_id)
    guards.check_revision(candidate, expected_revision)
    if candidate["status"] not in {"draft", "assessed", "blocked"}:
        raise ValueError("Size before an order is pending or filled")
    session = r.get_record("trading_sessions", candidate["session_id"])
    if session["review_block"] or session["status"] != "active":
        raise ValueError("Session is blocked or completed")
    timestamp(observed_at)
    snapshot = r.get_record("account_snapshots", snapshot_id)
    regime = r.get_record("risk_regimes", regime_id)
    spec = r.get_record("instrument_specs", instrument_spec_id)
    if any(row["account_id"] != candidate["account_id"] for row in (snapshot, regime)):
        raise ValueError("Snapshot/regime belongs to a different account")
    if spec["symbol"] != candidate["symbol"]:
        raise ValueError("Instrument must match the exact candidate contract/feed symbol")
    account = r.get_record("accounts", candidate["account_id"])
    if snapshot["currency"] != account["currency"]:
        raise ValueError("Snapshot currency differs from the account")
    if (account["account_type"] == "Demo") != (regime["mode"] == "demo"):
        raise ValueError("Account mode changed; configure a matching regime")
    for record, field in ((snapshot, "observed_at"), (regime, "effective_at"), (spec, "verified_at")):
        if datetime.fromisoformat(record[field]) > datetime.fromisoformat(observed_at):
            raise ValueError("Sizing cannot use evidence observed after its timestamp")
    plan = json.loads(db.fetch_one("SELECT plan FROM candidate_plans WHERE candidate_id=? ORDER BY version DESC LIMIT 1", (candidate_id,))["plan"])
    number(snapshot["balance"], "current balance", positive=True)
    for field in ("entry_price", "stop_price"):
        number(plan.get(field), field, positive=True)
        if not math.isclose(plan[field] / spec["tick_size"], round(plan[field] / spec["tick_size"]), abs_tol=1e-6):
            raise ValueError("Entry/stop must align with instrument tick size")
    pct = regime["risk_pct"] if risk_pct_override is None else risk_pct_override
    number(pct, "risk percentage", positive=True)
    if pct > 100:
        raise ValueError("risk percentage must be at most 100")
    if risk_pct_override is not None:
        text_value(override_reason, "override_reason")
    assumptions = economics(spec, account, fx_rate, fx_source, fx_observed_at)
    if fx_observed_at and datetime.fromisoformat(fx_observed_at) > datetime.fromisoformat(observed_at):
        raise ValueError("FX observation cannot follow sizing")
    number(costs_per_contract, "costs_per_contract")
    budget = snapshot["balance"] * pct / 100
    limit = budget
    if regime["max_trade_risk"] is not None:
        limit = min(limit, regime["max_trade_risk"])
    if regime["max_combined_risk"] is not None:
        number(existing_open_risk, "existing_open_risk")
        limit = min(limit, max(0, regime["max_combined_risk"] - existing_open_risk))
    if regime["max_daily_loss"] is not None:
        number(daily_loss, "daily_loss")
        limit = min(limit, max(0, regime["max_daily_loss"] - daily_loss - (existing_open_risk or 0)))
    loss_per_contract = abs(plan["entry_price"] - plan["stop_price"]) * assumptions["account_point_value"] + costs_per_contract
    number(loss_per_contract, "cash loss per contract", positive=True)
    step = Decimal(str(spec["quantity_step"]))
    quantity = float((Decimal(str(limit)) / Decimal(str(loss_per_contract)) / step).to_integral_value(rounding=ROUND_FLOOR) * step)
    below_minimum = quantity < spec["min_quantity"]
    if below_minimum:
        quantity = 0
    sizing = {**assumptions, "snapshot_id": snapshot_id, "regime_id": regime_id, "observed_at": observed_at,
              "balance_basis": "current_balance", "balance": snapshot["balance"], "balance_source": snapshot["source"], "balance_observed_at": snapshot["observed_at"],
              "risk_pct": pct, "override_scope": "this_trade" if risk_pct_override is not None else "none", "override_reason": override_reason,
              "risk_budget": budget, "allowed_risk": limit, "costs_per_contract": costs_per_contract,
              "existing_open_risk": existing_open_risk, "daily_loss": daily_loss,
              "quantity": quantity, "actual_risk": round(quantity * loss_per_contract, 8), "below_minimum": below_minimum}
    plan["sizing"] = sizing
    version = candidate["plan_version"] + 1
    plan_history(candidate_id, version, plan, "Persist verified sizing assumptions")
    patch("candidates", candidate_id, {"plan_version": version, "status": "draft"})
    return {"candidate": r.details("candidates", candidate_id), "sizing": sizing}


def assess_evidence(pb, evidence):
    """Four-state OR groups, with readiness distinct from score/completeness."""
    if not isinstance(evidence, dict):
        raise ValueError("evidence must map rule IDs to evidence objects")
    valid = {str(rule["id"]) for rule in pb["rules"]}
    if set(evidence) - valid:
        raise ValueError("Evidence contains rules outside this playbook")
    states = {}
    for key, value in evidence.items():
        fields(value, {"status", "evidence", "source", "observed_at"}, {"status"})
        if value["status"] not in {"pass", "fail", "unknown", "caution"}:
            raise ValueError("Evidence status must be pass, fail, unknown or caution")
        if value.get("observed_at"):
            timestamp(value["observed_at"])
        states[key] = value["status"]
    units = {kind: {} for kind in ("required", "optional", "bonus")}
    for rule in pb["rules"]:
        unit = (rule.get("rule_group") or "").strip() or f"rule:{rule['id']}"
        units[rule["rule_type"]].setdefault(unit, []).append(states.get(str(rule["id"]), "unknown"))
    def resolve(values):
        if "pass" in values:
            return "pass"
        if all(v == "fail" for v in values):
            return "fail"
        return "caution" if "caution" in values else "unknown"
    resolved = {kind: [resolve(values) for values in group.values()] for kind, group in units.items()}
    required = resolved["required"]
    readiness = "blocked" if "fail" in required else "incomplete" if any(v in {"unknown", "caution"} for v in required) else "ready"
    known_booleans = {key: state == "pass" for key, state in states.items() if state in {"pass", "fail"}}
    base = playbook_logic.evaluate_playbook_rules(pb, known_booleans)
    components = {}
    for kind, weight in (("required", 60), ("optional", 30), ("bonus", 10)):
        values = resolved[kind]
        components[kind] = {"weight": weight, "passed": values.count("pass"), "failed": values.count("fail"),
                            "unknown": values.count("unknown"), "caution": values.count("caution"), "total": len(values),
                            "points": round(weight * values.count("pass") / len(values), 1) if values else weight}
    incomplete = any(c["unknown"] or c["caution"] for c in components.values())
    # With unknowns, determine whether a threshold is definitely failed, satisfied,
    # or unresolved. Never turn missing optional evidence into a known failure.
    triggered, unresolved = None, False
    for threshold in sorted(pb["risk_rules"], key=lambda value: -value.get("min_required_pct", 0)):
        trigger = threshold.get("trigger_rule_id")
        if trigger:
            state = states.get(str(trigger), "unknown")
            if state not in {"pass", "fail"}:
                unresolved = True
                continue
            failed = (state == "pass") != bool(threshold.get("trigger_rule_must_be", 1))
        else:
            failed = False
            for kind, minimum in (("required", threshold.get("min_required_pct", 100)), ("optional", threshold.get("min_optional_pct", 0))):
                c = components[kind]
                low = 100 * c["passed"] / c["total"] if c["total"] else 100
                high = 100 * (c["passed"] + c["unknown"] + c["caution"]) / c["total"] if c["total"] else 100
                failed = failed or high < minimum
                unresolved = unresolved or low < minimum <= high
        if failed:
            triggered = threshold
            break
    base.update(readiness=readiness, evidence=evidence, score_components=components,
                evidence_complete=not incomplete, compliance_score=round(sum(c["points"] for c in components.values()), 1) if not incomplete else None,
                required_pct=round(100 * required.count("pass") / len(required), 1) if required and readiness != "incomplete" else 100 if not required else None,
                risk_score=round(sum(c["points"] for c in components.values()), 1) if not incomplete else None,
                risk_assessment=triggered,
                risk_level=triggered["risk_level"] if triggered else None if unresolved or readiness == "incomplete" else "normal",
                risk_multiplier=triggered["risk_multiplier"] if triggered else None if unresolved or readiness == "incomplete" else 1.0)
    base["warnings"] = ([triggered.get("warning_message") or "Minimum guard failed"] if triggered else []) + (["Required evidence incomplete; no readiness permission"] if readiness == "incomplete" else [])
    return base


def linked_context(journal_entry_id, candidate_id=None, trade_id=None):
    entry = r.get_record("journal_entries", journal_entry_id)
    candidate_id = candidate_id or entry.get("candidate_id")
    if candidate_id:
        candidate = r.get_record("candidates", candidate_id)
        if candidate["journal_entry_id"] != journal_entry_id or entry.get("account_id") != candidate["account_id"]:
            raise ValueError("Candidate does not own this journal entry")
    if trade_id:
        trade = r.get_record("trades", trade_id)
        if entry.get("account_id") != trade.get("account_id"):
            raise ValueError("Evidence/event and trade must share an account")
        if candidate_id:
            if not db.fetch_one("SELECT id FROM fill_events WHERE candidate_id=? AND trade_id=?", (candidate_id, trade_id)):
                raise ValueError("Trade has no confirmed fill link to the candidate")
        elif entry.get("trade_id") != trade_id:
            raise ValueError("Trade must be linked to this journal entry")
    return entry, candidate_id


@write
def append_management_event(data: dict, idempotency_key: str | None = None) -> dict:
    required = {"event_key", "journal_entry_id", "observed_at", "decision", "reason", "action_status"}
    allowed = required | {"candidate_id", "trade_id", "fill_event_id", "before_stop", "after_stop", "before_target", "after_target", "rule", "evidence_ids"}
    fields(data, allowed, required)
    for key in ("event_key", "decision", "reason"):
        text_value(data[key], key)
    timestamp(data["observed_at"])
    if data["action_status"] not in {"observed", "proposed", "executed", "cancelled"}:
        raise ValueError("action_status must be observed, proposed, executed or cancelled")
    entry, candidate_id = linked_context(data["journal_entry_id"], data.get("candidate_id"), data.get("trade_id"))
    for key in ("before_stop", "after_stop", "before_target", "after_target"):
        if data.get(key) is not None:
            number(data[key], key)
    if data.get("fill_event_id"):
        fill = r.get_record("fill_events", data["fill_event_id"])
        if fill["candidate_id"] != candidate_id or (data.get("trade_id") and fill["trade_id"] != data["trade_id"]):
            raise ValueError("Fill belongs to a different candidate/trade")
    evidence_ids = data.get("evidence_ids", [])
    if not isinstance(evidence_ids, list):
        raise ValueError("evidence_ids must be an array")
    for evidence_id in evidence_ids:
        if r.get_record("evidence", evidence_id)["journal_entry_id"] != entry["id"]:
            raise ValueError("Evidence belongs to a different journal entry")
    values = {**data, "candidate_id": candidate_id, "evidence_ids": encode(evidence_ids)}
    existing = db.fetch_one("SELECT * FROM management_events WHERE event_key=?", (data["event_key"],))
    if existing:
        if any(existing.get(key) != value for key, value in values.items()):
            raise ValueError("Event key already has different content; append a correction with a new key")
        return existing
    return r.get_record("management_events", insert("management_events", values))


@write
def record_fill(data: dict, idempotency_key: str | None = None) -> dict:
    """Append a confirmed fill linked to an existing journal trade; no order placement."""
    required = {"candidate_id", "trade_id", "source", "source_fill_id", "filled_at", "kind", "price", "quantity", "commission", "currency"}
    fields(data, required | {"pnl", "corrects_fill_id", "other_costs", "swap"}, required)
    candidate = r.get_record("candidates", data["candidate_id"])
    trade = r.get_record("trades", data["trade_id"])
    account = r.get_record("accounts", candidate["account_id"])
    if any(trade.get(key) != candidate.get(key) for key in ("account_id", "symbol", "direction")):
        raise ValueError("Fill trade must match candidate account, exact symbol and direction")
    if trade.get("trade_type") == "dividend":
        raise ValueError("A dividend is not an execution fill")
    if data["currency"] != account["currency"]:
        raise ValueError("Fill P&L and commission must use account currency")
    other = db.fetch_one("SELECT id FROM fill_events WHERE trade_id=? AND candidate_id<>? LIMIT 1", (trade["id"], candidate["id"]))
    if other:
        raise ValueError("This journal trade already belongs to another candidate's fills")
    timestamp(data["filled_at"])
    for key in ("source", "source_fill_id"):
        text_value(data[key], key)
    for key in ("quantity", "price"):
        number(data[key], key, positive=True)
    number(data["commission"], "commission")
    number(data.get("other_costs", 0), "other_costs")
    number(data.get("swap", 0), "swap", minimum=-float('inf'))
    if data["kind"] not in {"entry", "exit"}:
        raise ValueError("kind must be entry or exit")
    if data.get("pnl") is not None:
        number(data["pnl"], "pnl", minimum=-float('inf'))
    if trade.get("instrument_spec_id"):
        validate_trade_economics({**trade, "entry_price": data["price"], "quantity": data["quantity"]})
    values = {**data, "account_id": account["id"]}
    existing = db.fetch_one("SELECT * FROM fill_events WHERE account_id=? AND source=? AND source_fill_id=?", (account["id"], data["source"], data["source_fill_id"]))
    if existing:
        if any(existing.get(key) != value for key, value in values.items()):
            raise ValueError("Source fill ID already has different data; append a correction")
        return existing
    if data.get("corrects_fill_id"):
        corrected = r.get_record("fill_events", data["corrects_fill_id"])
        if corrected["candidate_id"] != candidate["id"] or corrected["kind"] != data["kind"]:
            raise ValueError("Correction must belong to the same candidate and fill kind")
        if db.fetch_one("SELECT id FROM fill_events WHERE corrects_fill_id=?", (corrected["id"],)):
            raise ValueError("Correct the latest correction instead")
    fill_id = insert("fill_events", values)
    entry = r.get_record("journal_entries", candidate["journal_entry_id"])
    if data["kind"] == "entry" and entry.get("trade_id") is None and entry.get("position_id") is None:
        r.save_record("journal_entries", {"trade_id": trade["id"]}, entry["id"])
    # Record facts even if the user's actual execution violated a review block.
    if data["kind"] == "entry" or candidate["status"] != "closed":
        patch("candidates", candidate["id"], {"status": "open"})
    plan_row = db.fetch_one("SELECT * FROM candidate_plans WHERE candidate_id=? ORDER BY version LIMIT 1", (candidate["id"],))
    if plan_row:
        metadata = {key: plan_row[key] for key in ("baseline_version", "playbook_version", "template_id", "template_version") if trade.get(key) is None}
        if metadata:
            r.save_record("trades", metadata, trade["id"])
    return r.get_record("fill_events", fill_id)


def reconcile_candidate(candidate_id: int) -> dict:
    """Reconcile individual entry/exit fills; report gaps without changing trade P&L."""
    candidate = r.get_record("candidates", candidate_id)
    rows = db.fetch_all("SELECT * FROM fill_events WHERE candidate_id=?", (candidate_id,))
    rows.sort(key=lambda row: (datetime.fromisoformat(row["filled_at"]), row["id"]))
    superseded = {row["corrects_fill_id"] for row in rows if row["corrects_fill_id"]}
    active = [row for row in rows if row["id"] not in superseded]
    entries = [row for row in active if row["kind"] == "entry"]
    exits = [row for row in active if row["kind"] == "exit"]
    entry_qty = sum(row["quantity"] for row in entries)
    exit_qty = sum(row["quantity"] for row in exits)
    commission = sum(row["commission"] for row in exits) + sum(row["commission"] for row in entries) * min(exit_qty / entry_qty, 1) if entry_qty else sum(row["commission"] for row in exits)
    allocation = min(exit_qty / entry_qty, 1) if entry_qty else 0
    other_costs = sum(row["other_costs"] for row in exits) + sum(row["other_costs"] for row in entries) * allocation
    swap = sum(row["swap"] for row in exits) + sum(row["swap"] for row in entries) * allocation
    issues = []
    if not entries:
        issues.append("No entry fills")
    if exit_qty > entry_qty + 1e-8:
        issues.append("Exit quantity exceeds entry quantity")
    running_qty = 0
    for row in active:
        running_qty += row["quantity"] * (1 if row["kind"] == "entry" else -1)
        if running_qty < -1e-8:
            issues.append("Exit precedes sufficient entry fills")
            break
    if any(row["pnl"] is None for row in exits):
        issues.append("Exit fills need verified gross account-currency pnl")
    trade_comparisons = []
    total_journal_quantity = 0
    journal_open_quantity = 0
    journal_commission, journal_swap = 0, 0
    for trade_id in sorted({row["trade_id"] for row in active}):
        trade = r.get_record("trades", trade_id)
        total_journal_quantity += trade["quantity"]
        if trade["status"] == "open":
            journal_open_quantity += trade["quantity"]
        journal_commission += trade.get("commission") or 0
        journal_swap += trade.get("swap") or 0
        linked = [row for row in active if row["trade_id"] == trade_id]
        closed = [row for row in linked if row["kind"] == "exit"]
        # Partial-close slices can share a candidate; compare completed slices only.
        difference = None
        if trade["status"] == "closed" and closed and all(row["pnl"] is not None for row in closed):
            difference = round(sum(row["pnl"] for row in closed) - (trade["pnl"] or 0), 8)
            if abs(difference) > 0.0001:
                issues.append(f"Trade #{trade_id} gross P&L differs from fills")
            if not math.isclose(sum(row["quantity"] for row in closed), trade["quantity"], abs_tol=1e-8):
                issues.append(f"Trade #{trade_id} exit quantity differs from fills")
        if trade["status"] == "closed" and not closed:
            issues.append(f"Closed trade #{trade_id} has no exit fills")
        trade_comparisons.append({"trade_id": trade_id, "journal_status": trade["status"], "gross_pnl_difference": difference})
    if not math.isclose(total_journal_quantity, entry_qty, abs_tol=1e-8):
        issues.append("Journal trade quantity differs from candidate entry fills")
    if not math.isclose(journal_open_quantity, entry_qty - exit_qty, abs_tol=1e-8):
        issues.append("Journal open quantity differs from fills; reconcile the confirmed close first")
    if not math.isclose(journal_commission, sum(row["commission"] + row["other_costs"] for row in active), abs_tol=0.0001):
        issues.append("Journal commissions/fees differ from candidate fills")
    if not math.isclose(journal_swap, sum(row["swap"] for row in active), abs_tol=0.0001):
        issues.append("Journal swap differs from candidate fills")
    # The earliest sized plan fixes the original monetary risk, even after stop moves.
    risk = None
    for row in db.fetch_all("SELECT plan FROM candidate_plans WHERE candidate_id=? ORDER BY version", (candidate_id,)):
        plan = json.loads(row["plan"])
        risk = plan.get("sizing", {}).get("actual_risk") or plan.get("planned_cash_risk")
        if risk:
            break
    gross = sum(row["pnl"] for row in exits) if all(row["pnl"] is not None for row in exits) else None
    net = round(gross - commission - other_costs + swap, 8) if gross is not None else None
    return {"candidate_id": candidate_id, "currency": r.get_record("accounts", candidate["account_id"])["currency"],
            "entry_quantity": entry_qty, "exit_quantity": exit_qty, "open_quantity": round(entry_qty - exit_qty, 8),
            "average_entry": sum(row["price"] * row["quantity"] for row in entries) / entry_qty if entry_qty else None,
            "average_exit": sum(row["price"] * row["quantity"] for row in exits) / exit_qty if exit_qty else None,
            "gross_realised_pnl": gross, "commissions": commission, "other_costs": other_costs, "swap": swap, "net_realised_pnl": net,
            "original_cash_risk": risk, "realised_net_r": round(net / risk, 8) if net is not None and risk else None,
            "complete": bool(entries) and not issues, "issues": issues, "trade_comparisons": trade_comparisons,
            "active_fill_ids": [row["id"] for row in active]}


def evidence_bytes(evidence_id):
    metadata = r.get_record("evidence", evidence_id)
    blob = db.fetch_one("SELECT content FROM evidence_blobs WHERE evidence_id=?", (evidence_id,))
    if not blob or hashlib.sha256(blob["content"]).hexdigest() != metadata["sha256"]:
        raise ValueError("Evidence missing or failed its integrity check")
    return metadata, blob["content"]


def evidence_download(evidence_id):
    token = secrets.token_urlsafe(32)
    expiry = time.time() + 3600
    db.execute("DELETE FROM evidence_access WHERE expires_at<?", (time.time(),))
    db.execute("INSERT INTO evidence_access VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), evidence_id, expiry))
    return {"download_path": f"/evidence/{evidence_id}?token={token}", "access": "One-hour bearer capability on the private-LAN MCP server", "expires_at": expiry}


def authorized_evidence(evidence_id, token):
    if not isinstance(token, str) or len(token) > 200:
        raise ValueError("Invalid evidence capability")
    access = db.fetch_one("SELECT * FROM evidence_access WHERE token_hash=? AND evidence_id=?", (hashlib.sha256(token.encode()).hexdigest(), evidence_id))
    if not access or access["expires_at"] < time.time():
        raise ValueError("Evidence capability expired or invalid")
    return evidence_bytes(evidence_id)


@write
def upload_evidence(content_base64: str, data: dict, idempotency_key: str | None = None) -> dict:
    """Upload screenshot bytes, never a local path. Stores the bytes in the database backup."""
    required = {"journal_entry_id", "filename", "mime_type", "stage", "captured_at", "symbol", "timeframe", "source"}
    fields(data, required | {"candidate_id", "trade_id", "chart_context"}, required)
    entry, candidate_id = linked_context(data["journal_entry_id"], data.get("candidate_id"), data.get("trade_id"))
    if data["stage"] not in {"pre", "management", "post"}:
        raise ValueError("stage must be pre, management or post")
    timestamp(data["captured_at"])
    for key in ("filename", "symbol", "timeframe", "source"):
        text_value(data[key], key)
    if any(char in data["filename"] for char in ('/', '\\', ':', '\r', '\n')) or len(data["filename"]) > 200:
        raise ValueError("filename must be a plain filename of at most 200 characters")
    if not isinstance(content_base64, str) or len(content_base64) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError("Screenshot exceeds 8 MiB")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except ValueError as exc:
        raise ValueError("Screenshot must be valid base64") from exc
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Screenshot must be 1 byte–8 MiB")
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(content)) as screenshot:
            actual_mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(screenshot.format)
            if actual_mime is None or actual_mime != data["mime_type"]:
                raise ValueError("Use PNG, JPEG or WebP with its actual MIME type")
            if screenshot.width * screenshot.height > 40_000_000:
                raise ValueError("Screenshot exceeds 40 megapixels")
            screenshot.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid screenshot image") from exc
    if candidate_id:
        candidate = r.get_record("candidates", candidate_id)
        if data["symbol"] != candidate["symbol"]:
            raise ValueError("Evidence symbol must match the candidate's exact chart/contract")
    values = {**data, "candidate_id": candidate_id, "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content)}
    existing = db.fetch_one("SELECT * FROM evidence WHERE journal_entry_id=? AND sha256=? AND stage=? AND captured_at=?", (entry["id"], values["sha256"], data["stage"], data["captured_at"]))
    if existing:
        if any(existing.get(key) != value for key, value in values.items()):
            raise ValueError("This capture already exists with different metadata")
        return {"evidence": existing}
    evidence_id = insert("evidence", values)
    db.execute("INSERT INTO evidence_blobs VALUES (?,?)", (evidence_id, content))
    return {"evidence": r.get_record("evidence", evidence_id)}


def read_evidence(evidence_id: int, offset: int = 0, max_bytes: int = 65536) -> dict:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or not 1 <= max_bytes <= 262144:
        raise ValueError("offset must be nonnegative; max_bytes must be 1–262144")
    metadata, content = evidence_bytes(evidence_id)
    if offset > len(content):
        raise ValueError("offset exceeds evidence length")
    chunk = content[offset:offset + max_bytes]
    return {"evidence": metadata, "encoding": "base64", "content": base64.b64encode(chunk).decode(),
            "next_offset": offset + len(chunk) if offset + len(chunk) < len(content) else None,
            **evidence_download(evidence_id)}
