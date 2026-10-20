"""Unassigned trade plans and journal template administration."""
import streamlit as st
from datetime import date
from database import fetch_all, execute
from utils.trade_ops import get_templates, save_template, delete_journal_entry
from utils.playbook_logic import get_playbooks, get_playbook
from utils.accounts import get_accounts
from pages.journal import _acc_where, _flash
from utils.trade_context import linked_entries, open_trade_detail

def show_plans(account_id=None):
    st.subheader("📋 Pre-Trade Plans")
    st.markdown("Write your plan before entering a trade, then link it to an execution below or in Trade Detail. Plans and reviews share the same Trade Detail workspace.")
    tab_new, tab_existing = st.tabs(["✏️ New Plan", "📂 Existing Plans"])
    with tab_new:      _new_pretrade_plan(account_id)
    with tab_existing: _list_pretrade_plans(account_id)


def _new_pretrade_plan(account_id=None):
    col1, col_sym, col2, col3 = st.columns([2, 1, 1, 1])
    with col1:
        plan_date = st.date_input("Date", value=date.today(), key="pt_date")
    with col_sym:
        plan_symbol = st.text_input("Symbol", key="pt_symbol", placeholder="e.g. AAPL").strip().upper()
    with col2:
        playbooks  = get_playbooks()
        pb_options = {"None": None} | {pb["name"]: pb["id"] for pb in playbooks}
        sel_pb     = st.selectbox("Playbook", list(pb_options.keys()), key="pt_pb")
        pb_id      = pb_options.get(sel_pb)
    with col3:
        accounts    = get_accounts()
        acc_options = {"— None —": None} | {a["name"]: a["id"] for a in accounts}
        default_acc = next((a["name"] for a in accounts if a["id"] == account_id), "— None —")
        sel_acc     = st.selectbox("Account", list(acc_options.keys()),
                                   index=list(acc_options.keys()).index(default_acc),
                                   key="pt_account")
        plan_acc_id = acc_options.get(sel_acc)

    if pb_id:
        pb = get_playbook(pb_id)
        if pb and pb.get("rules"):
            with st.expander("📖 Playbook rules checklist", expanded=True):
                for rule in pb["rules"]:
                    badge = {"required":"🔴","optional":"🟡","bonus":"🟢"}.get(rule["rule_type"],"⚪")
                    grp = f"  ⛓ _{rule['rule_group']} (any one)_" if rule.get("rule_group") else ""
                    st.checkbox(
                        f"{badge} {rule['name']}{grp}",
                        key=f"pt_rule_{pb_id}_{rule['id']}",
                    )

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        pre_analysis   = st.text_area("📊 Market Analysis & Setup", height=150, key="pt_analysis",
                                       placeholder="What's the macro context? What setup are you seeing?")
        pre_plan       = st.text_area("🎯 Trade Plan", height=150, key="pt_plan",
                                       placeholder="Entry trigger, stop loss level, take profit target, position size, invalidation point...")
    with col2:
        pre_psychology = st.text_area("🧠 Mindset Check", height=150, key="pt_psychology",
                                       placeholder="How are you feeling? Any emotional biases to watch for?")
        pre_risk_notes = st.text_area("⚖️ Risk Notes", height=150, key="pt_risk",
                                       placeholder="Planned risk %, lot size rationale, any special considerations...")

    if st.button("💾 Save Pre-Trade Plan", type="primary"):
        execute("""INSERT INTO journal_entries
                   (entry_type, entry_date, stage, playbook_id,
                    pre_analysis, pre_plan, pre_psychology, pre_risk_notes, account_id, symbol)
                   VALUES ('trade',?,'pre',?,?,?,?,?,?,?)""",
                (str(plan_date), pb_id, pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                 plan_acc_id, plan_symbol or None))
        _flash("Pre-trade plan saved! Come back after the trade to link it and write your post-trade review.")
        st.rerun()


def _list_pretrade_plans(account_id=None):
    plans = fetch_all(
        """SELECT j.*, p.name as playbook_name, a.name as account_name, t.symbol as trade_symbol,
           COALESCE(j.trade_id, (SELECT MIN(f.trade_id) FROM fill_events f JOIN candidates c ON c.id=f.candidate_id
               WHERE c.journal_entry_id=j.id AND f.account_id=j.account_id),
               (SELECT MIN(pt.id) FROM trades pt WHERE pt.position_id=j.position_id)) AS linked_trade_id,
           COALESCE(j.account_id,t.account_id,(SELECT MIN(pt.account_id) FROM trades pt WHERE pt.position_id=j.position_id)) AS owner_account_id
           FROM journal_entries j
           LEFT JOIN playbooks p ON j.playbook_id = p.id
           LEFT JOIN accounts  a ON j.account_id  = a.id
           LEFT JOIN trades    t ON j.trade_id    = t.id
           WHERE j.entry_type='trade' AND (j.stage='pre' OR COALESCE(j.pre_plan,'')!='' OR COALESCE(j.pre_analysis,'')!='')
           AND NOT EXISTS (SELECT 1 FROM pattern_entries pe WHERE pe.legacy_journal_entry_id=j.id)
           ORDER BY j.entry_date DESC, j.created_at DESC"""
    )
    # Journal account filter: show plans for that account + plans with no account set
    if account_id:
        plans = [p for p in plans if p.get("owner_account_id") in (None, account_id)]
    if not plans:
        st.caption("No pre-trade plans yet.")
        return

    unassigned = [p for p in plans if not p.get('linked_trade_id') and not p.get("position_id")]
    assigned   = [p for p in plans if p.get('linked_trade_id') or p.get("position_id")]

    if unassigned:
        st.markdown(f"**Unassigned plans ({len(unassigned)}) — waiting to be linked:**")
        for plan in unassigned:
            pb_badge  = f" · 📖 {plan['playbook_name']}" if plan.get("playbook_name") else ""
            acc_badge = f" · 🏦 {plan['account_name']}" if plan.get("account_name") else ""
            sym_label = f" · {plan['symbol']}" if plan.get("symbol") else ""
            with st.expander(f"📋 {plan['entry_date']}{pb_badge}{acc_badge}  _(unassigned{sym_label})_"):
                if plan.get("pre_analysis"): st.markdown(f"**Analysis:** {plan['pre_analysis'][:300]}")
                if plan.get("pre_plan"):     st.markdown(f"**Plan:** {plan['pre_plan'][:200]}")
                # Trades offered for linking: prefer the plan's own account, else journal filter.
                # Open trades included — swing plans link right after entry.
                link_acc = plan.get("account_id") or account_id
                acc_frag, acc_params = _acc_where(link_acc)
                trades = fetch_all(
                    f"""SELECT * FROM trades WHERE status IN ('open','closed') AND {acc_frag}
                        ORDER BY status='closed', id DESC LIMIT 200""",
                    acc_params,
                )
                if trades:
                    def trade_label(x):
                        t = next((t for t in trades if t["id"] == x), None)
                        if not t: return str(x)
                        badge = "🔓 OPEN " if t.get("status") == "open" else ""
                        return f"{badge}#{x} {t['symbol']} {t['direction']} {(t.get('entry_time') or '')[:10]}"
                    trade_id = st.selectbox(
                        "Link to trade", [None] + [t["id"] for t in trades],
                        format_func=lambda x: "— select —" if x is None else trade_label(x),
                        key=f"link_sel_{plan['id']}",
                    )
                    col1, col2 = st.columns(2)
                    if col1.button("🔗 Link", key=f"do_link_{plan['id']}", disabled=trade_id is None):
                        linked_entries(plan["id"], "trade_id", trade_id)
                        st.success("Linked!"); st.rerun()
                    if col2.button("🗑️ Delete", key=f"del_pre_{plan['id']}"):
                        delete_journal_entry(plan["id"]); st.rerun()

    if assigned:
        st.divider()
        st.markdown(f"**Linked plans ({len(assigned)}):**")
        for plan in assigned[:10]:
            pb_badge  = f" · {plan['playbook_name']}" if plan.get("playbook_name") else ""
            acc_badge = f" · 🏦 {plan['account_name']}" if plan.get("account_name") else ""
            trade_sym = f" {plan['trade_symbol']}" if plan.get("trade_symbol") else ""
            trade_info = f" → Position #{plan['position_id']}" if plan.get('position_id') else f" → Trade #{plan['linked_trade_id']}{trade_sym}"
            with st.expander(f"✅ {plan['entry_date']}{pb_badge}{acc_badge}{trade_info}"):
                if plan.get("pre_analysis"): st.markdown(f"**Analysis:** {plan['pre_analysis'][:200]}")
                if plan.get('pre_plan'): st.markdown(f"**Plan:** {plan['pre_plan'][:300]}")
                if plan.get('linked_trade_id') and st.button('Open plan & review', key=f"open_plan_{plan['id']}"):
                    open_trade_detail(plan['linked_trade_id'], plan['owner_account_id'], position_scope=bool(plan.get('position_id')))
                    st.rerun()



def show_templates():
    st.subheader("Journal Templates")
    templates = get_templates()

    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown("**Templates**")
        for t in templates:
            type_icon = {"daily":"📅","weekly":"📆","trade":"📊"}.get(t["template_type"],"📋")
            star = " ⭐" if t["is_default"] else ""
            if st.button(f"{type_icon} {t['name']}{star}", key=f"tmpl_{t['id']}", use_container_width=True):
                st.session_state["editing_template_id"] = t["id"]

    with col2:
        eid  = st.session_state.get("editing_template_id")
        tmpl = next((t for t in templates if t["id"] == eid), None) if eid else None

        with st.form("template_form"):
            t_name       = st.text_input("Name",    value=tmpl["name"]              if tmpl else "")
            t_type       = st.selectbox("Type",     ["trade","daily","weekly"],
                                         index=["trade","daily","weekly"].index(tmpl["template_type"]) if tmpl else 0)
            t_analysis   = st.text_area("Analysis Template",   value=tmpl.get("analysis_template","")   if tmpl else "", height=120)
            t_execution  = st.text_area("Execution Template",  value=tmpl.get("execution_template","")  if tmpl else "", height=120)
            t_psychology = st.text_area("Psychology Template", value=tmpl.get("psychology_template","") if tmpl else "", height=120)
            t_default    = st.checkbox("Default for type", value=bool(tmpl and tmpl["is_default"]))

            c1, c2 = st.columns(2)
            if c1.form_submit_button("💾 Save", type="primary"):
                if t_name:
                    save_template(eid, t_name, t_type, t_analysis, t_execution, t_psychology, t_default)
                    st.success("Saved!"); st.session_state.pop("editing_template_id", None); st.rerun()
            if c2.form_submit_button("➕ New"):
                st.session_state.pop("editing_template_id", None); st.rerun()
