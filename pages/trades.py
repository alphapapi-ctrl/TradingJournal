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


def _js_switch_tab(index: int):
    import streamlit.components.v1 as _c
    _c.html(
        f"""<script>
        setTimeout(function(){{
            var tabs = window.parent.document.querySelectorAll('button[data-baseweb="tab"]');
            if (tabs[{index}]) tabs[{index}].click();
        }}, 120);
        </script>""",
        height=0, scrolling=False,
    )


def show():
    st.header("📋 Trades")
    tab_calc, tab_add, tab_list, tab_positions, tab_detail = st.tabs([
        "⚖️ Risk Calculator", "➕ Add Trade", "🔗 Link Trade", "🧩 Positions", "🔍 Trade Detail"
    ])

    # Tab indices: 0=Risk Calc, 1=Add Trade, 2=Link Trade, 3=Positions, 4=Trade Detail
    if "_trades_pending_tab" in st.session_state:
        _js_switch_tab(st.session_state.pop("_trades_pending_tab"))

    with tab_calc:
        from pages.risk_calculator import show as rc_show
        rc_show(embedded=True)
    with tab_add:
        from pages.import_trades import show as it_show
        it_show(embedded=True)
    with tab_list:      _show_trades()
    with tab_positions: _show_positions()
    with tab_detail:
        from pages.trade_detail import show as td_show
        td_show(embedded=True)


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
        f"SELECT * FROM trades {where_clause} ORDER BY id DESC LIMIT 500", params
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


@st.cache_data(ttl=600, show_spinner=False)
def _quote_stats(symbol: str, exchange_hint: str):
    """Live quote + day/week change for a symbol. Returns dict or None."""
    import yfinance as yf
    sym = symbol.strip().upper()
    if "." in sym:
        candidates = [sym]
    elif exchange_hint.upper() in ("ASX", "AU"):
        candidates = [f"{sym}.AX", sym]
    else:
        candidates = [sym, f"{sym}.AX"]
    for cand in candidates:
        try:
            h = yf.Ticker(cand).history(period="15d")
            if h is None or h.empty:
                continue
            close = h["Close"]
            last = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) >= 2 else last
            week = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
            return {"sym": cand, "last": last,
                    "day_chg": last - prev, "day_pct": (last / prev - 1) * 100 if prev else 0,
                    "week_chg": last - week, "week_pct": (last / week - 1) * 100 if week else 0}
        except Exception:
            continue
    return None


def _exchange_hint(raw_data) -> str:
    try:
        d = json.loads(raw_data) if raw_data else None
        if isinstance(d, dict):
            return d.get("exchange", "") or ""
    except Exception:
        pass
    return ""


def _position_live_metrics(symbol, hint, open_qty, avg_entry, direction,
                           realized_pnl, risk_base, is_open):
    """Render Last / Day / Week / Current P&L / R metric row for a position."""
    sign = 1 if direction == "LONG" else -1
    q = _quote_stats(symbol, hint) if is_open else None

    current_pnl = realized_pnl
    if q and is_open and open_qty:
        current_pnl = realized_pnl + (q["last"] - avg_entry) * open_qty * sign

    r_val = current_pnl / risk_base if risk_base else None

    m1, m2, m3, m4, m5 = st.columns(5)
    if q:
        m1.metric("Last", f"{q['last']:,.4f}")
        m2.metric("Day", f"{q['day_pct']:+.2f}%",
                  delta=f"{q['day_chg'] * open_qty * sign:+,.2f}" if open_qty else None)
        m3.metric("Week", f"{q['week_pct']:+.2f}%",
                  delta=f"{q['week_chg'] * open_qty * sign:+,.2f}" if open_qty else None)
    else:
        m1.metric("Last", "—")
        m2.metric("Day", "—")
        m3.metric("Week", "—")
    m4.metric("Current P&L", f"{current_pnl:+,.2f}",
              help="Realized + unrealized at last price" if is_open else "Realized")
    m5.metric("R", f"{r_val:+.2f}R" if r_val is not None else "—",
              help="P&L ÷ (account balance × default risk %) — your standard 1R")


def _show_positions():
    from utils.accounts import get_accounts
    accounts = get_accounts()
    acc_opts = {"All Accounts": None} | {a["name"]: a["id"] for a in accounts}
    fc1, _ = st.columns([2, 4])
    sel_acc = fc1.selectbox("Account", list(acc_opts.keys()), key="pos_acc")
    account_id = acc_opts[sel_acc]

    positions = get_positions()
    # Filter merged positions by account via their constituent trades
    if account_id and positions:
        pos_ids_in_acc = {r["position_id"] for r in fetch_all(
            "SELECT DISTINCT position_id FROM trades WHERE account_id=? AND position_id IS NOT NULL",
            (account_id,))}
        positions = [p for p in positions if p["id"] in pos_ids_in_acc]

    pb_all = get_playbooks()
    pb_by_id   = {pb["id"]: pb["name"] for pb in pb_all}
    pb_name_id = {pb["name"]: pb["id"] for pb in pb_all}
    pb_select_opts = ["— None —"] + [pb["name"] for pb in pb_all]

    # 1R base = account balance × default risk %
    settings = {r["key"]: r["value"] for r in fetch_all("SELECT key, value FROM app_settings")}
    risk_pct = float(settings.get("risk_pct", 1.0))
    acc_bal  = {a["id"]: float(a.get("initial_balance") or 0) for a in accounts}

    if positions:
        st.markdown(f"**Merged positions ({len(positions)})**")
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

            # Live metrics: last price, day/week gain, current P&L, R
            if sub_trades:
                is_open  = any(s["status"] == "open" for s in sub_trades)
                open_qty = sum(float(s.get("quantity") or 0) for s in sub_trades
                               if s["status"] == "open")
                hint     = _exchange_hint(sub_trades[0].get("raw_data"))
                acct_id  = sub_trades[0].get("account_id")
                risk_base = (acc_bal.get(acct_id) or 0) * risk_pct / 100
                _position_live_metrics(
                    pos["symbol"], hint, open_qty,
                    float(pos.get("avg_entry_price") or 0), pos["direction"],
                    float(pos.get("total_pnl") or 0), risk_base, is_open,
                )
                st.divider()

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

    # ── Single-entry trades (not part of a merged position) ──────────────────
    single_where = "position_id IS NULL"
    single_params = []
    if account_id:
        single_where += " AND account_id=?"
        single_params.append(account_id)
    singles = fetch_all(
        f"SELECT * FROM trades WHERE {single_where} ORDER BY entry_time DESC LIMIT 100",
        single_params,
    )

    if not positions and not singles:
        st.info("No positions or trades for this account yet.")
        return

    if singles:
        st.markdown(f"**Single-entry positions ({len(singles)})**")
    for t in singles:
        pnl  = float(t.get("pnl") or 0)
        icon = "🔓" if t["status"] == "open" else ("🟢" if pnl >= 0 else "🔴")
        pnl_txt = "open" if t["status"] == "open" else f"P&L: {pnl:+.2f}"
        pb_name = pb_by_id.get(t.get("playbook_id"), "")
        pb_label = f" · 📖 {pb_name}" if pb_name else ""
        with st.expander(f"{icon} Trade #{t['id']} — {t['symbol']} {t['direction']} | {pnl_txt}{pb_label}"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Entry", f"{float(t.get('entry_price') or 0):.4f}" if t.get("entry_price") else "—")
            c2.metric("Exit",  f"{float(t.get('exit_price') or 0):.4f}"  if t.get("exit_price")  else "Open")
            c3.metric("Qty",   f"{float(t.get('quantity') or 0):g}")
            c4.metric("Net P&L", f"{pnl - abs(float(t.get('commission') or 0)):+.2f}"
                                 if t["status"] == "closed" else "—")

            is_open = t["status"] == "open"
            net_realized = pnl - abs(float(t.get("commission") or 0))
            risk_base = (acc_bal.get(t.get("account_id")) or 0) * risk_pct / 100
            _position_live_metrics(
                t["symbol"], _exchange_hint(t.get("raw_data")),
                float(t.get("quantity") or 0) if is_open else 0,
                float(t.get("entry_price") or 0), t["direction"],
                net_realized if not is_open else 0.0, risk_base, is_open,
            )
            st.caption(f"{(t.get('entry_time') or '—')[:10]} → {(t.get('exit_time') or 'open')[:10]}"
                       f" · {t.get('broker', '')}")

            sc1, sc2 = st.columns(2)
            # Playbook assignment for the single trade
            cur_name = pb_name if pb_name in pb_select_opts else "— None —"
            new_name = sc1.selectbox("Playbook", pb_select_opts,
                                     index=pb_select_opts.index(cur_name),
                                     key=f"single_pb_{t['id']}", label_visibility="collapsed")
            if sc1.button("Apply playbook", key=f"single_pb_apply_{t['id']}"):
                from database import execute as _execute
                _execute("UPDATE trades SET playbook_id=?, updated_at=datetime('now') WHERE id=?",
                         (pb_name_id.get(new_name), t["id"]))
                st.rerun()
            if sc2.button("🔍 Open in Trade Detail", key=f"single_detail_{t['id']}"):
                st.session_state["detail_trade_id"] = t["id"]
                st.session_state["_trades_pending_tab"] = 4  # Trade Detail tab
                st.rerun()
