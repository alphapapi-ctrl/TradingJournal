"""
Page: Import Trades — supports MT5 HTML/CSV, IC Markets CSV, CMC Markets CSV
"""
import streamlit as st
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from brokers.importers import auto_detect_and_parse
from utils.trade_ops import upsert_trade_from_broker
from database import execute, fetch_all


BROKER_INSTRUCTIONS = {
    "TradingView": """
**How to export from TradingView (paper trading):**
1. Open the **Trading Panel** at the bottom of your chart
2. Go to the **History** tab (or **Filled** orders)
3. Click the **Export** icon (⤓) to download the CSV
4. Upload it here and assign it to the matching portfolio account
   (AU stocks / US stocks / ETF growth / ETF income)

_Fills are FIFO-paired into round-trip trades. Cancelled/rejected orders are skipped._
""",
    "IBKR": """
**How to export from Interactive Brokers:**
1. Log in to Client Portal or TWS
2. Go to **Reports → Activity → Activity Statement**
3. Set your date range, select **CSV** format, click **Run**
4. Upload the `Statement_XXXXXXXX_YYYYMMDD_YYYYMMDD.csv` file here

_Stocks (ASX + US), Options included. Forex currency conversions are skipped._
""",
    "CMC Markets": """
**How to export from CMC Markets:**
1. Log in to the CMC Markets platform (web or desktop)
2. Go to **Account → History → Statement**
3. Select your date range and click **Download CSV**
4. Upload the `Statement-XXXXXX-YYYYMMDD-YYYYMMDD.csv` file here
""",
    "MT5": """
**How to export from MT5:**
1. Open MetaTrader 5
2. Go to **View → Terminal → Account History**
3. Right-click the history panel → **Save as Detailed Report**
4. Upload the HTML file, or choose CSV for a simpler format
""",
    "IC Markets": """
**How to export from IC Markets:**
1. Log in to the IC Markets client portal
2. Go to **My Account → Trade History**
3. Select date range and export as CSV
4. Upload the CSV file here
""",
}


def show():
    st.header("➕ Add Trade")

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("""
        Import your trade history from your broker's export file.
        Formats auto-detected: **TradingView CSV** · **IBKR CSV** · **CMC Markets CSV** · **MT5 HTML/CSV** · **IC Markets CSV**
        """)
    with col2:
        st.info("Parsed locally — your data never leaves this machine.")

    st.divider()

    # ── File upload ───────────────────────────────────────────────────────────
    uploaded = st.file_uploader(
        "Upload broker export file",
        type=["csv", "html", "htm"],
        help="CMC Markets: Statement CSV · MT5: HTML or CSV · IC Markets: Trade History CSV"
    )

    if uploaded:
        try:
            content = uploaded.read()
            with st.spinner("Detecting format and parsing…"):
                trades, broker = auto_detect_and_parse(uploaded.name, content)

            # Account selector for this import
            from utils.accounts import get_accounts, create_account
            accounts = get_accounts()
            acc_options = {a["name"]: a["id"] for a in accounts}

            import_col, acc_col = st.columns([3, 2])
            with acc_col:
                if acc_options:
                    selected_acc_name = st.selectbox(
                        "Assign to account",
                        list(acc_options.keys()),
                        help="All imported trades will be tagged to this account"
                    )
                    selected_acc_id = acc_options[selected_acc_name]
                else:
                    st.warning("No accounts yet — a new one will be created automatically.")
                    selected_acc_id = None

                # Option to create a new account on the fly
                with st.expander("➕ Create new account first"):
                    with st.form("quick_account"):
                        new_name = st.text_input("Account Name", value=f"{broker} Account")
                        new_curr = st.selectbox("Currency", ["AUD","USD","EUR","GBP"])
                        new_bal  = st.number_input("Initial Balance", value=10000.0)
                        if st.form_submit_button("Create"):
                            new_id = create_account(new_name, broker, new_curr, new_bal,
                                                     set_default=len(accounts)==0)
                            st.success(f"Created '{new_name}'")
                            st.rerun()

            _show_import_preview(trades, broker, uploaded.name, selected_acc_id)

        except ValueError as e:
            st.error(f"Parse error: {e}")
            with st.expander("📖 Export instructions"):
                for b, instr in BROKER_INSTRUCTIONS.items():
                    st.markdown(f"**{b}**")
                    st.markdown(instr)
        except Exception as e:
            st.error(f"Unexpected error: {e}")
            import traceback
            with st.expander("Error details"):
                st.code(traceback.format_exc())

    st.divider()

    # ── Import history ────────────────────────────────────────────────────────
    st.subheader("Import History")
    history = fetch_all("SELECT * FROM import_history ORDER BY imported_at DESC LIMIT 20")
    if history:
        st.dataframe(
            pd.DataFrame(history)[["broker", "filename", "trades_imported", "imported_at", "status"]],
            use_container_width=True, hide_index=True
        )
    else:
        st.caption("No imports yet.")

    # ── Manual trade entry ────────────────────────────────────────────────────
    with st.expander("➕ Add Trade Manually"):
        _manual_trade_form()


def _show_import_preview(trades: list, broker: str, filename: str, account_id=None):
    """Show parsed results, summary, and import button."""
    from brokers.cmc_markets import summarise_cmc_import

    # Split by trade type
    real_trades = [t for t in trades if t.get("trade_type") != "dividend"]
    dividends   = [t for t in trades if t.get("trade_type") == "dividend"]
    closed      = [t for t in real_trades if t["status"] == "closed"]
    open_pos    = [t for t in real_trades if t["status"] == "open"]

    # Header
    st.success(f"✅ **{broker}** detected — {len(trades)} records parsed from `{filename}`")

    # Summary metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Closed Trades", len(closed))
    c2.metric("Open Positions", len(open_pos))
    c3.metric("Dividends/Income", len(dividends))
    if closed:
        total_pnl = sum(t["pnl"] for t in closed)
        wins = sum(1 for t in closed if t["pnl"] > 0)
        c4.metric("Net P&L", f"{total_pnl:+,.2f}")
        c5.metric("Win Rate", f"{wins/len(closed)*100:.0f}%")

    # CMC-specific extra info
    if broker == "CMC Markets" and closed:
        symbols = sorted(set(t["symbol"] for t in closed))
        st.caption(f"Symbols traded: {', '.join(symbols)}")

    st.divider()

    # Preview tabs
    tab_closed, tab_open, tab_div = st.tabs([
        f"Closed Trades ({len(closed)})",
        f"Open Positions ({len(open_pos)})",
        f"Dividends ({len(dividends)})",
    ])

    with tab_closed:
        if closed:
            df = pd.DataFrame(closed)
            show_cols = ["symbol", "direction", "entry_price", "exit_price",
                         "entry_time", "exit_time", "quantity", "pnl", "commission"]
            avail = [c for c in show_cols if c in df.columns]
            df_show = df[avail].copy()
            df_show["pnl"] = pd.to_numeric(df_show["pnl"], errors="coerce").round(2)
            df_show["entry_price"] = pd.to_numeric(df_show["entry_price"], errors="coerce").round(4)
            df_show["exit_price"]  = pd.to_numeric(df_show["exit_price"],  errors="coerce").round(4)
            st.dataframe(df_show, use_container_width=True, height=300, hide_index=True,
                column_config={
                    "pnl": st.column_config.NumberColumn("P&L", format="%.2f"),
                    "entry_price": st.column_config.NumberColumn("Entry", format="%.4f"),
                    "exit_price":  st.column_config.NumberColumn("Exit",  format="%.4f"),
                })
        else:
            st.caption("No closed trades.")

    with tab_open:
        if open_pos:
            df = pd.DataFrame(open_pos)
            show_cols = ["symbol", "direction", "entry_price", "entry_time", "quantity"]
            avail = [c for c in show_cols if c in df.columns]
            st.dataframe(df[avail], use_container_width=True, hide_index=True)
        else:
            st.caption("No open positions.")

    with tab_div:
        if dividends:
            df = pd.DataFrame(dividends)
            show_cols = ["symbol", "entry_time", "pnl"]
            avail = [c for c in show_cols if c in df.columns]
            st.dataframe(df[avail].rename(columns={"pnl": "amount", "entry_time": "date"}),
                         use_container_width=True, hide_index=True)
        else:
            st.caption("No dividends/income entries.")

    st.divider()

    # Import options
    col1, col2, col3 = st.columns([2, 2, 3])
    with col1:
        import_divs = st.checkbox("Include dividends as trades", value=False,
                                   help="Dividend payments will be stored as income entries")
    with col2:
        import_open = st.checkbox("Include open positions", value=True,
                                   help="Unclosed positions will be stored as open trades")

    trades_to_import = [t for t in real_trades
                        if t["status"] == "closed"
                        or (t["status"] == "open" and import_open)]
    if import_divs:
        trades_to_import += dividends

    st.caption(f"Ready to import: **{len(trades_to_import)}** records")

    if st.button(f"💾 Import {len(trades_to_import)} records", type="primary"):
        new_count = updated_count = 0
        progress = st.progress(0)
        for i, trade in enumerate(trades_to_import):
            clean = {k: v for k, v in trade.items()
                     if k not in ("trade_type", "currency")}
            if account_id:
                clean["account_id"] = account_id
            tid, is_new = upsert_trade_from_broker(clean)
            new_count     += int(is_new)
            updated_count += int(not is_new)
            progress.progress((i + 1) / len(trades_to_import))

        execute(
            "INSERT INTO import_history (broker, filename, trades_imported) VALUES (?,?,?)",
            ("CMC Markets" if "CMC" in str(trades[0].get("broker","")) else trades[0].get("broker","Unknown"),
             filename, new_count)
        )
        progress.empty()
        st.success(f"✅ Imported **{new_count}** new records, updated **{updated_count}** existing.")
        st.rerun()


def _manual_trade_form():
    from utils.accounts import get_accounts
    accounts = get_accounts()

    with st.form("manual_trade"):
        acc_options = {"— No account —": None} | {f"{a['name']}  ({a['broker']})": a["id"] for a in accounts}
        sel_acc = st.selectbox("Account", list(acc_options.keys()), key="manual_acc")

        c1, c2, c3 = st.columns(3)
        symbol    = c1.text_input("Symbol", placeholder="e.g. BHP / AAPL / EURUSD")
        direction = c2.selectbox("Direction", ["LONG", "SHORT"])
        quantity  = c3.number_input("Quantity / Shares", min_value=0.0, step=0.01, value=1.0)

        c4, c5 = st.columns(2)
        entry_price = c4.number_input("Entry Price", min_value=0.0, step=0.00001, format="%.5f")
        exit_price  = c5.number_input("Exit Price (0 = still open)", min_value=0.0, step=0.00001, format="%.5f")

        c6, c7 = st.columns(2)
        entry_time = c6.text_input("Entry Time", placeholder="2024-01-15 09:30:00")
        exit_time  = c7.text_input("Exit Time",  placeholder="2024-01-15 11:45:00")

        c8, c9, c10 = st.columns(3)
        pnl        = c8.number_input("P&L",        step=0.01)
        commission = c9.number_input("Commission", step=0.01)
        swap       = c10.number_input("Swap",      step=0.01)

        if st.form_submit_button("Add Trade", type="primary"):
            if not symbol:
                st.error("Symbol is required")
            else:
                trade = {
                    "broker": "Manual", "broker_trade_id": None,
                    "symbol": symbol.upper(), "direction": direction,
                    "entry_price": entry_price or None,
                    "exit_price":  exit_price  or None,
                    "entry_time":  entry_time  or None,
                    "exit_time":   exit_time   or None,
                    "quantity": quantity, "pnl": pnl,
                    "commission": commission, "swap": swap, "raw_data": None,
                    "status": "closed" if exit_price > 0 else "open",
                }
                acc_id = acc_options.get(sel_acc)
                if acc_id:
                    trade["account_id"] = acc_id
                upsert_trade_from_broker(trade)
                st.success("Trade added!")
                st.rerun()
