"""Pre-trade planning and post-trade review in the Trade Detail workspace."""
import streamlit as st
from datetime import date
from database import fetch_all, execute
from utils.trade_ops import get_templates
from utils.playbook_logic import get_playbooks
from pages.journal import _p, _journal_form, _flash
from utils.trade_context import entries_for_trade, linked_entries


def _get_entry(entry_type, entry_date, trade_id=None, stage=None):
    trade = fetch_all('SELECT * FROM trades WHERE id=?', (trade_id,))[0]
    entries = [row for row in entries_for_trade(trade, include_position=False) if row['entry_type'] == 'trade' and not row.get('position_id')]
    if stage == 'pre':
        return next((row for row in entries if row.get('stage') == 'pre' or any(row.get(key) for key in ('pre_plan','pre_analysis','pre_psychology'))), None)
    return next((row for row in entries if row.get('stage') == 'post'), None) or next((row for row in entries if row.get('candidate_id')), None) or (entries[0] if entries else None)


def _get_position_entry(position_id, stage=None):
    entries = fetch_all("SELECT * FROM journal_entries WHERE position_id=? AND entry_type='trade' ORDER BY id DESC", (position_id,))
    if stage == 'pre':
        return next((row for row in entries if row.get('stage') == 'pre' or row.get('pre_plan') or row.get('pre_analysis')), None)
    return next((row for row in entries if row.get('stage') == 'post'), None) or (entries[0] if entries else None)


def show_review(trade, between=None):
    templates = get_templates('trade')
    chosen = st.selectbox('Review template', [None] + [row['id'] for row in templates], format_func=lambda key: next((row['name'] for row in templates if row['id']==key), 'None'), key=f"review_template_{trade['id']}")
    template = next((row for row in templates if row['id']==chosen), None)
    scope = 'This trade'
    position = None
    if trade.get('position_id'):
        rows = fetch_all('SELECT * FROM positions WHERE id=?', (trade['position_id'],))
        position = rows[0] if rows else None
        if position and position.get('account_id') is None:
            position['account_id'] = trade['account_id']
    requested = st.session_state.pop('_detail_scope', None)
    if position:
        key = f"review_scope_{trade['id']}"
        if requested in ('This trade', 'Merged position'):
            st.session_state[key] = requested
        scope = st.selectbox('Journal scope', ['This trade', 'Merged position'], key=key)
    if scope == 'Merged position':
        trades = fetch_all('SELECT * FROM trades WHERE position_id=? AND account_id=? ORDER BY id', (position['id'], trade['account_id']))
        _journal_position(position['id'], {'pos': position, 'trades': trades}, template, _p(), between)
    else:
        _journal_single_trade(trade['id'], [trade], template, _p(), between)


def _journal_position(pos_id, pd_, template, p, between=None):
    """Journal UI for a merged position."""
    pos    = pd_["pos"]
    trades = sorted(pd_["trades"], key=lambda t: t.get("entry_time") or "")
    total_pnl = sum(float(t.get("pnl") or 0) for t in trades)
    pnl_col = p["--accent"] if total_pnl >= 0 else p["--danger"]
    open_dt  = min((t.get("entry_time") or "")[:10] for t in trades)
    close_dt = max((t.get("exit_time")  or t.get("entry_time") or "")[:10] for t in trades)

    # Position header
    st.markdown(
        f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border2"]};'
        f'border-left:4px solid {p["--accent"]};border-radius:8px;padding:12px 16px;margin-bottom:12px;">'
        f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">'
        f'<span style="font-size:1rem;font-weight:700;color:{p["--text-primary"]};">POS-{pos_id}</span>'
        f'<span style="color:{p["--text-muted"]};">{pos["symbol"]} {pos["direction"]}</span>'
        f'<span style="color:{p["--text-faint"]};font-size:0.8rem;">{open_dt} → {close_dt}</span>'
        f'<span style="font-family:\'JetBrains Mono\';font-weight:700;color:{pnl_col};margin-left:auto;">'
        f'Net P&L: {total_pnl:+,.2f}</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Constituent trades table
    with st.expander(f"📊 {len(trades)} constituent trades", expanded=True):
        for t in trades:
            t_pnl = float(t.get("pnl") or 0)
            t_col = p["--accent"] if t_pnl >= 0 else p["--danger"]
            comm  = abs(float(t.get("commission") or 0))
            st.markdown(
                f'<div style="display:flex;gap:16px;align-items:center;padding:5px 8px;'
                f'margin:2px 0;background:{p["--bg-card2"]};border-radius:5px;font-size:0.82rem;">'
                f'<span style="color:{p["--text-faint"]};width:36px;">#{t["id"]}</span>'
                f'<span style="color:{p["--text-primary"]};width:60px;">{(t.get("entry_time") or "")[:10]}</span>'
                f'<span style="color:{p["--text-muted"]};">'
                f'Entry: {t.get("entry_price") or "—"}  →  Exit: {t.get("exit_price") or "—"}</span>'
                f'<span style="font-family:\'JetBrains Mono\';color:{t_col};margin-left:auto;font-weight:600;">'
                f'{t_pnl:+.2f}</span>'
                f'{"<span style=\"color:" + p["--text-faint"] + ";font-size:0.75rem;\"> comm " + f"{comm:.2f}" + "</span>" if comm else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )

    entry_date = open_dt
    pre_entry  = _get_position_entry(pos_id, stage="pre")
    post_entry = _get_position_entry(pos_id, stage="post")

    pb_id = (
        (pre_entry or {}).get("playbook_id")
        or pos.get("playbook_id")
        or next((t.get("playbook_id") for t in trades if t.get("playbook_id")), None)
    )

    # Unassigned pre-trade plans
    unassigned = fetch_all(
        "SELECT * FROM journal_entries WHERE entry_type='trade' AND stage='pre' AND trade_id IS NULL AND position_id IS NULL AND (account_id IS NULL OR account_id=?) ORDER BY entry_date DESC LIMIT 20",
        (pos.get('account_id'),)
    )
    if unassigned:
        with st.expander(f"🔗 Link an existing pre-trade plan ({len(unassigned)} unassigned)"):
            for u in unassigned:
                pb_name = f" · {fetch_all('SELECT name FROM playbooks WHERE id=?', (u['playbook_id'],))[0]['name']}" if u.get("playbook_id") else ""
                u_sym = f" **{u['symbol']}**" if u.get("symbol") else ""
                st.markdown(f"**#{u['id']}**{u_sym} {u['entry_date']}{pb_name}")
                if u.get("pre_analysis") or u.get("pre_plan"): st.caption((u.get("pre_analysis") or u.get("pre_plan"))[:150])
                if st.button(f"Link #{u['id']} to POS-{pos_id}", key=f"link_pre_pos_{u['id']}_{pos_id}"):
                    linked_entries(u["id"], "position_id", pos_id)
                    st.success("Linked!"); st.rerun()
                st.divider()

    st.subheader('Pre-trade plan')
    if pre_entry:
        _show_pretrade_details(pre_entry)
        if st.button("✏️ Edit Pre-Trade Plan", key=f"edit_pre_pos_btn_{pos_id}"):
            st.session_state[f"pre_form_open_pos_{pos_id}"] = True
        if st.session_state.get(f"pre_form_open_pos_{pos_id}"):
            _pre_trade_form_position(pos_id, entry_date, pre_entry)
    else:
        st.caption("No pre-trade plan recorded for this position.")
        if st.button("➕ Add Pre-Trade Plan", key=f"add_pre_pos_btn_{pos_id}"):
            st.session_state[f"pre_form_open_pos_{pos_id}"] = True
        if st.session_state.get(f"pre_form_open_pos_{pos_id}"):
            _pre_trade_form_position(pos_id, entry_date, None)

    if between:
        between()

    st.subheader('Post-trade review')
    _journal_form("trade", entry_date, post_entry, template, position_id=pos_id, playbook_id=(post_entry or {}).get("playbook_id") or pb_id, stage="post")


def _pre_trade_psych_check(key_suffix, existing_body="", existing_accuracy="", existing_risk=""):
    """Render Menaker pre-trade psychology check-in fields. Returns (body_state, accuracy_check, risk_type)."""
    p = _p()
    with st.expander("🧠 Pre-Trade Psychology Check *(Menaker)*", expanded=not existing_body):
        st.markdown(
            f'<div style="font-size:0.78rem;color:{p["--text-muted"]};margin-bottom:8px;">'
            f'<b>Before you hit the button</b> — check your inner state, not just the chart.</div>',
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        with c1:
            accuracy_check = st.radio(
                "Am I being…",
                ["— Not answered —", "✅ Accurate (following my plan)", "⚡ Trying to make money (impulse)"],
                index=["— Not answered —", "✅ Accurate (following my plan)", "⚡ Trying to make money (impulse)"].index(existing_accuracy)
                       if existing_accuracy in ["— Not answered —", "✅ Accurate (following my plan)", "⚡ Trying to make money (impulse)"] else 0,
                key=f"accuracy_{key_suffix}",
                label_visibility="visible",
            )
            risk_type = st.radio(
                "This is…",
                ["— Not answered —", "✅ Smart risk (defined, on-plan)", "⚠️ Sloppy risk (unclear, reactive)"],
                index=["— Not answered —", "✅ Smart risk (defined, on-plan)", "⚠️ Sloppy risk (unclear, reactive)"].index(existing_risk)
                       if existing_risk in ["— Not answered —", "✅ Smart risk (defined, on-plan)", "⚠️ Sloppy risk (unclear, reactive)"] else 0,
                key=f"risk_type_{key_suffix}",
                label_visibility="visible",
            )
        with c2:
            body_state = st.text_area(
                "Body scan — what do you notice?",
                value=existing_body,
                height=120,
                placeholder="Breathing rate? Tense? Leaning forward? Calm? Energy level?",
                key=f"body_{key_suffix}",
            )
    return body_state, accuracy_check, risk_type


def _pre_trade_form_position(pos_id, entry_date, existing):
    e = existing or {}
    playbooks  = get_playbooks()
    pb_options = {"None": None} | {pb["name"]: pb["id"] for pb in playbooks}
    current_pb = next((pb["name"] for pb in playbooks if pb["id"] == e.get("playbook_id")), None) if e.get("playbook_id") else None
    sel_pb = st.selectbox("Playbook", list(pb_options.keys()),
                           index=list(pb_options.keys()).index(current_pb) if current_pb in pb_options else 0,
                           key=f"inline_pb_pos_{pos_id}")
    pre_analysis   = st.text_area("Market Analysis / Setup", value=e.get("pre_analysis",""),   height=100, key=f"i_analysis_pos_{pos_id}")
    pre_plan       = st.text_area("Trade Plan",              value=e.get("pre_plan",""),        height=100, key=f"i_plan_pos_{pos_id}")

    body_state, accuracy_check, risk_type = _pre_trade_psych_check(
        key_suffix=f"pos_{pos_id}",
        existing_body=e.get("pre_body_state",""),
        existing_accuracy=e.get("pre_accuracy_check",""),
        existing_risk=e.get("pre_risk_type",""),
    )

    pre_psychology = st.text_area("Mindset / Additional Notes", value=e.get("pre_psychology",""), height=80, key=f"i_psych_pos_{pos_id}")
    pre_risk_notes = st.text_area("Risk / Sizing Notes",        value=e.get("pre_risk_notes",""), height=60, key=f"i_risk_pos_{pos_id}")
    if st.button("💾 Save Pre-Trade Plan", type="primary", key=f"save_pre_pos_{pos_id}"):
        pb_id = pb_options.get(sel_pb)
        if e.get("id"):
            execute("""UPDATE journal_entries SET pre_analysis=?, pre_plan=?, pre_psychology=?,
                       pre_risk_notes=?, playbook_id=?, pre_body_state=?, pre_accuracy_check=?,
                       pre_risk_type=?, updated_at=datetime('now') WHERE id=?""",
                    (pre_analysis, pre_plan, pre_psychology, pre_risk_notes, pb_id,
                     body_state, accuracy_check, risk_type, e["id"]))
        else:
            execute("""INSERT INTO journal_entries
                       (entry_type, entry_date, position_id, account_id, stage, playbook_id,
                        pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                        pre_body_state, pre_accuracy_check, pre_risk_type)
                       VALUES ('trade',?,?,(SELECT MIN(account_id) FROM trades WHERE position_id=?),'pre',?,?,?,?,?,?,?,?)""",
                    (entry_date, pos_id, pos_id, pb_id, pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                     body_state, accuracy_check, risk_type))
        st.session_state.pop(f"pre_form_open_pos_{pos_id}", None)
        _flash("Pre-trade plan saved!")
        st.rerun()


def _journal_single_trade(trade_id, raw_trades, template, p, between=None):
    """Journal UI for an individual (unmerged) trade."""
    trade = next((t for t in raw_trades if t["id"] == trade_id), None)
    if not trade:
        return

    entry_date = (trade.get("entry_time") or str(date.today()))[:10]
    pre_entry  = _get_entry("trade", entry_date, trade_id=trade_id, stage="pre")
    post_entry = _get_entry("trade", entry_date, trade_id=trade_id, stage="post")

    unassigned = fetch_all(
        "SELECT * FROM journal_entries WHERE entry_type='trade' AND stage='pre' AND trade_id IS NULL AND position_id IS NULL AND (account_id IS NULL OR account_id=?) ORDER BY entry_date DESC LIMIT 20",
        (trade.get('account_id'),)
    )
    if unassigned:
        with st.expander(f"🔗 Link an existing pre-trade plan ({len(unassigned)} unassigned)"):
            for u in unassigned:
                u_sym = f" **{u['symbol']}**" if u.get("symbol") else ""
                st.markdown(f"**#{u['id']}**{u_sym} {u['entry_date']}")
                if u.get("pre_analysis") or u.get("pre_plan"): st.caption((u.get("pre_analysis") or u.get("pre_plan"))[:150])
                if st.button(f"Link #{u['id']} to this trade", key=f"link_pre_{u['id']}_{trade_id}"):
                    linked_entries(u["id"], "trade_id", trade_id)
                    st.success("Linked!"); st.rerun()
                st.divider()

    st.subheader('Pre-trade plan')
    if pre_entry:
        _show_pretrade_details(pre_entry)
        if st.button("✏️ Edit Pre-Trade Plan", key=f"edit_pre_btn_{trade_id}"):
            st.session_state[f"pre_form_open_{trade_id}"] = True
        if st.session_state.get(f"pre_form_open_{trade_id}"):
            _pre_trade_form_inline(trade, pre_entry)
    else:
        st.caption("No pre-trade plan recorded for this trade.")
        if st.button("➕ Add Pre-Trade Plan", key=f"add_pre_btn_{trade_id}"):
            st.session_state[f"pre_form_open_{trade_id}"] = True
        if st.session_state.get(f"pre_form_open_{trade_id}"):
            _pre_trade_form_inline(trade, None)

    if between:
        between()

    st.subheader('Post-trade review')
    _journal_form("trade", entry_date, post_entry, template, trade_id=trade_id, playbook_id=(post_entry or {}).get("playbook_id") or trade.get("playbook_id"), stage="post")


def _pre_trade_form_inline(trade, existing):
    e = existing or {}
    entry_date = (trade.get("entry_time") or str(date.today()))[:10]
    playbooks  = get_playbooks()
    pb_options = {"None": None} | {pb["name"]: pb["id"] for pb in playbooks}
    current_pb = None
    if e.get("playbook_id"):
        current_pb = next((pb["name"] for pb in playbooks if pb["id"] == e["playbook_id"]), None)
    sel_pb = st.selectbox("Playbook", list(pb_options.keys()),
                           index=list(pb_options.keys()).index(current_pb) if current_pb in pb_options else 0,
                           key=f"inline_pb_{trade['id']}")
    pre_analysis   = st.text_area("Market Analysis / Setup", value=e.get("pre_analysis",""),   height=100, key=f"i_analysis_{trade['id']}")
    pre_plan       = st.text_area("Trade Plan",              value=e.get("pre_plan",""),        height=100, key=f"i_plan_{trade['id']}")

    body_state, accuracy_check, risk_type = _pre_trade_psych_check(
        key_suffix=f"trade_{trade['id']}",
        existing_body=e.get("pre_body_state",""),
        existing_accuracy=e.get("pre_accuracy_check",""),
        existing_risk=e.get("pre_risk_type",""),
    )

    pre_psychology = st.text_area("Mindset / Additional Notes", value=e.get("pre_psychology",""), height=80, key=f"i_psych_{trade['id']}")
    pre_risk_notes = st.text_area("Risk / Sizing Notes",        value=e.get("pre_risk_notes",""), height=60, key=f"i_risk_{trade['id']}")
    if st.button("💾 Save Pre-Trade Plan", type="primary", key=f"save_pre_{trade['id']}"):
        pb_id = pb_options.get(sel_pb)
        if e.get("id"):
            execute("""UPDATE journal_entries SET pre_analysis=?, pre_plan=?, pre_psychology=?,
                       pre_risk_notes=?, playbook_id=?, pre_body_state=?, pre_accuracy_check=?,
                       pre_risk_type=?, updated_at=datetime('now') WHERE id=?""",
                    (pre_analysis, pre_plan, pre_psychology, pre_risk_notes, pb_id,
                     body_state, accuracy_check, risk_type, e["id"]))
        else:
            execute("""INSERT INTO journal_entries
                       (entry_type, entry_date, trade_id, account_id, stage, playbook_id,
                        pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                        pre_body_state, pre_accuracy_check, pre_risk_type)
                       VALUES ('trade',?,?,?,'pre',?,?,?,?,?,?,?,?)""",
                    (entry_date, trade["id"], trade['account_id'], pb_id,
                     pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                     body_state, accuracy_check, risk_type))
        st.session_state.pop(f"pre_form_open_{trade['id']}", None)
        _flash("Pre-trade plan saved!")
        st.rerun()


def _show_pretrade_details(entry):
    from utils.journal_display import show_note
    fields = [('pre_analysis', 'Analysis / setup'), ('pre_plan', 'Trade plan'),
              ('pre_playbook_notes', 'Playbook notes'), ('pre_risk_notes', 'Risk / sizing'),
              ('pre_accuracy_check', 'Accuracy check'), ('pre_risk_type', 'Risk type'),
              ('pre_body_state', 'Body state'), ('pre_psychology', 'Mindset')]
    for field, title in fields:
        value = entry.get(field)
        if value and 'Not answered' not in value:
            st.markdown(f'**{title}**')
            show_note(value)
