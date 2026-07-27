"""
Page: Dashboard
Layout:
  1. KPI row (always all-time)
  2. P&L Calendar (full width) — Monthly or Weekly heatmap
     Monthly mode: month selector → drives stats panel below
  3. Period Stats Panel (full width) — filtered to selected month or all-time
  4. Equity Curve (full width)
  5. Bottom row: win/loss ring · recent trades · streaks & records
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import date, timedelta
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all
from utils.statistics import get_trade_stats
from utils.trade_ops import get_journal_entries
from utils.theme import get_theme, get_chart_font_color, get_chart_grid_color, _PALETTES


# ── Theme helpers ─────────────────────────────────────────────────────────────

def _theme():
    t = get_theme()
    return t, _PALETTES.get(t, _PALETTES["dark"])

def _chart_layout(**kwargs):
    t, _ = _theme()
    fc   = get_chart_font_color(t)
    gc   = get_chart_grid_color(t)
    base = dict(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=fc, family="JetBrains Mono, sans-serif"),
        margin=dict(l=8, r=8, t=36, b=8),
    )
    base.update(kwargs)
    return base

def _card(label, value, colour=None, sub=None):
    _, p = _theme()
    col = colour or p["--text-primary"]
    sub_html = f'<div style="font-size:0.7rem;color:{p["--text-muted"]};margin-top:1px;">{sub}</div>' if sub else ""
    return (
        f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border"]};'
        f'border-radius:8px;padding:10px 14px;">'
        f'<div style="font-size:0.66rem;color:{p["--text-muted"]};text-transform:uppercase;letter-spacing:0.8px;">{label}</div>'
        f'<div style="font-size:1rem;font-weight:700;color:{col};font-family:\'JetBrains Mono\',monospace;margin-top:2px;">{value}</div>'
        f'{sub_html}</div>'
    )

def _row_card(label, value, colour=None):
    _, p = _theme()
    col = colour or p["--text-primary"]
    return (
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'padding:7px 12px;margin:3px 0;background:{p["--bg-card"]};'
        f'border:1px solid {p["--border"]};border-radius:6px;">'
        f'<span style="font-size:0.84rem;color:{p["--text-secondary"]};">{label}</span>'
        f'<span style="font-family:\'JetBrains Mono\';font-weight:600;color:{col};">{value}</span>'
        f'</div>'
    )

def _build_daily_df(account_id=None):
    """Shared daily P&L dataframe used by calendar and stats."""
    where = "status='closed' AND entry_time IS NOT NULL"
    params = []
    if account_id:
        where += " AND account_id=?"
        params.append(account_id)
    trades = fetch_all(f"SELECT entry_time, pnl, commission, swap FROM trades WHERE {where}", params)
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    df["net"]  = (pd.to_numeric(df["pnl"],        errors="coerce").fillna(0)
                - pd.to_numeric(df["commission"], errors="coerce").fillna(0).abs()
                - pd.to_numeric(df["swap"],       errors="coerce").fillna(0).abs())
    df["date"] = pd.to_datetime(df["entry_time"], errors="coerce").dt.date
    df = df.dropna(subset=["date"])
    daily = df.groupby("date").agg(
        net_pnl=("net", "sum"),
        trades =("net", "count"),
        wins   =("net", lambda x: (x > 0).sum()),
    ).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    return daily

def _account_size(account_id=None) -> float:
    """Return initial_balance for the selected account, or sum across all accounts."""
    if account_id:
        row = fetch_all("SELECT initial_balance FROM accounts WHERE id=?", (account_id,))
        if row and row[0]["initial_balance"] is not None:
            return float(row[0]["initial_balance"]) or 1.0
    # All Accounts — sum of all initial balances
    rows = fetch_all("SELECT SUM(initial_balance) as total FROM accounts")
    total = float(rows[0]["total"] or 0) if rows else 0
    if total > 0:
        return total
    # Last resort: app settings override
    setting = fetch_all("SELECT value FROM app_settings WHERE key='account_balance'")
    return float(setting[0]["value"]) if setting and setting[0]["value"] else 10000.0


# ── Main entry ────────────────────────────────────────────────────────────────

def show():
    from utils.accounts import get_accounts, ensure_default_accounts
    ensure_default_accounts()

    accounts = get_accounts()

    # ── Account selector ─────────────────────────────────────────────────────
    _, p = _theme()
    selected_account_id = None
    selected_acc_obj    = None
    if accounts:
        acc_col, _ = st.columns([2, 5])
        with acc_col:
            # Options as (label, id_or_None) tuples to avoid dict key lookup issues
            acc_entries   = [(f"{a['name']} ({a['broker']})", a["id"]) for a in accounts]
            display_list  = ["All Accounts"] + [lbl for lbl, _ in acc_entries]

            sel_index = st.selectbox(
                "Account",
                range(len(display_list)),
                format_func=lambda i: display_list[i],
                key="dash_account_idx",
                label_visibility="collapsed",
            )
            if sel_index > 0:
                selected_account_id = acc_entries[sel_index - 1][1]
                selected_acc_obj    = accounts[sel_index - 1]

        # Account badge
        if selected_acc_obj:
            pnl_row = fetch_all(
                "SELECT ROUND(SUM(pnl),2) as p FROM trades WHERE account_id=? AND status='closed'",
                (selected_account_id,)
            )
            acc_pnl = float(pnl_row[0]["p"] or 0) if pnl_row else 0
            col = p["--accent"] if acc_pnl >= 0 else p["--danger"]
            st.markdown(
                f'<div style="font-size:0.75rem;color:{p["--text-muted"]};margin:-8px 0 8px;">'
                f'{selected_acc_obj["broker"]} · {selected_acc_obj["currency"]} · '
                f'Net P&L: <span style="color:{col};font-weight:600;">{acc_pnl:+,.2f}</span>'
                f'</div>',
                unsafe_allow_html=True
            )

    # ── Data scoped to selected account ──────────────────────────────────────
    all_stats = get_trade_stats(account_id=selected_account_id)
    if not all_stats:
        _empty_state()
        return

    # Compute account size directly from the loaded account objects — no string-lookup risk
    if selected_acc_obj:
        acct_size = float(selected_acc_obj.get("initial_balance") or 0) or 1.0
    else:
        acct_size = sum(float(a.get("initial_balance") or 0) for a in accounts)
        if acct_size == 0:
            setting = fetch_all("SELECT value FROM app_settings WHERE key='account_balance'")
            acct_size = float(setting[0]["value"]) if setting and setting[0]["value"] else 10000.0

    # ── 1. KPI row ────────────────────────────────────────────────────────────
    _kpi_row(all_stats, selected_account_id)
    st.divider()

    # ── 2. Calendar ───────────────────────────────────────────────────────────
    daily = _build_daily_df(selected_account_id)

    ctl1, ctl2 = st.columns([2, 2])
    with ctl1:
        view_mode = st.radio("", ["Monthly", "Weekly heatmap"],
                             horizontal=True, key="cal_view", label_visibility="collapsed")
    with ctl2:
        if view_mode == "Monthly" and not daily.empty:
            months_available = sorted(daily["date"].dt.to_period("M").unique(), reverse=True)
            month_strs = ["All Time"] + [str(m) for m in months_available]
            sel_month = st.selectbox("", month_strs, key="cal_month", label_visibility="collapsed")
        else:
            sel_month = "All Time"

    if view_mode == "Monthly":
        _monthly_calendar(daily, acct_size)
    else:
        _weekly_heatmap(daily, acct_size)

    # ── 3. Period stats panel ─────────────────────────────────────────────────
    st.divider()
    if view_mode == "Monthly" and sel_month != "All Time":
        period = pd.Period(sel_month, "M")
        p_start = period.to_timestamp().strftime("%Y-%m-%d")
        p_end   = ((period + 1).to_timestamp() - timedelta(days=1)).strftime("%Y-%m-%d")
        period_stats = get_trade_stats(start_date=p_start, end_date=p_end,
                                       account_id=selected_account_id)
        period_label = sel_month
    else:
        period_stats = all_stats
        period_label = "All Time"

    _period_stats_panel(period_stats, period_label, acct_size)
    st.divider()

    # ── 4. Equity curve ───────────────────────────────────────────────────────
    _equity_curve(all_stats)
    st.divider()

    # ── 5. Bottom row ─────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        _performance_ring(all_stats)
    with col2:
        _recent_activity(selected_account_id)
    with col3:
        _streak_widget(all_stats)


# ── Empty state ───────────────────────────────────────────────────────────────

def _empty_state():
    _, p = _theme()
    st.markdown(f"""
    <div style="text-align:center;padding:80px 0 60px;">
        <div style="font-size:3.5rem;margin-bottom:16px;">📈</div>
        <div style="font-size:1.4rem;font-weight:700;color:{p['--text-muted']};margin-bottom:8px;">
            Welcome to Trading Journal
        </div>
        <div style="color:{p['--text-faint']};max-width:400px;margin:0 auto;line-height:1.6;">
            Import your trade history or add trades manually to get started.
        </div>
    </div>""", unsafe_allow_html=True)


# ── 1. KPI Row ────────────────────────────────────────────────────────────────

def _kpi_row(stats, account_id=None):
    prev_stats = get_trade_stats(end_date=str(date.today() - timedelta(days=7)),
                                 account_id=account_id)
    pnl_delta  = stats["net_pnl"] - (prev_stats.get("net_pnl", 0) if prev_stats else 0)
    c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
    c1.metric("Total Trades",  stats["total_trades"])
    c2.metric("Win Rate",      f"{stats['win_rate']}%")
    c3.metric("Net P&L",       f"{stats['net_pnl']:+,.2f}",
              delta=f"{pnl_delta:+,.2f} vs 7d")
    c4.metric("Profit Factor", f"{stats['profit_factor']:.2f}")
    c5.metric("R:R Ratio",     f"{stats['rr_ratio']:.2f}")
    c6.metric("Expectancy",    f"{stats['expectancy']:+,.2f}")
    c7.metric("Max Drawdown",  f"{stats['max_drawdown']:,.2f}")


# ── 2. Calendar ───────────────────────────────────────────────────────────────

def _monthly_calendar(daily: pd.DataFrame, account_size: float):
    if daily.empty:
        st.caption("No data yet.")
        return

    _, p = _theme()
    sel_month_str = st.session_state.get("cal_month", "All Time")

    if sel_month_str == "All Time":
        # Show most recent month by default when All Time is selected
        most_recent = str(daily["date"].dt.to_period("M").max())
        sel_period  = pd.Period(most_recent, "M")
    else:
        sel_period = pd.Period(sel_month_str, "M")

    month_data = daily[daily["date"].dt.to_period("M") == sel_period]
    day_data   = {int(row["date"].day): row for _, row in month_data.iterrows()}

    first_day = sel_period.to_timestamp()
    num_days  = ((sel_period + 1).to_timestamp() - timedelta(days=1)).day
    start_dow = first_day.weekday()  # 0=Mon

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # Month summary bar
    if len(month_data):
        m_pnl  = month_data["net_pnl"].sum()
        m_tr   = int(month_data["trades"].sum())
        m_wins = int(month_data["wins"].sum())
        m_wr   = m_wins / m_tr * 100 if m_tr else 0
        m_pct  = m_pnl / account_size * 100
        pnl_col = p["--accent"] if m_pnl >= 0 else p["--danger"]
        best_day  = month_data.loc[month_data["net_pnl"].idxmax(), "net_pnl"]
        worst_day = month_data.loc[month_data["net_pnl"].idxmin(), "net_pnl"]
        trading_days = len(month_data)

        cols = st.columns(6)
        cols[0].markdown(_card("Month P&L", f"{m_pnl:+,.2f}", colour=pnl_col, sub=f"{m_pct:+.2f}% of acct"), unsafe_allow_html=True)
        cols[1].markdown(_card("Trades",    str(m_tr),                           sub=f"{trading_days} trading days"), unsafe_allow_html=True)
        cols[2].markdown(_card("Win Rate",  f"{m_wr:.0f}%",                      sub=f"{m_wins}W / {m_tr-m_wins}L"), unsafe_allow_html=True)
        cols[3].markdown(_card("Avg/Day",   f"{m_pnl/trading_days:+,.2f}",       sub="per trading day"), unsafe_allow_html=True)
        cols[4].markdown(_card("Best Day",  f"{best_day:+,.2f}",  colour=p["--accent"]), unsafe_allow_html=True)
        cols[5].markdown(_card("Worst Day", f"{worst_day:+,.2f}", colour=p["--danger"]), unsafe_allow_html=True)
        st.markdown("<div style='margin-top:6px;'></div>", unsafe_allow_html=True)

    # Day-of-week headers
    hdr = st.columns(7)
    for i, d in enumerate(day_names):
        hdr[i].markdown(
            f'<div style="text-align:center;font-size:0.7rem;font-weight:600;'
            f'color:{p["--text-muted"]};padding:4px 0 2px;text-transform:uppercase;letter-spacing:1px;">{d}</div>',
            unsafe_allow_html=True
        )

    # Grid
    day = 1
    for week in range(6):
        if day > num_days:
            break
        cols = st.columns(7)
        for dow in range(7):
            if week == 0 and dow < start_dow:
                cols[dow].markdown(
                    f'<div style="min-height:80px;background:{p["--bg-app"]};border-radius:6px;"></div>',
                    unsafe_allow_html=True
                )
            elif day > num_days:
                cols[dow].markdown(
                    f'<div style="min-height:80px;background:{p["--bg-app"]};border-radius:6px;"></div>',
                    unsafe_allow_html=True
                )
            else:
                info = day_data.get(day)
                if info is not None:
                    net    = float(info["net_pnl"])
                    tr     = int(info["trades"])
                    wins   = int(info["wins"])
                    pct    = net / account_size * 100
                    is_pos = net >= 0
                    bg     = "rgba(0,200,150,0.13)" if is_pos else "rgba(255,75,110,0.13)"
                    bord   = p["--accent"] if is_pos else p["--danger"]
                    vc     = p["--accent"] if is_pos else p["--danger"]
                    cols[dow].markdown(
                        f'<div style="min-height:80px;background:{bg};border:1px solid {bord};'
                        f'border-radius:6px;padding:5px 7px;">'
                        f'<div style="color:{p["--text-muted"]};font-size:0.7rem;font-weight:700;">{day}</div>'
                        f'<div style="font-family:\'JetBrains Mono\';font-weight:700;color:{vc};'
                        f'font-size:0.78rem;margin-top:2px;">{net:+,.0f}</div>'
                        f'<div style="color:{p["--text-muted"]};font-size:0.65rem;">{pct:+.1f}%</div>'
                        f'<div style="color:{p["--text-muted"]};font-size:0.62rem;margin-top:1px;">'
                        f'{tr}T · {wins}W {tr-wins}L</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                else:
                    cols[dow].markdown(
                        f'<div style="min-height:80px;background:{p["--bg-card2"]};'
                        f'border:1px solid {p["--border"]};border-radius:6px;padding:5px 7px;">'
                        f'<div style="color:{p["--text-faint"]};font-size:0.7rem;">{day}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                day += 1


def _weekly_heatmap(daily: pd.DataFrame, account_size: float):
    if daily.empty:
        st.caption("No data yet.")
        return

    _, p   = _theme()
    t_name = get_theme()
    fc     = get_chart_font_color(t_name)
    gc     = get_chart_grid_color(t_name)

    d = daily.copy()
    d["week"] = d["date"].dt.isocalendar().week.astype(int)
    d["dow"]  = d["date"].dt.dayofweek
    d["year"] = d["date"].dt.year
    max_d = d["date"].max()
    d = d[d["date"] >= max_d - timedelta(weeks=16)]
    if d.empty:
        st.caption("Not enough data.")
        return

    weeks = sorted(d["week"].unique())
    days  = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    z, text = [], []
    for dow in range(5):
        rz, rt = [], []
        for wk in weeks:
            cell = d[(d["week"] == wk) & (d["dow"] == dow)]
            if cell.empty:
                rz.append(None); rt.append("")
            else:
                r   = cell.iloc[0]
                net = r["net_pnl"]
                pct = net / account_size * 100
                tr  = int(r["trades"]); wins = int(r["wins"])
                rz.append(net)
                rt.append(f"{r['date'].strftime('%b %d')}<br>{net:+,.2f} ({pct:+.1f}%)<br>{tr} trades · {wins}W {tr-wins}L")
        z.append(rz); text.append(rt)

    month_map = {}
    for _, r in d.iterrows():
        if r["week"] not in month_map:
            month_map[r["week"]] = r["date"].strftime("%b")
    x_labels = [month_map.get(w, "") for w in weeks]

    fig = go.Figure(go.Heatmap(
        z=z, x=weeks, y=days, text=text,
        hovertemplate="%{text}<extra></extra>",
        colorscale=[[0.0,"#7b1a2a"],[0.35,"#c0392b"],[0.49,"#888888"],
                    [0.51,"#888888"],[0.65,"#1a6b4a"],[1.0,"#00c896"]],
        zmid=0, xgap=3, ygap=3,
        colorbar=dict(thickness=10, tickfont=dict(color=fc, size=10),
                      title=dict(text="P&L", font=dict(color=fc, size=10))),
    ))
    fig.update_layout(_chart_layout(
        height=210,
        xaxis=dict(tickmode="array", tickvals=weeks, ticktext=x_labels,
                   tickfont=dict(size=10, color=fc), side="top", gridcolor=gc),
        yaxis=dict(tickfont=dict(size=11, color=fc), autorange="reversed", gridcolor=gc),
    ))
    st.plotly_chart(fig, use_container_width=True)


# ── 3. Period stats panel ─────────────────────────────────────────────────────

def _period_stats_panel(stats: dict | None, label: str, account_size: float):
    if not stats:
        st.info(f"No trades found for {label}.")
        return

    _, p   = _theme()
    t_name = get_theme()
    fc     = get_chart_font_color(t_name)
    gc     = get_chart_grid_color(t_name)

    # Section heading
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
        f'<span style="font-size:1.05rem;font-weight:700;color:{p["--text-primary"]};">'
        f'📊 Stats — {label}</span>'
        f'<span style="font-size:0.75rem;color:{p["--text-muted"]};padding:2px 8px;'
        f'background:{p["--bg-card2"]};border-radius:12px;border:1px solid {p["--border"]};">'
        f'{stats["total_trades"]} trades</span>'
        f'</div>',
        unsafe_allow_html=True
    )

    # KPI cards row
    net_pnl = stats["net_pnl"]
    net_pct = net_pnl / account_size * 100
    pnl_col = p["--accent"] if net_pnl >= 0 else p["--danger"]

    kpi_html = "".join([
        _card("Net P&L",       f"{net_pnl:+,.2f}", colour=pnl_col, sub=f"{net_pct:+.2f}% of account"),
        _card("Win Rate",      f"{stats['win_rate']}%",       sub=f"{stats['wins']}W / {stats['losses']}L"),
        _card("Profit Factor", f"{stats['profit_factor']:.2f}"),
        _card("R:R Ratio",     f"{stats['rr_ratio']:.2f}"),
        _card("Expectancy",    f"{stats['expectancy']:+,.2f}", sub="per trade"),
        _card("Avg Win",       f"{stats['avg_win']:+,.2f}",   colour=p["--accent"]),
        _card("Avg Loss",      f"{stats['avg_loss']:+,.2f}",  colour=p["--danger"]),
        _card("Max Drawdown",  f"{stats['max_drawdown']:,.2f}", colour=p["--danger"]),
    ])
    # Render as a CSS grid
    st.markdown(
        f'<div style="display:grid;grid-template-columns:repeat(8,1fr);gap:8px;margin-bottom:12px;">'
        f'{kpi_html}</div>',
        unsafe_allow_html=True
    )

    # Charts row: monthly P&L bars + symbol breakdown
    has_monthly = bool(stats.get("monthly"))
    has_symbols = bool(stats.get("by_symbol"))

    if has_monthly or has_symbols:
        ncols = (1 if has_monthly else 0) + (1 if has_symbols else 0)
        cols  = st.columns(ncols)
        ci    = 0

        if has_monthly:
            m = pd.DataFrame(stats["monthly"])
            colors = [p["--accent"] if v >= 0 else p["--danger"] for v in m["pnl"]]
            fig = go.Figure(go.Bar(
                x=m["month"], y=m["pnl"], marker_color=colors,
                hovertemplate="%{x}: %{y:+,.2f}<extra></extra>",
                text=[f"{v:+,.0f}" for v in m["pnl"]],
                textfont=dict(color=fc, size=10), textposition="outside",
            ))
            fig.update_layout(_chart_layout(
                title="Monthly P&L", height=230,
                xaxis=dict(gridcolor=gc, tickfont=dict(color=fc)),
                yaxis=dict(gridcolor=gc, tickfont=dict(color=fc)),
            ))
            cols[ci].plotly_chart(fig, use_container_width=True)
            ci += 1

        if has_symbols:
            sd = pd.DataFrame(stats["by_symbol"]).sort_values("total_pnl", ascending=True)
            colors2 = [p["--accent"] if v >= 0 else p["--danger"] for v in sd["total_pnl"]]
            fig2 = go.Figure(go.Bar(
                x=sd["total_pnl"], y=sd["symbol"], orientation="h",
                marker_color=colors2,
                text=[f"{v:+,.0f}" for v in sd["total_pnl"]],
                textfont=dict(color=fc, size=10), textposition="outside",
                hovertemplate="%{y}: %{x:+,.2f}<extra></extra>",
            ))
            fig2.update_layout(_chart_layout(
                title="P&L by Symbol", height=230,
                xaxis=dict(gridcolor=gc, tickfont=dict(color=fc)),
                yaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(color=fc)),
                bargap=0.3,
            ))
            cols[ci].plotly_chart(fig2, use_container_width=True)


# ── 4. Equity Curve ───────────────────────────────────────────────────────────

def _equity_curve(stats):
    st.markdown("#### Equity Curve")
    if not stats.get("equity_curve"):
        st.caption("No data yet.")
        return

    _, p   = _theme()
    t_name = get_theme()
    fc     = get_chart_font_color(t_name)
    gc     = get_chart_grid_color(t_name)

    ec = pd.DataFrame(stats["equity_curve"])
    ec["entry_time"] = pd.to_datetime(ec["entry_time"], errors="coerce")
    ec = ec.dropna(subset=["entry_time"]).sort_values("entry_time")
    peak = ec["cumulative_pnl"].cummax()
    ec["drawdown"] = ec["cumulative_pnl"] - peak

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ec["entry_time"], y=ec["drawdown"],
        fill="tozeroy", line=dict(color="rgba(0,0,0,0)", width=0),
        fillcolor="rgba(255,75,110,0.10)", name="Drawdown", yaxis="y2",
        hovertemplate="DD: %{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=ec["entry_time"], y=ec["cumulative_pnl"],
        line=dict(color=p["--accent"], width=2.5),
        fill="tozeroy", fillcolor="rgba(0,200,150,0.06)",
        name="Equity", hovertemplate="P&L: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=ec["entry_time"], y=ec["cumulative_pnl"],
        mode="markers", marker=dict(size=4, color=p["--accent"], opacity=0.45),
        showlegend=False, hovertemplate="%{x|%b %d}<br>%{y:,.2f}<extra></extra>"))

    fig.update_layout(_chart_layout(
        height=260,
        xaxis=dict(gridcolor=gc, tickfont=dict(color=fc)),
        yaxis=dict(title="P&L", gridcolor=gc, tickfont=dict(color=fc)),
        yaxis2=dict(overlaying="y", side="right", showgrid=False,
                    title="DD", tickfont=dict(color=fc)),
        legend=dict(orientation="h", y=1.08, x=0, font=dict(size=11, color=fc)),
        hovermode="x unified",
    ))
    st.plotly_chart(fig, use_container_width=True)


# ── 5a. Performance Ring ──────────────────────────────────────────────────────

def _performance_ring(stats):
    st.markdown("#### Win / Loss")
    _, p   = _theme()
    t_name = get_theme()
    fc     = get_chart_font_color(t_name)

    fig = go.Figure(go.Pie(
        labels=["Wins","Losses","Breakeven"],
        values=[stats["wins"], stats["losses"], stats["breakeven"]],
        hole=0.60,
        marker=dict(colors=[p["--accent"], p["--danger"], p["--text-faint"]],
                    line=dict(color=p["--bg-app"], width=3)),
        textinfo="label+percent",
        textfont=dict(size=10, color=fc),
        hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
    ))
    fig.add_annotation(
        text=f"<b>{stats['win_rate']}%</b>",
        x=0.5, y=0.5, xref="paper", yref="paper",
        showarrow=False, font=dict(size=20, color=p["--text-primary"]), align="center",
    )
    fig.update_layout(_chart_layout(height=200, showlegend=False))
    st.plotly_chart(fig, use_container_width=True)
    c1, c2 = st.columns(2)
    c1.markdown(_card("Avg Win",  f"+{stats['avg_win']:,.2f}",  colour=p["--accent"]),  unsafe_allow_html=True)
    c2.markdown(_card("Avg Loss", f"{stats['avg_loss']:,.2f}",  colour=p["--danger"]),  unsafe_allow_html=True)


# ── 5b. Recent Activity ───────────────────────────────────────────────────────

def _recent_activity(account_id=None):
    st.markdown("#### Recent Trades")
    _, p = _theme()
    where  = "status='closed'" + (" AND account_id=?" if account_id else "")
    params = (account_id,) if account_id else ()
    trades  = fetch_all(f"SELECT * FROM trades WHERE {where} ORDER BY entry_time DESC LIMIT 8", params)
    entries = get_journal_entries()[:3]

    for t in trades:
        net   = float(t.get("pnl") or 0) - abs(float(t.get("commission") or 0))
        col   = p["--accent"] if net >= 0 else p["--danger"]
        sign  = "▲" if net >= 0 else "▼"
        dt    = (t.get("entry_time") or "")[:10]
        st.markdown(
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:5px 10px;margin:2px 0;background:{p["--bg-card"]};'
            f'border:1px solid {p["--border"]};border-radius:6px;">'
            f'<div><span style="font-weight:600;color:{p["--text-primary"]};">{t["symbol"]}</span>'
            f'<span style="font-size:0.72rem;color:{p["--text-muted"]};margin-left:8px;">'
            f'{t["direction"]} · {dt}</span></div>'
            f'<div style="font-family:\'JetBrains Mono\';color:{col};font-weight:600;font-size:0.9rem;">'
            f'{sign} {abs(net):,.2f}</div></div>',
            unsafe_allow_html=True
        )

    if entries:
        st.markdown(
            f'<div style="margin-top:10px;font-size:0.65rem;color:{p["--text-faint"]};'
            f'letter-spacing:1px;text-transform:uppercase;padding:4px 0;">Recent Journal</div>',
            unsafe_allow_html=True
        )
        for j in entries:
            icon  = {"daily":"📅","weekly":"📆","trade":"📊"}.get(j["entry_type"],"📋")
            grade = f' <b style="color:{p["--accent"]};">{j["grade"]}</b>' if j.get("grade") else ""
            st.markdown(
                f'<div style="padding:4px 10px;margin:2px 0;background:{p["--bg-card"]};'
                f'border:1px solid {p["--border"]};border-radius:6px;font-size:0.8rem;">'
                f'{icon} <span style="color:{p["--text-secondary"]};">{j["entry_date"]}</span>'
                f'<span style="color:{p["--text-faint"]};"> {j["entry_type"]}</span>{grade}</div>',
                unsafe_allow_html=True
            )


# ── 5c. Streaks & Records ─────────────────────────────────────────────────────

def _streak_widget(stats):
    st.markdown("#### Records")
    _, p = _theme()
    items = [
        ("🔥 Max Win Streak",  f"{stats['max_win_streak']} trades",   p["--accent"]),
        ("❄️ Max Loss Streak", f"{stats['max_loss_streak']} trades",   p["--danger"]),
        ("🏆 Best Trade",      f"+{stats['best_trade']:,.2f}",          p["--warning"]),
        ("💀 Worst Trade",     f"{stats['worst_trade']:,.2f}",          p["--danger"]),
        ("📉 Max Drawdown",    f"{stats['max_drawdown']:,.2f}",         p["--danger"]),
        ("⚡ Profit Factor",  f"{stats['profit_factor']:.2f}",         p["--accent"]),
    ]
    st.markdown("".join(_row_card(l, v, c) for l, v, c in items), unsafe_allow_html=True)

    # Current streak
    recent = fetch_all("SELECT pnl FROM trades WHERE status='closed' ORDER BY exit_time DESC LIMIT 20")
    if recent:
        streak, direction = 0, None
        for t in recent:
            win = float(t.get("pnl") or 0) > 0
            if direction is None:
                direction, streak = win, 1
            elif win == direction:
                streak += 1
            else:
                break
        label = "🔥 Win streak" if direction else "❄️ Loss streak"
        col   = p["--accent"] if direction else p["--danger"]
        st.markdown(
            f'<div style="margin-top:8px;padding:10px 14px;background:{p["--bg-card"]};'
            f'border:2px solid {col};border-radius:8px;text-align:center;">'
            f'<div style="font-size:0.65rem;color:{p["--text-muted"]};letter-spacing:1px;text-transform:uppercase;">Current</div>'
            f'<div style="font-size:1.2rem;font-weight:700;color:{col};margin-top:2px;">{label}: {streak}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
