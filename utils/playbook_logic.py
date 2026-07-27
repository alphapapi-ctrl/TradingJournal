"""
Playbook business logic: rule evaluation, risk scoring, compliance stats.
"""
import json
from database import fetch_all, fetch_one, execute


def get_playbooks():
    return fetch_all("SELECT * FROM playbooks ORDER BY name")

def get_playbook(playbook_id: int):
    pb = fetch_one("SELECT * FROM playbooks WHERE id=?", (playbook_id,))
    if not pb:
        return None
    pb["rules"] = fetch_all(
        "SELECT * FROM playbook_rules WHERE playbook_id=? ORDER BY sort_order, id",
        (playbook_id,)
    )
    pb["risk_rules"] = fetch_all(
        "SELECT * FROM playbook_risk_rules WHERE playbook_id=? ORDER BY min_required_pct DESC",
        (playbook_id,)
    )
    return pb

def create_playbook(name, description="", symbol_filter="*", direction="BOTH"):
    return execute(
        "INSERT INTO playbooks (name, description, symbol_filter, direction) VALUES (?,?,?,?)",
        (name, description, symbol_filter, direction)
    )

def update_playbook(playbook_id, name, description, symbol_filter, direction):
    execute(
        "UPDATE playbooks SET name=?, description=?, symbol_filter=?, direction=?, updated_at=datetime('now') WHERE id=?",
        (name, description, symbol_filter, direction, playbook_id)
    )

def delete_playbook(playbook_id):
    execute("DELETE FROM playbook_rules WHERE playbook_id=?", (playbook_id,))
    execute("DELETE FROM playbook_risk_rules WHERE playbook_id=?", (playbook_id,))
    execute("DELETE FROM playbooks WHERE id=?", (playbook_id,))

def upsert_rule(playbook_id, rule_id, name, description, rule_type, weight=1.0, sort_order=0,
                rule_group=None):
    rule_group = (rule_group or "").strip() or None
    if rule_id:
        execute(
            "UPDATE playbook_rules SET name=?, description=?, rule_type=?, weight=?, sort_order=?, rule_group=? WHERE id=?",
            (name, description, rule_type, weight, sort_order, rule_group, rule_id)
        )
        return rule_id
    else:
        return execute(
            "INSERT INTO playbook_rules (playbook_id, name, description, rule_type, weight, sort_order, rule_group) VALUES (?,?,?,?,?,?,?)",
            (playbook_id, name, description, rule_type, weight, sort_order, rule_group)
        )

def delete_rule(rule_id):
    execute("DELETE FROM playbook_rules WHERE id=?", (rule_id,))

def upsert_risk_rule(playbook_id, risk_rule_id, min_required_pct, min_optional_pct,
                     risk_level, risk_multiplier, warning_message,
                     trigger_rule_id=None, trigger_rule_must_be=1):
    if risk_rule_id:
        execute(
            """UPDATE playbook_risk_rules SET min_required_pct=?, min_optional_pct=?,
               risk_level=?, risk_multiplier=?, warning_message=?,
               trigger_rule_id=?, trigger_rule_must_be=? WHERE id=?""",
            (min_required_pct, min_optional_pct, risk_level, risk_multiplier,
             warning_message, trigger_rule_id, trigger_rule_must_be, risk_rule_id)
        )
    else:
        execute(
            """INSERT INTO playbook_risk_rules
               (playbook_id, min_required_pct, min_optional_pct, risk_level,
                risk_multiplier, warning_message, trigger_rule_id, trigger_rule_must_be)
               VALUES (?,?,?,?,?,?,?,?)""",
            (playbook_id, min_required_pct, min_optional_pct, risk_level,
             risk_multiplier, warning_message, trigger_rule_id, trigger_rule_must_be)
        )

def delete_risk_rule(risk_rule_id):
    execute("DELETE FROM playbook_risk_rules WHERE id=?", (risk_rule_id,))


def evaluate_trade_risk(playbook_id: int, rules_met: dict[int, bool]) -> dict:
    """
    Given a playbook and a map of {rule_id: bool}, compute:
    - required_pct: % of required rules met
    - optional_pct: % of optional rules met
    - bonus_count: number of bonus rules met
    - risk_assessment: matching risk rule (or None)
    - risk_score: 0-100
    - warnings: list of warning strings
    """
    pb = get_playbook(playbook_id)
    if not pb:
        return {}

    required = [r for r in pb["rules"] if r["rule_type"] == "required"]
    optional = [r for r in pb["rules"] if r["rule_type"] == "optional"]
    bonus    = [r for r in pb["rules"] if r["rule_type"] == "bonus"]

    def _or_units(rules):
        """Collapse rules sharing a rule_group into single OR-units.
        A grouped unit is met if ANY of its rules is met."""
        units, groups = [], {}
        for r in rules:
            met = bool(rules_met.get(r["id"], rules_met.get(str(r["id"]), False)))
            g = (r.get("rule_group") or "").strip()
            if g:
                if g not in groups:
                    groups[g] = {"names": [], "met": False}
                    units.append(groups[g])
                groups[g]["names"].append(r["name"])
                groups[g]["met"] = groups[g]["met"] or met
            else:
                units.append({"names": [r["name"]], "met": met})
        return units

    req_units   = _or_units(required)
    opt_units   = _or_units(optional)
    bonus_units = _or_units(bonus)

    req_met   = sum(1 for u in req_units   if u["met"])
    opt_met   = sum(1 for u in opt_units   if u["met"])
    bonus_met = sum(1 for u in bonus_units if u["met"])

    req_pct  = (req_met  / len(req_units)  * 100) if req_units  else 100.0
    opt_pct  = (opt_met  / len(opt_units)  * 100) if opt_units  else 100.0

    # Find triggered risk rules — check per-rule triggers first, then pct-based
    triggered_risk = None
    for rr in sorted(pb["risk_rules"], key=lambda x: -x.get("min_required_pct", 0)):
        trigger_rule_id = rr.get("trigger_rule_id")
        if trigger_rule_id:
            # Per-rule trigger: check if specific rule is met or not
            must_be = bool(rr.get("trigger_rule_must_be", 1))
            actual  = bool(rules_met.get(trigger_rule_id, rules_met.get(str(trigger_rule_id), False)))
            if actual != must_be:
                triggered_risk = rr
                break
        else:
            # Percentage-based trigger
            if req_pct < rr.get("min_required_pct", 100) or opt_pct < rr.get("min_optional_pct", 0):
                triggered_risk = rr
                break

    # Compute a score 0-100
    # Required rules are 60% of score, optional 30%, bonus 10%
    score = 0
    if req_units:
        score += (req_met / len(req_units)) * 60
    else:
        score += 60
    if opt_units:
        score += (opt_met / len(opt_units)) * 30
    else:
        score += 30
    if bonus_units:
        score += min(bonus_met / len(bonus_units), 1.0) * 10
    else:
        score += 10

    warnings = []
    if triggered_risk:
        warnings.append(triggered_risk.get("warning_message") or f"Risk level: {triggered_risk['risk_level']}")

    # Warn on any missing required unit (OR-groups named as "A OR B")
    for u in req_units:
        if not u["met"]:
            warnings.append(f"⚠️ Required rule not met: {' OR '.join(u['names'])}")

    return {
        "required_pct": round(req_pct, 1),
        "optional_pct": round(opt_pct, 1),
        "bonus_met": bonus_met,
        "risk_assessment": triggered_risk,
        "risk_score": round(score, 1),
        "risk_level": triggered_risk["risk_level"] if triggered_risk else "normal",
        "risk_multiplier": triggered_risk["risk_multiplier"] if triggered_risk else 1.0,
        "warnings": warnings,
        "rules_met_count": sum(rules_met.values()),
        "rules_total": len(pb["rules"]),
    }


def get_playbook_compliance_stats(playbook_id: int) -> dict:
    """Aggregate compliance stats across all trades using this playbook."""
    trades = fetch_all(
        "SELECT playbook_rules_met, pnl, risk_score FROM trades WHERE playbook_id=? AND status='closed' AND playbook_rules_met IS NOT NULL",
        (playbook_id,)
    )
    pb = get_playbook(playbook_id)
    if not pb or not trades:
        return {}

    rule_stats = {r["id"]: {"name": r["name"], "type": r["rule_type"], "met": 0, "total": 0, "pnl_when_met": 0, "pnl_when_not_met": 0} for r in pb["rules"]}

    for t in trades:
        try:
            rules_met = json.loads(t["playbook_rules_met"] or "{}")
        except:
            continue
        pnl = t["pnl"] or 0
        for r in pb["rules"]:
            rid = str(r["id"])
            rule_stats[r["id"]]["total"] += 1
            met = rules_met.get(rid, rules_met.get(r["id"], False))
            if met:
                rule_stats[r["id"]]["met"] += 1
                rule_stats[r["id"]]["pnl_when_met"] += pnl
            else:
                rule_stats[r["id"]]["pnl_when_not_met"] += pnl

    for rid in rule_stats:
        s = rule_stats[rid]
        s["compliance_pct"] = round(s["met"] / s["total"] * 100, 1) if s["total"] > 0 else 0

    return {
        "rule_stats": list(rule_stats.values()),
        "total_trades": len(trades),
        "avg_risk_score": round(sum(t["risk_score"] or 0 for t in trades) / len(trades), 1),
    }
