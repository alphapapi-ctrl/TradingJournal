"""
Page: Risk Calculator — stock/share and IC Markets CFD/lot sizing + Kelly criterion
"""
import streamlit as st
import numpy as np
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all, execute
from utils.app_settings import get_settings_dict


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_settings() -> dict:
    return get_settings_dict()

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

def _reset_sizing_instrument():
    from utils.position_sizing import IC_MARKETS_CONTRACTS
    contract = IC_MARKETS_CONTRACTS[st.session_state.get("rc_cfd_contract", "ic_gold")]
    st.session_state["rc_ticker"] = contract["symbol"] if st.session_state["rc_asset_type"] == "CFD" else ""
    st.session_state["rc_entry"] = 0.0
    st.session_state["rc_stop"] = 0.0
    st.session_state["rc_costs"] = 0.0
    st.session_state.pop("rc_live_price", None)
    st.session_state.pop("rc_live_sym", None)


def _reset_sizing_account():
    st.session_state["rc_balance"] = 0.0


def _change_sizing_type():
    _reset_sizing_instrument()
    _reset_sizing_account()


def _position_sizer():
    from utils.position_sizing import IC_MARKETS_CONTRACTS, size_cash_risk
    from utils.accounts import get_accounts
    accounts = get_accounts()
    settings = _get_settings()
    st.subheader("Position Size Calculator")
    defaults = {"rc_ticker": "", "rc_entry": 10.0, "rc_stop": 9.5,
        "rc_dir": "LONG", "rc_risk": float(settings.get("risk_pct", 1.0)),
        "rc_account": 0, "rc_asset_type": "Normal stock", "rc_cfd_contract": "ic_gold", "rc_costs": 0.0}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    saved = fetch_all("SELECT * FROM risk_setups ORDER BY created_at DESC, id DESC LIMIT 50")
    if saved:
        with st.expander(f"📂 Saved setups ({len(saved)})"):
            def setup_label(sid):
                item = next(s for s in saved if s["id"] == sid)
                unit = "lots" if item.get("instrument_key", "stocks") in IC_MARKETS_CONTRACTS else "shares"
                return f"{(item['created_at'] or '')[:10]}  {item['ticker']} {item['direction']}  {item['shares']:g} {unit} @ {item['entry_price']:.4f}"
            selected = st.selectbox("Setup", [s["id"] for s in saved], format_func=setup_label, key="rc_saved_sel")
            load, delete, _ = st.columns([1, 1, 3])
            if load.button("📥 Load", key="rc_load_btn", use_container_width=True):
                item = next(s for s in saved if s["id"] == selected)
                instrument_key = item.get("instrument_key") or "stocks"
                st.session_state["rc_asset_type"] = "CFD" if instrument_key in IC_MARKETS_CONTRACTS else "Normal stock"
                if instrument_key in IC_MARKETS_CONTRACTS:
                    st.session_state["rc_cfd_contract"] = instrument_key
                for state, field in (("rc_ticker", "ticker"), ("rc_entry", "entry_price"), ("rc_stop", "stop_price"), ("rc_dir", "direction"), ("rc_risk", "risk_pct"), ("rc_balance", "balance")):
                    st.session_state[state] = item[field]
                st.session_state["rc_costs"] = float(item.get("costs_per_unit") or 0)
                st.session_state["rc_account"] = next((i for i, a in enumerate(accounts) if a["id"] == item.get("account_id")), 0)
                st.session_state.pop("rc_live_price", None)
                st.session_state.pop("rc_live_sym", None)
                st.rerun()
            if delete.button("🗑️ Delete", key="rc_del_btn", use_container_width=True):
                execute("DELETE FROM risk_setups WHERE id=?", (selected,))
                st.rerun()

    st.selectbox("Instrument type", ["Normal stock", "CFD"], key="rc_asset_type", on_change=_change_sizing_type)
    is_cfd = st.session_state["rc_asset_type"] == "CFD"
    contract = None
    if is_cfd:
        key = st.selectbox("CFD contract", list(IC_MARKETS_CONTRACTS), format_func=lambda key: IC_MARKETS_CONTRACTS[key]["label"], key="rc_cfd_contract", on_change=_reset_sizing_instrument)
        contract = IC_MARKETS_CONTRACTS[key]
        st.caption(f"1.00 lot = {contract['contract_size']:g} contract units · USD {contract['contract_size']:g} per full 1.00 price move per lot · minimum/step {contract['min_quantity']:g}/{contract['quantity_step']:g} lots · maximum {contract['max_quantity']:g} lots. Tick values are not needed.")
    instrument_key = st.session_state["rc_cfd_contract"] if is_cfd else "stocks"
    unit = "lots" if is_cfd else "shares"
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Account**")
        account = None
        if accounts:
            if st.session_state["rc_account"] >= len(accounts):
                st.session_state["rc_account"] = 0
            index = st.selectbox("Account", range(len(accounts)), format_func=lambda i: f"{accounts[i]['name']} ({accounts[i]['currency']})", key="rc_account", on_change=_reset_sizing_account)
            account = accounts[index]
        currency = (account.get("currency") if account else ("USD" if is_cfd else "AUD")) or "USD"
        if "rc_balance" not in st.session_state:
            st.session_state["rc_balance"] = 0.0
        balance = st.number_input(f"Current balance ({currency})", min_value=0.0, step=100.0, key="rc_balance")
        st.caption("Enter the current balance; a saved setup's balance may need updating.")
        risk_pct = st.slider("Risk per trade (%)", 0.1, 5.0, step=0.1, format="%.1f%%", key="rc_risk")
        budget = balance * risk_pct / 100
        st.metric(f"Risk budget ({currency})", f"{budget:,.2f}")
        costs = st.number_input(f"Round-trip cost allowance per {'1.00 lot' if is_cfd else 'share'} ({currency})", min_value=0.0, step=0.1, key="rc_costs")
        if costs == 0:
            st.caption("Sizing excludes commission, slippage and any spread allowance not already reflected in your entry/stop prices.")

    with col2:
        st.markdown("**Trade Setup**")
        if is_cfd:
            symbol = contract["symbol"]
            st.write(f"**Broker symbol:** {symbol}")
        else:
            ticker_col, price_col = st.columns([2, 1])
            ticker = ticker_col.text_input("Ticker", placeholder="BHP.AX / AAPL / VAS.AX", key="rc_ticker")
            if price_col.button("📡 Get price", key="rc_fetch", use_container_width=True) and ticker.strip():
                try:
                    from utils.stock_data import fetch_history
                    yf_symbol = ticker.strip().upper()
                    try:
                        history = fetch_history(yf_symbol, "5d", "1d")
                    except Exception:
                        yf_symbol = yf_symbol + ".AX" if "." not in yf_symbol else yf_symbol
                        history = fetch_history(yf_symbol, "5d", "1d")
                    live = float(history["Close"].iloc[-1])
                    st.session_state.update(rc_live_price=live, rc_live_sym=yf_symbol, rc_entry=round(live, 4), rc_stop=round(live * (0.95 if st.session_state["rc_dir"] == "LONG" else 1.05), 4))
                except Exception as error:
                    st.error(f"Could not fetch price: {error}")
            # A fetched alias is usable only while the ticker it came from still matches.
            live_symbol = st.session_state.get("rc_live_sym", "")
            typed = ticker.strip().upper()
            symbol = live_symbol if live_symbol in (typed, typed + ".AX") else typed
            if st.session_state.get("rc_live_price") and symbol == live_symbol:
                st.caption(f"Fetched {symbol}: {st.session_state['rc_live_price']:,.4f}; review entry and stop.")
        entry = st.number_input("Entry Price", min_value=0.0, step=0.01, format="%.4f", key="rc_entry")
        stop = st.number_input("Stop Loss Price", min_value=0.0, step=0.01, format="%.4f", key="rc_stop")
        direction = st.selectbox("Direction", ["LONG", "SHORT"], key="rc_dir")

    if is_cfd and currency.upper() != "USD":
        st.warning("Select a USD account for these IC Markets contracts. Convert risk to USD before using a non-USD account.")
        return
    if balance <= 0:
        st.info("Enter a current balance to calculate position size.")
        return
    try:
        result = size_cash_risk(entry, stop, budget, point_value=contract["contract_size"] if is_cfd else 1,
            quantity_step=contract["quantity_step"] if is_cfd else 1,
            min_quantity=contract["min_quantity"] if is_cfd else 1,
            max_quantity=contract["max_quantity"] if is_cfd else None,
            costs_per_unit=costs, direction=direction)
    except ValueError as error:
        st.warning(str(error))
        return
    quantity = result["quantity"]
    quantity_text = f"{quantity:,.2f} lots" if is_cfd else f"{quantity:,.0f} shares"
    value = result["position_value"]
    percentage = value / balance * 100
    st.divider()
    size_col, risk_col, value_col = st.columns(3)
    size_col.metric("Position Size", quantity_text)
    risk_col.metric(f"Planned risk ({currency})", f"{result['actual_risk']:,.2f}")
    value_col.metric(f"{'Notional' if is_cfd else 'Position'} value ({currency})", f"{value:,.2f}")
    if result["below_minimum"]:
        st.warning(f"The risk budget cannot support the minimum {'lot size' if is_cfd else 'share quantity'}. No position fits; size has not been rounded up.")
    if result["capped_at_maximum"]:
        st.info("Size is capped at this contract's maximum volume; part of the risk budget remains unused.")
    if is_cfd:
        st.caption("Notional value is exposure, not required margin. Check available broker margin separately. This calculation does not place an order or record an execution.")
    elif percentage > 100:
        st.warning("Position value exceeds account balance.")

    # Preserve the existing stock-only convenience action; a CFD sizing preview is not a fill.
    if not is_cfd and st.button("➕ Create open trade", disabled=quantity <= 0 or not symbol, use_container_width=True):
        from utils.trade_ops import upsert_trade_from_broker
        from datetime import date
        trade_id, _ = upsert_trade_from_broker({"broker": "Manual", "broker_trade_id": None,
            "symbol": symbol, "direction": direction, "entry_price": entry, "exit_price": None,
            "entry_time": str(date.today()), "exit_time": None, "quantity": quantity, "pnl": 0,
            "commission": 0, "swap": 0, "raw_data": None, "status": "open",
            "account_id": account["id"] if account else None})
        st.success(f"Trade #{trade_id} created: {symbol} {direction} {quantity_text}.")

    note = st.text_input("Note (optional)", key="rc_setup_note")
    if st.button("💾 Save Setup", key="rc_save_setup", disabled=not symbol or quantity <= 0):
        execute("""INSERT INTO risk_setups
            (ticker,direction,entry_price,stop_price,risk_pct,balance,account_id,shares,note,instrument_key,costs_per_unit)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (symbol, direction, entry, stop, risk_pct, balance,
            account["id"] if account else None, quantity, note.strip() or None, instrument_key, costs))
        st.success(f"Setup saved: {symbol} {direction} {quantity_text}.")

    st.divider()
    st.markdown("**📋 Pre-Trade Plan**")
    st.caption("Saves a plan without creating a trade. Link confirmed executions later.")
    from utils.playbook_logic import get_playbooks
    playbooks = {"None": None} | {pb["name"]: pb["id"] for pb in get_playbooks()}
    selected_playbook = st.selectbox("Playbook", list(playbooks), key="rc_playbook")
    if st.button("📝 Create Pre-Trade Journal Entry", type="primary", key="rc_create_plan", disabled=not symbol or quantity <= 0):
        from datetime import date
        import json
        account_text = f"Account: {account['name']}\n" if account else ""
        basis = f"CFD contract size {contract['contract_size']}; lot step {contract['quantity_step']}; no tick values required" if is_cfd else "Whole-share sizing"
        plan = f"{account_text}Ticker: {symbol}\nDirection: {direction}\nEntry: {entry:.4f}\nStop: {stop:.4f} (distance {result['stop_distance']:g})\nSize: {quantity_text}\n{basis}\nNotional value: {currency} {value:,.2f}"
        risk = f"{risk_pct:g}% of current balance {currency} {balance:,.2f}; budget {budget:,.2f}; planned risk {result['actual_risk']:,.2f}; round-trip cost allowance per {unit}: {costs:g}"
        assumptions = {"position_sizing": {"instrument_key": instrument_key, "quantity": quantity,
            "quantity_unit": unit, "contract_size": contract["contract_size"] if is_cfd else 1,
            "costs_per_unit": costs, "risk_budget": budget, "actual_risk": result["actual_risk"],
            "currency": currency, "source": contract["source"] if is_cfd else "User stock inputs"}}
        execute("""INSERT INTO journal_entries
            (entry_type,entry_date,stage,playbook_id,pre_plan,pre_risk_notes,account_id,custom_fields)
            VALUES ('trade',?,'pre',?,?,?,?,?)""", (str(date.today()), playbooks[selected_playbook], plan, risk,
            account["id"] if account else None, json.dumps(assumptions)))
        st.success(f"Pre-trade plan created for {symbol}; no execution recorded.")


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
