"""Trade reviews shared by the review queue and the Trade Detail workspace."""
import streamlit as st
import json
from datetime import date
from database import fetch_all, execute
from utils.trade_ops import get_templates
from utils.playbook_logic import get_playbooks, get_playbook
from pages.journal import _p, _acc_where, _journal_form, _flash
from utils.trade_context import entries_for_trade, open_trade_detail, linked_entries


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


def show_review(trade):
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
        _journal_position(position['id'], {'pos': position, 'trades': trades}, template, _p())
    else:
        _journal_single_trade(trade['id'], [trade], template, _p())


def show_notes(account_id=None):
    p = _p()
    st.subheader("Trade Notes")
    st.caption("Review coverage and open the shared plan/review editor in Trade Detail. Merged positions remain a single journal unit.")

    acc_frag, acc_params = _acc_where(account_id)
    raw_trades = fetch_all(
        f"""SELECT * FROM trades WHERE status IN ('open','closed') AND {acc_frag}
            ORDER BY status='closed', id DESC LIMIT 500""",
        acc_params,
    )
    if not raw_trades:
        st.info("No trades found for the selected account.")
        return

    # ── Build reviewed sets ───────────────────────────────────────────────────
    all_trade_ids = [t["id"] for t in raw_trades]
    reviewed_trade_ids = set()
    if all_trade_ids:
        ph = ",".join("?" * len(all_trade_ids))
        rows = fetch_all(
            f"""SELECT DISTINCT t.id AS trade_id FROM trades t
                JOIN journal_entries j ON j.trade_id=t.id OR j.id IN (
                    SELECT c.journal_entry_id FROM candidates c JOIN fill_events f ON f.candidate_id=c.id
                    WHERE f.trade_id=t.id AND f.account_id=t.account_id)
                WHERE j.stage='post' AND j.entry_type='trade' AND t.id IN ({ph})
                AND (j.account_id IS NULL OR j.account_id=t.account_id)
                AND NOT EXISTS (SELECT 1 FROM pattern_entries p WHERE p.legacy_journal_entry_id=j.id)""",
            all_trade_ids,
        )
        reviewed_trade_ids = {r["trade_id"] for r in rows}

    # Position-level reviews propagate to their constituent trades
    reviewed_pos_ids = set()
    pos_review_rows = fetch_all(
        """SELECT DISTINCT j.position_id FROM journal_entries j JOIN positions pos ON pos.id=j.position_id
           WHERE j.stage='post' AND j.entry_type='trade' AND (j.account_id IS NULL OR EXISTS (SELECT 1 FROM trades t WHERE t.position_id=pos.id AND t.account_id=j.account_id))
           AND NOT EXISTS (SELECT 1 FROM pattern_entries p WHERE p.legacy_journal_entry_id=j.id)"""
    )
    reviewed_pos_ids = {r["position_id"] for r in pos_review_rows}

    # ── Build unified selector items ──────────────────────────────────────────
    # positions that have at least one trade in raw_trades
    trade_pos_ids = {t["position_id"] for t in raw_trades if t.get("position_id")}
    positions_in_scope = {}
    for pos_id in trade_pos_ids:
        pos_trades = [t for t in raw_trades if t.get("position_id") == pos_id]
        if not pos_trades:
            continue
        pos_row = fetch_all("SELECT * FROM positions WHERE id=?", (pos_id,))
        if pos_row:
            positions_in_scope[pos_id] = {"pos": pos_row[0], "trades": pos_trades}

    # Items: ("pos", position_id) or ("trade", trade_id)
    items = []
    seen_trade_ids = set()
    # Positions first, sorted by highest trade number they contain
    for pos_id, pd_ in sorted(
        positions_in_scope.items(),
        key=lambda kv: max(t["id"] for t in kv[1]["trades"]),
        reverse=True,
    ):
        items.append(("pos", pos_id))
        for t in pd_["trades"]:
            seen_trade_ids.add(t["id"])

    # Individual trades not part of any position
    for t in raw_trades:
        if t["id"] not in seen_trade_ids:
            items.append(("trade", t["id"]))

    # ── Filter ────────────────────────────────────────────────────────────────
    col1, col3 = st.columns([4, 1])
    with col3:
        filter_opt = st.selectbox("Show", ["All", "Not reviewed", "Reviewed"], key="tj_filter")

    def item_reviewed(item):
        kind, iid = item
        if kind == "pos":
            return iid in reviewed_pos_ids
        return iid in reviewed_trade_ids

    if filter_opt == "Not reviewed":
        items = [i for i in items if not item_reviewed(i)]
    elif filter_opt == "Reviewed":
        items = [i for i in items if item_reviewed(i)]

    if not items:
        st.caption("No items match the current filter.")
        return

    # Coverage summary
    n_positions = len(positions_in_scope)
    n_ind_trades = len([t for t in raw_trades if t["id"] not in seen_trade_ids])
    n_units = n_positions + n_ind_trades
    n_rev = sum(1 for pos_id in positions_in_scope if pos_id in reviewed_pos_ids)
    n_rev += sum(1 for t in raw_trades if t["id"] not in seen_trade_ids and t["id"] in reviewed_trade_ids)
    pct = n_rev / n_units * 100 if n_units else 0
    bar_col = p["--accent"] if pct >= 80 else (p["--warning"] if pct >= 50 else p["--danger"])
    st.markdown(
        f'<div style="margin:4px 0 10px;font-size:0.78rem;color:{p["--text-muted"]};">'
        f'Journal coverage: <b style="color:{bar_col};">{n_rev}/{n_units}</b> '
        f'units reviewed ({pct:.0f}%) '
        f'<span style="color:{p["--text-faint"]};">· {n_positions} position{"s" if n_positions!=1 else ""}, '
        f'{n_ind_trades} individual trade{"s" if n_ind_trades!=1 else ""}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Selector ──────────────────────────────────────────────────────────────
    def item_label(item):
        kind, iid = item
        rev = "✅" if item_reviewed(item) else "⚠️"
        if kind == "pos":
            pd_ = positions_in_scope[iid]
            pos = pd_["pos"]
            tr  = pd_["trades"]
            pnl = sum(float(t.get("pnl") or 0) for t in tr)
            dt  = min((t.get("entry_time") or "")[:10] for t in tr)
            return f"{rev} POS-{iid}  {pos['symbol']} {pos['direction']}  {dt}  ({len(tr)} trades)  {pnl:+.2f}"
        else:
            t = next((t for t in raw_trades if t["id"] == iid), None)
            if not t: return str(iid)
            if t.get("status") == "open":
                return f"{rev} 🔓 #{iid}  {t['symbol']} {t['direction']}  {(t.get('entry_time') or '')[:10]}  (open)"
            pnl = float(t.get("pnl") or 0)
            return f"{rev} #{iid}  {t['symbol']} {t['direction']}  {(t.get('entry_time') or '')[:10]}  {pnl:+.2f}"

    # Handle prefill from calendar drill-down (trade_id → find its item)
    prefill_trade_id = st.session_state.pop("trade_journal_selected", None)
    default_index = 0
    if prefill_trade_id:
        for idx, item in enumerate(items):
            kind, iid = item
            if kind == "trade" and iid == prefill_trade_id:
                default_index = idx
                break
            if kind == "pos":
                if any(t["id"] == prefill_trade_id for t in positions_in_scope[iid]["trades"]):
                    default_index = idx
                    break

    with col1:
        selected_item = st.selectbox(
            "Select position or trade", items,
            format_func=item_label,
            index=default_index,
            key="tj_item_sel",
        )

    kind, selected_id = selected_item
    if kind == 'pos':
        trade = max(positions_in_scope[selected_id]['trades'], key=lambda item: item['id'])
    else:
        trade = next(item for item in raw_trades if item['id'] == selected_id)
    st.caption('Plan, review, playbook, screenshots and management history are edited together in Trade Detail.')
    if st.button('Open plan & review', key='open_trade_review', type='primary'):
        open_trade_detail(trade['id'], trade['account_id'], position_scope=kind == 'pos')
        st.rerun()


def _playbook_expander(pb_id, rules_met_json=None):
    """Collapsible playbook details: description + rules, marking met rules when known."""
    if not pb_id:
        return
    pb = get_playbook(pb_id)
    if not pb:
        return
    met = {}
    if rules_met_json:
        try:
            met = {str(k): v for k, v in json.loads(rules_met_json).items()}
        except Exception:
            pass
    with st.expander(f"📖 Playbook: {pb['name']}"):
        if pb.get("description"):
            st.caption(pb["description"])
        for rule in pb.get("rules") or []:
            badge = {"required": "🔴", "optional": "🟡", "bonus": "🟢"}.get(rule["rule_type"], "⚪")
            grp   = f" ⛓ _{rule['rule_group']} (any one)_" if rule.get("rule_group") else ""
            check = (" ✅" if met.get(str(rule["id"])) else " ⬜") if met else ""
            st.markdown(f"{badge}{check} **{rule['name']}** — _{rule['rule_type']}_{grp}")
            if rule.get("description"):
                st.caption(rule["description"])


def _journal_position(pos_id, pd_, template, p):
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
    rules_met = next((t.get("playbook_rules_met") for t in trades if t.get("playbook_rules_met")), None)
    _playbook_expander(pb_id, rules_met)

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

    col_pre, col_post = st.columns(2)

    with col_pre:
        st.markdown("### 📋 Pre-Trade Plan")
        if pre_entry:
            st.markdown(
                f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border"]};'
                f'border-left:3px solid {p["--accent"]};border-radius:8px;padding:12px;font-size:0.85rem;">',
                unsafe_allow_html=True,
            )
            if pre_entry.get("pre_analysis"):    st.markdown(f"**Analysis:**\n{pre_entry['pre_analysis']}")
            if pre_entry.get("pre_plan"):         st.markdown(f"**Plan:**\n{pre_entry['pre_plan']}")
            if pre_entry.get("pre_accuracy_check") and "Not answered" not in pre_entry["pre_accuracy_check"]:
                st.markdown(f"**Accuracy check:** {pre_entry['pre_accuracy_check']}")
            if pre_entry.get("pre_risk_type") and "Not answered" not in pre_entry["pre_risk_type"]:
                st.markdown(f"**Risk type:** {pre_entry['pre_risk_type']}")
            if pre_entry.get("pre_body_state"):   st.markdown(f"**Body state:** {pre_entry['pre_body_state']}")
            if pre_entry.get("pre_psychology"):   st.markdown(f"**Mindset:**\n{pre_entry['pre_psychology']}")
            if pre_entry.get("pre_risk_notes"):   st.markdown(f"**Risk Notes:**\n{pre_entry['pre_risk_notes']}")
            if pre_entry.get("playbook_id"):
                pbs = fetch_all("SELECT name FROM playbooks WHERE id=?", (pre_entry["playbook_id"],))
                if pbs: st.markdown(f"📖 Playbook: **{pbs[0]['name']}**")
            st.markdown("</div>", unsafe_allow_html=True)
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

    with col_post:
        st.markdown("### 📝 Post-Trade Review")
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


def _journal_single_trade(trade_id, raw_trades, template, p):
    """Journal UI for an individual (unmerged) trade."""
    trade = next((t for t in raw_trades if t["id"] == trade_id), None)
    if not trade:
        return

    pnl = float(trade.get("pnl") or 0)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Symbol",    trade["symbol"])
    c2.metric("Direction", trade["direction"])
    c3.metric("Entry",     f"{trade.get('entry_price',0):.5f}" if trade.get("entry_price") else "—")
    c4.metric("Exit",      f"{trade.get('exit_price',0):.5f}"  if trade.get("exit_price")  else "—")
    c5.metric("P&L",       f"{pnl:+.2f}")

    entry_date = (trade.get("entry_time") or str(date.today()))[:10]
    pre_entry  = _get_entry("trade", entry_date, trade_id=trade_id, stage="pre")
    post_entry = _get_entry("trade", entry_date, trade_id=trade_id, stage="post")

    _playbook_expander(
        trade.get("playbook_id") or (pre_entry or {}).get("playbook_id"),
        trade.get("playbook_rules_met"),
    )

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

    col_pre, col_post = st.columns(2)

    with col_pre:
        st.markdown("### 📋 Pre-Trade Plan")
        if pre_entry:
            st.markdown(
                f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border"]};'
                f'border-left:3px solid {p["--accent"]};border-radius:8px;padding:12px;font-size:0.85rem;">',
                unsafe_allow_html=True,
            )
            if pre_entry.get("pre_analysis"):    st.markdown(f"**Analysis:**\n{pre_entry['pre_analysis']}")
            if pre_entry.get("pre_plan"):         st.markdown(f"**Plan:**\n{pre_entry['pre_plan']}")
            if pre_entry.get("pre_accuracy_check") and "Not answered" not in pre_entry["pre_accuracy_check"]:
                st.markdown(f"**Accuracy check:** {pre_entry['pre_accuracy_check']}")
            if pre_entry.get("pre_risk_type") and "Not answered" not in pre_entry["pre_risk_type"]:
                st.markdown(f"**Risk type:** {pre_entry['pre_risk_type']}")
            if pre_entry.get("pre_body_state"):   st.markdown(f"**Body state:** {pre_entry['pre_body_state']}")
            if pre_entry.get("pre_psychology"):   st.markdown(f"**Mindset:**\n{pre_entry['pre_psychology']}")
            if pre_entry.get("pre_risk_notes"):   st.markdown(f"**Risk Notes:**\n{pre_entry['pre_risk_notes']}")
            if pre_entry.get("playbook_id"):
                pbs = fetch_all("SELECT name FROM playbooks WHERE id=?", (pre_entry["playbook_id"],))
                if pbs: st.markdown(f"📖 Playbook: **{pbs[0]['name']}**")
            st.markdown("</div>", unsafe_allow_html=True)
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

    with col_post:
        st.markdown("### 📝 Post-Trade Review")
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
