"""
Page: EA Dashboard — multi-account MT5 EA performance overview.
Reads closed trades from the DB (populated by FTP sync) and open positions
from DB status='open' trades. Shows account cards, prop firm bars, open
positions, calendar, and multi-mode trade analysis.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, datetime, timedelta
import calendar

from database import fetch_all
from brokers.mt5_parser import calc_stats


# ── DB → DataFrame ────────────────────────────────────────────────────────────

def _trades_to_df(trades: list[dict]) -> pd.DataFrame:
    """Convert DB trade rows to a DataFrame compatible with calc_stats."""
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    df["open_time"]    = pd.to_datetime(df["entry_time"],  errors="coerce")
    df["close_time"]   = pd.to_datetime(df["exit_time"],   errors="coerce")
    df["type"]         = df["direction"].str.lower().map({"long": "buy", "short": "sell"}).fillna("buy")
    df["volume"]       = pd.to_numeric(df["quantity"],     errors="coerce").fillna(0)
    df["open_price"]   = pd.to_numeric(df["entry_price"],  errors="coerce").fillna(0)
    df["close_price"]  = pd.to_numeric(df["exit_price"],   errors="coerce").fillna(0)
    df["net_profit"]   = pd.to_numeric(df["pnl"],          errors="coerce").fillna(0)
    df["profit"]       = df["net_profit"]
    df["commission"]   = pd.to_numeric(df["commission"],   errors="coerce").fillna(0)
    df["swap"]         = pd.to_numeric(df["swap"],         errors="coerce").fillna(0)
    df["win"]          = df["net_profit"] > 0
    df["day_of_week"]  = df["open_time"].dt.day_name()
    df["hour"]         = df["open_time"].dt.hour
    df["duration_min"] = ((df["close_time"] - df["open_time"])
                          .dt.total_seconds() / 60).round(1)
    df["position"]     = df.get("broker_trade_id", "")
    df["comment"]      = ""
    df["symbol_base"]  = df["symbol"].str.replace(r"\.[a-z]+$", "", regex=True).str.upper()
    return df


def _load_account_data() -> list[dict]:
    """Load EA-type accounts that have an FTP folder configured, with their closed trades."""
    accounts = fetch_all(
        "SELECT * FROM accounts "
        "WHERE account_type = 'EA' "
        "  AND ftp_folder IS NOT NULL AND TRIM(ftp_folder) != '' "
        "ORDER BY name"
    )
    result = []
    for acc in accounts:
        trades = fetch_all(
            "SELECT * FROM trades WHERE account_id=? AND status='closed' ORDER BY exit_time",
            (acc["id"],)
        )
        df = _trades_to_df(trades)
        open_trades = fetch_all(
            "SELECT * FROM trades WHERE account_id=? AND status='open'",
            (acc["id"],)
        )
        result.append({
            "acc":        acc,
            "df":         df,
            "open":       open_trades,
            "stats":      calc_stats(df, deposit=float(acc.get("initial_balance") or 0)) if not df.empty else {},
        })
    return result


# ── Page ──────────────────────────────────────────────────────────────────────

def show():
    st.header("📡 EA Dashboard")

    all_data = _load_account_data()

    if not all_data:
        st.info(
            "No EA accounts with FTP sync configured. "
            "Go to **Settings → Accounts**, set the account type to **EA**, "
            "then assign an FTP folder in **Settings → MT5 FTP**."
        )
        return

    # ── Last sync timestamp ───────────────────────────────────────────────────
    last_sync = fetch_all("SELECT value FROM app_settings WHERE key='ftp_last_sync'")
    if last_sync and last_sync[0]["value"]:
        st.caption(f"Last FTP sync: {last_sync[0]['value']}")

    # ── Account selector ──────────────────────────────────────────────────────
    all_labels = [d["acc"]["name"] for d in all_data]
    sel_labels = st.multiselect("Accounts", all_labels, default=all_labels, key="ea_sel_accounts")
    sel_data   = [d for d in all_data if d["acc"]["name"] in sel_labels]

    if not sel_data:
        st.info("Select at least one account.")
        return

    # ── Account summary cards ─────────────────────────────────────────────────
    _render_account_cards(sel_data)

    # ── Open positions ────────────────────────────────────────────────────────
    _render_open_positions(sel_data)

    # ── Correlation matrix ────────────────────────────────────────────────────
    if len(sel_data) > 1:
        _render_correlation(sel_data)

    # ── Combined DataFrame ────────────────────────────────────────────────────
    dfs = []
    for d in sel_data:
        if d["df"].empty:
            continue
        df = d["df"].copy()
        df["_account"] = d["acc"]["name"]
        df["_balance"] = float(d["acc"].get("initial_balance") or 0)
        dfs.append(df)

    if not dfs:
        st.info("No closed trades found for selected accounts.")
        return

    df_all = pd.concat(dfs, ignore_index=True)
    df_all["close_time"] = pd.to_datetime(df_all["close_time"], errors="coerce")
    df_all["open_time"]  = pd.to_datetime(df_all["open_time"],  errors="coerce")
    df_all = df_all.dropna(subset=["close_time"]).sort_values("close_time").reset_index(drop=True)

    total_balance = sum(float(d["acc"].get("initial_balance") or 0) for d in sel_data)

    # ── Calendar ──────────────────────────────────────────────────────────────
    st.divider()
    _render_calendar(df_all, total_balance)

    # ── Trade analysis ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Trade Analysis")
    _render_filters_and_analysis(df_all, sel_data, all_labels, sel_labels)


# ── Account cards ─────────────────────────────────────────────────────────────

def _render_account_cards(sel_data: list[dict]):
    cards_html = []
    today = date.today()

    for d in sel_data:
        acc      = d["acc"]
        df       = d["df"]
        stats    = d["stats"]
        balance  = float(acc.get("initial_balance") or 0)
        acc_type = acc.get("account_type") or "Personal"

        current_pnl = float(stats.get("net_profit", 0)) if stats else 0
        current_bal = balance + current_pnl
        pnl_pct     = round(current_pnl / balance * 100, 2) if balance else 0
        pnl_color   = "#34C27A" if current_pnl >= 0 else "#E05555"

        badge_bg = {
            "Prop Firm": "rgba(255,165,0,0.3)",
            "EA":        "rgba(124,106,247,0.3)",
            "Personal":  "rgba(52,194,122,0.3)",
        }.get(acc_type, "rgba(128,128,128,0.2)")

        # Recovery factor
        max_dd   = stats.get("max_drawdown", 0) if stats else 0
        recovery = round(current_pnl / abs(max_dd), 2) if max_dd != 0 else "—"
        rec_col  = "#34C27A" if isinstance(recovery, float) and recovery >= 1 else "#E05555"

        # Loss streak
        streak = 0
        if not df.empty:
            for _, row in df.sort_values("close_time", ascending=False).iterrows():
                if not row.get("win", True):
                    streak += 1
                else:
                    break
        streak_col = "#E05555" if streak >= 3 else ("#F5A623" if streak >= 1 else "#34C27A")

        # Stagnation days
        stag_days = 0
        if not df.empty:
            df_s = df.sort_values("close_time").copy()
            df_s["_cum"]  = df_s["net_profit"].cumsum()
            df_s["_peak"] = df_s["_cum"].cummax()
            at_peak = df_s[df_s["_cum"] >= df_s["_peak"]]
            if not at_peak.empty:
                last_high = pd.to_datetime(at_peak["close_time"].max())
                stag_days = (datetime.now() - last_high).days
        stag_col = "#E05555" if stag_days >= 14 else ("#F5A623" if stag_days >= 7 else "#34C27A")

        # Today's P&L
        today_pnl = 0.0
        if not df.empty:
            today_df  = df[df["close_time"].dt.date == today]
            today_pnl = float(today_df["net_profit"].sum())
        today_pct = round(today_pnl / balance * 100, 2) if balance else 0
        today_col = "#34C27A" if today_pnl >= 0 else "#E05555"

        # Prop firm bars
        prop_html = ""
        if acc_type == "Prop Firm":
            pt = acc.get("prop_profit_target_pct") or 0
            ml = acc.get("prop_max_loss_pct") or 0
            dl = acc.get("prop_daily_loss_pct") or 0
            dp = acc.get("prop_personal_daily_loss_pct") or 0

            def _bar(label, used_pct, limit_pct, color, val_str):
                w = min(round(used_pct / limit_pct * 100, 1) if limit_pct else 0, 100)
                return (
                    f'<div style="font-size:12px;color:#A0A8B8;margin-bottom:2px">{label}: {val_str}</div>'
                    f'<div style="background:rgba(128,128,128,0.12);border-radius:3px;height:5px;margin-bottom:5px">'
                    f'<div style="background:{color};width:{w}%;height:100%;border-radius:3px"></div></div>'
                )

            if pt:
                prop_html += _bar(f"Profit target", max(pnl_pct, 0), pt, "#34C27A",
                                  f"{pnl_pct:+.2f}% / {pt:.0f}%")
            if ml:
                prop_html += _bar(f"Max loss", max(-pnl_pct, 0), ml, "#E05555",
                                  f"{max(-pnl_pct,0):.2f}% / {ml:.0f}%")
            if dl:
                dl_col = "#E05555" if (-today_pct) >= dl * 0.8 else ("#F5A623" if (-today_pct) >= dl * 0.5 else "#34C27A")
                prop_html += _bar("Firm daily", max(-today_pct, 0), dl, dl_col,
                                  f"{today_pct:.2f}% / -{dl:.0f}%")
            if dp:
                dp_col = "#E05555" if (-today_pct) >= dp else "#34C27A"
                prop_html += _bar("My guardrail", max(-today_pct, 0), dp, dp_col,
                                  f"{today_pct:.2f}% / -{dp:.0f}%")
            if dl and (-today_pct) >= dl:
                prop_html += (
                    '<div style="background:rgba(220,80,80,0.15);border:1px solid rgba(220,80,80,0.4);'
                    'border-radius:4px;padding:5px 8px;margin-top:4px;font-size:11px;font-weight:600;color:#E05555">'
                    '⛔ Firm daily limit reached</div>'
                )
            elif dp and (-today_pct) >= dp:
                prop_html += (
                    '<div style="background:rgba(220,80,80,0.15);border:1px solid rgba(220,80,80,0.4);'
                    'border-radius:4px;padding:5px 8px;margin-top:4px;font-size:11px;font-weight:600;color:#E05555">'
                    '🛑 Personal guardrail hit — stop trading today</div>'
                )

        card = (
            '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);'
            'border-radius:8px;padding:14px 16px;flex:1;min-width:230px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
            f'<span style="font-size:15px;font-weight:600">{acc["name"]}</span>'
            f'<span style="font-size:11px;padding:2px 8px;border-radius:4px;background:{badge_bg};font-weight:600">{acc_type}</span>'
            '</div>'
            f'<div style="font-size:13px;color:#A0A8B8;margin-bottom:6px">{acc["broker"]}</div>'
            f'<div style="font-size:20px;font-weight:700;color:{pnl_color}">{current_pnl:+,.2f} ({pnl_pct:+.2f}%)</div>'
            f'<div style="font-size:12px;color:#A0A8B8;margin-top:2px">'
            f'Balance: ${balance:,.0f}  →  Current: ${current_bal:,.2f}</div>'
            f'<div style="display:flex;gap:12px;margin-top:8px;flex-wrap:wrap">'
            f'<span style="font-size:12px;color:#A0A8B8">Recovery: <b style="color:{rec_col}">{recovery}</b></span>'
            f'<span style="font-size:12px;color:#A0A8B8">L-streak: <b style="color:{streak_col}">{streak}</b></span>'
            f'<span style="font-size:12px;color:#A0A8B8">Stagnation: <b style="color:{stag_col}">{stag_days}d</b></span>'
            f'<span style="font-size:12px;color:#A0A8B8">Today: <b style="color:{today_col}">{today_pnl:+.2f} ({today_pct:+.2f}%)</b></span>'
            '</div>'
            + (f'<div style="margin-top:10px">{prop_html}</div>' if prop_html else "")
            + '</div>'
        )
        cards_html.append(card)

    st.markdown(
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">'
        + "".join(cards_html) + '</div>',
        unsafe_allow_html=True,
    )


# ── Open positions ────────────────────────────────────────────────────────────

def _render_open_positions(sel_data: list[dict]):
    rows = []
    for d in sel_data:
        for t in d["open"]:
            rows.append({
                "Account":    d["acc"]["name"],
                "Symbol":     t.get("symbol", ""),
                "Direction":  t.get("direction", ""),
                "Volume":     t.get("quantity", ""),
                "Open Price": t.get("entry_price", ""),
                "Entry Time": t.get("entry_time", ""),
                "Swap":       t.get("swap", 0),
                "Unrealised": t.get("pnl", 0),
            })
    if rows:
        with st.expander(f"🔴 Open Positions ({len(rows)})", expanded=True):
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No open positions.")


# ── Correlation matrix ────────────────────────────────────────────────────────

def _render_correlation(sel_data: list[dict]):
    with st.expander("📊 Symbol Correlation across Accounts", expanded=False):
        corr_rows = []
        for d in sel_data:
            if d["df"].empty:
                continue
            by_sym = d["df"].groupby("symbol")["net_profit"].sum()
            by_sym.name = d["acc"]["name"]
            corr_rows.append(by_sym)

        if len(corr_rows) < 2:
            st.caption("Need at least two accounts with trades.")
            return

        corr_df = pd.DataFrame(corr_rows).T.fillna(0)
        if corr_df.shape[1] < 2 or len(corr_df) < 3:
            st.caption("Not enough shared symbols to compute correlation.")
            return

        corr_matrix = corr_df.corr().round(2)
        labels = corr_matrix.columns.tolist()
        z      = corr_matrix.values.tolist()
        fig = go.Figure(go.Heatmap(
            z=z, x=labels, y=labels,
            colorscale=[[0, "#E05555"], [0.5, "#444"], [1, "#34C27A"]],
            zmin=-1, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in z],
            texttemplate="%{text}",
        ))
        fig.update_layout(
            height=300, title="Account Correlation (by symbol P&L)",
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="sans-serif"),
            margin=dict(l=80, r=20, t=40, b=80),
        )
        st.plotly_chart(fig, use_container_width=True, key="ea_corr")


# ── Calendar ──────────────────────────────────────────────────────────────────

def _render_calendar(df_all: pd.DataFrame, total_balance: float):
    st.subheader("Calendar")

    c1, c2, c3 = st.columns([2, 2, 2])
    cal_view = c1.radio("View", ["Month", "Week", "Year"], horizontal=True, key="ea_cal_view")
    cal_unit = c2.radio("Unit",  ["$", "%"],               horizontal=True, key="ea_cal_unit")
    cal_bal  = c3.number_input("Balance ($) for %",
                               value=float(total_balance or 1),
                               min_value=1.0, step=1000.0, format="%.0f",
                               key="ea_cal_balance")

    today = date.today()
    if "ea_cal_y" not in st.session_state:
        st.session_state["ea_cal_y"] = today.year
        st.session_state["ea_cal_m"] = today.month
        st.session_state["ea_cal_w"] = today.isocalendar()[1]

    nav1, nav2, nav3 = st.columns([1, 4, 1])
    with nav1:
        if st.button("◀", key="ea_prev"):
            if cal_view == "Month":
                m = st.session_state["ea_cal_m"] - 1
                if m < 1: m = 12; st.session_state["ea_cal_y"] -= 1
                st.session_state["ea_cal_m"] = m
            elif cal_view == "Week":
                w = st.session_state["ea_cal_w"] - 1
                if w < 1: st.session_state["ea_cal_y"] -= 1; w = 52
                st.session_state["ea_cal_w"] = w
            else:
                st.session_state["ea_cal_y"] -= 1
            st.rerun()
    with nav3:
        if st.button("▶", key="ea_next"):
            if cal_view == "Month":
                m = st.session_state["ea_cal_m"] + 1
                if m > 12: m = 1; st.session_state["ea_cal_y"] += 1
                st.session_state["ea_cal_m"] = m
            elif cal_view == "Week":
                w = st.session_state["ea_cal_w"] + 1
                if w > 52: st.session_state["ea_cal_y"] += 1; w = 1
                st.session_state["ea_cal_w"] = w
            else:
                st.session_state["ea_cal_y"] += 1
            st.rerun()
    with nav2:
        sel_y, sel_m, sel_w = (st.session_state["ea_cal_y"],
                               st.session_state["ea_cal_m"],
                               st.session_state["ea_cal_w"])
        if cal_view == "Month":
            label = f"{calendar.month_name[sel_m]} {sel_y}"
        elif cal_view == "Week":
            label = f"Week {sel_w} — {sel_y}"
        else:
            label = str(sel_y)
        st.markdown(f"<h3 style='text-align:center;margin:4px 0'>{label}</h3>",
                    unsafe_allow_html=True)

    # Daily aggregates
    df_all["_day"] = df_all["close_time"].dt.date
    day_agg = df_all.groupby("_day").agg(
        pnl_dollar=("net_profit", "sum"),
        trades=("net_profit", "count"),
        wins=("win", "sum"),
    ).reset_index()
    day_agg["losses"]  = day_agg["trades"] - day_agg["wins"]
    day_agg["pnl_pct"] = (day_agg["pnl_dollar"] / cal_bal * 100).round(3)
    day_map = {row["_day"]: row for _, row in day_agg.iterrows()}

    # Period summary
    if cal_view == "Month":
        period_days = [d for d in day_map if d.year == sel_y and d.month == sel_m]
    elif cal_view == "Week":
        period_days = [d for d in day_map
                       if d.isocalendar()[0] == sel_y and d.isocalendar()[1] == sel_w]
    else:
        period_days = [d for d in day_map if d.year == sel_y]

    p = day_agg[day_agg["_day"].isin(period_days)]
    tot_pnl = p["pnl_dollar"].sum()
    tot_pct = p["pnl_pct"].sum()
    tot_tr  = int(p["trades"].sum())
    tot_w   = int(p["wins"].sum())
    tot_l   = int(p["losses"].sum())
    wr      = round(tot_w / tot_tr * 100, 1) if tot_tr else 0

    sc = st.columns(6)
    sc[0].metric("P&L ($)",       f"${tot_pnl:,.2f}")
    sc[1].metric("P&L (%)",       f"{tot_pct:+.2f}%")
    sc[2].metric("Trades",        tot_tr)
    sc[3].metric("Win Rate",      f"{wr}%")
    sc[4].metric("Wins / Losses", f"{tot_w} / {tot_l}")
    sc[5].metric("Trading Days",  len(p))

    st.markdown("<br>", unsafe_allow_html=True)

    if cal_view == "Month":
        _render_month_grid(sel_y, sel_m, day_map, today, cal_unit, cal_bal)
    elif cal_view == "Week":
        _render_week_grid(sel_y, sel_w, day_map, today, cal_unit, cal_bal)
    else:
        _render_year_grid(sel_y, day_map, today, cal_unit, cal_bal)


# ── Trade analysis ────────────────────────────────────────────────────────────

def _render_filters_and_analysis(df_all, sel_data, all_labels, sel_labels):
    fc1, fc2, fc3, fc4 = st.columns(4)

    valid_times = df_all["open_time"].dropna()
    d_min = valid_times.min().date()
    d_max = valid_times.max().date()

    with fc1:
        date_from = st.date_input("From", value=d_min, min_value=d_min, max_value=d_max, key="ea_from")
        date_to   = st.date_input("To",   value=d_max, min_value=d_min, max_value=d_max, key="ea_to")
    with fc2:
        syms    = sorted(df_all["symbol"].dropna().unique())
        sel_sym = st.multiselect("Symbol", syms, key="ea_sym")
    with fc3:
        sel_days = st.multiselect("Day of week",
                                  ["Monday","Tuesday","Wednesday","Thursday","Friday"],
                                  key="ea_days")
        sel_type = st.multiselect("Direction", ["buy", "sell"], key="ea_type")
    with fc4:
        sel_accs = st.multiselect("Account", all_labels, default=sel_labels, key="ea_acc_filter")
        deposit  = st.number_input(
            "Balance ($)",
            value=float(sum(float(d["acc"].get("initial_balance") or 0)
                            for d in sel_data if d["acc"]["name"] in (sel_accs or sel_labels))),
            min_value=1.0, step=1000.0, format="%.0f", key="ea_deposit"
        )

    df = df_all.copy()
    df = df[(df["open_time"].dt.date >= date_from) & (df["open_time"].dt.date <= date_to)]
    if sel_sym:  df = df[df["symbol"].isin(sel_sym)]
    if sel_days: df = df[df["day_of_week"].isin(sel_days)]
    if sel_type: df = df[df["type"].isin(sel_type)]
    if sel_accs: df = df[df["_account"].isin(sel_accs)]
    df = df.reset_index(drop=True)

    st.caption(f"**{len(df)}** trades · Balance: **${deposit:,.0f}**")

    if df.empty:
        st.info("No trades match the current filters.")
        return

    mode = st.radio("Analysis mode",
                    ["Overall", "By Account", "By Symbol", "By Day of Week"],
                    horizontal=True, key="ea_mode")
    st.divider()

    if mode == "Overall":
        _render_analysis(df, calc_stats(df, deposit=deposit), deposit, "ea_overall")

    elif mode == "By Account":
        rows = []
        for a in sorted(df["_account"].dropna().unique()):
            s = calc_stats(df[df["_account"] == a],
                           deposit=next((float(d["acc"].get("initial_balance") or 0)
                                         for d in sel_data if d["acc"]["name"] == a), 0))
            rows.append({"Account": a, "Trades": s["total_trades"],
                         "Net P&L": s["net_profit"], "Win Rate %": s["win_rate"],
                         "Profit Factor": s["profit_factor"],
                         "Expectancy": s["expectancy"], "Max DD": s["max_drawdown"]})
        st.dataframe(pd.DataFrame(rows).sort_values("Net P&L", ascending=False),
                     use_container_width=True, hide_index=True)
        st.divider()
        sel = st.selectbox("Account detail", sorted(df["_account"].dropna().unique()), key="ea_acc_sel")
        if sel:
            sub = df[df["_account"] == sel]
            _render_analysis(sub, calc_stats(sub, deposit=deposit), deposit, f"ea_acc_{sel}")

    elif mode == "By Symbol":
        rows = []
        for s in sorted(df["symbol"].dropna().unique()):
            st_ = calc_stats(df[df["symbol"] == s], deposit=deposit)
            rows.append({"Symbol": s, "Trades": st_["total_trades"],
                         "Net P&L": st_["net_profit"], "Win Rate %": st_["win_rate"],
                         "Profit Factor": st_["profit_factor"],
                         "Expectancy": st_["expectancy"], "Max DD": st_["max_drawdown"]})
        st.dataframe(pd.DataFrame(rows).sort_values("Net P&L", ascending=False),
                     use_container_width=True, hide_index=True)
        sel = st.selectbox("Symbol detail", sorted(df["symbol"].dropna().unique()), key="ea_sym_sel")
        if sel:
            sub = df[df["symbol"] == sel]
            _render_analysis(sub, calc_stats(sub, deposit=deposit), deposit, f"ea_sym_{sel}")

    elif mode == "By Day of Week":
        _render_dow(df, "ea_dow")
        _render_hour(df, "ea_hour")


# ── Analysis sub-renderers ────────────────────────────────────────────────────

def _render_analysis(df, stats, deposit, key_prefix):
    _render_stats(stats)
    _render_equity(df, key_prefix)
    col1, col2 = st.columns(2)
    with col1:
        _render_dow(df, key_prefix)
    with col2:
        _render_hour(df, key_prefix)
    st.divider()
    _render_monthly(df, deposit, key_prefix)


def _render_stats(stats):
    LAYOUT = dict(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                  font=dict(family="sans-serif"))

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Net Profit",    f"${stats.get('net_profit', 0):,.2f}")
    c2.metric("Win Rate",      f"{stats.get('win_rate', 0)}%")
    c3.metric("Profit Factor", f"{stats.get('profit_factor', 0)}")
    c4.metric("R:R Ratio",     f"{stats.get('rr_ratio', 0)}")
    c5.metric("Expectancy",    f"${stats.get('expectancy', 0):,.2f}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Trades",  stats.get("total_trades", 0))
    c2.metric("Avg Win",       f"${stats.get('avg_win', 0):,.2f}")
    c3.metric("Avg Loss",      f"${stats.get('avg_loss', 0):,.2f}")
    dd_abs = stats.get("max_drawdown", 0)
    dd_pct = stats.get("max_drawdown_pct", 0)
    c4.metric("Max DD",        f"${dd_abs:,.2f} ({abs(dd_pct):.2f}%)")
    c5.metric("Best Trade",    f"${stats.get('best_trade', 0):,.2f}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Max Consec W",  stats.get("max_consec_wins", 0))
    c2.metric("Max Consec L",  stats.get("max_consec_losses", 0))
    c3.metric("Trading Days",  stats.get("trading_days", 0))
    c4.metric("Trades/Day",    stats.get("trades_per_day", 0))
    c5.metric("Worst Trade",   f"${stats.get('worst_trade', 0):,.2f}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Long Trades",   stats.get("long_trades", 0))
    c2.metric("Long WR",       f"{stats.get('long_win_rate', 0)}%")
    c3.metric("Short Trades",  stats.get("short_trades", 0))
    c4.metric("Short WR",      f"{stats.get('short_win_rate', 0)}%")


def _render_equity(df, key_prefix):
    df_s = df.sort_values("close_time").copy()
    df_s["_cum"]  = df_s["net_profit"].cumsum()
    df_s["_peak"] = df_s["_cum"].cummax()
    df_s["_dd"]   = df_s["_cum"] - df_s["_peak"]

    dd_unit = st.radio("Drawdown unit", ["$", "%"], horizontal=True,
                       key=f"{key_prefix}_dd_unit")
    if dd_unit == "%":
        peak_safe = df_s["_peak"].replace(0, float("nan"))
        dd_vals   = (df_s["_dd"] / peak_safe * 100).fillna(0)
        dd_pfx, dd_sfx = "", "%"
    else:
        dd_vals   = df_s["_dd"]
        dd_pfx, dd_sfx = "$", ""

    BASE = dict(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font=dict(family="sans-serif"),
                xaxis=dict(gridcolor="rgba(128,128,128,0.15)"),
                yaxis=dict(gridcolor="rgba(128,128,128,0.15)"))

    fig_eq = go.Figure(go.Scatter(
        x=df_s["close_time"], y=df_s["_cum"], mode="lines",
        line=dict(color="#7c6af7", width=2, shape="spline", smoothing=0.6),
        fill="tozeroy", fillcolor="rgba(124,106,247,0.08)"))
    fig_eq.update_layout(height=300, title="Equity Curve", hovermode="x unified",
                         margin=dict(l=60, r=20, t=40, b=40),
                         yaxis=dict(tickprefix="$", gridcolor="rgba(128,128,128,0.15)"),
                         **{k: v for k, v in BASE.items() if k not in ("xaxis","yaxis")},
                         xaxis=BASE["xaxis"])
    st.plotly_chart(fig_eq, use_container_width=True, key=f"{key_prefix}_eq")

    fig_dd = go.Figure(go.Scatter(
        x=df_s["close_time"], y=dd_vals, mode="lines",
        fill="tozeroy",
        line=dict(color="rgba(220,80,80,0.8)", width=1.5, shape="spline", smoothing=0.6),
        fillcolor="rgba(220,80,80,0.15)",
        hovertemplate=f"%{{x}}<br>DD: {dd_pfx}%{{y:.2f}}{dd_sfx}<extra></extra>"))
    fig_dd.update_layout(height=130, showlegend=False,
                         xaxis=dict(gridcolor="rgba(128,128,128,0.15)", showticklabels=False),
                         yaxis=dict(gridcolor="rgba(128,128,128,0.15)",
                                    tickprefix=dd_pfx, ticksuffix=dd_sfx),
                         plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                         font=dict(family="sans-serif"),
                         margin=dict(l=60, r=20, t=8, b=4))
    st.plotly_chart(fig_dd, use_container_width=True, key=f"{key_prefix}_dd")

    # Daily P&L bars
    daily = df_s.groupby(df_s["close_time"].dt.date)["net_profit"].sum().reset_index()
    daily.columns = ["date", "pnl"]
    fig_d = go.Figure(go.Bar(
        x=[str(d) for d in daily["date"]], y=daily["pnl"].round(2).tolist(),
        marker_color=["rgba(52,194,122,0.85)" if v >= 0 else "rgba(220,80,80,0.85)"
                      for v in daily["pnl"]]))
    fig_d.update_layout(height=160, showlegend=False,
                        xaxis=dict(type="category", gridcolor="rgba(128,128,128,0.15)",
                                   showticklabels=False),
                        yaxis=dict(gridcolor="rgba(128,128,128,0.15)",
                                   tickprefix="$", zeroline=True,
                                   zerolinecolor="rgba(128,128,128,0.3)"),
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="sans-serif"),
                        margin=dict(l=60, r=20, t=4, b=40))
    st.plotly_chart(fig_d, use_container_width=True, key=f"{key_prefix}_daily")


def _render_dow(df, key_prefix):
    order  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    pres   = [d for d in order if d in df["day_of_week"].values]
    wins   = df[df["win"]].groupby("day_of_week")["net_profit"].sum().reindex(pres, fill_value=0)
    losses = df[~df["win"]].groupby("day_of_week")["net_profit"].sum().reindex(pres, fill_value=0)
    fig = go.Figure([
        go.Bar(x=pres, y=wins.values,   name="Profit", marker_color="rgba(52,194,122,0.85)"),
        go.Bar(x=pres, y=losses.values, name="Loss",   marker_color="rgba(220,80,80,0.85)"),
    ])
    fig.update_layout(height=260, title="P&L by Day of Week", barmode="relative",
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="sans-serif"),
                      xaxis=dict(type="category", gridcolor="rgba(128,128,128,0.15)"),
                      yaxis=dict(gridcolor="rgba(128,128,128,0.15)", tickprefix="$"),
                      legend=dict(bgcolor="rgba(0,0,0,0)"),
                      margin=dict(l=60, r=20, t=40, b=40))
    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_dow")


def _render_hour(df, key_prefix):
    hours  = sorted(df["hour"].dropna().unique())
    sh     = [str(int(h)) for h in hours]
    wins   = df[df["win"]].groupby("hour")["net_profit"].sum().reindex(hours, fill_value=0)
    losses = df[~df["win"]].groupby("hour")["net_profit"].sum().reindex(hours, fill_value=0)
    fig = go.Figure([
        go.Bar(x=sh, y=wins.values,   name="Profit", marker_color="rgba(52,194,122,0.85)"),
        go.Bar(x=sh, y=losses.values, name="Loss",   marker_color="rgba(220,80,80,0.85)"),
    ])
    fig.update_layout(height=260, title="P&L by Hour of Day", barmode="relative",
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="sans-serif"),
                      xaxis=dict(type="category", title="Hour (UTC)",
                                 gridcolor="rgba(128,128,128,0.15)"),
                      yaxis=dict(gridcolor="rgba(128,128,128,0.15)", tickprefix="$"),
                      legend=dict(bgcolor="rgba(0,0,0,0)"),
                      margin=dict(l=60, r=20, t=40, b=40))
    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_hour")


def _render_monthly(df, deposit, key_prefix):
    tmp = df[["close_time", "net_profit"]].dropna().copy()
    tmp["close_time"] = pd.to_datetime(tmp["close_time"], errors="coerce")
    tmp["year"]  = tmp["close_time"].dt.year
    tmp["month"] = tmp["close_time"].dt.month
    monthly = tmp.groupby(["year", "month"])["net_profit"].sum().reset_index()
    if monthly.empty:
        return

    pivot = monthly.pivot(index="year", columns="month", values="net_profit").fillna(0)
    pivot.columns = [pd.Timestamp(2000, int(m), 1).strftime("%b") for m in pivot.columns]
    pivot["YTD"] = pivot.sum(axis=1)
    pivot = pivot.sort_index(ascending=False)
    order = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","YTD"]
    cols  = [c for c in order if c in pivot.columns]

    c1, _ = st.columns([1, 5])
    toggle = c1.radio("", ["$", "%"], horizontal=True,
                      key=f"{key_prefix}_mt", label_visibility="collapsed")

    def _cell(v):
        pv  = round(v / deposit * 100, 2) if toggle == "%" and deposit else v
        bg  = "rgba(52,194,122,0.18)" if pv > 0 else ("rgba(220,80,80,0.18)" if pv < 0 else "transparent")
        fg  = "#34C27A" if pv > 0 else ("#E05555" if pv < 0 else "#888")
        txt = (f"{pv:+.2f}%" if pv != 0 else "—") if toggle == "%" else (f"{pv:+.2f}" if pv != 0 else "—")
        return (f'<td style="background:{bg};color:{fg};padding:5px 10px;'
                f'text-align:right;font-size:12px;font-family:monospace;'
                f'border-bottom:1px solid rgba(128,128,128,0.1)">{txt}</td>')

    rows_html = ""
    for year, row in pivot[cols].iterrows():
        cells = (f'<td style="padding:5px 10px;font-size:12px;font-weight:600;'
                 f'border-bottom:1px solid rgba(128,128,128,0.1)">{year}</td>')
        for col in cols:
            cells += _cell(row.get(col, 0))
        rows_html += f"<tr>{cells}</tr>"

    hdr = ('<tr><th style="padding:5px 10px;font-size:11px;color:#888;text-align:left;'
           'border-bottom:1px solid rgba(128,128,128,0.2)">Year</th>'
           + "".join(f'<th style="padding:5px 10px;font-size:11px;color:#888;text-align:right;'
                     f'border-bottom:1px solid rgba(128,128,128,0.2)">{c}</th>' for c in cols)
           + "</tr>")

    st.markdown(
        f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        f'<thead>{hdr}</thead><tbody>{rows_html}</tbody></table></div>',
        unsafe_allow_html=True)


# ── Calendar grid helpers ─────────────────────────────────────────────────────

def _cell_html(day_num, row, is_today, unit, balance):
    if row is not None:
        val  = row["pnl_pct"] if unit == "%" else row["pnl_dollar"]
        pos  = val >= 0
        bg   = "rgba(52,194,122,0.15)" if pos else "rgba(220,80,80,0.15)"
        vc   = "#34C27A" if pos else "#E05555"
        disp = f"{'+' if pos else ''}{val:.2f}%" if unit == "%" else f"${val:,.2f}"
        alt  = f"${row['pnl_dollar']:,.2f}" if unit == "%" else f"{row['pnl_pct']:+.2f}%"
        tr   = int(row["trades"])
        content = (
            f'<div style="font-size:13px;font-weight:700;color:{vc}">'
            f'{disp} <span style="font-size:10px;opacity:0.7">({alt})</span></div>'
            f'<div style="font-size:11px;color:#aaa;margin-top:2px">{tr} trade{"s" if tr!=1 else ""}</div>'
            f'<div style="font-size:11px;color:#888">✅{int(row["wins"])} ❌{int(row["losses"])}</div>'
        )
    else:
        bg = "rgba(255,255,255,0.02)"
        content = '<div style="color:#333;font-size:11px">—</div>'

    border = "border:2px solid rgba(124,106,247,0.6);" if is_today \
             else "border:1px solid rgba(255,255,255,0.06);"
    return (
        f'<td style="padding:3px;vertical-align:top">'
        f'<div style="background:{bg};border-radius:6px;{border}'
        f'padding:6px 8px;min-height:80px;min-width:90px">'
        f'<div style="font-size:12px;color:#A0A8B8;margin-bottom:3px">{day_num}</div>'
        f'{content}</div></td>'
    )


def _table_wrap(hdr, body):
    return (
        '<div style="overflow-x:auto"><table style="width:100%;'
        'border-collapse:separate;border-spacing:0">'
        f'<thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table></div>'
    )


def _dow_header():
    days = "".join(
        f'<th style="text-align:center;padding:6px 0;font-size:11px;color:#888;font-weight:500">{d}</th>'
        for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]
    )
    week = ('<th style="text-align:center;padding:6px 8px;font-size:11px;color:#888;'
            'font-weight:500;border-left:1px solid rgba(128,128,128,0.15)">Week</th>')
    return days + week


def _render_month_grid(year, month, day_map, today, unit, balance):
    body = ""
    for week in calendar.monthcalendar(year, month):
        row_html = ""
        for dow in range(5):
            dn = week[dow]
            if dn == 0:
                row_html += '<td style="padding:3px"></td>'
            else:
                d = date(year, month, dn)
                row_html += _cell_html(dn, day_map.get(d), d == today, unit, balance)

        week_days = [date(year, month, week[i]) for i in range(5) if week[i] != 0]
        week_rows = [day_map[d] for d in week_days if d in day_map]
        if week_rows:
            wd = sum(r["pnl_dollar"] for r in week_rows)
            wp = sum(r["pnl_pct"]    for r in week_rows)
            wt = sum(int(r["trades"]) for r in week_rows)
            ww = sum(int(r["wins"])   for r in week_rows)
            wl = sum(int(r["losses"]) for r in week_rows)
            pos  = (wd if unit == "$" else wp) >= 0
            bg   = "rgba(52,194,122,0.12)" if pos else "rgba(220,80,80,0.12)"
            vc   = "#34C27A" if pos else "#E05555"
            disp = f"${wd:,.2f}" if unit == "$" else f"{wp:+.2f}%"
            alt  = f"{wp:+.2f}%" if unit == "$" else f"${wd:,.2f}"
            week_cell = (
                f'<td style="padding:3px;vertical-align:top;border-left:1px solid rgba(128,128,128,0.15)">'
                f'<div style="background:{bg};border-radius:6px;border:1px solid rgba(255,255,255,0.06);'
                f'padding:6px 8px;min-height:80px;min-width:80px">'
                f'<div style="font-size:10px;color:#777;margin-bottom:3px;font-weight:500;text-transform:uppercase">Weekly</div>'
                f'<div style="font-size:13px;font-weight:700;color:{vc}">{disp}</div>'
                f'<div style="font-size:10px;color:{vc};opacity:0.7">({alt})</div>'
                f'<div style="font-size:11px;color:#aaa;margin-top:3px">{wt} trades</div>'
                f'<div style="font-size:11px;color:#888">✅{ww} ❌{wl}</div></div></td>'
            )
        else:
            week_cell = '<td style="padding:3px;border-left:1px solid rgba(128,128,128,0.15)"><div style="min-height:80px"></div></td>'

        body += f"<tr>{row_html}{week_cell}</tr>"
    st.markdown(_table_wrap(_dow_header(), body), unsafe_allow_html=True)


def _render_week_grid(year, week_num, day_map, today, unit, balance):
    jan4  = date(year, 1, 4)
    start = jan4 + timedelta(weeks=week_num - jan4.isocalendar()[1], days=-jan4.weekday())
    days  = [start + timedelta(days=i) for i in range(5)]
    cells = "".join(_cell_html(d.day, day_map.get(d), d == today, unit, balance) for d in days)
    hdr   = "".join(
        f'<th style="text-align:center;padding:6px 4px;font-size:11px;color:#888">'
        f'{["Mon","Tue","Wed","Thu","Fri"][i]}<br>'
        f'<span style="color:#A0A8B8">{days[i].strftime("%d %b")}</span></th>'
        for i in range(5)
    )
    st.markdown(_table_wrap(hdr, f"<tr>{cells}</tr>"), unsafe_allow_html=True)


def _render_year_grid(year, day_map, today, unit, balance):
    hdr = (
        '<th style="padding:5px 10px;font-size:11px;color:#888;text-align:left">Month</th>'
        '<th style="padding:5px 10px;font-size:11px;color:#888;text-align:right">P&L</th>'
        '<th style="padding:5px 10px;font-size:11px;color:#888;text-align:right">Trades</th>'
        '<th style="padding:5px 10px;font-size:11px;color:#888;text-align:right">Win Rate</th>'
        '<th style="padding:5px 10px;font-size:11px;color:#888;text-align:right">Trading Days</th>'
    )
    body = ""
    for m in range(1, 13):
        days_in = [d for d in day_map if d.year == year and d.month == m]
        if not days_in:
            continue
        rows   = [day_map[d] for d in days_in]
        pnl    = sum(r["pnl_dollar"] for r in rows)
        pct    = sum(r["pnl_pct"]    for r in rows)
        trades = sum(int(r["trades"]) for r in rows)
        wins   = sum(int(r["wins"])   for r in rows)
        wr     = round(wins / trades * 100, 1) if trades else 0
        val    = pct if unit == "%" else pnl
        pos    = val >= 0
        bg     = "rgba(52,194,122,0.12)" if pos else "rgba(220,80,80,0.12)"
        fg     = "#34C27A" if pos else "#E05555"
        disp   = f"{val:+.2f}%" if unit == "%" else f"${val:,.2f}"
        body += (
            f'<tr style="border-bottom:1px solid rgba(128,128,128,0.08)">'
            f'<td style="padding:6px 10px;font-size:12px;font-weight:600">{calendar.month_name[m]}</td>'
            f'<td style="padding:6px 10px;text-align:right;background:{bg};color:{fg};font-family:monospace;font-size:12px">{disp}</td>'
            f'<td style="padding:6px 10px;text-align:right;font-size:12px">{trades}</td>'
            f'<td style="padding:6px 10px;text-align:right;font-size:12px">{wr}%</td>'
            f'<td style="padding:6px 10px;text-align:right;font-size:12px">{len(days_in)}</td>'
            f'</tr>'
        )
    st.markdown(
        f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        f'<thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table></div>',
        unsafe_allow_html=True)
