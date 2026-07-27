"""
Page: Trade List — view, filter, link playbooks, merge into positions
"""
import streamlit as st
import pandas as pd
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all
from utils.trade_ops import (
    get_trades, get_trade, update_trade_playbook, delete_trade,
    merge_trades_into_position, get_positions, unmerge_position, set_position_playbook
)
from utils.playbook_logic import get_playbooks, get_playbook, evaluate_trade_risk


def show():
    st.header("📋 Trades")
    tab_trades, tab_positions = st.tabs(["Individual Trades", "Positions (Merged)"])
    with tab_trades:   _show_trades()
    with tab_positions: _show_positions()


def _show_trades():
    # ── Filters + refresh ────────────────────────────────────────────────────
    from utils.accounts import get_accounts
    accounts   = get_accounts()
    acc_opts   = {"All Accounts": None} | {a["name"]: a["id"] for a in accounts}

    col1, col2, col3, col4, col5 = st.columns([2, 2, 2, 2, 1])
    sel_acc       = col1.selectbox("Account",  list(acc_opts.keys()),   key="tr_acc")
    account_id    = acc_opts[sel_acc]

    acc_where  = f"AND account_id={account_id}" if account_id else ""
    symbols    = [r["symbol"] for r in fetch_all(
        f"SELECT DISTINCT symbol FROM trades WHERE 1=1 {acc_where} ORDER BY symbol"
    )]
    sym_filter    = col2.selectbox("Symbol",   ["All"] + symbols,       key="tr_sym")
    status_filter = col3.selectbox("Status",   ["All", "closed", "open"], key="tr_status")
    playbooks     = get_playbooks()
    pb_filter     = col4.selectbox("Playbook", ["All"] + [pb["name"] for pb in playbooks], key="tr_pb")
    col5.write("")
    col5.write("")
    if col5.button("🔄", use_container_width=True, help="Refresh"):
        st.rerun()

    # Build query with account filter
    where_parts = []
    params = []
    if account_id:
        where_parts.append("account_id=?")
        params.append(account_id)
    if status_filter != "All":
        where_parts.append("status=?")
        params.append(status_filter)
    if sym_filter != "All":
        where_parts.append("symbol=?")
        params.append(sym_filter)

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    trades = fetch_all(
        f"SELECT * FROM trades {where_clause} ORDER BY entry_time DESC LIMIT 500", params
    )

    if pb_filter != "All":
        pb_id  = next((pb["id"] for pb in playbooks if pb["name"] == pb_filter), None)
        trades = [t for t in trades if t.get("playbook_id") == pb_id]

    if not trades:
        st.info("No trades found. Use **Import Trades** to load your broker history, or add one manually.")
        return

    # ── Build clean dataframe ────────────────────────────────────────────────
    df = pd.DataFrame(trades)
    df["net_pnl"] = (
        pd.to_numeric(df["pnl"],        errors="coerce").fillna(0)
      - pd.to_numeric(df["commission"], errors="coerce").fillna(0).abs()
      - pd.to_numeric(df["swap"],       errors="coerce").fillna(0).abs()
    )
    df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
    df["exit_time"]  = pd.to_datetime(df["exit_time"],  errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
    df["net_pnl"]    = df["net_pnl"].round(2)
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce").round(5)
    df["exit_price"]  = pd.to_numeric(df["exit_price"],  errors="coerce").round(5)
    df["risk_score"]  = pd.to_numeric(df["risk_score"],  errors="coerce").round(1)

    show_cols = ["id", "symbol", "direction", "entry_price", "exit_price",
                 "entry_time", "exit_time", "quantity", "net_pnl", "risk_score", "status"]
    avail = [c for c in show_cols if c in df.columns]
    display_df = df[avail].copy()

    # Use column_config for coloured P&L — works in all Streamlit versions
    st.dataframe(
        display_df,
        use_container_width=True,
        height=380,
        column_config={
            "id":           st.column_config.NumberColumn("ID",    width="small"),
            "symbol":       st.column_config.TextColumn("Symbol",  width="small"),
            "direction":    st.column_config.TextColumn("Dir",     width="small"),
            "entry_price":  st.column_config.NumberColumn("Entry",  format="%.5f"),
            "exit_price":   st.column_config.NumberColumn("Exit",   format="%.5f"),
            "entry_time":   st.column_config.TextColumn("Opened"),
            "exit_time":    st.column_config.TextColumn("Closed"),
            "quantity":     st.column_config.NumberColumn("Qty",   format="%.2f", width="small"),
            "net_pnl":      st.column_config.NumberColumn("Net P&L", format="%.2f"),
            "risk_score":   st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
            "status":       st.column_config.TextColumn("Status",  width="small"),
        },
        hide_index=True,
    )

    # Summary row
    total_pnl = df["net_pnl"].sum()
    wins       = (df["net_pnl"] > 0).sum()
    wr         = wins / len(df) * 100 if len(df) else 0
    pnl_col    = "#00c896" if total_pnl >= 0 else "#ff4b6e"
    st.markdown(
        f"<div style='font-size:0.8rem;color:var(--text-muted,#6b7a99);padding:4px 0;'>"
        f"Showing <b>{len(trades)}</b> trades &nbsp;·&nbsp; "
        f"Win rate: <b>{wr:.1f}%</b> &nbsp;·&nbsp; "
        f"Net P&L: <b style='color:{pnl_col};'>{total_pnl:+,.2f}</b>"
        f"</div>",
        unsafe_allow_html=True
    )

    # ── Actions ──────────────────────────────────────────────────────────────
    st.divider()
    col1, col2 = st.columns(2)

    with col1:
        with st.expander("🔗 Link Playbook to Trade"):
            if not playbooks:
                st.warning("No playbooks yet — create one in the Playbook page first.")
            else:
                sel_trade_id = st.selectbox(
                    "Trade",
                    [t["id"] for t in trades],
                    format_func=lambda x: next(
                        (f"#{x} {t['symbol']} {t['direction']} {(t.get('entry_time') or '')[:10]}"
                         for t in trades if t["id"] == x), str(x)
                    ),
                    key="link_trade_sel"
                )
                pb_names = {pb["name"]: pb["id"] for pb in playbooks}
                sel_pb_name = st.selectbox("Playbook", list(pb_names.keys()), key="link_pb_sel")
                pb_id = pb_names[sel_pb_name]
                pb    = get_playbook(pb_id)

                if pb and pb.get("rules"):
                    trade_obj     = get_trade(sel_trade_id)
                    existing_rules = {}
                    if trade_obj and trade_obj.get("playbook_rules_met"):
                        try:
                            existing_rules = json.loads(trade_obj["playbook_rules_met"])
                        except:
                            pass

                    st.markdown("**Rules met on this trade:**")
                    rules_met = {}
                    for rule in pb["rules"]:
                        badge   = {"required": "🔴", "optional": "🟡", "bonus": "🟢"}.get(rule["rule_type"], "⚪")
                        default = bool(existing_rules.get(str(rule["id"]), existing_rules.get(rule["id"], False)))
                        rules_met[rule["id"]] = st.checkbox(
                            f"{badge} {rule['name']}",
                            value=default,
                            key=f"link_rule_{rule['id']}_{sel_trade_id}"
                        )

                    if st.button("💾 Save", type="primary", key="save_link"):
                        assessment = evaluate_trade_risk(pb_id, rules_met)
                        update_trade_playbook(sel_trade_id, pb_id, rules_met, assessment.get("risk_score", 0))
                        st.success(f"Saved! Score: {assessment.get('risk_score')}/100")
                        for w in assessment.get("warnings", []):
                            st.warning(w)
                        st.rerun()

    with col2:
        with st.expander("🔀 Merge into Position"):
            st.markdown("Select trades on the same symbol & direction:")
            sel_merge = st.multiselect(
                "Trades to merge",
                [t["id"] for t in trades],
                format_func=lambda x: next(
                    (f"#{x} {t['symbol']} {t['direction']} @ {t.get('entry_price','')}"
                     for t in trades if t["id"] == x), str(x)
                )
            )
            # Playbook assignment at merge time
            pb_all = get_playbooks()
            pb_opts = {pb["name"]: pb["id"] for pb in pb_all}
            merge_pb_name = st.selectbox(
                "Assign playbook (optional)",
                ["— None —"] + list(pb_opts.keys()),
                key="merge_playbook_sel",
            )
            merge_pb_id = pb_opts.get(merge_pb_name) if merge_pb_name != "— None —" else None
            merge_notes = st.text_area("Notes", height=60, key="merge_notes")
            if st.button("Merge", type="primary", disabled=len(sel_merge) < 2):
                try:
                    pos_id = merge_trades_into_position(sel_merge, merge_notes, merge_pb_id)
                    pb_msg = f" with playbook **{merge_pb_name}**" if merge_pb_id else ""
                    st.success(f"Position #{pos_id} created{pb_msg}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    with st.expander("🏦 Assign Account"):
        from utils.accounts import get_accounts
        from database import execute as _execute
        accounts = get_accounts()
        if accounts:
            acc_map = {f"{a['name']}  ({a['broker']})": a["id"] for a in accounts}
            aa_col1, aa_col2 = st.columns([3, 2])
            with aa_col1:
                aa_trades = st.multiselect(
                    "Trades",
                    [t["id"] for t in trades],
                    format_func=lambda x: next(
                        (f"#{x} {t['symbol']} {(t.get('entry_time') or '')[:10]}"
                         + (f"  [{next((a['name'] for a in accounts if a['id']==t.get('account_id')), '?')}]"
                            if t.get("account_id") else "  [no account]")
                         for t in trades if t["id"] == x), str(x)),
                    key="assign_acc_trades",
                )
            with aa_col2:
                aa_target = st.selectbox("Assign to", list(acc_map.keys()), key="assign_acc_target")
            if st.button("Assign", type="primary", key="assign_acc_btn",
                         disabled=not aa_trades):
                for tid in aa_trades:
                    _execute("UPDATE trades SET account_id=?, updated_at=datetime('now') WHERE id=?",
                             (acc_map[aa_target], tid))
                st.success(f"Assigned {len(aa_trades)} trade(s) to {aa_target}")
                st.rerun()
        else:
            st.caption("No accounts yet — create one in Settings.")

    with st.expander("🗑️ Delete Trade"):
        del_id = st.number_input("Trade ID", min_value=1, step=1, key="del_trade_id")
        if st.button("Delete", type="primary", key="del_trade_btn"):
            delete_trade(int(del_id))
            st.success(f"Trade #{del_id} deleted.")
            st.rerun()


def _show_positions():
    positions = get_positions()
    if not positions:
        st.info("No positions yet. Merge trades from the Trades tab.")
        return

    pb_all = get_playbooks()
    pb_by_id   = {pb["id"]: pb["name"] for pb in pb_all}
    pb_name_id = {pb["name"]: pb["id"] for pb in pb_all}
    pb_select_opts = ["— None —"] + [pb["name"] for pb in pb_all]

    for pos in positions:
        pnl   = pos.get("total_pnl", 0) or 0
        icon  = "🟢" if pnl >= 0 else "🔴"
        cur_pb_name = pb_by_id.get(pos.get("playbook_id"), "")
        pb_label = f" · 📖 {cur_pb_name}" if cur_pb_name else ""
        with st.expander(f"{icon} Position #{pos['id']} — {pos['symbol']} {pos['direction']} | P&L: {pnl:+.2f}{pb_label}"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Avg Entry",  f"{pos.get('avg_entry_price',0):.5f}" if pos.get("avg_entry_price") else "—")
            c2.metric("Avg Exit",   f"{pos.get('avg_exit_price',0):.5f}"  if pos.get("avg_exit_price")  else "—")
            c3.metric("Total Qty",  f"{pos.get('total_quantity',0):.2f}")
            c4.metric("Net P&L",    f"{pnl:+.2f}")

            sub_trades = fetch_all("SELECT * FROM trades WHERE position_id=?", (pos["id"],))
            if sub_trades:
                st.caption(f"**{len(sub_trades)} trades in this position:**")
                sub_df = pd.DataFrame(sub_trades)
                show_cols = ["id", "symbol", "entry_price", "exit_price", "entry_time", "exit_time", "quantity", "pnl", "playbook_id"]
                disp = sub_df[[c for c in show_cols if c in sub_df.columns]].copy()
                if "playbook_id" in disp.columns:
                    disp["playbook"] = disp["playbook_id"].map(pb_by_id).fillna("—")
                    disp = disp.drop(columns=["playbook_id"])
                st.dataframe(disp, use_container_width=True, hide_index=True)

            if pos.get("notes"):
                st.markdown(f"**Notes:** {pos['notes']}")

            # Playbook assignment / change
            st.markdown("**Assign Playbook**")
            cur_idx = pb_select_opts.index(cur_pb_name) if cur_pb_name in pb_select_opts else 0
            new_pb_name = st.selectbox(
                "Playbook",
                pb_select_opts,
                index=cur_idx,
                key=f"pos_pb_{pos['id']}",
                label_visibility="collapsed",
            )
            new_pb_id = pb_name_id.get(new_pb_name) if new_pb_name != "— None —" else None
            if st.button("Apply to position + all trades", key=f"pos_pb_apply_{pos['id']}"):
                set_position_playbook(pos["id"], new_pb_id)
                st.success(
                    f"Playbook **{new_pb_name}** assigned to position #{pos['id']} and {len(sub_trades)} trade(s)."
                    if new_pb_id else
                    f"Playbook cleared from position #{pos['id']} and {len(sub_trades)} trade(s)."
                )
                st.rerun()

            if st.button(f"Unmerge #{pos['id']}", key=f"unmerge_{pos['id']}"):
                unmerge_position(pos["id"])
                st.rerun()
