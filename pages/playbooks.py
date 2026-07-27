"""
Page: Playbook Manager — create/edit playbooks, rules, risk thresholds
"""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.playbook_logic import (
    get_playbooks, get_playbook, create_playbook, update_playbook, delete_playbook,
    upsert_rule, delete_rule, upsert_risk_rule, delete_risk_rule, evaluate_trade_risk
)

RULE_TYPES   = ["required", "optional", "bonus"]
RISK_LEVELS  = ["normal", "reduced", "no_trade"]
RISK_COLORS  = {"normal": "🟢", "reduced": "🟡", "no_trade": "🔴"}

WEIGHT_HELP = """**Weight** controls how much a rule contributes to the quality score relative to other rules of the same type.

- All rules default to weight **1.0**
- A rule with weight **2.0** counts twice as much as a rule with weight **1.0**
- Useful when some rules are more significant than others

**Score formula:**
- Required rules → 60% of total score (weighted)
- Optional rules → 30% of total score (weighted)
- Bonus rules    → 10% of total score (weighted)

A trade hitting all required rules scores at least **60/100**."""


def show():
    st.header("📖 Playbooks")
    playbooks = get_playbooks()

    col_list, col_detail = st.columns([1, 2])

    with col_list:
        st.subheader("My Playbooks")
        if st.button("➕ New Playbook", use_container_width=True, type="primary"):
            st.session_state["editing_playbook"] = "new"
            st.session_state.pop("selected_playbook_id", None)

        for pb in playbooks:
            active = st.session_state.get("selected_playbook_id") == pb["id"]
            prefix = "▶ " if active else ""
            if st.button(f"{prefix}{pb['name']}", key=f"pb_{pb['id']}", use_container_width=True):
                st.session_state["selected_playbook_id"] = pb["id"]
                st.session_state.pop("editing_playbook", None)

        if not playbooks:
            st.caption("No playbooks yet.")

    with col_detail:
        editing     = st.session_state.get("editing_playbook")
        selected_id = st.session_state.get("selected_playbook_id")

        if editing == "new":
            _create_playbook_form()
        elif selected_id:
            pb = get_playbook(selected_id)
            if pb:
                _show_playbook_detail(pb)
        else:
            _show_intro()


def _show_intro():
    st.markdown("""
    ### How Playbooks Work

    A **Playbook** is a named trade setup with a checklist of rules. Before or after a trade,
    you mark which rules were met — the app scores it and can warn you if risk thresholds are breached.

    **Rule types:**
    | Type | Weight in score | Purpose |
    |---|---|---|
    | 🔴 Required | 60% of score | Non-negotiable criteria — missing these kills the setup |
    | 🟡 Optional | 30% of score | Good to have — missing some reduces score |
    | 🟢 Bonus | 10% of score | Extra confirmation — adds quality when present |

    **Risk Thresholds** let you define automatic warnings:
    - When required rules are only partially met → reduce size or skip
    - When a *specific* rule is missing → trigger a warning regardless of overall score

    Select a playbook on the left, or create a new one.
    """)


def _create_playbook_form():
    st.subheader("Create New Playbook")
    with st.form("new_playbook"):
        name          = st.text_input("Name*", placeholder="e.g. London Breakout Setup")
        description   = st.text_area("Description", placeholder="When to use this playbook...")
        c1, c2 = st.columns(2)
        symbol_filter = c1.text_input("Symbol Filter", value="*",
                                       help="'*' for all symbols, or comma-separated e.g. 'EURUSD,GBPUSD'")
        direction     = c2.selectbox("Direction", ["BOTH", "LONG", "SHORT"])
        if st.form_submit_button("Create", type="primary"):
            if not name:
                st.error("Name required")
            else:
                pb_id = create_playbook(name, description, symbol_filter, direction)
                st.session_state["selected_playbook_id"] = pb_id
                st.session_state.pop("editing_playbook", None)
                st.rerun()


def _show_playbook_detail(pb):
    # Header
    c1, c2 = st.columns([4, 1])
    c1.subheader(pb["name"])
    if c2.button("🗑️ Delete", key="del_pb"):
        delete_playbook(pb["id"])
        st.session_state.pop("selected_playbook_id", None)
        st.rerun()

    with st.expander("✏️ Edit Details"):
        with st.form(f"edit_pb_{pb['id']}"):
            name          = st.text_input("Name", value=pb["name"])
            description   = st.text_area("Description", value=pb.get("description", ""))
            c1, c2 = st.columns(2)
            symbol_filter = c1.text_input("Symbol Filter", value=pb.get("symbol_filter", "*"))
            direction     = c2.selectbox("Direction", ["BOTH", "LONG", "SHORT"],
                                          index=["BOTH","LONG","SHORT"].index(pb.get("direction","BOTH")))
            if st.form_submit_button("Save"):
                update_playbook(pb["id"], name, description, symbol_filter, direction)
                st.rerun()

    st.caption(f"_{pb.get('description','')}_  ·  Symbols: `{pb.get('symbol_filter','*')}`  ·  Direction: `{pb.get('direction','BOTH')}`")
    st.divider()

    # ── RULES ────────────────────────────────────────────────────────────────
    st.subheader("Rules")

    # Scoring explanation callout
    with st.expander("ℹ️ How rule types and weights work"):
        st.markdown(WEIGHT_HELP)

    _GROUP_HELP = ("Rules sharing the same group name form an OR set — ANY one of them "
                   "satisfies the whole group (counted as a single requirement). "
                   "Leave blank for a normal standalone rule.")

    for rule in pb.get("rules", []):
        badge = {"required": "🔴", "optional": "🟡", "bonus": "🟢"}.get(rule["rule_type"], "⚪")
        grp_lbl = f"  ·  ⛓ OR: {rule['rule_group']}" if rule.get("rule_group") else ""
        with st.expander(f"{badge} {rule['name']}  ·  _{rule['rule_type']}_  ·  weight {rule.get('weight',1.0):.1f}{grp_lbl}"):
            with st.form(f"rule_{rule['id']}"):
                c1, c2 = st.columns([3, 1])
                rname  = c1.text_input("Rule Name", value=rule["name"])
                rtype  = c2.selectbox("Type", RULE_TYPES, index=RULE_TYPES.index(rule["rule_type"]))
                rdesc  = st.text_area("Description", value=rule.get("description", ""),
                                       placeholder="What exactly does this rule mean? What do you look for?")
                c3, c4, c5 = st.columns(3)
                weight = c3.number_input("Weight", value=float(rule.get("weight", 1.0)),
                                          min_value=0.1, max_value=5.0, step=0.5,
                                          help="Relative importance vs other rules of the same type. Default 1.0 = equal weight.")
                order  = c4.number_input("Sort Order", value=int(rule.get("sort_order", 0)),
                                          min_value=0, step=1)
                rgroup = c5.text_input("OR Group", value=rule.get("rule_group") or "",
                                        placeholder="e.g. rebalance", help=_GROUP_HELP)

                ca, cb = st.columns(2)
                if ca.form_submit_button("💾 Save"):
                    upsert_rule(pb["id"], rule["id"], rname, rdesc, rtype, weight, order, rgroup)
                    st.rerun()
                if cb.form_submit_button("🗑️ Delete"):
                    delete_rule(rule["id"])
                    st.rerun()

    with st.expander("➕ Add Rule"):
        with st.form(f"new_rule_{pb['id']}"):
            c1, c2 = st.columns([3, 1])
            rname  = c1.text_input("Rule Name*", placeholder="e.g. Price above 200 EMA")
            rtype  = c2.selectbox("Type", RULE_TYPES)
            rdesc  = st.text_area("Description", placeholder="What does this rule mean in practice?")
            c3, c4, c5 = st.columns(3)
            weight = c3.number_input("Weight", value=1.0, min_value=0.1, max_value=5.0, step=0.5,
                                      help="Higher = more influence on score. Most rules should be 1.0.")
            order  = c4.number_input("Sort Order", value=len(pb.get("rules", [])), min_value=0, step=1)
            rgroup = c5.text_input("OR Group", placeholder="e.g. rebalance", help=_GROUP_HELP)
            if st.form_submit_button("Add Rule", type="primary"):
                if not rname:
                    st.error("Rule name required")
                else:
                    upsert_rule(pb["id"], None, rname, rdesc, rtype, weight, order, rgroup)
                    st.rerun()

    st.divider()

    # ── RISK THRESHOLDS ──────────────────────────────────────────────────────
    st.subheader("Risk Thresholds")
    st.markdown(
        "Define consequences when rules aren't met. "
        "Thresholds can trigger on **overall % compliance** or when a **specific rule** is/isn't met."
    )

    rules = pb.get("rules", [])
    rule_options = {f"#{r['id']} {r['name']} ({r['rule_type']})": r["id"] for r in rules}

    for rr in pb.get("risk_rules", []):
        icon    = RISK_COLORS.get(rr["risk_level"], "⚪")
        # Build label
        trid    = rr.get("trigger_rule_id")
        if trid:
            rule_name = next((r["name"] for r in rules if r["id"] == trid), f"Rule #{trid}")
            must_be   = "met" if rr.get("trigger_rule_must_be", 1) else "NOT met"
            lbl = f"{icon} {rr['risk_level'].upper()} — if **{rule_name}** is {must_be}"
        else:
            lbl = f"{icon} {rr['risk_level'].upper()} — Required ≥ {rr['min_required_pct']:.0f}% | Optional ≥ {rr.get('min_optional_pct',0):.0f}%"

        with st.expander(lbl):
            with st.form(f"rr_{rr['id']}"):
                trigger_mode = st.radio(
                    "Trigger mode",
                    ["Percentage-based", "Specific rule"],
                    index=1 if trid else 0,
                    horizontal=True,
                    key=f"rr_mode_{rr['id']}"
                )

                if trigger_mode == "Specific rule":
                    st.caption("This threshold fires when a named rule is (or isn't) met, regardless of overall score.")
                    c1, c2 = st.columns(2)
                    rule_sel = c1.selectbox(
                        "Rule",
                        list(rule_options.keys()),
                        index=list(rule_options.values()).index(trid) if trid and trid in rule_options.values() else 0,
                        key=f"rr_rule_sel_{rr['id']}"
                    )
                    must_be_val = c2.selectbox("Condition", ["Must be met", "Must NOT be met"],
                                                index=0 if rr.get("trigger_rule_must_be", 1) else 1,
                                                key=f"rr_mustbe_{rr['id']}")
                    new_trigger_id    = rule_options[rule_sel]
                    new_trigger_must  = 1 if must_be_val == "Must be met" else 0
                    req_pct_val = float(rr.get("min_required_pct", 100))
                    opt_pct_val = float(rr.get("min_optional_pct", 0))
                else:
                    st.caption("This threshold fires when overall compliance drops below the percentages.")
                    c1, c2 = st.columns(2)
                    req_pct_val = c1.number_input("Min Required %", value=float(rr["min_required_pct"]),
                                                   min_value=0.0, max_value=100.0, step=5.0)
                    opt_pct_val = c2.number_input("Min Optional %", value=float(rr.get("min_optional_pct",0)),
                                                   min_value=0.0, max_value=100.0, step=5.0)
                    new_trigger_id   = None
                    new_trigger_must = 1

                st.divider()
                c3, c4, c5 = st.columns(3)
                risk_level = c3.selectbox("Risk Level", RISK_LEVELS,
                                           index=RISK_LEVELS.index(rr["risk_level"]),
                                           key=f"rr_rl_{rr['id']}")
                multiplier = c4.number_input("Size Multiplier", value=float(rr["risk_multiplier"]),
                                              min_value=0.0, max_value=2.0, step=0.25,
                                              help="0 = no trade, 0.5 = half size, 1.0 = full size")
                warning    = c5.text_input("Warning message", value=rr.get("warning_message", ""))

                ca, cb = st.columns(2)
                if ca.form_submit_button("💾 Save"):
                    upsert_risk_rule(pb["id"], rr["id"], req_pct_val, opt_pct_val,
                                     risk_level, multiplier, warning,
                                     new_trigger_id, new_trigger_must)
                    st.rerun()
                if cb.form_submit_button("🗑️ Delete"):
                    delete_risk_rule(rr["id"])
                    st.rerun()

    with st.expander("➕ Add Risk Threshold"):
        with st.form(f"new_rr_{pb['id']}"):
            trigger_mode = st.radio(
                "Trigger mode",
                ["Percentage-based", "Specific rule"],
                horizontal=True,
                key=f"new_rr_mode_{pb['id']}"
            )

            new_trigger_id   = None
            new_trigger_must = 1
            req_pct_new = 100.0
            opt_pct_new = 0.0

            if trigger_mode == "Specific rule" and rule_options:
                c1, c2 = st.columns(2)
                rule_sel  = c1.selectbox("Rule", list(rule_options.keys()))
                must_be_v = c2.selectbox("Condition", ["Must be met", "Must NOT be met"])
                new_trigger_id   = rule_options[rule_sel]
                new_trigger_must = 1 if must_be_v == "Must be met" else 0
                st.caption("⚠️ This threshold fires every time the selected rule condition is not satisfied.")
            elif trigger_mode == "Specific rule":
                st.warning("Add rules to this playbook first, then add rule-specific thresholds.")
            else:
                c1, c2 = st.columns(2)
                req_pct_new = c1.number_input("Min Required %", value=100.0, min_value=0.0, max_value=100.0, step=5.0)
                opt_pct_new = c2.number_input("Min Optional %", value=50.0,  min_value=0.0, max_value=100.0, step=5.0)

            st.divider()
            c3, c4, c5 = st.columns(3)
            risk_level_new = c3.selectbox("Risk Level", RISK_LEVELS)
            multiplier_new = c4.number_input("Size Multiplier", value=0.5, min_value=0.0, max_value=2.0, step=0.25)
            warning_new    = c5.text_input("Warning message",
                                            placeholder="e.g. Core rule missing — skip or reduce to 50%")

            if st.form_submit_button("Add Threshold", type="primary"):
                upsert_risk_rule(pb["id"], None, req_pct_new, opt_pct_new,
                                 risk_level_new, multiplier_new, warning_new,
                                 new_trigger_id, new_trigger_must)
                st.rerun()

    st.divider()

    # ── LIVE EVALUATOR ───────────────────────────────────────────────────────
    st.subheader("🧪 Live Evaluator")
    st.caption("Tick rules to preview the score and risk level in real time:")

    if pb.get("rules"):
        rules_met = {}
        for rule in pb["rules"]:
            badge = {"required": "🔴", "optional": "🟡", "bonus": "🟢"}.get(rule["rule_type"], "⚪")
            grp = f"  ⛓ _{rule['rule_group']} (any one)_" if rule.get("rule_group") else ""
            rules_met[rule["id"]] = st.checkbox(
                f"{badge} {rule['name']}{grp}",
                key=f"eval_{rule['id']}"
            )

        result = evaluate_trade_risk(pb["id"], rules_met)
        if result:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Quality Score",   f"{result['risk_score']} / 100")
            c2.metric("Required Met",    f"{result['required_pct']}%")
            c3.metric("Optional Met",    f"{result['optional_pct']}%")
            c4.metric("Bonus Rules",     f"{result['bonus_met']}")

            rl   = result.get("risk_level", "normal")
            icon = RISK_COLORS.get(rl, "⚪")
            mult = result.get("risk_multiplier", 1.0)
            st.markdown(
                f"**Risk Level:** {icon} {rl.upper()}  ·  "
                f"**Size Multiplier:** {mult:.2f}x  ·  "
                f"**Rules met:** {result['rules_met_count']} / {result['rules_total']}"
            )
            for w in result.get("warnings", []):
                st.warning(w)
    else:
        st.info("Add rules to this playbook to use the evaluator.")
