"""
Page: Statistics — general trading stats, playbook compliance, risk threshold reporting
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.statistics import get_trade_stats, get_playbook_stats
from utils.playbook_logic import get_playbooks, get_playbook
from database import fetch_all

CHART_STYLE = dict(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#6b7a99"),
    margin=dict(l=4, r=4, t=36, b=4),
)


def show():
    st.header("📊 Reports")

    # Account filter — shared across all tabs
    from utils.accounts import get_accounts
    accounts = get_accounts()
    acc_options = {"All Accounts": None} | {a["name"]: a["id"] for a in accounts}
    sel_acc = st.selectbox("Account", list(acc_options.keys()), key="stats_account",
                           label_visibility="collapsed")
    account_id = acc_options[sel_acc]

    tab_gen, tab_cat, tab_disc, tab_pb, tab_thresh = st.tabs([
        "📈 General", "🏷️ Trade Categories", "📔 Journal Discipline",
        "📖 Playbook Compliance", "⚠️ Risk Threshold Outcomes"
    ])
    with tab_gen:    _general_stats(account_id)
    with tab_cat:    _category_stats(account_id)
    with tab_disc:   _journal_discipline(account_id)
    with tab_pb:     _playbook_stats()
    with tab_thresh: _threshold_stats()


# ── General Stats ─────────────────────────────────────────────────────────────

def _general_stats(account_id=None):
    st.subheader("Trading Performance")
    c1, c2, c3 = st.columns(3)
    sym_where = f"{'AND account_id=' + str(account_id) if account_id else ''}"
    symbols   = [r["symbol"] for r in fetch_all(
        f"SELECT DISTINCT symbol FROM trades WHERE status='closed' {sym_where} ORDER BY symbol"
    )]
    sym   = c1.selectbox("Symbol", ["All"] + symbols, key="gs_sym")
    start = c2.date_input("From", value=None, key="gs_start")
    end   = c3.date_input("To",   value=None, key="gs_end")

    stats = get_trade_stats(
        symbol     =None if sym   == "All" else sym,
        start_date =str(start) if start else None,
        end_date   =str(end)   if end   else None,
        account_id =account_id,
    )
    if not stats:
        st.info("No closed trades yet. Import trades to see statistics.")
        return

    # KPI rows
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Trades",   stats["total_trades"])
    c2.metric("Win Rate",       f"{stats['win_rate']}%")
    c3.metric("Net P&L",        f"{stats['net_pnl']:+,.2f}")
    c4.metric("Profit Factor",  f"{stats['profit_factor']:.2f}")
    c5.metric("Expectancy",     f"{stats['expectancy']:+,.2f}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Avg Win",        f"{stats['avg_win']:+,.2f}")
    c2.metric("Avg Loss",       f"{stats['avg_loss']:+,.2f}")
    c3.metric("R:R Ratio",      f"{stats['rr_ratio']:.2f}")
    c4.metric("Max Drawdown",   f"{stats['max_drawdown']:,.2f}")
    c5.metric("Win Streak",     f"{stats['max_win_streak']} 🔥")
    st.divider()

    # Charts row 1
    col1, col2 = st.columns(2)
    with col1:
        if stats.get("equity_curve"):
            ec = pd.DataFrame(stats["equity_curve"])
            ec["entry_time"] = pd.to_datetime(ec["entry_time"], errors="coerce")
            fig = go.Figure()
            peak = ec["cumulative_pnl"].cummax()
            ec["dd"] = ec["cumulative_pnl"] - peak
            fig.add_trace(go.Scatter(x=ec["entry_time"], y=ec["dd"],
                fill="tozeroy", line=dict(color="rgba(0,0,0,0)"),
                fillcolor="rgba(255,75,110,0.1)", name="Drawdown", yaxis="y2",
                hovertemplate="DD: %{y:.2f}<extra></extra>"))
            fig.add_trace(go.Scatter(x=ec["entry_time"], y=ec["cumulative_pnl"],
                fill="tozeroy", fillcolor="rgba(0,200,150,0.08)",
                line=dict(color="#00c896", width=2), name="Equity",
                hovertemplate="P&L: %{y:,.2f}<extra></extra>"))
            fig.update_layout(**CHART_STYLE, title="Equity Curve", height=280,
                xaxis=dict(gridcolor="#1a2030"),
                yaxis=dict(title="P&L", gridcolor="#1a2030"),
                yaxis2=dict(overlaying="y", side="right", showgrid=False, title="DD"),
                hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        if stats.get("monthly"):
            m = pd.DataFrame(stats["monthly"])
            colors = ["#00c896" if p >= 0 else "#ff4b6e" for p in m["pnl"]]
            fig = go.Figure(go.Bar(x=m["month"], y=m["pnl"],
                marker_color=colors, hovertemplate="%{x}: %{y:+,.2f}<extra></extra>"))
            fig.update_layout(**CHART_STYLE, title="Monthly P&L", height=280,
                xaxis=dict(gridcolor="#1a2030"), yaxis=dict(gridcolor="#1a2030"))
            st.plotly_chart(fig, use_container_width=True)

    # Charts row 2
    col1, col2 = st.columns(2)
    with col1:
        fig = go.Figure(go.Pie(
            labels=["Wins", "Losses", "Breakeven"],
            values=[stats["wins"], stats["losses"], stats["breakeven"]],
            hole=0.5,
            marker=dict(colors=["#00c896","#ff4b6e","#6b7a99"],
                        line=dict(color="rgba(0,0,0,0.1)", width=2)),
            textinfo="label+percent", textfont=dict(size=12),
        ))
        fig.update_layout(**CHART_STYLE, title="Win / Loss Split", height=260, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        if stats.get("by_symbol"):
            sd = pd.DataFrame(stats["by_symbol"]).sort_values("total_pnl")
            colors = ["#00c896" if p >= 0 else "#ff4b6e" for p in sd["total_pnl"]]
            fig = go.Figure(go.Bar(x=sd["total_pnl"], y=sd["symbol"],
                orientation="h", marker_color=colors,
                hovertemplate="%{y}: %{x:+,.2f}<extra></extra>"))
            fig.update_layout(**CHART_STYLE, title="P&L by Symbol", height=260,
                xaxis=dict(gridcolor="#1a2030"), yaxis=dict(gridcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)

    # Tables
    if stats.get("by_symbol"):
        st.subheader("By Symbol")
        sd = pd.DataFrame(stats["by_symbol"])
        sd["win_rate"]  = sd["win_rate"].round(1)
        sd["total_pnl"] = sd["total_pnl"].round(2)
        sd["avg_pnl"]   = sd["avg_pnl"].round(2)
        st.dataframe(sd.sort_values("total_pnl", ascending=False), use_container_width=True, hide_index=True)

    if stats.get("by_direction"):
        st.subheader("Long vs Short")
        dd = pd.DataFrame(stats["by_direction"])
        dd["win_rate"]  = dd["win_rate"].round(1)
        dd["total_pnl"] = dd["total_pnl"].round(2)
        st.dataframe(dd, use_container_width=True, hide_index=True)


# ── Trade Categories (Menaker) ────────────────────────────────────────────────

_CAT_META = {
    1: ("Type 1 · On-plan + Win",  "#00c896"),
    2: ("Type 2 · On-plan + Stop", "#4a9eff"),
    3: ("Type 3 · Off-plan + Loss","#ff4b6e"),
    4: ("Type 4 · Off-plan + Win", "#f5a623"),
}

def _category_stats(account_id=None):
    st.subheader("Trade Categories (Menaker Framework)")
    st.caption("The development goal: grow Type 1/2 (on-plan) and shrink Type 3/4 (off-plan) "
               "month over month — regardless of P&L.")

    acc_frag = "AND t.account_id = ?" if account_id else ""
    params = [account_id] if account_id else []
    rows = fetch_all(
        f"""SELECT je.trade_category as cat, t.pnl, t.commission, t.entry_time, t.symbol
            FROM journal_entries je
            JOIN trades t ON (je.trade_id = t.id OR (je.position_id IS NOT NULL AND je.position_id = t.position_id))
            WHERE je.stage='post' AND je.trade_category IS NOT NULL
              AND t.status='closed' {acc_frag}""",
        params,
    )
    if not rows:
        st.info("No categorised trades yet — set a Trade Category when writing post-trade reviews in the Journal.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    df["net_pnl"] = (pd.to_numeric(df["pnl"], errors="coerce").fillna(0)
                     - pd.to_numeric(df["commission"], errors="coerce").fillna(0).abs())
    df["month"] = df["entry_time"].astype(str).str[:7]

    total = len(df)
    on_plan  = int(df["cat"].isin([1, 2]).sum())
    off_plan = total - on_plan

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Categorised Trades", total)
    c2.metric("On-plan (T1+T2)", f"{on_plan}  ({on_plan/total*100:.0f}%)")
    c3.metric("Off-plan (T3+T4)", f"{off_plan}  ({off_plan/total*100:.0f}%)")
    on_pnl  = df[df["cat"].isin([1, 2])]["net_pnl"].sum()
    off_pnl = df[df["cat"].isin([3, 4])]["net_pnl"].sum()
    c4.metric("On-plan vs Off-plan P&L", f"{on_pnl:+,.0f} / {off_pnl:+,.0f}")

    st.divider()
    col1, col2 = st.columns(2)

    with col1:
        # Monthly stacked counts
        pivot = df.pivot_table(index="month", columns="cat", values="net_pnl",
                               aggfunc="count").fillna(0).sort_index()
        fig = go.Figure()
        for cat in [1, 2, 3, 4]:
            if cat in pivot.columns:
                label, color = _CAT_META[cat]
                fig.add_trace(go.Bar(name=label, x=pivot.index, y=pivot[cat],
                                     marker_color=color,
                                     hovertemplate="%{x}: %{y} trades<extra>" + label + "</extra>"))
        fig.update_layout(**CHART_STYLE, barmode="stack", title="Categories by Month",
                          height=300, xaxis=dict(gridcolor="#1a2030"),
                          yaxis=dict(gridcolor="#1a2030", title="Trades"),
                          legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # P&L by category
        agg = df.groupby("cat").agg(trades=("net_pnl", "count"),
                                    total_pnl=("net_pnl", "sum")).reset_index()
        fig2 = go.Figure(go.Bar(
            x=[_CAT_META[c][0] for c in agg["cat"]],
            y=agg["total_pnl"],
            marker_color=[_CAT_META[c][1] for c in agg["cat"]],
            text=[f"{n} trades" for n in agg["trades"]], textposition="outside",
            hovertemplate="%{x}<br>P&L %{y:+,.2f}<extra></extra>",
        ))
        fig2.update_layout(**CHART_STYLE, title="Net P&L by Category", height=300,
                           xaxis=dict(gridcolor="#1a2030"),
                           yaxis=dict(gridcolor="#1a2030", title="Net P&L"))
        st.plotly_chart(fig2, use_container_width=True)

    # Off-plan trend — the number Menaker says to drive down
    st.subheader("Off-plan Trend")
    monthly = df.groupby("month").apply(
        lambda g: pd.Series({
            "off_plan_pct": g["cat"].isin([3, 4]).mean() * 100,
            "trades": len(g),
        }), include_groups=False,
    ).reset_index().sort_values("month")
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(x=monthly["month"], y=monthly["off_plan_pct"],
                              mode="lines+markers", line=dict(color="#f5a623", width=2),
                              hovertemplate="%{x}: %{y:.0f}% off-plan<extra></extra>"))
    fig3.update_layout(**CHART_STYLE, title="Off-plan % of Trades by Month", height=260,
                       xaxis=dict(gridcolor="#1a2030"),
                       yaxis=dict(gridcolor="#1a2030", title="% off-plan", range=[0, 100]))
    st.plotly_chart(fig3, use_container_width=True)

    # Category table by symbol
    st.subheader("By Symbol")
    sym_tab = df.pivot_table(index="symbol", columns="cat", values="net_pnl",
                             aggfunc="count").fillna(0).astype(int)
    sym_tab.columns = [_CAT_META[c][0].split(" · ")[0] for c in sym_tab.columns]
    sym_tab["Net P&L"] = df.groupby("symbol")["net_pnl"].sum().round(2)
    st.dataframe(sym_tab.reset_index(), use_container_width=True, hide_index=True)


# ── Journal Discipline (grades, mood, journalling compliance) ─────────────────

_GRADE_ORDER = ["A+", "A", "B+", "B", "C+", "C", "D", "F"]

def _journal_discipline(account_id=None):
    st.subheader("Journal Discipline")
    st.caption("Process compliance: did every trade get a pre-trade plan and a post-trade review? "
               "And what do your grades and mood say about execution quality?")

    acc_frag = "AND account_id = ?" if account_id else ""
    params = [account_id] if account_id else []
    trades = fetch_all(
        f"""SELECT id, position_id, symbol, pnl, commission, entry_time
            FROM trades WHERE status='closed' {acc_frag}""",
        params,
    )
    if not trades:
        st.info("No closed trades yet.")
        return

    post_rows = fetch_all(
        """SELECT trade_id, position_id, grade, mood, trade_category
           FROM journal_entries WHERE entry_type='trade' AND stage='post'"""
    )
    pre_rows = fetch_all(
        """SELECT trade_id, position_id
           FROM journal_entries WHERE entry_type='trade' AND stage='pre'"""
    )

    post_by_trade = {r["trade_id"]: r for r in post_rows if r.get("trade_id")}
    post_by_pos   = {r["position_id"]: r for r in post_rows if r.get("position_id")}
    pre_trade_ids = {r["trade_id"] for r in pre_rows if r.get("trade_id")}
    pre_pos_ids   = {r["position_id"] for r in pre_rows if r.get("position_id")}

    rows = []
    for t in trades:
        post = post_by_trade.get(t["id"]) or (post_by_pos.get(t["position_id"]) if t.get("position_id") else None)
        planned = (t["id"] in pre_trade_ids or
                   (t.get("position_id") and t["position_id"] in pre_pos_ids))
        rows.append({
            "id": t["id"], "symbol": t["symbol"],
            "month": (t.get("entry_time") or "")[:7],
            "net_pnl": float(t.get("pnl") or 0) - abs(float(t.get("commission") or 0)),
            "reviewed": post is not None,
            "planned": bool(planned),
            "grade": (post or {}).get("grade") or None,
            "mood": (post or {}).get("mood"),
            "cat": (post or {}).get("trade_category"),
        })
    df = pd.DataFrame(rows)
    total = len(df)

    # ── Compliance KPIs ──────────────────────────────────────────────────────
    n_rev  = int(df["reviewed"].sum())
    n_plan = int(df["planned"].sum())
    n_full = int((df["reviewed"] & df["planned"]).sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Closed Trades", total)
    c2.metric("Pre-trade Plan", f"{n_plan}/{total}  ({n_plan/total*100:.0f}%)")
    c3.metric("Post Review", f"{n_rev}/{total}  ({n_rev/total*100:.0f}%)")
    c4.metric("Fully Journalled", f"{n_full}/{total}  ({n_full/total*100:.0f}%)",
              help="Both a pre-trade plan AND a post-trade review")

    # P&L: planned vs unplanned — does the process pay?
    if 0 < n_plan < total:
        p_pnl = df[df["planned"]]["net_pnl"].mean()
        u_pnl = df[~df["planned"]]["net_pnl"].mean()
        delta_col = "#00c896" if p_pnl >= u_pnl else "#ff4b6e"
        st.markdown(
            f'<div style="font-size:0.85rem;color:#6b7a99;margin:4px 0 0;">'
            f'Avg net P&L with a plan: <b style="color:{delta_col};font-family:JetBrains Mono;">{p_pnl:+,.2f}</b>'
            f' &nbsp;·&nbsp; without: <b style="font-family:JetBrains Mono;">{u_pnl:+,.2f}</b></div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Monthly compliance trend ─────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        m = df.groupby("month").agg(
            planned_pct=("planned", lambda x: x.mean() * 100),
            reviewed_pct=("reviewed", lambda x: x.mean() * 100),
        ).reset_index().sort_values("month")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=m["month"], y=m["planned_pct"], name="Pre-plan %",
                                 mode="lines+markers", line=dict(color="#4a9eff", width=2)))
        fig.add_trace(go.Scatter(x=m["month"], y=m["reviewed_pct"], name="Review %",
                                 mode="lines+markers", line=dict(color="#00c896", width=2)))
        fig.update_layout(**CHART_STYLE, title="Journalling Compliance by Month",
                          height=280, yaxis=dict(gridcolor="#1a2030", range=[0, 105], title="%"),
                          xaxis=dict(gridcolor="#1a2030"),
                          legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Grade distribution + P&L by grade
        gd = df[df["grade"].notna() & (df["grade"] != "")]
        if len(gd):
            agg = gd.groupby("grade").agg(n=("net_pnl", "count"),
                                          avg_pnl=("net_pnl", "mean")).reindex(_GRADE_ORDER).dropna()
            colors = ["#00c896" if g in ("A+", "A", "B+", "B") else
                      ("#f5a623" if g in ("C+", "C") else "#ff4b6e") for g in agg.index]
            fig2 = go.Figure(go.Bar(
                x=agg.index, y=agg["n"], marker_color=colors,
                text=[f"{v:+,.0f} avg" for v in agg["avg_pnl"]], textposition="outside",
                hovertemplate="Grade %{x}: %{y} trades<extra></extra>",
            ))
            fig2.update_layout(**CHART_STYLE, title="Grade Distribution (avg P&L labelled)",
                               height=280, xaxis=dict(gridcolor="#1a2030"),
                               yaxis=dict(gridcolor="#1a2030", title="Trades"))
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.caption("No graded reviews yet.")

    # ── Mood analysis ────────────────────────────────────────────────────────
    md = df[df["mood"].notna()]
    if len(md):
        col1, col2 = st.columns(2)
        with col1:
            fig3 = go.Figure(go.Scatter(
                x=md["mood"], y=md["net_pnl"], mode="markers",
                marker=dict(size=9, color=["#00c896" if p >= 0 else "#ff4b6e" for p in md["net_pnl"]],
                            opacity=0.8),
                hovertemplate="Mood %{x} · P&L %{y:+,.2f}<extra></extra>",
            ))
            fig3.add_hline(y=0, line=dict(color="#6b7a99", dash="dot", width=1), opacity=0.5)
            fig3.update_layout(**CHART_STYLE, title="Mood vs Net P&L", height=280,
                               xaxis=dict(gridcolor="#1a2030", title="Mood (1-10)", dtick=1),
                               yaxis=dict(gridcolor="#1a2030", title="Net P&L"))
            st.plotly_chart(fig3, use_container_width=True)
        with col2:
            # Avg mood by Menaker category
            mc = md[md["cat"].notna()]
            if len(mc):
                agg = mc.groupby("cat")["mood"].mean().reindex([1, 2, 3, 4]).dropna()
                fig4 = go.Figure(go.Bar(
                    x=[_CAT_META[int(c)][0] for c in agg.index], y=agg.values,
                    marker_color=[_CAT_META[int(c)][1] for c in agg.index],
                    hovertemplate="%{x}<br>Avg mood %{y:.1f}<extra></extra>",
                ))
                fig4.update_layout(**CHART_STYLE, title="Avg Mood by Trade Category",
                                   height=280, xaxis=dict(gridcolor="#1a2030"),
                                   yaxis=dict(gridcolor="#1a2030", title="Mood", range=[0, 10]))
                st.plotly_chart(fig4, use_container_width=True)

    # ── Unjournalled trades list ─────────────────────────────────────────────
    missing = df[~(df["reviewed"] & df["planned"])]
    if len(missing):
        with st.expander(f"⚠️ Incomplete journalling ({len(missing)} trades)"):
            show = missing[["id", "symbol", "month", "net_pnl", "planned", "reviewed"]].copy()
            show["planned"]  = show["planned"].map({True: "✅", False: "—"})
            show["reviewed"] = show["reviewed"].map({True: "✅", False: "—"})
            st.dataframe(show.rename(columns={
                "id": "Trade", "symbol": "Symbol", "month": "Month",
                "net_pnl": "Net P&L", "planned": "Pre-plan", "reviewed": "Review",
            }).round(2), use_container_width=True, hide_index=True)


# ── Playbook Compliance ───────────────────────────────────────────────────────

def _playbook_stats():
    st.subheader("Playbook & Rule Compliance")
    playbooks = get_playbooks()
    if not playbooks:
        st.info("No playbooks found.")
        return

    pb_names = {pb["name"]: pb["id"] for pb in playbooks}
    selected = st.selectbox("Playbook", list(pb_names.keys()))
    pb_id    = pb_names[selected]

    stats = get_playbook_stats(pb_id)
    if not stats:
        st.info("No trades linked to this playbook yet.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Trades",        stats.get("total_trades", 0))
    c2.metric("Avg Risk Score",       f"{stats.get('avg_risk_score', 0):.1f} / 100")
    c3.metric("Score ↔ P&L Corr.",   f"{stats.get('score_pnl_correlation', 0):.2f}",
              help="How well quality score predicts P&L. +1 = perfect, 0 = no relationship.")
    st.divider()

    rule_stats = stats.get("rule_stats", [])
    if not rule_stats:
        return

    rs = pd.DataFrame(rule_stats)
    type_colors = {"required": "#ff4b6e", "optional": "#f5a623", "bonus": "#00c896"}

    col1, col2 = st.columns(2)
    with col1:
        fig = go.Figure()
        for rtype in ["required", "optional", "bonus"]:
            sub = rs[rs["type"] == rtype]
            if len(sub):
                fig.add_trace(go.Bar(name=rtype.capitalize(), x=sub["name"],
                    y=sub["compliance_pct"], marker_color=type_colors[rtype],
                    hovertemplate="%{x}<br>Compliance: %{y:.1f}%<extra></extra>"))
        fig.update_layout(**CHART_STYLE, title="Rule Compliance %",
                          xaxis_title="Rule", yaxis_title="Compliance %",
                          yaxis_range=[0, 100], barmode="group", height=300,
                          xaxis=dict(gridcolor="#1a2030"), yaxis=dict(gridcolor="#1a2030"))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(name="P&L when Met",     x=rs["name"], y=rs["pnl_when_met"],
                               marker_color="#00c896",
                               hovertemplate="%{x}<br>P&L: %{y:+,.2f}<extra></extra>"))
        fig2.add_trace(go.Bar(name="P&L when NOT Met", x=rs["name"], y=rs["pnl_when_not_met"],
                               marker_color="#ff4b6e",
                               hovertemplate="%{x}<br>P&L: %{y:+,.2f}<extra></extra>"))
        fig2.update_layout(**CHART_STYLE, title="P&L Impact per Rule",
                           barmode="group", height=300,
                           xaxis=dict(gridcolor="#1a2030"), yaxis=dict(gridcolor="#1a2030"))
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Rule Detail Table")
    st.dataframe(
        rs[["name","type","compliance_pct","met","total","pnl_when_met","pnl_when_not_met"]].round(2),
        use_container_width=True, hide_index=True,
        column_config={
            "name":             st.column_config.TextColumn("Rule"),
            "type":             st.column_config.TextColumn("Type"),
            "compliance_pct":   st.column_config.ProgressColumn("Compliance %", min_value=0, max_value=100, format="%.1f%%"),
            "met":              st.column_config.NumberColumn("Times Met"),
            "total":            st.column_config.NumberColumn("Total Trades"),
            "pnl_when_met":     st.column_config.NumberColumn("P&L When Met",     format="%.2f"),
            "pnl_when_not_met": st.column_config.NumberColumn("P&L When NOT Met", format="%.2f"),
        }
    )


# ── Risk Threshold Outcomes ───────────────────────────────────────────────────

def _threshold_stats():
    st.subheader("Risk Threshold Outcomes")
    st.markdown(
        "How did trades perform when risk thresholds were (or weren't) triggered? "
        "This helps validate whether your thresholds are catching bad trades."
    )

    playbooks = get_playbooks()
    if not playbooks:
        st.info("No playbooks found.")
        return

    pb_names = {pb["name"]: pb["id"] for pb in playbooks}
    selected = st.selectbox("Playbook", list(pb_names.keys()), key="thresh_pb")
    pb_id    = pb_names[selected]
    pb       = get_playbook(pb_id)

    if not pb or not pb.get("risk_rules"):
        st.info("This playbook has no risk thresholds defined yet.")
        return

    trades = fetch_all(
        """SELECT t.id, t.pnl, t.commission, t.risk_score, t.playbook_rules_met,
                  t.entry_time, t.symbol, t.direction
           FROM trades t
           WHERE t.playbook_id=? AND t.status='closed' AND t.playbook_rules_met IS NOT NULL""",
        (pb_id,)
    )
    if not trades:
        st.info("No trades linked to this playbook yet.")
        return

    rules       = pb.get("rules", [])
    risk_rules  = pb.get("risk_rules", [])

    # For each trade determine which risk threshold (if any) would have been triggered
    from utils.playbook_logic import evaluate_trade_risk
    rows = []
    for t in trades:
        try:
            rm = json.loads(t["playbook_rules_met"] or "{}")
        except:
            rm = {}
        net_pnl = float(t.get("pnl") or 0) - abs(float(t.get("commission") or 0))
        result  = evaluate_trade_risk(pb_id, rm)
        triggered = result.get("risk_level", "normal")
        multiplier = result.get("risk_multiplier", 1.0)
        warnings   = "; ".join(result.get("warnings", []))
        rows.append({
            "trade_id":    t["id"],
            "symbol":      t["symbol"],
            "direction":   t["direction"],
            "entry_date":  (t.get("entry_time") or "")[:10],
            "risk_level":  triggered,
            "multiplier":  multiplier,
            "risk_score":  t.get("risk_score") or 0,
            "net_pnl":     round(net_pnl, 2),
            "warning":     warnings[:80] if warnings else "",
        })

    df = pd.DataFrame(rows)

    # Summary by risk level
    st.subheader("Outcomes by Risk Level")
    summary = df.groupby("risk_level").agg(
        trades   =("net_pnl", "count"),
        total_pnl=("net_pnl", "sum"),
        avg_pnl  =("net_pnl", "mean"),
        win_rate =("net_pnl", lambda x: (x > 0).mean() * 100),
        avg_score=("risk_score", "mean"),
    ).reset_index().round(2)

    # Display as styled cards
    rl_icons = {"normal": "🟢", "reduced": "🟡", "no_trade": "🔴"}
    cols = st.columns(len(summary))
    for i, row in summary.iterrows():
        rl = row["risk_level"]
        icon = rl_icons.get(rl, "⚪")
        pnl_col = "#00c896" if row["total_pnl"] >= 0 else "#ff4b6e"
        cols[i].markdown(f"""
        <div style="background:var(--bg-card,#fff);border:1px solid var(--border,#d0d6e8);
                    border-radius:10px;padding:14px;text-align:center;">
            <div style="font-size:1.2rem;">{icon}</div>
            <div style="font-weight:700;color:var(--text-primary,#1a2035);margin:4px 0;">
                {rl.upper().replace('_',' ')}
            </div>
            <div style="font-size:0.8rem;color:var(--text-muted,#6b7a99);">{int(row['trades'])} trades</div>
            <div style="font-size:1rem;font-weight:700;color:{pnl_col};font-family:'JetBrains Mono';">
                {row['total_pnl']:+,.2f}
            </div>
            <div style="font-size:0.75rem;color:var(--text-muted,#6b7a99);">
                WR {row['win_rate']:.0f}% · Score {row['avg_score']:.0f}
            </div>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # Chart: P&L distribution by risk level
    col1, col2 = st.columns(2)
    with col1:
        fig = go.Figure()
        rl_colors = {"normal": "#00c896", "reduced": "#f5a623", "no_trade": "#ff4b6e"}
        for rl in df["risk_level"].unique():
            sub = df[df["risk_level"] == rl]["net_pnl"]
            fig.add_trace(go.Box(
                y=sub, name=rl.upper().replace("_"," "),
                marker_color=rl_colors.get(rl, "#aaaaaa"),
                boxpoints="outliers",
            ))
        fig.update_layout(**CHART_STYLE, title="P&L Distribution by Risk Level",
                          yaxis_title="Net P&L", height=300,
                          yaxis=dict(gridcolor="#1a2030"))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Scatter: risk score vs P&L, coloured by risk level
        fig2 = go.Figure()
        for rl in df["risk_level"].unique():
            sub = df[df["risk_level"] == rl]
            fig2.add_trace(go.Scatter(
                x=sub["risk_score"], y=sub["net_pnl"],
                mode="markers",
                name=rl.upper().replace("_"," "),
                marker=dict(color=rl_colors.get(rl, "#aaaaaa"), size=8, opacity=0.8),
                hovertemplate="Score: %{x}<br>P&L: %{y:+.2f}<extra></extra>",
            ))
        fig2.add_hline(y=0, line=dict(color="#6b7a99", dash="dot", width=1), opacity=0.5)
        fig2.update_layout(**CHART_STYLE, title="Risk Score vs P&L",
                           xaxis_title="Quality Score", yaxis_title="Net P&L", height=300,
                           xaxis=dict(gridcolor="#1a2030"), yaxis=dict(gridcolor="#1a2030"))
        st.plotly_chart(fig2, use_container_width=True)

    # No-trade analysis — "what if you'd skipped no_trade signals?"
    no_trade = df[df["risk_level"] == "no_trade"]
    if len(no_trade) > 0:
        st.divider()
        st.subheader("🚫 'No Trade' Signal Analysis")
        nt_pnl   = no_trade["net_pnl"].sum()
        nt_wins  = (no_trade["net_pnl"] > 0).sum()
        avoided  = -nt_pnl  # negative = you would have saved this by not trading
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("No-Trade Signals",   len(no_trade))
        col2.metric("Trades Taken Anyway", len(no_trade),
                    help="These trades had a 'no_trade' risk level but were still taken")
        col3.metric("Combined P&L",        f"{nt_pnl:+,.2f}")
        avoidance_col = "#00c896" if avoided >= 0 else "#ff4b6e"
        col4.metric("Would Have Saved",    f"{avoided:+,.2f}",
                    help="If all no-trade signals had been skipped, this is the P&L impact")

        if avoided > 0:
            st.success(f"✅ Skipping these {len(no_trade)} trades would have saved **{avoided:+,.2f}**. Your thresholds are working.")
        else:
            st.warning(f"⚠️ These {len(no_trade)} 'no trade' signals were actually profitable ({nt_pnl:+,.2f}). Consider reviewing your thresholds.")

    # Full trade table
    st.divider()
    st.subheader("All Linked Trades")
    display = df[["trade_id","entry_date","symbol","direction","risk_level","risk_score","net_pnl","warning"]].copy()
    st.dataframe(
        display, use_container_width=True, hide_index=True,
        column_config={
            "trade_id":   st.column_config.NumberColumn("ID",     width="small"),
            "entry_date": st.column_config.TextColumn("Date"),
            "symbol":     st.column_config.TextColumn("Symbol"),
            "direction":  st.column_config.TextColumn("Dir",     width="small"),
            "risk_level": st.column_config.TextColumn("Risk Level"),
            "risk_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
            "net_pnl":    st.column_config.NumberColumn("Net P&L", format="%.2f"),
            "warning":    st.column_config.TextColumn("Warning"),
        }
    )
