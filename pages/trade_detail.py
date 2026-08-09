"""
Page: Trade Detail — full drill-down on a single trade with journal, playbook, and analytics
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all, fetch_one
from utils.trade_ops import (
    get_trade, get_trades, update_trade_playbook, delete_trade,
    get_journal_entry, save_journal_entry, get_templates,
    close_trade, partial_close_trade, reopen_trade
)
from utils.playbook_logic import get_playbooks, get_playbook, evaluate_trade_risk


def show(embedded=False):
    if not embedded:
        st.header("🔍 Trade Detail")

    from utils.accounts import get_accounts
    accounts = get_accounts()
    acc_options = {"All Accounts": None} | {a["name"]: a["id"] for a in accounts}

    col_acc, col1, col2 = st.columns([1, 3, 1])
    with col_acc:
        sel_acc = st.selectbox("Account", list(acc_options.keys()), key="td_account")
        account_id = acc_options[sel_acc]

    if account_id:
        trades = fetch_all(
            "SELECT * FROM trades WHERE account_id=? ORDER BY id DESC LIMIT 500",
            (account_id,),
        )
    else:
        trades = get_trades(limit=500)
    trades = sorted(trades, key=lambda t: t["id"], reverse=True)
    if not trades:
        st.info("No trades found for this account.")
        return

    with col1:
        def trade_label(x):
            t = next((t for t in trades if t["id"] == x), None)
            if not t:
                return str(x)
            pnl = float(t.get("pnl") or 0)
            sign = "+" if pnl >= 0 else ""
            return f"#{x}  {t['symbol']} {t['direction']}  @{t.get('entry_price','')}  P&L: {sign}{pnl:.2f}  [{(t.get('entry_time') or '')[:10]}]"

        selected_id = st.selectbox(
            "Select trade",
            options=[t["id"] for t in trades],
            format_func=trade_label,
        )
    with col2:
        st.write("")
        st.write("")
        if st.button("⬅ Previous") and selected_id:
            ids = [t["id"] for t in trades]
            idx = ids.index(selected_id)
            if idx + 1 < len(ids):
                st.session_state["detail_trade_id"] = ids[idx + 1]
                st.rerun()
        
    if "detail_trade_id" in st.session_state:
        selected_id = st.session_state["detail_trade_id"]

    trade = get_trade(selected_id)
    if not trade:
        st.error("Trade not found.")
        return

    _trade_header(trade)
    st.divider()

    tab_overview, tab_manage, tab_playbook, tab_journal, tab_related = st.tabs([
        "📊 Overview", "🛠️ Manage", "📖 Playbook", "📔 Journal", "🔗 Related"
    ])

    with tab_overview:
        _trade_overview(trade)

    with tab_manage:
        _trade_manage(trade)

    with tab_playbook:
        _trade_playbook(trade)

    with tab_journal:
        _trade_journal_tab(trade)

    with tab_related:
        _trade_related(trade)


# ── Header ────────────────────────────────────────────────────────────────────

def _trade_header(trade):
    pnl = float(trade.get("pnl") or 0)
    comm = float(trade.get("commission") or 0)
    swap = float(trade.get("swap") or 0)
    net = pnl - abs(comm) - abs(swap)

    pnl_color = "#00c896" if net >= 0 else "#ff4b6e"
    dir_color = "#00c896" if trade["direction"] == "LONG" else "#ff4b6e"
    status_color = "#f5a623" if trade["status"] == "open" else "#6b7a99"

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#131720,#0f1520);border:1px solid #1e2533;
                border-left:4px solid {pnl_color};border-radius:12px;padding:20px 24px;
                display:flex;align-items:center;justify-content:space-between;">
        <div>
            <div style="display:flex;align-items:center;gap:12px;">
                <span style="font-size:1.6rem;font-weight:800;color:#e8eaf0;">{trade['symbol']}</span>
                <span style="background:{dir_color}22;color:{dir_color};padding:3px 10px;
                             border-radius:20px;font-size:0.8rem;font-weight:600;border:1px solid {dir_color}55;">
                    {trade['direction']}
                </span>
                <span style="background:{status_color}22;color:{status_color};padding:3px 10px;
                             border-radius:20px;font-size:0.75rem;border:1px solid {status_color}44;">
                    {trade['status'].upper()}
                </span>
            </div>
            <div style="font-size:0.8rem;color:#6b7a99;margin-top:6px;">
                Trade #{trade['id']} · {trade.get('broker','Manual')} · 
                {(trade.get('entry_time') or '')[:16]}
            </div>
        </div>
        <div style="text-align:right;">
            <div style="font-size:2rem;font-weight:800;font-family:'JetBrains Mono';color:{pnl_color};">
                {'+' if net >= 0 else ''}{net:,.2f}
            </div>
            <div style="font-size:0.75rem;color:#6b7a99;">Net P&L</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Overview ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _price_history_for(symbol: str, exchange_hint: str, start: str, end: str,
                       interval: str = "1d"):
    """Resolve symbol on yfinance and fetch OHLCV between dates."""
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
            h = yf.Ticker(cand).history(start=start, end=end, interval=interval)
            if h is not None and not h.empty:
                return cand, h
        except Exception:
            continue
    return None, None


def _trade_overview(trade):
    pnl  = float(trade.get("pnl") or 0)
    comm = float(trade.get("commission") or 0)
    swap = float(trade.get("swap") or 0)
    net  = pnl - abs(comm) - abs(swap)
    qty  = float(trade.get("quantity") or 0)
    entry = float(trade.get("entry_price") or 0)
    exit_ = float(trade.get("exit_price") or 0)

    # Price movement
    if entry and exit_:
        move = exit_ - entry
        move_pct = (move / entry * 100) if entry else 0
        if trade["direction"] == "SHORT":
            move = -move
            move_pct = -move_pct
    else:
        move = move_pct = 0

    # Duration
    try:
        entry_dt = pd.to_datetime(trade.get("entry_time"))
        exit_dt  = pd.to_datetime(trade.get("exit_time"))
        if entry_dt is not None and exit_dt is not None and not pd.isna(exit_dt):
            dur = exit_dt - entry_dt
            dur_str = (f"{dur.days}d {dur.seconds//3600}h {(dur.seconds%3600)//60}m"
                       if dur.days > 0 else f"{dur.seconds//3600}h {(dur.seconds%3600)//60}m")
        else:
            dur_str = "Open"
    except Exception:
        dur_str = "—"

    # ── Info table ───────────────────────────────────────────────────────────
    rows = [
        ("Entry Price",  f"{entry:.4f}" if entry else "—"),
        ("Exit Price",   f"{exit_:.4f}" if exit_ else "Open"),
        ("Quantity",     f"{qty:g}"),
        ("Position Value", f"{entry * qty:,.2f}" if entry else "—"),
        ("Price Move",   f"{move_pct:+.2f}%  ({move:+.4f})" if (entry and exit_) else "—"),
        ("Gross P&L",    f"{pnl:+,.2f}"),
        ("Commission",   f"{comm:,.2f}"),
        ("Swap",         f"{swap:,.2f}"),
        ("Net P&L",      f"{net:+,.2f}"),
        ("Entry Time",   (trade.get("entry_time") or "—")[:16]),
        ("Exit Time",    (trade.get("exit_time") or "Open")[:16]),
        ("Duration",     dur_str),
    ]
    info_df = pd.DataFrame(rows, columns=["Metric", "Value"])
    st.dataframe(info_df, use_container_width=True, hide_index=True,
                 height=38 * len(rows) + 40)

    # Risk score
    if trade.get("risk_score") is not None:
        st.divider()
        rs = float(trade["risk_score"])
        rs_color = "#00c896" if rs >= 70 else ("#f5a623" if rs >= 50 else "#ff4b6e")
        col1, col2 = st.columns([1, 3])
        col1.metric("Risk / Quality Score", f"{rs:.1f} / 100")
        with col2:
            st.markdown(f"""
            <div style="margin-top:8px;">
                <div style="background:#1a2030;border-radius:6px;height:12px;overflow:hidden;">
                    <div style="width:{rs}%;background:{rs_color};height:100%;
                                border-radius:6px;transition:width 0.5s;"></div>
                </div>
            </div>""", unsafe_allow_html=True)

    # ── Real price chart with entry/exit marked ──────────────────────────────
    st.divider()
    _real_price_chart(trade, entry, exit_)


def _real_price_chart(trade, entry, exit_):
    """Daily candlestick chart around the trade with entry (and exit) marked."""
    entry_time = trade.get("entry_time")
    if not entry_time or not entry:
        st.caption("No entry price/time recorded — cannot chart.")
        return

    try:
        entry_dt = pd.to_datetime(entry_time[:10])
    except Exception:
        st.caption("Unparseable entry time — cannot chart.")
        return
    exit_dt = None
    if trade.get("exit_time"):
        try:
            exit_dt = pd.to_datetime(trade["exit_time"][:10])
        except Exception:
            pass

    # Timeframe toggle
    tf = st.radio("Timeframe", ["Daily", "Weekly"], horizontal=True,
                  key=f"chart_tf_{trade['id']}", label_visibility="collapsed")
    interval = "1d" if tf == "Daily" else "1wk"

    # Display window: 6 months before entry → now/exit.
    # Fetch much further back so the 200 EMA is warmed up at the left edge.
    display_start = entry_dt - pd.Timedelta(days=182)
    fetch_back_days = 550 if interval == "1d" else 365 * 5
    start = (entry_dt - pd.Timedelta(days=fetch_back_days)).strftime("%Y-%m-%d")
    end_dt = (exit_dt or pd.Timestamp.today()) + pd.Timedelta(days=15)
    end = min(end_dt, pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    # Exchange hint from import raw_data
    hint = ""
    try:
        d = json.loads(trade.get("raw_data") or "")
        if isinstance(d, dict):
            hint = d.get("exchange", "") or ""
    except Exception:
        pass

    with st.spinner("Loading price history…"):
        yf_sym, hist = _price_history_for(trade["symbol"], hint, start, end, interval)

    if hist is None:
        st.caption(f"No market data found for {trade['symbol']} — chart unavailable "
                   f"(non-listed symbol or unsupported instrument).")
        return

    # EMAs computed on the full fetch so they're accurate in the display window
    hist = hist.copy()
    hist["EMA20"]  = hist["Close"].ewm(span=20,  adjust=False).mean()
    hist["EMA50"]  = hist["Close"].ewm(span=50,  adjust=False).mean()
    hist["EMA200"] = hist["Close"].ewm(span=200, adjust=False).mean()

    # Slice to the 6-month display window
    ds = display_start
    if hist.index.tz is not None:
        ds = ds.tz_localize(hist.index.tz) if ds.tzinfo is None else ds
    hist = hist[hist.index >= ds]
    if hist.empty:
        st.caption("Not enough history in the display window.")
        return

    st.markdown(f"**{yf_sym} — {tf.lower()}** (entry marked · EMA 20/50/200)")
    line_color = "#00c896" if trade["direction"] == "LONG" else "#ff4b6e"

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=hist.index, open=hist["Open"], high=hist["High"],
        low=hist["Low"], close=hist["Close"], name="Price",
        increasing_line_color="#00c896", decreasing_line_color="#ff4b6e",
    ))
    fig.add_trace(go.Scatter(x=hist.index, y=hist["EMA20"],  name="EMA 20",
                             line=dict(color="#f5a623", width=1.2)))
    fig.add_trace(go.Scatter(x=hist.index, y=hist["EMA50"],  name="EMA 50",
                             line=dict(color="#4a9eff", width=1.2)))
    fig.add_trace(go.Scatter(x=hist.index, y=hist["EMA200"], name="EMA 200",
                             line=dict(color="#b06aff", width=1.4)))

    # Entry marker + level
    fig.add_hline(y=entry, line=dict(color="#00c896", dash="dot", width=1), opacity=0.5)
    fig.add_trace(go.Scatter(
        x=[entry_dt], y=[entry], mode="markers+text",
        marker=dict(symbol="triangle-up" if trade["direction"] == "LONG" else "triangle-down",
                    size=14, color="#00c896", line=dict(color="#ffffff", width=1)),
        text=["Entry"], textposition="bottom center",
        textfont=dict(size=11, color="#00c896"),
        showlegend=False, hovertemplate=f"Entry {entry:.4f}<extra></extra>",
    ))

    # Exit marker + level
    if exit_ and exit_dt is not None:
        fig.add_hline(y=exit_, line=dict(color=line_color, dash="dot", width=1), opacity=0.5)
        fig.add_trace(go.Scatter(
            x=[exit_dt], y=[exit_], mode="markers+text",
            marker=dict(symbol="x", size=12, color=line_color,
                        line=dict(color="#ffffff", width=1)),
            text=["Exit"], textposition="top center",
            textfont=dict(size=11, color=line_color),
            showlegend=False, hovertemplate=f"Exit {exit_:.4f}<extra></extra>",
        ))

    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor="#1a2030", rangeslider_visible=False,
                   rangebreaks=[dict(bounds=["sat", "mon"])] if interval == "1d" else None),
        yaxis=dict(gridcolor="#1a2030", title="Price"),
        font=dict(color="#6b7a99"),
        margin=dict(l=8, r=8, t=8, b=8),
        height=420, showlegend=True,
        legend=dict(orientation="h", y=1.04),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Manage Tab (close / partial close / reopen) ───────────────────────────────

def _trade_manage(trade):
    from datetime import datetime as _dt
    qty = float(trade.get("quantity") or 0)
    ep  = float(trade.get("entry_price") or 0)
    sign = 1 if trade["direction"] == "LONG" else -1

    # ── Edit trade fields ────────────────────────────────────────────────────
    with st.expander("✏️ Edit Trade"):
        from database import execute as _execute
        with st.form(f"edit_trade_{trade['id']}"):
            e1, e2, e3 = st.columns(3)
            new_symbol = e1.text_input("Symbol", value=trade.get("symbol") or "")
            new_dir    = e2.selectbox("Direction", ["LONG", "SHORT"],
                                      index=0 if trade["direction"] == "LONG" else 1)
            new_qty    = e3.number_input("Quantity / Shares", min_value=0.0, step=1.0,
                                         value=float(qty), format="%.4f")

            e4, e5 = st.columns(2)
            new_entry_price = e4.number_input("Entry Price", min_value=0.0, step=0.01,
                                              value=float(ep), format="%.4f")
            new_exit_price  = e5.number_input("Exit Price (0 = none)", min_value=0.0, step=0.01,
                                              value=float(trade.get("exit_price") or 0), format="%.4f")

            e6, e7 = st.columns(2)
            new_entry_time = e6.text_input("Entry Time", value=trade.get("entry_time") or "")
            new_exit_time  = e7.text_input("Exit Time",  value=trade.get("exit_time") or "")

            e8, e9, e10 = st.columns(3)
            new_comm = e8.number_input("Commission", step=0.01,
                                       value=float(trade.get("commission") or 0), format="%.2f")
            new_pnl  = e9.number_input("P&L", step=0.01,
                                       value=float(trade.get("pnl") or 0), format="%.2f")
            recalc   = e10.checkbox("Recalc P&L from prices",
                                    help="Overrides the P&L field with (exit − entry) × qty when an exit price is set")

            if st.form_submit_button("💾 Save Changes", type="primary"):
                pnl_save = new_pnl
                if recalc and new_exit_price > 0:
                    s = 1 if new_dir == "LONG" else -1
                    pnl_save = (new_exit_price - new_entry_price) * new_qty * s
                _execute(
                    """UPDATE trades SET symbol=?, direction=?, quantity=?, entry_price=?,
                       exit_price=?, entry_time=?, exit_time=?, commission=?, pnl=?,
                       updated_at=datetime('now') WHERE id=?""",
                    (new_symbol.strip().upper(), new_dir, new_qty, new_entry_price,
                     new_exit_price if new_exit_price > 0 else None,
                     new_entry_time.strip() or None, new_exit_time.strip() or None,
                     new_comm, round(pnl_save, 4), trade["id"]),
                )
                if trade.get("position_id"):
                    from utils.trade_ops import recompute_position
                    recompute_position(trade["position_id"])
                st.success("Trade updated." + (" Position totals recomputed." if trade.get("position_id") else ""))
                st.rerun()

    if trade["status"] == "open":
        st.markdown(f"**Open position:** {qty:g} @ {ep:.4f} {trade['direction']}")

        col_partial, col_full = st.columns(2)

        # ── Partial close ────────────────────────────────────────────────────
        with col_partial:
            st.markdown("##### ✂️ Partial Close (take profits)")
            pc_qty = st.number_input("Quantity to close", min_value=0.0,
                                     max_value=float(qty), step=1.0,
                                     value=float(round(qty / 2)), key=f"pc_qty_f_{trade['id']}")
            pc_price = st.number_input("Exit price", min_value=0.0, step=0.01,
                                       value=ep, format="%.4f", key=f"pc_price_{trade['id']}")
            pc_time = st.text_input("Exit time", value=_dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    key=f"pc_time_{trade['id']}")
            pc_comm = st.number_input("Exit commission ($)", min_value=0.0, step=0.5,
                                      value=0.0, key=f"pc_comm_{trade['id']}")
            if pc_qty > 0 and pc_price > 0:
                est = (pc_price - ep) * pc_qty * sign
                est_col = "#00c896" if est >= 0 else "#ff4b6e"
                st.markdown(f"Est. P&L: <b style='color:{est_col};font-family:JetBrains Mono;'>{est:+,.2f}</b> "
                            f"<span style='color:#6b7a99;font-size:0.8rem;'>· {qty - pc_qty:g} remain open</span>",
                            unsafe_allow_html=True)
            if st.button("✂️ Close Partial", type="primary", key=f"pc_btn_{trade['id']}",
                         disabled=not (0 < pc_qty < qty and pc_price > 0)):
                try:
                    new_id = partial_close_trade(trade["id"], pc_qty, pc_price, pc_time, pc_comm)
                    st.success(f"Closed {pc_qty:g} @ {pc_price} → trade #{new_id}. "
                               f"{qty - pc_qty:g} remain open.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

        # ── Full close ───────────────────────────────────────────────────────
        with col_full:
            st.markdown("##### 🏁 Close Trade (full)")
            fc_price = st.number_input("Exit price", min_value=0.0, step=0.01,
                                       value=ep, format="%.4f", key=f"fc_price_{trade['id']}")
            fc_time = st.text_input("Exit time", value=_dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    key=f"fc_time_{trade['id']}")
            fc_comm = st.number_input("Exit commission ($)", min_value=0.0, step=0.5,
                                      value=0.0, key=f"fc_comm_{trade['id']}")
            if fc_price > 0:
                est = (fc_price - ep) * qty * sign
                est_col = "#00c896" if est >= 0 else "#ff4b6e"
                st.markdown(f"Est. P&L: <b style='color:{est_col};font-family:JetBrains Mono;'>{est:+,.2f}</b>",
                            unsafe_allow_html=True)
            if st.button("🏁 Close Trade", type="primary", key=f"fc_btn_{trade['id']}",
                         disabled=fc_price <= 0):
                close_trade(trade["id"], fc_price, fc_time, extra_commission=fc_comm)
                st.success(f"Trade #{trade['id']} closed @ {fc_price}")
                st.rerun()

    else:
        st.markdown(f"**Closed:** exit {trade.get('exit_price') or '—'} · "
                    f"{(trade.get('exit_time') or '—')[:16]} · P&L {float(trade.get('pnl') or 0):+,.2f}")
        st.caption("Accidentally closed (e.g. exit price entered on the manual form)? "
                   "Reopen clears exit price/time and resets P&L to 0.")
        if st.button("🔓 Reopen Trade", key=f"reopen_{trade['id']}"):
            reopen_trade(trade["id"])
            st.success(f"Trade #{trade['id']} reopened.")
            st.rerun()


# ── Playbook Tab ──────────────────────────────────────────────────────────────

def _trade_playbook(trade):
    playbooks = get_playbooks()
    if not playbooks:
        st.info("No playbooks yet. Create one in the Playbooks page.")
        return

    pb_map = {"— None —": None} | {pb["name"]: pb["id"] for pb in playbooks}
    current_pb_id = trade.get("playbook_id")
    current_pb_name = next((pb["name"] for pb in playbooks if pb["id"] == current_pb_id), None)

    selected_pb_name = st.selectbox(
        "Playbook",
        list(pb_map.keys()),
        index=list(pb_map.keys()).index(current_pb_name) if current_pb_name in pb_map else 0
    )
    pb_id = pb_map[selected_pb_name]

    if pb_id is None:
        if current_pb_id:
            st.caption(f"This trade currently has a playbook assigned.")
            if st.button("🗑️ Clear playbook from this trade", key=f"clear_pb_{trade['id']}"):
                from database import execute as _execute
                _execute("""UPDATE trades SET playbook_id=NULL, playbook_rules_met=NULL,
                            risk_score=NULL, updated_at=datetime('now') WHERE id=?""",
                         (trade["id"],))
                st.success("Playbook cleared.")
                st.rerun()
        else:
            st.caption("No playbook assigned to this trade.")
        return

    pb = get_playbook(pb_id)

    # Load existing rules_met
    existing_rules = {}
    if trade.get("playbook_rules_met"):
        try:
            existing_rules = json.loads(trade["playbook_rules_met"])
        except:
            pass

    if not pb or not pb.get("rules"):
        st.info("This playbook has no rules yet.")
        return

    st.markdown("**Mark rules that were met for this trade:**")
    rules_met = {}
    for rule in pb["rules"]:
        badge = {"required": "🔴", "optional": "🟡", "bonus": "🟢"}.get(rule["rule_type"], "⚪")
        key = str(rule["id"])
        default = bool(existing_rules.get(key, existing_rules.get(rule["id"], False)))
        grp = f" ⛓ _{rule['rule_group']} (any one)_" if rule.get("rule_group") else ""
        checked = st.checkbox(
            f"{badge} **{rule['name']}** — _{rule['rule_type']}_{grp}  \n{rule.get('description', '')}",
            value=default,
            key=f"td_rule_{rule['id']}"
        )
        rules_met[rule["id"]] = checked

    if st.button("💾 Save Playbook Assessment", type="primary"):
        from utils.playbook_logic import evaluate_trade_risk
        assessment = evaluate_trade_risk(pb_id, rules_met)
        update_trade_playbook(trade["id"], pb_id, rules_met, assessment.get("risk_score", 0))
        st.success(f"Saved! Risk Score: **{assessment['risk_score']}** / 100")
        for w in assessment.get("warnings", []):
            st.warning(w)
        st.rerun()

    # Live score preview
    from utils.playbook_logic import evaluate_trade_risk
    live = evaluate_trade_risk(pb_id, {r["id"]: st.session_state.get(f"td_rule_{r['id']}", False) for r in pb["rules"]})
    if live:
        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Quality Score",   f"{live['risk_score']} / 100")
        c2.metric("Required Met",    f"{live['required_pct']}%")
        c3.metric("Optional Met",    f"{live['optional_pct']}%")
        risk_icons = {"normal": "🟢", "reduced": "🟡", "no_trade": "🔴"}
        rl = live.get("risk_level", "normal")
        st.markdown(f"**Risk Level:** {risk_icons.get(rl,'⚪')} {rl.upper()}  |  **Size Multiplier:** {live.get('risk_multiplier',1.0)}x")


# ── Journal Tab ───────────────────────────────────────────────────────────────

def _trade_journal_tab(trade):
    existing = fetch_one(
        "SELECT * FROM journal_entries WHERE trade_id=? AND entry_type='trade' ORDER BY created_at DESC LIMIT 1",
        (trade["id"],)
    )
    templates = get_templates("trade")

    col1, col2 = st.columns([3, 1])
    with col2:
        template = None
        if templates:
            sel_t = st.selectbox("Template", ["None"] + [t["name"] for t in templates], key="td_tmpl")
            template = next((t for t in templates if t["name"] == sel_t), None) if sel_t != "None" else None

    t = template or {}
    e = existing or {}

    if st.session_state.pop("_td_journal_saved", None):
        st.toast("Journal notes saved!", icon="✅")

    tid = trade["id"]
    analysis   = st.text_area("📊 Analysis",   value=e.get("analysis")   or t.get("analysis_template",""),   height=150, key=f"td_analysis_{tid}")
    execution  = st.text_area("⚡ Execution",  value=e.get("execution")  or t.get("execution_template",""),  height=150, key=f"td_execution_{tid}")
    psychology = st.text_area("🧠 Psychology", value=e.get("psychology") or t.get("psychology_template",""), height=150, key=f"td_psychology_{tid}")
    lessons    = st.text_area("💡 Lessons",    value=e.get("lessons", ""), height=80, key=f"td_lessons_{tid}")

    col1, col2, col3 = st.columns(3)
    GRADES = ["", "A+", "A", "B+", "B", "C+", "C", "D", "F"]
    grade_idx = GRADES.index(e.get("grade", "")) if e.get("grade") in GRADES else 0
    grade = col1.selectbox("Grade", GRADES, index=grade_idx, key=f"td_grade_{tid}")
    mood  = col2.slider("Mood", 1, 10, value=int(e.get("mood") or 5), key=f"td_mood_{tid}")

    if st.button("💾 Save Journal Notes", type="primary"):
        entry_date = (trade.get("entry_time") or "")[:10] or str(pd.Timestamp.today().date())
        save_journal_entry(
            entry_id=e.get("id"),
            entry_type="trade",
            entry_date=entry_date,
            analysis=analysis, execution=execution,
            psychology=psychology, lessons=lessons,
            grade=grade, mood=mood,
            trade_id=trade["id"],
        )
        st.session_state["_td_journal_saved"] = True
        st.rerun()
    if e.get("id"):
        ts = (e.get("updated_at") or e.get("created_at") or "")[:16]
        st.caption(f"✅ Saved entry #{e['id']}" + (f" · last updated {ts}" if ts else ""))


# ── Related Trades Tab ────────────────────────────────────────────────────────

def _trade_related(trade):
    st.markdown("**Other trades on the same symbol:**")
    related = fetch_all(
        "SELECT * FROM trades WHERE symbol=? AND id!=? ORDER BY entry_time DESC LIMIT 20",
        (trade["symbol"], trade["id"])
    )
    if not related:
        st.caption(f"No other trades on {trade['symbol']}.")
        return

    df = pd.DataFrame(related)
    df["net_pnl"] = (pd.to_numeric(df["pnl"], errors="coerce").fillna(0)
                    - pd.to_numeric(df["commission"], errors="coerce").fillna(0).abs())

    win_rate_sym = (df["net_pnl"] > 0).mean() * 100
    total_pnl_sym = df["net_pnl"].sum()

    c1, c2, c3 = st.columns(3)
    c1.metric(f"{trade['symbol']} Win Rate", f"{win_rate_sym:.1f}%")
    c2.metric(f"{trade['symbol']} Total P&L", f"{total_pnl_sym:+,.2f}")
    c3.metric("Trades", len(df))

    show_cols = ["id", "direction", "entry_price", "exit_price", "entry_time", "net_pnl", "status"]
    avail = [c for c in show_cols if c in df.columns]
    st.dataframe(df[avail].head(15), use_container_width=True, height=300)
