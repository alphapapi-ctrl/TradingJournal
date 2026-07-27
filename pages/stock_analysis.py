"""
Page: Stock Analysis — forward-test companion for the Wyckoff stock strategy.
Pulls real data via yfinance for any ticker (imported or typed) and reviews
Technical and Fundamental components separately.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all
from utils.theme import get_theme, _PALETTES
from utils.stock_data import (
    resolve_yf_symbol, fetch_history, fetch_info,
    compute_technicals, fundamental_checks,
)

_STATUS_ICON = {"pass": "🟢", "warn": "🟡", "fail": "🔴", "na": "⚪"}


def _p():
    return _PALETTES.get(get_theme(), _PALETTES["dark"])


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_history(yf_sym: str, period: str, interval: str):
    return fetch_history(yf_sym, period, interval)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_info(yf_sym: str):
    return fetch_info(yf_sym)


def _exchange_hint_for(symbol: str) -> str:
    """Look at imported trades to guess the exchange (raw_data may hold it)."""
    rows = fetch_all(
        "SELECT raw_data, broker FROM trades WHERE symbol=? AND raw_data IS NOT NULL LIMIT 5",
        (symbol,),
    )
    for r in rows:
        try:
            d = json.loads(r["raw_data"])
            if isinstance(d, dict) and d.get("exchange"):
                return d["exchange"]
        except Exception:
            continue
    # IBKR AU stocks were stored with .AU stripped — if any trade came from IBKR in AUD assume ASX ambiguous; leave blank
    return ""


def show():
    st.header("🔬 Stock Analysis")
    p = _p()
    st.caption("Forward-test companion — real market data via yfinance. "
               "Technical read follows the Wyckoff strategy (EMA 21/50, SMA 200, volume, OBV); "
               "fundamentals are a crude Buffett/Burry value prequalification.")

    # ── Ticker selection ──────────────────────────────────────────────────────
    imported = fetch_all(
        "SELECT DISTINCT symbol FROM trades WHERE broker IN ('TradingView','IBKR','CMC Markets') ORDER BY symbol"
    )
    imported_syms = [r["symbol"] for r in imported]

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        pick = st.selectbox("Imported tickers", ["— type manually —"] + imported_syms, key="sa_pick")
    with c2:
        manual = st.text_input("Or enter ticker (e.g. BHP.AX, AAPL, VAS.AX)", key="sa_manual",
                               placeholder="AAPL / BHP.AX / IVV.AX")
    with c3:
        market_hint = st.selectbox("Market", ["Auto", "ASX (.AX)", "US"], key="sa_market")

    symbol = manual.strip().upper() if manual.strip() else (pick if pick != "— type manually —" else None)
    if not symbol:
        st.info("Pick an imported ticker or type one to analyse.")
        return

    hint = ""
    if market_hint == "ASX (.AX)":
        hint = "ASX"
    elif market_hint == "Auto":
        hint = _exchange_hint_for(symbol)

    yf_sym = resolve_yf_symbol(symbol, hint)

    # ── Fetch data ────────────────────────────────────────────────────────────
    try:
        with st.spinner(f"Fetching {yf_sym}…"):
            daily  = _cached_history(yf_sym, "2y", "1d")
            weekly = _cached_history(yf_sym, "5y", "1wk")
            info   = _cached_info(yf_sym)
    except Exception as e:
        # Retry with .AX for bare AU-looking symbols
        if "." not in yf_sym:
            try:
                yf_sym = yf_sym + ".AX"
                with st.spinner(f"Retrying as {yf_sym}…"):
                    daily  = _cached_history(yf_sym, "2y", "1d")
                    weekly = _cached_history(yf_sym, "5y", "1wk")
                    info   = _cached_info(yf_sym)
            except Exception:
                st.error(f"No data found for {symbol} (tried {symbol} and {symbol}.AX). {e}")
                return
        else:
            st.error(f"No data for {yf_sym}: {e}")
            return

    name = info.get("longName") or info.get("shortName") or yf_sym
    sector = info.get("sector") or info.get("category") or ""
    quote_type = info.get("quoteType", "")
    is_etf = quote_type == "ETF"

    st.markdown(
        f'<div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:4px;">'
        f'<span style="font-size:1.25rem;font-weight:700;color:{p["--text-primary"]};">{name}</span>'
        f'<span style="font-family:\'JetBrains Mono\';color:{p["--text-muted"]};">{yf_sym}</span>'
        f'{f"<span style=\"font-size:0.75rem;color:{p['--text-faint']};border:1px solid {p['--border']};border-radius:4px;padding:1px 8px;\">{sector or quote_type}</span>" if (sector or quote_type) else ""}'
        f'</div>',
        unsafe_allow_html=True,
    )

    tech = compute_technicals(daily, weekly)

    # Quick metrics row
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Price", f"{tech['price']:,.2f}")
    m2.metric("vs 21 EMA", f"{(tech['price']/tech['ema21']-1)*100:+.1f}%")
    m3.metric("vs 50 EMA", f"{(tech['price']/tech['ema50']-1)*100:+.1f}%")
    m4.metric("vs SMA 200", f"{(tech['price']/tech['sma200']-1)*100:+.1f}%" if tech["sma200"] else "—")
    m5.metric("Off 52w High", f"{tech['pct_off_52w_high']:+.1f}%")

    tab_tech, tab_fund = st.tabs(["📐 Technical", "🏛️ Fundamental"])

    with tab_tech:
        _technical_tab(tech, daily, p)

    with tab_fund:
        if is_etf:
            _etf_fundamental_tab(info, p)
        else:
            _fundamental_tab(info, p)


# ─── TECHNICAL TAB ────────────────────────────────────────────────────────────

def _technical_tab(tech: dict, daily: pd.DataFrame, p: dict):
    # ── Strategy signal cards ────────────────────────────────────────────────
    state, state_txt = tech["trend_state"]
    state_col = {"continuation": p["--accent"], "pullback": p["--warning"], "warning": p["--danger"]}[state]

    st.markdown(
        f'<div style="background:{state_col}18;border:1px solid {state_col};border-radius:8px;'
        f'padding:10px 14px;margin-bottom:10px;font-size:0.9rem;color:{p["--text-primary"]};">'
        f'<b>Trade management state:</b> {state_txt}</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    def flag(col, label, ok, txt_ok, txt_no):
        icon = "🟢" if ok else "🔴"
        col.markdown(f"{icon} **{label}**  \n<span style='font-size:0.75rem;color:{p['--text-muted']};'>{txt_ok if ok else txt_no}</span>", unsafe_allow_html=True)

    flag(c1, "SMA 200", tech["above_sma200"], "Above — long-term uptrend", "Below — long-term downtrend")
    flag(c2, "EMA alignment", tech["ema_cross_bull"], "21 > 50 — bullish momentum", "21 < 50 — bearish momentum")
    if tech.get("weekly"):
        w = tech["weekly"]
        flag(c3, "Weekly 21 EMA", w["above_ema21"], "Above — weekly trend intact", "Below — weekly trend weak")
        c4.markdown(f"**Weekly RSI**  \n<span style='font-family:JetBrains Mono;font-size:1.1rem;color:{p['--text-primary']};'>{w['rsi']:.0f}</span>" if w.get("rsi") else "—", unsafe_allow_html=True)

    rsi = tech.get("rsi")
    if rsi is not None:
        rsi_col = p["--danger"] if rsi > 70 else (p["--accent"] if rsi < 35 else p["--text-muted"])
        st.markdown(f"Daily RSI: <b style='color:{rsi_col};font-family:JetBrains Mono;'>{rsi:.0f}</b>", unsafe_allow_html=True)

    # OBV note
    if tech.get("obv_note"):
        kind, note = tech["obv_note"]
        obv_col = {"bullish": p["--accent"], "bearish": p["--danger"], "neutral": p["--text-faint"]}[kind]
        st.markdown(f'<span style="color:{obv_col};font-size:0.85rem;">📊 {note}</span>', unsafe_allow_html=True)

    # Volume spikes (capitulation candidates)
    spikes = tech.get("recent_volume_spikes", [])
    if spikes:
        st.markdown("**⚡ Volume spikes (last 30 bars)** — potential Wyckoff stopping action:")
        for s in spikes:
            kind = "Bearish (possible selling climax)" if s["bearish"] else "Bullish"
            st.markdown(
                f'<div style="font-size:0.8rem;color:{p["--text-muted"]};padding:2px 0;">'
                f'• {s["date"]} — {s["ratio"]}× avg volume, close {s["close"]} '
                f'<span style="color:{p["--warning"] if s["bearish"] else p["--accent"]};">({kind})</span></div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No abnormal volume in the last 30 bars.")

    # Weekly demand zone
    if tech.get("weekly_demand_zone"):
        z = tech["weekly_demand_zone"]
        st.markdown(
            f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border"]};'
            f'border-left:3px solid {p["--accent"]};border-radius:6px;padding:8px 12px;margin-top:6px;'
            f'font-size:0.82rem;color:{p["--text-muted"]};">'
            f'🎯 <b>Weekly demand zone</b> (last bearish weekly candle, close→wick): '
            f'<span style="font-family:JetBrains Mono;color:{p["--text-primary"]};">'
            f'{z["zone_bottom"]} – {z["zone_top"]}</span> <span style="color:{p["--text-faint"]};">({z["date"]})</span></div>',
            unsafe_allow_html=True,
        )

    # ── Chart ─────────────────────────────────────────────────────────────────
    df = tech["df"].tail(260)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.2, 0.2],
                        vertical_spacing=0.02)
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name="Price", increasing_line_color="#00c896", decreasing_line_color="#ff4b6e",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["EMA21"],  name="EMA 21",  line=dict(color="#f5a623", width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["EMA50"],  name="EMA 50",  line=dict(color="#4a9eff", width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["SMA200"], name="SMA 200", line=dict(color="#b06aff", width=1.4)), row=1, col=1)
    vol_colors = ["#ff4b6e" if c < o else "#00c896" for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="Volume", marker_color=vol_colors, opacity=0.6), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["VolAvg20"], name="Vol 20avg", line=dict(color="#888", width=1)), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], name="RSI", line=dict(color="#4a9eff", width=1.2)), row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#ff4b6e", opacity=0.4, row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#00c896", opacity=0.4, row=3, col=1)
    fig.update_layout(
        height=620, showlegend=True, xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=p["--text-muted"], size=11),
        legend=dict(orientation="h", y=1.02),
    )
    fig.update_xaxes(gridcolor=p["--border"], rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(gridcolor=p["--border"])
    st.plotly_chart(fig, use_container_width=True)


# ─── FUNDAMENTAL TAB ──────────────────────────────────────────────────────────

def _fundamental_tab(info: dict, p: dict):
    fund = fundamental_checks(info)
    v_status, v_text = fund["verdict"]
    v_col = {"pass": p["--accent"], "warn": p["--warning"], "fail": p["--danger"], "na": p["--text-faint"]}[v_status]

    st.markdown(
        f'<div style="background:{v_col}18;border:1px solid {v_col};border-radius:8px;'
        f'padding:12px 16px;margin-bottom:12px;">'
        f'<span style="font-size:1rem;font-weight:700;color:{v_col};">{_STATUS_ICON[v_status]} {v_text}</span>'
        f'<span style="font-size:0.8rem;color:{p["--text-muted"]};margin-left:12px;">'
        f'{fund["summary"]["pass"]} pass · {fund["summary"]["fail"]} fail of {fund["summary"]["total"]} checks</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.caption("Crude prequalification only — Buffett quality (ROE, margins, FCF) + Burry value/survivability (P/B, debt, liquidity). Not a substitute for reading the filings.")

    for name, c in fund["checks"].items():
        col = {"pass": p["--accent"], "warn": p["--warning"], "fail": p["--danger"], "na": p["--text-faint"]}[c["status"]]
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;padding:7px 12px;margin:3px 0;'
            f'background:{p["--bg-card"]};border:1px solid {p["--border"]};border-radius:6px;">'
            f'<span style="width:20px;">{_STATUS_ICON[c["status"]]}</span>'
            f'<span style="width:150px;font-weight:600;color:{p["--text-primary"]};font-size:0.85rem;">{name}</span>'
            f'<span style="width:130px;font-family:\'JetBrains Mono\';color:{col};font-size:0.85rem;">{c["value"]}</span>'
            f'<span style="font-size:0.78rem;color:{p["--text-muted"]};">{c["note"]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Raw key stats expander
    with st.expander("📄 Key stats (raw)"):
        keys = ["marketCap", "trailingPE", "forwardPE", "priceToBook", "returnOnEquity",
                "returnOnAssets", "profitMargins", "grossMargins", "operatingMargins",
                "debtToEquity", "currentRatio", "quickRatio", "freeCashflow", "operatingCashflow",
                "totalCash", "totalDebt", "revenueGrowth", "earningsGrowth",
                "dividendYield", "payoutRatio", "beta", "heldPercentInsiders"]
        rows = [{"Metric": k, "Value": info.get(k)} for k in keys if info.get(k) is not None]
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No fundamental data available.")


def _etf_fundamental_tab(info: dict, p: dict):
    """ETFs don't have balance sheets — show fund-relevant stats instead."""
    st.markdown("**ETF — fund characteristics** (value checks don't apply)")
    rows = []
    mapping = [
        ("Category",        info.get("category")),
        ("Fund family",     info.get("fundFamily")),
        ("Total assets",    f"{info.get('totalAssets',0)/1e9:,.2f}B" if info.get("totalAssets") else None),
        ("Yield",           f"{info.get('yield',0)*100:.2f}%" if info.get("yield") else None),
        ("Trailing div yield", f"{(info.get('trailingAnnualDividendYield') or 0)*100:.2f}%" if info.get("trailingAnnualDividendYield") else None),
        ("Expense ratio",   f"{(info.get('annualReportExpenseRatio') or info.get('netExpenseRatio') or 0)*100:.2f}%" if (info.get("annualReportExpenseRatio") or info.get("netExpenseRatio")) else None),
        ("3y return",       f"{(info.get('threeYearAverageReturn') or 0)*100:.1f}%" if info.get("threeYearAverageReturn") else None),
        ("5y return",       f"{(info.get('fiveYearAverageReturn') or 0)*100:.1f}%" if info.get("fiveYearAverageReturn") else None),
        ("Beta (3y)",       info.get("beta3Year")),
        ("52w range",       f"{info.get('fiftyTwoWeekLow')} – {info.get('fiftyTwoWeekHigh')}"
                            if info.get("fiftyTwoWeekLow") else None),
    ]
    for label, val in mapping:
        if val is not None:
            rows.append({"Metric": label, "Value": str(val)})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("Limited fund data available from yfinance for this ETF.")

    st.caption("For income ETFs: focus on yield + expense ratio. For growth ETFs: 3y/5y return + beta.")
