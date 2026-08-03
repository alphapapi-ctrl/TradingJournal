"""
Page: Instrument Reference — historical scenario browser.

Works on any instrument with a School Run App 1-min cache. A strategy
selector controls which filters/levels are shown:
  - Key levels (default): prior session H/L/C, overnight range, gaps, news
  - School Run: + 1st/2nd 15m setup-bar filters and entry/stop lines
  - Custom ORB: + opening-range (15/30/60m) breakout lines
"""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import plotly.graph_objects as go

from components.lwchart import lwchart
from utils import market_data as md
from utils import day_index as di
from utils.instruments import label as inst_label, get_instrument, reload_registry, session_opens
from utils.news_events import events_for_date, all_event_types, reload_events
from utils.chart_config import chart_colors, get_candle_colors, save_candle_colors, DEFAULT_CANDLE_COLORS

INDICATOR_CHOICES = {
    "EMA 20": {"type": "ema", "period": 20, "color": "#f5a623"},
    "EMA 50": {"type": "ema", "period": 50, "color": "#4a9eff"},
    "EMA 200": {"type": "ema", "period": 200, "color": "#b06aff"},
    "VWAP": {"type": "vwap", "color": "#e858c8", "width": 2},
    "RSI 14 (pane)": {"type": "rsi", "period": 14, "color": "#4a9eff"},
}

STRATEGIES = {
    "Key levels (default)": {"setup": False, "orb": False,
                             "levels": ["Prior session H/L", "Prior close", "Overnight H/L"]},
    "School Run": {"setup": True, "orb": False,
                   "levels": ["Setup entry/stop", "Prior session H/L", "Overnight H/L"]},
    "Custom ORB": {"setup": True, "orb": True,
                   "levels": ["Setup entry/stop", "Prior session H/L", "Overnight H/L"]},
}

LEVEL_CHOICES = ["Setup entry/stop", "Prior session H/L", "Prior close", "Overnight H/L"]


def show():
    st.header("🎓 Instrument Reference")

    instruments = md.list_instruments()
    if not instruments:
        st.warning("No 1-min caches found. Set the School Run data path in the Data tab below.")
        _data_tab_container()
        return

    c1, c2, c3, c4 = st.columns([1.6, 1.1, 1.3, 0.9])
    with c1:
        cur = md.current_instrument()
        if cur not in instruments:
            cur = instruments[0]
        instrument = st.selectbox("Instrument", instruments,
                                  index=instruments.index(cur),
                                  format_func=inst_label, key="ref_instrument")
        if instrument != cur:
            md.set_current_instrument(instrument)
    with c2:
        strat_name = st.selectbox("Strategy", list(STRATEGIES),
                                  index=list(STRATEGIES).index(
                                      md.get_setting("reference_strategy", "Key levels (default)")
                                      if md.get_setting("reference_strategy") in STRATEGIES
                                      else "Key levels (default)"),
                                  key="ref_strategy")
        md.save_setting("reference_strategy", strat_name)
        strat = STRATEGIES[strat_name]
    with c3:
        sessions = session_opens(instrument)
        saved = md.get_setting(f"focus_session:{instrument}", "Native session")
        if saved not in sessions:
            saved = "Native session"
        sess_label = st.selectbox(
            "Focus session", list(sessions),
            index=list(sessions).index(saved),
            key=f"ref_session_{instrument}",
            help="Bar numbering counts from this session's open — even when the "
                 "whole day is shown. 'Session only' clips the chart to it.")
        md.save_setting(f"focus_session:{instrument}", sess_label)
        focus = sessions[sess_label]
        st.caption(f"{focus['open']}–{focus['close']} {focus['tz']}")
    with c4:
        orb_window = 15
        if strat["orb"]:
            orb_window = st.selectbox("ORB window (min)", [15, 30, 60], key="ref_orb_window")

    tab_scanner, tab_data = st.tabs(["🔍 Scanner", "🗄️ Data"])
    with tab_scanner:
        _scanner_tab(instrument, strat, orb_window, sess_label, focus)
    with tab_data:
        _data_tab(instrument)


# ── Filters ───────────────────────────────────────────────────────────────
def _filters_form(strat: dict) -> dict | None:
    with st.expander("🎛️ Filters — find historical scenarios", expanded=False):
        enabled = st.checkbox("Enable filters", key="ref_filter_on")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            gap_dir = st.selectbox("Gap direction", ["(any)", "up", "down"])
            gap_min = st.number_input("Min gap size (pts)", 0.0, 2000.0, 0.0, 10.0)
            gap_max = st.number_input("Max gap size (pts)", 0.0, 5000.0, 0.0, 10.0,
                                      help="0 = no maximum")
        with c2:
            gap_closed = st.selectbox("Gap closed in session (hindsight)", ["(any)", "yes", "no"])
            open_vs_prior = st.selectbox("Open vs prior session",
                                         ["(any)", "inside", "above_high", "below_low"])
            open_vs_onr = st.selectbox("Open vs overnight range",
                                       ["(any)", "inside", "above", "below"])
        with c3:
            if strat["setup"]:
                b1_type = st.selectbox("1st 15m bar", ["(any)", "bull", "bear", "doji"])
                b2_type = st.selectbox("2nd 15m bar", ["(any)", "bull", "bear", "doji"])
                b2_rel = st.selectbox("2nd bar vs 1st",
                                      ["(any)", "inside", "broke_high", "broke_low", "outside"])
            else:
                b1_type = b2_type = b2_rel = "(any)"
                st.caption("Setup-bar filters available for School Run / ORB strategies.")
        with c4:
            news = st.selectbox("News day", ["(any)", "none", "any"] + all_event_types())
            date_from = st.date_input("From", value=None, key="ref_from")
            date_to = st.date_input("To", value=None, key="ref_to")
        max_cards = st.slider("Max results", 10, 200, 60, 10)

    if not enabled:
        return None
    f = {"limit": max_cards}
    if gap_dir != "(any)":
        f["gap_dir"] = gap_dir
    if gap_min > 0:
        f["gap_min"] = gap_min
    if gap_max > 0:
        f["gap_max"] = gap_max
    if gap_closed != "(any)":
        f["gap_closed"] = gap_closed == "yes"
    if open_vs_prior != "(any)":
        f["open_vs_prior"] = open_vs_prior
    if open_vs_onr != "(any)":
        f["open_vs_onr"] = open_vs_onr
    if b1_type != "(any)":
        f["b1_type"] = b1_type
    if b2_type != "(any)":
        f["b2_type"] = b2_type
    if b2_rel != "(any)":
        f["b2_rel_b1"] = b2_rel
    if news != "(any)":
        f["news"] = news
    if date_from:
        f["date_from"] = date_from
    if date_to:
        f["date_to"] = date_to
    return f


# ── Cards ─────────────────────────────────────────────────────────────────
def _mini_chart(day: str, instrument: str) -> go.Figure | None:
    bars = md.get_day_bars(day, session_only=True, instrument=instrument)
    if bars.empty:
        return None
    m15 = bars.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    cc = get_candle_colors()
    fig = go.Figure(go.Candlestick(
        x=list(range(len(m15))), open=m15["open"], high=m15["high"],
        low=m15["low"], close=m15["close"],
        increasing_line_color=cc["up"], decreasing_line_color=cc["down"],
        increasing_fillcolor=cc["up"], decreasing_fillcolor=cc["down"], line_width=1))
    fig.update_layout(height=110, margin=dict(l=0, r=0, t=2, b=2),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(visible=False, rangeslider_visible=False),
                      yaxis=dict(visible=False), showlegend=False)
    return fig


def _result_card(f: dict, idx: int, instrument: str, strat: dict):
    with st.container(border=True):
        top = st.columns([2, 1])
        with top[0]:
            st.markdown(f"**{f['date']}**" + (f"  ·  📰 {f['news_flags']}" if f["news_flags"] else ""))
        with top[1]:
            if st.button("Load", key=f"card_{idx}_{f['date']}", use_container_width=True):
                st.session_state["ref_day"] = f["date"]
                st.session_state["ref_day_select"] = f["date"]
                st.rerun()
        fig = _mini_chart(f["date"], instrument)
        if fig:
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False, "staticPlot": True},
                            key=f"mini_{idx}_{f['date']}")
        gap = f"{f['gap_pts']:+.0f}pts" if f["gap_pts"] is not None else "n/a"
        closed = "closed " + (f["gap_close_time"] or "") if f["gap_closed"] else "not closed"
        parts = [f"Gap {gap} ({closed})",
                 f"open {f['open_vs_prior'] or '?'} prior / {f['open_vs_onr'] or '?'} ON"]
        if strat["setup"]:
            parts.append(f"b1 {f['b1_type']} · b2 {f['b2_rel_b1'] or '—'}")
        st.caption(" · ".join(parts))


# ── Info box / levels ─────────────────────────────────────────────────────
def _info_box(f: dict, day: str, strat: dict):
    ev = events_for_date(day)
    news = "  ·  ".join(f"📰 {e['event']} {e['time_et']} ET — {e['name']}" for e in ev) \
        if ev else "No CPI/NFP/FOMC events"
    gap_txt = "—"
    if f.get("gap_pts") is not None:
        closed = f"closed at {f['gap_close_time']}" if f["gap_closed"] else "NOT closed in session"
        gap_txt = f"{f['gap_pts']:+.1f} pts ({f['gap_pct']:+.2f}%), {closed}"
    onr = f"H {f['overnight_high']:.0f} / L {f['overnight_low']:.0f}" \
        if f.get("overnight_high") else "—"
    prior = f"H {f['prior_high']:.0f} / L {f['prior_low']:.0f} / C {f['prior_close']:.0f}" \
        if f.get("prior_high") else "—"
    rows = [
        ("News", news),
        ("Gap", gap_txt),
        ("Open vs prior session", f"{f.get('open_vs_prior') or '—'} (prior {prior})"),
        ("Open vs overnight", f"{f.get('open_vs_onr') or '—'} (ON {onr} — from 00:00 local)"),
    ]
    if strat["setup"]:
        b1 = (f"{f['b1_type']} (O {f['b1_o']:.0f} H {f['b1_h']:.0f} "
              f"L {f['b1_l']:.0f} C {f['b1_c']:.0f})") if f.get("b1_o") else "—"
        b2 = (f"{f['b2_type']}, {f['b2_rel_b1']} vs b1 "
              f"(H {f['b2_h']:.0f} L {f['b2_l']:.0f})") if f.get("b2_o") else "—"
        rows += [("1st 15m bar", b1), ("2nd 15m bar", b2),
                 ("Setup levels", f"long {f['sr_long_entry']:.1f} (SL {f['sr_long_stop']:.1f}) · "
                                  f"short {f['sr_short_entry']:.1f} (SL {f['sr_short_stop']:.1f})")]
    st.markdown("| | |\n|---|---|\n" + "\n".join(f"| **{k}** | {v} |" for k, v in rows))


def _orb_levels(day: str, instrument: str, window_min: int) -> dict | None:
    """Opening-range breakout levels for an arbitrary window, computed live."""
    bars = md.get_day_bars(day, session_only=True, instrument=instrument)
    if bars.empty:
        return None
    orb = bars.iloc[:window_min]
    hi, lo = float(orb["high"].max()), float(orb["low"].min())
    buf = di.ENTRY_BUFFER_PTS
    return {"long_entry": round(hi + buf, 1), "long_stop": round(lo, 1),
            "short_entry": round(lo - buf, 1), "short_stop": round(hi, 1)}


def _levels_for(f: dict, selected: list[str], strat: dict, day: str,
                instrument: str, orb_window: int) -> list[dict]:
    lv = []
    if "Setup entry/stop" in selected and strat["setup"]:
        if strat["orb"] and orb_window != 15:
            orb = _orb_levels(day, instrument, orb_window)
        else:
            orb = {"long_entry": f["sr_long_entry"], "long_stop": f["sr_long_stop"],
                   "short_entry": f["sr_short_entry"], "short_stop": f["sr_short_stop"]}
        if orb:
            lv += [
                {"price": orb["long_entry"], "title": "long", "color": "#00c896", "style": "solid", "width": 2},
                {"price": orb["long_stop"], "title": "long SL", "color": "#00c896", "style": "dotted"},
                {"price": orb["short_entry"], "title": "short", "color": "#ff4b6e", "style": "solid", "width": 2},
                {"price": orb["short_stop"], "title": "short SL", "color": "#ff4b6e", "style": "dotted"},
            ]
    if "Prior session H/L" in selected and f.get("prior_high"):
        lv += [{"price": f["prior_high"], "title": "pdH", "color": "#f5a623", "style": "dashed"},
               {"price": f["prior_low"], "title": "pdL", "color": "#f5a623", "style": "dashed"}]
    if "Prior close" in selected and f.get("prior_close"):
        lv += [{"price": f["prior_close"], "title": "pdC", "color": "#b06aff", "style": "dashed"}]
    if "Overnight H/L" in selected and f.get("overnight_high"):
        lv += [{"price": f["overnight_high"], "title": "onH", "color": "#4a9eff", "style": "dotted"},
               {"price": f["overnight_low"], "title": "onL", "color": "#4a9eff", "style": "dotted"}]
    return lv


# ── Scanner ───────────────────────────────────────────────────────────────
def _scanner_tab(instrument: str, strat: dict, orb_window: int,
                 sess_label: str, focus: dict):
    days = md.list_available_days(instrument)
    if not days:
        st.warning("No data for this instrument — check the Data tab.")
        return
    if di.index_count(instrument) == 0:
        st.info("Day index is empty for this instrument — build it in the Data tab to enable filters.")

    filters = _filters_form(strat)
    results = (di.query_days(filters, instrument=instrument, limit=filters.pop("limit", 60))
               if filters else None)

    if results is not None:
        col_cards, col_chart = st.columns([1, 3])
    else:
        col_cards, col_chart = None, st.container()

    if results is not None:
        with col_cards:
            st.caption(f"**{len(results)} matching day(s)**")
            with st.container(height=760):
                for i, f in enumerate(results):
                    _result_card(f, i, instrument, strat)

    with col_chart:
        c1, c2, c3, c4 = st.columns([1.2, 1, 1.6, 1.6])
        with c1:
            default_day = st.session_state.get("ref_day", days[-1])
            if default_day not in days:
                default_day = days[-1]
                st.session_state.pop("ref_day_select", None)
            day = st.selectbox("Day", days, index=days.index(default_day), key="ref_day_select")
            st.session_state["ref_day"] = day
        with c2:
            session_only = st.toggle("Session only", value=True, key="ref_session_only",
                                     help=f"Clip to the focus session ({sess_label}). "
                                          "Off = whole day.")
        with c3:
            inds = st.multiselect("Indicators", list(INDICATOR_CHOICES), key="ref_indicators")
        with c4:
            level_default = [l for l in strat["levels"] if l in LEVEL_CHOICES]
            level_sel = st.multiselect("Levels", LEVEL_CHOICES, default=level_default,
                                       key=f"ref_levels_{'setup' if strat['setup'] else 'plain'}")

        feats = di.get_day(day, instrument)
        focus_arg = None if sess_label == "Native session" else focus
        bars = md.get_day_bars(day, session_only=session_only, instrument=instrument,
                               session=focus_arg)
        if bars.empty:
            st.info("No data for this day"
                    + (f" in the {sess_label} window — try 'Session only' off."
                       if focus_arg and session_only else "."))
            return
        event = lwchart(
            bars_1m=md.bars_to_lwc(bars, instrument),
            data_key=f"{instrument}:{day}:{'sess' if session_only else 'day'}"
                     f":{orb_window}:{sess_label}",
            mode="static",
            colors=chart_colors(),
            indicators=[INDICATOR_CHOICES[i] for i in inds],
            levels=_levels_for(feats, level_sel, strat, day, instrument, orb_window) if feats else [],
            ack=st.session_state.get("ref_shot_seq", 0),
            session_start=md.session_start_epoch(day, instrument, focus_arg),
            default_tf=5,
            height=560,
            key="ref_chart",
        )
        _handle_chart_event(event, instrument, day)
        if feats:
            _info_box(feats, day, strat)
        else:
            st.caption("Day not in index — rebuild the index in the Data tab for filter info.")


def _handle_chart_event(event, instrument: str, day: str):
    """Save 📷 screenshots from the reference chart (dedupe by seq)."""
    if not event or not isinstance(event, dict) or event.get("type") != "screenshot":
        return
    if event.get("seq", 0) <= st.session_state.get("ref_shot_seq", 0):
        return
    st.session_state["ref_shot_seq"] = event["seq"]
    import base64
    from datetime import datetime
    from pathlib import Path
    b64 = event.get("payload", {}).get("png_base64")
    if not b64:
        return
    folder = Path(__file__).resolve().parent.parent / "data" / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"ref_{instrument}_{day}_{datetime.now().strftime('%H%M%S')}.png"
    path.write_bytes(base64.b64decode(b64))
    st.toast(f"Screenshot saved: {path.name}", icon="📷")
    st.rerun()  # deliver the updated ack to the component


# ── Data ──────────────────────────────────────────────────────────────────
def _data_tab_container():
    tab, = st.tabs(["🗄️ Data"])
    with tab:
        _data_tab(md.current_instrument())


def _data_tab(instrument: str):
    st.subheader("Data source")
    root = st.text_input("School Run App folder", value=str(md.get_sr_root()))
    if root != str(md.get_sr_root()):
        md.save_setting("school_run_root", root)
        reload_registry()
        st.rerun()

    caches = md.list_caches("1min", instrument)
    tick_caches = md.list_caches("ticks", instrument)
    if caches:
        best = md.find_cache("1min", instrument)
        rows = [{
            "file": c["path"].name,
            "from": c["start"], "to": c["end"],
            "hours": "extended" if (c["ext"] or c["fd"]) else "session",
            "active": "✓" if c["path"] == best else "",
        } for c in sorted(caches, key=lambda c: c["end"], reverse=True)]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption(f"{len(tick_caches)} tick cache file(s) for {instrument}.")
    else:
        st.error(f"No 1-min caches found for {instrument} under data/1min.")

    st.subheader("Update data")
    st.caption(f"Downloads Dukascopy data for {instrument} via the School Run App fetcher.")
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("⬇️ Update to today", type="primary",
                     help="Extends the existing cache from its start date to today."):
            proc = md.run_data_update(instrument)
            st.session_state["_data_update_pid"] = proc.pid
            st.rerun()
    with c2:
        if st.session_state.get("_data_update_pid"):
            st.info(f"Update running (pid {st.session_state['_data_update_pid']}). "
                    "Refresh this tab to see log progress; rebuild the index afterwards.")

    with st.expander("⏬ Initial download — specify a date range"):
        st.caption("For a fresh instrument (or to backfill further into the past). "
                   "Long ranges download hour-by-hour from Dukascopy and can take a while — "
                   "the log below tracks progress.")
        import datetime as _dt
        d1, d2, d3 = st.columns([1, 1, 1])
        with d1:
            dl_from = st.date_input("From", value=_dt.date(2021, 1, 1), key="dl_from")
        with d2:
            dl_to = st.date_input("To", value=_dt.date.today(), key="dl_to")
        with d3:
            st.write("")
            if st.button("⏬ Download range", disabled=dl_from >= dl_to):
                proc = md.run_data_update(instrument, start=str(dl_from), end=str(dl_to))
                st.session_state["_data_update_pid"] = proc.pid
                st.rerun()

    log = md.read_update_log()
    if log:
        st.code(log, language=None)

    st.subheader("Day index")
    st.caption(f"{di.index_count(instrument)} day(s) indexed for {instrument}.")
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("🔄 Update index (new days only)"):
            bar = st.progress(0.0, "Indexing…")
            n = di.rebuild_index(instrument,
                                 progress=lambda a, b: bar.progress(min(a / max(b, 1), 1.0)))
            bar.empty()
            st.success(f"Indexed {n} new day(s).")
    with c2:
        if st.button("🧨 Full rebuild"):
            bar = st.progress(0.0, "Rebuilding…")
            n = di.rebuild_index(instrument, force=True,
                                 progress=lambda a, b: bar.progress(min(a / max(b, 1), 1.0)))
            bar.empty()
            reload_events()
            st.success(f"Rebuilt index: {n} day(s).")

    st.subheader("Candle colors")
    colors = get_candle_colors()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        up = st.color_picker("Body up", colors["up"])
        down = st.color_picker("Body down", colors["down"])
    with c2:
        wick_up = st.color_picker("Wick up", colors["wick_up"])
        wick_down = st.color_picker("Wick down", colors["wick_down"])
    with c3:
        border_up = st.color_picker("Outline up", colors["border_up"])
        border_down = st.color_picker("Outline down", colors["border_down"])
    with c4:
        border_visible = st.checkbox("Show outline", value=colors["border_visible"])
        number_candles = st.checkbox("Number every 2nd candle", value=colors["number_candles"])
    b1, b2 = st.columns([1, 1])
    with b1:
        if st.button("💾 Save colors"):
            save_candle_colors({
                "up": up, "down": down, "wick_up": wick_up, "wick_down": wick_down,
                "border_up": border_up, "border_down": border_down,
                "border_visible": border_visible, "number_candles": number_candles,
            })
            st.success("Saved.")
            st.rerun()
    with b2:
        if st.button("↩️ Reset to defaults"):
            save_candle_colors(DEFAULT_CANDLE_COLORS)
            st.rerun()
