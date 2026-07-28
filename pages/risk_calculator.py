"""
Page: Risk Calculator — stocks-first position sizing + Kelly criterion
"""
import streamlit as st
import numpy as np
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all, execute


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_settings() -> dict:
    rows = fetch_all("SELECT key, value FROM app_settings")
    return {r["key"]: r["value"] for r in rows} if rows else {}

CHART_STYLE = dict(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#6b7a99"),
    margin=dict(l=8, r=8, t=36, b=8),
)


def show(embedded=False):
    if not embedded:
        st.header("⚖️ Risk Calculator")
    tab_pos, tab_kelly = st.tabs(["📐 Position Size", "🎲 Kelly Criterion"])
    with tab_pos:   _position_sizer()
    with tab_kelly: _kelly_criterion()


# ── Position Sizer (stocks-first) ─────────────────────────────────────────────

def _position_sizer():
    st.subheader("Position Size Calculator")
    settings = _get_settings()

    from utils.accounts import get_accounts
    accounts = get_accounts()

    # ── Saved setups — load one back to revisit later ─────────────────────────
    saved = fetch_all("SELECT * FROM risk_setups ORDER BY created_at DESC LIMIT 50")
    if saved:
        with st.expander(f"📂 Saved setups ({len(saved)})"):
            def setup_label(sid):
                s = next(x for x in saved if x["id"] == sid)
                note = f"  · {s['note'][:30]}" if s.get("note") else ""
                return (f"{(s['created_at'] or '')[:10]}  {s['ticker']} {s['direction']}  "
                        f"{s['shares']:g} @ {s['entry_price']:.4f}  (stop {s['stop_price']:.4f}, "
                        f"{s['risk_pct']:.1f}%){note}")
            sel_id = st.selectbox("Setup", [s["id"] for s in saved],
                                  format_func=setup_label, key="rc_saved_sel")
            cl, cd, _ = st.columns([1, 1, 3])
            if cl.button("📥 Load", key="rc_load_btn", use_container_width=True):
                s = next(x for x in saved if x["id"] == sel_id)
                # Write straight into widget state — survives any later rerun
                st.session_state["rc_ticker"] = s["ticker"] or ""
                st.session_state["rc_entry"]  = float(s["entry_price"] or 0)
                st.session_state["rc_stop"]   = float(s["stop_price"] or 0)
                st.session_state["rc_dir"]    = s["direction"] or "LONG"
                st.session_state["rc_risk"]   = float(s["risk_pct"] or 1.0)
                st.session_state["rc_balance"] = float(s["balance"] or 10000.0)
                if s.get("account_id") and accounts:
                    st.session_state["rc_account"] = next(
                        (i for i, a in enumerate(accounts) if a["id"] == s["account_id"]), 0)
                st.session_state.pop("rc_live_price", None)
                st.session_state["rc_live_sym"] = s["ticker"]
                st.rerun()
            if cd.button("🗑️ Delete", key="rc_del_btn", use_container_width=True):
                execute("DELETE FROM risk_setups WHERE id=?", (sel_id,))
                st.rerun()

    # ── Initialise widget state once (all inputs are session-state driven) ───
    init_defaults = {
        "rc_ticker": "",
        "rc_entry": 10.00,
        "rc_stop": 9.50,
        "rc_dir": "LONG",
        "rc_risk": float(settings.get("risk_pct", 1.0)),
        "rc_account": 0,
    }
    for k, v in init_defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Account**")
        acc_obj = None
        if accounts:
            acc_names = [f"{a['name']}  ({a['broker']})" for a in accounts]
            if st.session_state["rc_account"] >= len(accounts):
                st.session_state["rc_account"] = 0
            acc_idx = st.selectbox("Account", range(len(acc_names)),
                                   format_func=lambda i: acc_names[i], key="rc_account")
            acc_obj = accounts[acc_idx]
            default_bal = float(acc_obj.get("initial_balance") or 0) or 10000.0
        else:
            default_bal = float(settings.get("account_balance", 10000))
        if "rc_balance" not in st.session_state:
            st.session_state["rc_balance"] = default_bal
        account_balance = st.number_input("Balance ($)", min_value=100.0, step=100.0,
                                          key="rc_balance")
        risk_pct    = st.slider("Risk per trade (%)", 0.1, 5.0,
                                 step=0.1, format="%.1f%%", key="rc_risk")
        risk_amount = account_balance * risk_pct / 100
        st.metric("Risk Amount ($)", f"{risk_amount:,.2f}")

    with col2:
        st.markdown("**Trade Setup**")
        tc1, tc2 = st.columns([2, 1])
        with tc1:
            ticker = st.text_input("Ticker", placeholder="BHP.AX / AAPL / VAS.AX",
                                   key="rc_ticker")
        with tc2:
            st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
            fetch_clicked = st.button("📡 Get price", key="rc_fetch", use_container_width=True)

        if fetch_clicked and ticker.strip():
            try:
                from utils.stock_data import resolve_yf_symbol, fetch_history
                yf_sym = ticker.strip().upper()
                try:
                    hist = fetch_history(yf_sym, "5d", "1d")
                except Exception:
                    yf_sym = yf_sym + ".AX" if "." not in yf_sym else yf_sym
                    hist = fetch_history(yf_sym, "5d", "1d")
                live = float(hist["Close"].iloc[-1])
                st.session_state["rc_live_price"] = live
                st.session_state["rc_live_sym"] = yf_sym
                # Push the live price into the entry/stop inputs
                st.session_state["rc_entry"] = round(live, 4)
                st.session_state["rc_stop"]  = round(live * 0.95, 4)
            except Exception as e:
                st.error(f"Could not fetch price for {ticker}: {e}")

        live_price = st.session_state.get("rc_live_price")
        if live_price:
            st.caption(f"Live: **{st.session_state.get('rc_live_sym','')} {live_price:,.2f}** "
                       f"(entry/stop pre-filled — stop at −5%)")

        entry_price = st.number_input("Entry Price", min_value=0.0, step=0.01,
                                      format="%.4f", key="rc_entry")
        stop_price  = st.number_input("Stop Loss Price", min_value=0.0, step=0.01,
                                      format="%.4f", key="rc_stop")
        direction   = st.selectbox("Direction", ["LONG", "SHORT"], key="rc_dir")

    if entry_price <= 0 or stop_price <= 0 or entry_price == stop_price:
        st.warning("Enter a valid entry and stop price.")
        return

    stop_distance = abs(entry_price - stop_price)
    shares = int(risk_amount / stop_distance) if stop_distance > 0 else 0
    position_value = shares * entry_price
    pos_pct = position_value / account_balance * 100 if account_balance else 0

    st.divider()
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    c1.metric("Position Size", f"{shares:,} shares")
    c2.metric("Position Value", f"${position_value:,.2f}")
    c3.metric("% of Account", f"{pos_pct:.1f}%")
    with c4:
        st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
        if st.button("➕ Add as trade", use_container_width=True,
                     help="Open the Add Trade tab with these values pre-filled"):
            st.session_state["manual_trade_prefill"] = {
                "symbol": (st.session_state.get("rc_live_sym") or ticker or "").strip().upper(),
                "direction": direction,
                "quantity": float(shares),
                "entry_price": float(entry_price),
                "account_id": acc_obj["id"] if acc_obj else None,
            }
            st.session_state["page"] = "trades"
            st.session_state["_trades_pending_tab"] = 1  # Add Trade tab
            st.rerun()
    if pos_pct > 100:
        st.warning("Position value exceeds account balance — stop is too tight for this risk % without leverage.")

    # ── Save setup for later (capital constraints etc.) ───────────────────────
    sc1, sc2 = st.columns([3, 1])
    with sc1:
        setup_note = st.text_input("Note (optional)", key="rc_setup_note",
                                   placeholder="e.g. waiting on capital from SPYI exit")
    with sc2:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        if st.button("💾 Save Setup", key="rc_save_setup", use_container_width=True,
                     disabled=not (ticker.strip() or st.session_state.get("rc_live_sym"))):
            sym = (st.session_state.get("rc_live_sym") or ticker or "").strip().upper()
            execute(
                """INSERT INTO risk_setups
                   (ticker, direction, entry_price, stop_price, risk_pct, balance,
                    account_id, shares, note)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (sym, direction, entry_price, stop_price, risk_pct, account_balance,
                 acc_obj["id"] if acc_obj else None, shares, setup_note.strip() or None),
            )
            st.success(f"Setup saved: {sym} {direction} {shares:,} @ {entry_price:.4f} — "
                       f"load it from 📂 Saved setups any time.")

    # ── Create pre-trade journal entry ────────────────────────────────────────
    st.divider()
    st.markdown("**📋 Pre-Trade Plan**")
    st.caption("Creates an unassigned pre-trade journal entry with this sizing. "
               "Complete the plan & playbook in the Journal, then link imported trades to it to form a position.")

    from utils.playbook_logic import get_playbooks
    playbooks  = get_playbooks()
    pb_options = {"None": None} | {pb["name"]: pb["id"] for pb in playbooks}
    pc1, pc2 = st.columns([2, 2])
    with pc1:
        sel_pb = st.selectbox("Playbook", list(pb_options.keys()), key="rc_playbook")

    if st.button("📝 Create Pre-Trade Journal Entry", type="primary", key="rc_create_plan"):
        from datetime import date as _date
        sym = (st.session_state.get("rc_live_sym") or ticker or "").strip().upper() or "UNKNOWN"
        acc_txt = f"Account: {acc_obj['name']}\n" if acc_obj else ""
        plan_txt = (
            f"{acc_txt}"
            f"Ticker: {sym}\n"
            f"Direction: {direction}\n"
            f"Entry: {entry_price:.4f}\n"
            f"Stop: {stop_price:.4f}  (distance {stop_distance:.4f})\n"
            f"Size: {shares:,} shares  (~${position_value:,.2f}, {pos_pct:.1f}% of account)"
        )
        risk_txt = (
            f"Risk: {risk_pct:.1f}% of ${account_balance:,.2f} = ${risk_amount:,.2f}\n"
            f"Smart risk check: defined stop at {stop_price:.4f}, planned size {shares:,} shares"
        )
        execute(
            """INSERT INTO journal_entries
               (entry_type, entry_date, stage, playbook_id, pre_plan, pre_risk_notes, account_id)
               VALUES ('trade', ?, 'pre', ?, ?, ?, ?)""",
            (str(_date.today()), pb_options.get(sel_pb), plan_txt, risk_txt,
             acc_obj["id"] if acc_obj else None),
        )
        st.success(
            f"Pre-trade plan created for **{sym}** — find it in **Journal → Pre-Trade Plan** "
            f"(or link it from Trade Notes once trades are imported)."
        )


# ── Kelly Criterion ───────────────────────────────────────────────────────────

def _kelly_criterion():
    st.subheader("Kelly Criterion — Optimal Risk Fraction")
    st.caption(
        "Kelly sizes risk from your **edge**: f* = W − (1−W)/R, where W = win rate and "
        "R = avg win ÷ avg loss. Full Kelly maximises long-run growth but assumes your "
        "stats are exact and tolerates brutal drawdowns — **half or quarter Kelly** is the "
        "practical choice."
    )

    from utils.accounts import get_accounts
    accounts = get_accounts()

    # ── Source stats: from trade history (per account) or manual ─────────────
    c1, c2 = st.columns([2, 2])
    with c1:
        acc_filter_names = ["All Accounts"] + [f"{a['name']}  ({a['broker']})" for a in accounts]
        acc_sel = st.selectbox("Trade history from", range(len(acc_filter_names)),
                               format_func=lambda i: acc_filter_names[i], key="kelly_acc")
        acc_id = accounts[acc_sel - 1]["id"] if acc_sel > 0 else None

    where = "status='closed'"
    params = []
    if acc_id:
        where += " AND account_id=?"
        params.append(acc_id)
    trades = fetch_all(f"SELECT pnl, commission FROM trades WHERE {where}", params)

    net = [float(t.get("pnl") or 0) - abs(float(t.get("commission") or 0)) for t in trades]
    wins   = [x for x in net if x > 0]
    losses = [abs(x) for x in net if x < 0]

    hist_available = len(wins) >= 1 and len(losses) >= 1
    if hist_available:
        hist_w  = len(wins) / len(net)
        hist_aw = float(np.mean(wins))
        hist_al = float(np.mean(losses))
        st.markdown(
            f"📊 History: **{len(net)}** closed trades — win rate **{hist_w*100:.0f}%**, "
            f"avg win **{hist_aw:,.2f}**, avg loss **{hist_al:,.2f}**"
        )
        if len(net) < 30:
            st.warning(f"Only {len(net)} trades — Kelly estimates are unreliable below ~30 trades. "
                       "Treat the output as indicative only while the forward test builds history.")
    else:
        st.info("Not enough closed trade history (need at least 1 win and 1 loss) — enter estimates manually.")
        hist_w, hist_aw, hist_al = 0.55, 200.0, 150.0

    with c2:
        use_manual = st.checkbox("Override with manual estimates", value=not hist_available, key="kelly_manual")

    if use_manual:
        m1, m2, m3 = st.columns(3)
        win_rate = m1.number_input("Win rate (%)", value=round(hist_w * 100, 1),
                                    min_value=1.0, max_value=99.0, step=1.0) / 100
        avg_win  = m2.number_input("Avg win ($)",  value=round(hist_aw, 2), min_value=0.01, step=10.0)
        avg_loss = m3.number_input("Avg loss ($)", value=round(hist_al, 2), min_value=0.01, step=10.0)
    else:
        win_rate, avg_win, avg_loss = hist_w, hist_aw, hist_al

    payoff = avg_win / avg_loss if avg_loss > 0 else 0
    if payoff <= 0:
        st.warning("Need a positive payoff ratio.")
        return

    kelly = win_rate - (1 - win_rate) / payoff

    st.divider()

    if kelly <= 0:
        st.error(
            f"**Kelly fraction is {kelly*100:.1f}% — no positive edge.** "
            f"With a {win_rate*100:.0f}% win rate you need a payoff ratio above "
            f"{(1-win_rate)/win_rate:.2f} (currently {payoff:.2f}). "
            "Kelly says don't size up — fix the edge first."
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Payoff Ratio (R)", f"{payoff:.2f}")
    c2.metric("Full Kelly", f"{kelly*100:.1f}%")
    c3.metric("Half Kelly ✅", f"{kelly/2*100:.1f}%")
    c4.metric("Quarter Kelly", f"{kelly/4*100:.1f}%")

    # Dollar amounts against a balance
    settings = _get_settings()
    default_bal = float(settings.get("account_balance", 10000))
    if acc_id:
        acc_obj = next((a for a in accounts if a["id"] == acc_id), None)
        if acc_obj and acc_obj.get("initial_balance"):
            default_bal = float(acc_obj["initial_balance"])
    balance = st.number_input("Account balance ($)", value=default_bal, min_value=100.0,
                              step=500.0, key="kelly_balance")
    st.markdown(
        f"Risk per trade at **half Kelly**: **${balance*kelly/2:,.2f}** "
        f"&nbsp;·&nbsp; quarter Kelly: **${balance*kelly/4:,.2f}** "
        f"&nbsp;·&nbsp; full Kelly: ${balance*kelly:,.2f}",
        unsafe_allow_html=True,
    )

    cur_risk = float(settings.get("risk_pct", 1.0))
    if kelly / 2 * 100 < cur_risk:
        st.warning(f"Your current default risk ({cur_risk:.1f}%) is **above** half Kelly "
                   f"({kelly/2*100:.1f}%) — you may be over-betting your measured edge.")
    else:
        st.success(f"Your current default risk ({cur_risk:.1f}%) is at or below half Kelly "
                   f"({kelly/2*100:.1f}%) — conservative relative to your measured edge.")

    # ── Growth-rate curve ─────────────────────────────────────────────────────
    fs = np.linspace(0.001, min(kelly * 2.5, 0.99), 200)
    # Expected log-growth per trade: g(f) = W·ln(1 + f·R) + (1−W)·ln(1 − f)
    g = win_rate * np.log(1 + fs * payoff) + (1 - win_rate) * np.log(1 - fs)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fs * 100, y=g * 100, mode="lines", name="Growth rate",
                             line=dict(color="#00c896", width=2)))
    fig.add_vline(x=kelly * 100, line=dict(color="#f5a623", dash="dash"),
                  annotation_text=f"Full Kelly {kelly*100:.1f}%", annotation_font_color="#f5a623")
    fig.add_vline(x=kelly / 2 * 100, line=dict(color="#4a9eff", dash="dot"),
                  annotation_text=f"Half {kelly/2*100:.1f}%", annotation_font_color="#4a9eff")
    zero_cross = fs[g < 0]
    if len(zero_cross):
        fig.add_vline(x=float(zero_cross[0]) * 100, line=dict(color="#ff4b6e", dash="dot"),
                      annotation_text="Growth turns negative", annotation_font_color="#ff4b6e")
    fig.update_layout(**CHART_STYLE, title="Expected log-growth vs risk fraction",
                      xaxis_title="Risk per trade (% of account)", yaxis_title="Growth per trade (%)",
                      height=320, showlegend=False,
                      xaxis=dict(gridcolor="#1a2030"), yaxis=dict(gridcolor="#1a2030"))
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "The curve peaks at full Kelly, but note how flat it is to the left and how quickly it "
        "collapses to the right: half Kelly gives ~75% of the growth with far smaller drawdowns, "
        "while betting past full Kelly destroys the account even with a genuine edge. "
        "Position-based scaling (your multiple-entry model) effectively splits this fraction "
        "across entries."
    )
