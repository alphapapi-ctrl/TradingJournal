"""
Page: Replay Trainer — bar replay of historical days with simulated trading.

Every stored session owns its own auto-generated demo account (SIM-XXXXXX),
so replay trades and journal entries never mix with real accounts. The chart
plays in real-time pacing (1x = wall clock; speed multiplies). Python
re-validates every client-side fill against bar data before writing trades.
"""
import json
import random

import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from components.lwchart import lwchart
from utils import market_data as md
from utils import day_index as di
from utils import replay_engine as re_
from utils.chart_config import chart_colors
from utils.news_events import events_for_date

DEFAULT_LEVEL_STYLES = {
    "prior_high": {"color": "#f5a623", "style": "dashed", "width": 1, "on": True},
    "prior_low": {"color": "#f5a623", "style": "dashed", "width": 1, "on": True},
    "prior_close": {"color": "#b06aff", "style": "dashed", "width": 1, "on": True},
    "overnight_high": {"color": "#4a9eff", "style": "dotted", "width": 1, "on": True},
    "overnight_low": {"color": "#4a9eff", "style": "dotted", "width": 1, "on": True},
}
LEVEL_LABELS = {
    "prior_high": "pdH", "prior_low": "pdL", "prior_close": "pdC",
    "overnight_high": "onH", "overnight_low": "onL",
}
INDICATOR_CHOICES = {
    "EMA 9": {"type": "ema", "period": 9, "color": "#e858c8"},
    "EMA 20": {"type": "ema", "period": 20, "color": "#f5a623"},
    "EMA 50": {"type": "ema", "period": 50, "color": "#4a9eff"},
    "VWAP": {"type": "vwap", "color": "#b06aff", "width": 2},
    "RSI 14 (pane)": {"type": "rsi", "period": 14, "color": "#4a9eff"},
}


# ── Session-state helpers ─────────────────────────────────────────────────
def _sid() -> int | None:
    return st.session_state.get("replay_session_id")


def _orders_key(sid, day):   return f"replay_orders:{sid}:{day}"
def _reqs_key(sid, day):     return f"replay_reqs:{sid}:{day}"
def _seq_key(sid, day):      return f"replay_seq:{sid}:{day}"
def _px_key(sid, day):       return f"replay_px:{sid}:{day}"


def _pending_orders(sid, day) -> list[dict]:
    return st.session_state.setdefault(_orders_key(sid, day), [])


def _requests(sid, day) -> list[dict]:
    return st.session_state.setdefault(_reqs_key(sid, day), [])


def _next_id(prefix: str) -> str:
    n = st.session_state.get("_replay_id_counter", 0) + 1
    st.session_state["_replay_id_counter"] = n
    return f"{prefix}{n}"


@st.cache_data(show_spinner="Loading tick data…", max_entries=8)
def _day_ticks_payload(instrument: str, day: str, sess_label: str,
                       session: dict | None) -> dict | None:
    """Compact tick payload for the chart's true-tick engine (cached per day+session)."""
    ticks = md.get_day_ticks(day, instrument, session_only=True, session=session)
    return md.ticks_to_lwc(ticks, instrument)


def _level_styles() -> dict:
    raw = md.get_setting("replay_level_styles")
    styles = {k: dict(v) for k, v in DEFAULT_LEVEL_STYLES.items()}
    if raw:
        try:
            for k, v in json.loads(raw).items():
                if k in styles:
                    styles[k].update(v)
        except (ValueError, TypeError):
            pass
    return styles


# ── Page ──────────────────────────────────────────────────────────────────
def show():
    st.header("⏪ Replay Trainer")

    sess = re_.get_session(_sid()) if _sid() else None
    if not sess:
        _session_picker()
        return

    sid = sess["id"]
    account_id = sess["account_id"]
    instrument = sess["instrument"]
    sym = re_.currency_symbol(account_id)

    days = md.list_available_days(instrument)
    if not days:
        st.warning("No data for this instrument — check Instrument Reference → Data.")
        return
    # next/random day navigation lands here BEFORE the Day widget is created
    # (a widget's session-state key cannot be written after instantiation)
    pending = st.session_state.pop("_replay_day_pending", None)
    if pending in days:
        st.session_state["replay_day"] = pending
        st.session_state["replay_day_select"] = pending
    if "replay_day" not in st.session_state or st.session_state["replay_day"] not in days:
        if sess["day"] in days:
            default_day = sess["day"]
        else:
            # newest day that actually has an indexed session (skips
            # sliver days that only carry a few overnight bars)
            from database import fetch_all
            indexed = {r["date"] for r in fetch_all(
                "SELECT date FROM day_features WHERE instrument=?", (instrument,))}
            default_day = next((d for d in reversed(days) if d in indexed), days[-1])
        st.session_state["replay_day"] = default_day
        st.session_state.pop("replay_day_select", None)
    day = st.session_state["replay_day"]
    feats = di.get_day(day, instrument)

    # ── Top controls ──────────────────────────────────────────────────────
    c0, c1, c2, c3, c4, c5 = st.columns([1.3, 1.2, 0.5, 0.5, 1.3, 1.5])
    with c0:
        st.markdown(f"**📼 {sess['name']}**")
        st.caption(f"{instrument} · acct {re_.get_account_number(account_id)}")
        if st.button("⏏ Switch session"):
            st.session_state.pop("replay_session_id", None)
            st.rerun()
    with c1:
        sel = st.selectbox("Day", days, index=days.index(day), key="replay_day_select")
        if sel != day:
            st.session_state["replay_day"] = sel
            st.rerun()
    with c2:
        st.write("")
        if st.button("▶", help="Next trading day", use_container_width=True):
            idx = days.index(day)
            if idx + 1 < len(days):
                _goto_day(days[idx + 1])
    with c3:
        st.write("")
        if st.button("🎲", help="Random day", use_container_width=True):
            _goto_day(random.choice(days))
    with c4:
        inds = st.multiselect("Indicators", list(INDICATOR_CHOICES), key="replay_indicators")
    with c5:
        styles = _level_styles()
        _vp_opts = ["VP (prior day full)", "VP (prior session)"]
        shown_levels = st.multiselect(
            "Prior-day levels", list(LEVEL_LABELS.values()) + _vp_opts,
            default=[v for k, v in LEVEL_LABELS.items() if styles[k]["on"]]
                    + ["VP (prior day full)"],
            key="replay_levels",
            help="Volume profile scope: 'prior day full' = whole prior day incl. "
                 "overnight; 'prior session' = the prior cash session only. "
                 "If both are ticked, the session profile wins.")

    # ── Trading session (native for indices; London/NY/Tokyo for 24h markets)
    from utils.instruments import session_opens
    sessions = session_opens(instrument)
    saved_focus = md.get_setting(f"replay_focus:{sid}", "Native session")
    if saved_focus not in sessions:
        saved_focus = "Native session"
    sess_label = st.selectbox(
        "Trading session", list(sessions),
        index=list(sessions).index(saved_focus), key=f"replay_sess_{sid}",
        help="The window you trade during replay. Indices default to their native "
             "cash session; for gold/FX pick London or New York (native = whole day).")
    focus = sessions[sess_label]
    focus_arg = None if sess_label == "Native session" else focus
    if sess_label != saved_focus:
        # session window changed → saved tick cursor no longer applies
        md.save_setting(f"replay_focus:{sid}", sess_label)
        re_.save_session(sid, day, 0, 0, sess["speed"] or 10, _pending_orders(sid, day))
        st.rerun()

    # ── Context bars: prior day (whole day) + replay-day overnight up to the
    # session open — i.e. everything from the prior close onwards is visible.
    # 24h markets (native session = whole day) naturally get midnight→midnight.
    import pandas as pd
    idx = days.index(day)
    prior_day = days[idx - 1] if idx > 0 else None
    ctx_parts = []
    if prior_day:
        pb = md.get_day_bars(prior_day, session_only=False, instrument=instrument)
        if not pb.empty:
            ctx_parts.append(pb)
    sess_start_utc, _ = md.session_bounds(day, instrument, focus_arg)
    pre = md.get_day_bars(day, session_only=False, instrument=instrument)
    pre = pre[pre.index < sess_start_utc.astimezone(md.pytz.UTC)]
    if not pre.empty:
        ctx_parts.append(pre)
    ctx_bars = pd.concat(ctx_parts) if ctx_parts else None

    bars = md.get_day_bars(day, session_only=True, instrument=instrument, session=focus_arg)
    if bars.empty:
        st.info(f"No data for this day in the {sess_label} window.")
        return
    lwc_prior = md.bars_to_lwc(ctx_bars, instrument) if ctx_bars is not None else []
    lwc_day = md.bars_to_lwc(bars, instrument)
    prior_len = len(lwc_prior)

    # ── Levels ────────────────────────────────────────────────────────────
    levels = []
    if feats:
        for key, label in LEVEL_LABELS.items():
            price = feats.get(key)
            s = styles[key]
            if price and label in shown_levels:
                levels.append({"price": price, "title": label, "color": s["color"],
                               "style": s["style"], "width": s["width"]})
    vprofile = None
    vp_src = None
    if "VP (prior session)" in shown_levels and prior_day:
        vp_src = md.get_day_bars(prior_day, session_only=True, instrument=instrument)
    elif "VP (prior day full)" in shown_levels:
        vp_src = ctx_bars  # whole prior day + overnight up to the open
    if vp_src is not None and not vp_src.empty:
        vp = md.volume_profile(vp_src)
        if vp:
            levels += [
                {"price": vp["poc"], "title": "POC", "color": "#ffb340", "style": "solid", "width": 2},
                {"price": vp["vah"], "title": "VAH", "color": "#8b93a6", "style": "dashed"},
                {"price": vp["val"], "title": "VAL", "color": "#8b93a6", "style": "dashed"},
            ]
            if lwc_prior:
                # histogram overlay anchored at the prior day's first bar
                vprofile = {"bins": vp["bins"], "anchor_time": lwc_prior[0]["time"]}

    # ── Orders arg ────────────────────────────────────────────────────────
    open_trades = re_.open_replay_trades(account_id, day)
    orders_arg = list(_pending_orders(sid, day))
    for t in open_trades:
        orders_arg.append({
            "id": t["id"], "kind": "position",
            "side": "buy" if t["direction"] == "LONG" else "sell",
            "price": t["entry_price"], "sl": t.get("stop_price"),
            "tp": t.get("take_profit"), "qty": t["quantity"],
            "risk": t.get("planned_risk") or 0,
        })

    # True-tick engine when tick data exists for this day; bar engine otherwise
    ticks = _day_ticks_payload(instrument, day, sess_label, focus_arg)
    tick_mode = bool(ticks)

    # resume point: saved cursor when returning to the session's saved day
    # (tick index in tick mode, bar index in bar mode)
    if sess["day"] == day and (sess["cursor"] or 0) > 0:
        start_cursor, start_substep = sess["cursor"], sess["sub_step"] or 0
    elif tick_mode:
        start_cursor, start_substep = 0, 0   # watch the session form from the open
    else:
        start_cursor, start_substep = prior_len + 15, 0

    col_chart, col_panel = st.columns([3.2, 1])
    with col_chart:
        event = lwchart(
            bars_1m=lwc_prior if tick_mode else lwc_prior + lwc_day,
            ticks=ticks,
            data_key=f"replay:{instrument}:{day}:s{sid}:{sess_label}",
            mode="replay",
            colors=chart_colors(),
            indicators=[INDICATOR_CHOICES[i] for i in inds],
            levels=levels,
            orders=orders_arg,
            requests=_requests(sid, day),
            vprofile=vprofile,
            ack=st.session_state.get(_seq_key(sid, day), 0),
            session_start=md.session_start_epoch(day, instrument, focus_arg),
            replay={"session_end": md.session_end_epoch(day, instrument, focus_arg),
                    "start_cursor": start_cursor, "start_substep": start_substep,
                    "speed": sess["speed"] or 10, "ccy": sym},
            default_tf=1,
            height=600,
            key="replay_chart",
        )
        if tick_mode:
            n = len(ticks["b"])
            st.caption(f"⚡ True tick replay — {n:,} ticks, real inter-tick timing "
                       "(1x = actual market pace). Spread-aware fills: buys on ask, "
                       "sells on bid.")
        else:
            st.caption("▦ Bar replay (no tick cache for this day) — 1-minute bars "
                       "with synthetic intra-bar stepping.")
        _handle_event(event, sess, day,
                      expected_key=f"replay:{instrument}:{day}:s{sid}:{sess_label}")

    with col_panel:
        _trade_panel(sess, day, open_trades, sym)

    tab_calc, tab_plan, tab_review, tab_sessions, tab_styles = st.tabs(
        ["🧮 Calculator", "📖 Pre-trade plan", "📔 Review", "💾 Sessions", "🎨 Line styles"])
    with tab_calc:
        _calculator(sess, day)
    with tab_plan:
        _pretrade_plan(sess, day)
    with tab_review:
        _review_tab(sess, day, sym)
    with tab_sessions:
        _sessions_tab(current=sess)
    with tab_styles:
        _styles_editor()


def _goto_day(new_day: str):
    st.session_state["_replay_day_pending"] = new_day
    st.rerun()


# ── Session picker (no active session) ────────────────────────────────────
def _session_picker():
    st.caption("Each session gets its own demo account (SIM-XXXXXX) so replay trades "
               "stay separate from your real journal. Reports can still be filtered "
               "to a session's account.")
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("▶ New session")
        instruments = md.list_instruments()
        if not instruments:
            st.warning("No data caches found — set up data in Instrument Reference → Data.")
            return
        from utils.instruments import label as inst_label
        cur = md.current_instrument()
        instrument = st.selectbox("Instrument", instruments,
                                  index=instruments.index(cur) if cur in instruments else 0,
                                  format_func=inst_label)
        name = st.text_input("Session name", value="")
        balance = st.number_input("Starting balance", 100.0, 10_000_000.0, 10000.0, 500.0)
        currency = st.selectbox("Currency", ["USD", "EUR", "GBP", "AUD"], index=0)
        if st.button("🚀 Start session", type="primary", disabled=not name.strip()):
            days = md.list_available_days(instrument)
            from database import fetch_all
            indexed = {r["date"] for r in fetch_all(
                "SELECT date FROM day_features WHERE instrument=?", (instrument,))}
            start_day = next((d for d in reversed(days) if d in indexed),
                             days[-1] if days else "")
            sid = re_.create_replay_session(name.strip(), instrument,
                                            start_day, balance, currency)
            st.session_state["replay_session_id"] = sid
            st.session_state.pop("replay_day", None)
            st.session_state.pop("replay_day_select", None)
            st.rerun()
    with c2:
        st.subheader("⏯ Resume session")
        _sessions_tab(current=None)


def _sessions_tab(current: dict | None):
    sessions = re_.list_sessions()
    if current:
        if st.button("💾 Save progress now", type="primary",
                     help="Snapshots the replay position and working orders."):
            day = st.session_state.get("replay_day", current["day"])
            _requests(current["id"], day).append(
                {"req_id": _next_id("r"), "action": "snapshot"})
            st.rerun()
        st.caption("Progress also auto-saves whenever you pause playback.")
    if not sessions:
        st.caption("No stored sessions yet.")
        return
    for s in sessions:
        with st.container(border=True):
            bal = re_.demo_balance(s["account_id"]) if s.get("account_id") else 0
            sym = re_.currency_symbol(s["account_id"]) if s.get("account_id") else "$"
            c1, c2, c3 = st.columns([2.4, 1, 1])
            with c1:
                cur_tag = " ✅ (current)" if current and s["id"] == current["id"] else ""
                st.markdown(f"**{s['name']}**{cur_tag}")
                st.caption(f"{s['instrument']} · {s['day']} · bar {s['cursor'] or 0} · "
                           f"{sym}{bal:,.2f} · updated {s['updated_at'][:16]}")
            with c2:
                if (not current or s["id"] != current["id"]) and \
                        st.button("Resume", key=f"res_{s['id']}", use_container_width=True):
                    st.session_state["replay_session_id"] = s["id"]
                    st.session_state["replay_day"] = s["day"]
                    st.session_state.pop("replay_day_select", None)
                    try:
                        st.session_state[_orders_key(s["id"], s["day"])] = \
                            json.loads(s.get("pending_orders") or "[]")
                    except ValueError:
                        pass
                    st.rerun()
            with c3:
                wipe = st.checkbox("wipe trades", key=f"wipe_{s['id']}",
                                   help="Also delete this session's demo account, trades and journal entries.")
                if st.button("🗑️", key=f"del_{s['id']}", use_container_width=True):
                    re_.delete_session(s["id"], wipe_account=wipe)
                    if current and s["id"] == current["id"]:
                        st.session_state.pop("replay_session_id", None)
                    st.rerun()


# ── Event handling ────────────────────────────────────────────────────────
def _handle_event(event, sess: dict, day: str, expected_key: str):
    """expected_key MUST be built from the exact data_key passed to lwchart —
    a mismatch silently rejects every event and wedges the component queue."""
    sid, account_id, instrument = sess["id"], sess["account_id"], sess["instrument"]
    if not event or not isinstance(event, dict) \
            or event.get("data_key") != expected_key:
        return
    if event.get("seq", 0) <= st.session_state.get(_seq_key(sid, day), 0):
        return
    st.session_state[_seq_key(sid, day)] = event["seq"]

    p = event.get("payload", {})
    if p.get("px") is not None:
        st.session_state[_px_key(sid, day)] = {
            "mid": float(p["px"]),
            "bid": float(p.get("bid") or p["px"]),
            "ask": float(p.get("ask") or p["px"]),
        }

    etype = event.get("type")
    if etype == "fill":
        _on_fill(p, sess, day)
    elif etype == "exit":
        _on_exit(p, sess, day)
    elif etype == "order_moved":
        _on_order_moved(p, sess, day)
    elif etype == "close_ack":
        _on_close_ack(p, sess, day)
    elif etype in ("paused", "snapshot"):
        re_.save_session(sid, day, int(p.get("cursor") or 0), int(p.get("sub_step") or 0),
                         float(p.get("speed") or 10), _pending_orders(sid, day))
        if etype == "snapshot":
            reqs = _requests(sid, day)
            req = next((r for r in reqs if r["req_id"] == p.get("req_id")), None)
            if req:
                reqs.remove(req)
            st.toast("Session progress saved.", icon="💾")
        st.rerun()
    elif etype == "day_complete":
        s = re_.day_stats(account_id, day)
        sym = re_.currency_symbol(account_id)
        re_.save_session(sid, day, int(p.get("cursor") or 0), 0,
                         float(p.get("speed") or 10), _pending_orders(sid, day))
        st.toast(f"Session complete — {s['trades']} trade(s), "
                 f"{sym}{s['pnl']:+,.2f} ({s['r']:+.2f}R)", icon="🏁")
    elif etype == "screenshot":
        _save_screenshot(p, day)
    # rerun so the component receives the updated ack (unjams its event queue);
    # handlers above that already called st.rerun() never reach this line
    st.rerun()


def _on_fill(p, sess, day):
    sid, account_id, instrument = sess["id"], sess["account_id"], sess["instrument"]
    orders = _pending_orders(sid, day)
    order = next((o for o in orders if o["id"] == p.get("order_id")), None)
    if order is None:
        return
    price = float(p["price"])
    if not re_.validate_fill(day, p.get("kind", ""), p.get("side", ""), price,
                             p.get("bar_time") or 0, instrument):
        st.toast(f"Fill at {price} rejected (not within bar range)", icon="⚠️")
        orders.remove(order)
        st.rerun()
        return
    trade_id = re_.open_replay_trade(
        account_id, day, order["side"], price, order["qty"],
        order.get("sl"), order.get("tp"), p.get("bar_time"), symbol=instrument)
    orders.remove(order)

    # attach the prepared pre-trade plan (journal entry + playbook) if any
    plan = st.session_state.pop(f"replay_plan:{sid}:{day}", None)
    if plan:
        from utils.trade_ops import update_trade_playbook
        from database import execute
        if plan.get("playbook_id"):
            update_trade_playbook(trade_id, plan["playbook_id"],
                                  plan.get("rules_met") or {}, plan.get("risk_score") or 0)
        if plan.get("entry_id"):
            execute("UPDATE journal_entries SET trade_id=? WHERE id=?",
                    (trade_id, plan["entry_id"]))
    st.toast(f"Filled {order['side'].upper()} {order['qty']} @ {price} (trade #{trade_id})"
             + (" · plan linked" if plan else ""), icon="✅")
    st.rerun()


def _on_exit(p, sess, day):
    account_id, instrument = sess["account_id"], sess["instrument"]
    trade_id = p.get("trade_id")
    t = next((x for x in re_.open_replay_trades(account_id, day) if x["id"] == trade_id), None)
    if not t:
        return
    price = float(p["price"])
    if not re_.validate_fill(day, "exit", "", price, p.get("bar_time") or 0, instrument):
        st.toast(f"Exit at {price} rejected (not within bar range)", icon="⚠️")
        return
    re_.close_replay_trade(trade_id, price, day, p.get("bar_time"))
    reason = p.get("reason", "").upper()
    st.toast(f"{reason} hit — trade #{trade_id} closed @ {price}",
             icon="🎯" if reason == "TP" else "🛑")
    st.rerun()


def _on_order_moved(p, sess, day):
    sid = sess["id"]
    oid, field, price = p.get("order_id"), p.get("field"), float(p.get("price") or 0)
    if p.get("kind") == "position":
        re_.update_trade_levels(oid, sl=price if field == "sl" else None,
                                tp=price if field == "tp" else None)
        st.rerun()
        return
    orders = _pending_orders(sid, day)
    order = next((o for o in orders if o["id"] == oid), None)
    if not order:
        return
    order[field] = price
    if field in ("price", "sl") and order.get("sl") is not None and order.get("_risk"):
        order["qty"] = re_.position_size(order["_risk"], order["price"], order["sl"])
    st.rerun()


def _on_close_ack(p, sess, day):
    sid, account_id = sess["id"], sess["account_id"]
    reqs = _requests(sid, day)
    req = next((r for r in reqs if r["req_id"] == p.get("req_id")), None)
    if req:
        reqs.remove(req)
    trade_id, price = p.get("trade_id"), p.get("price")
    if price is None:
        return
    t = next((x for x in re_.open_replay_trades(account_id, day) if x["id"] == trade_id), None)
    if not t:
        return
    # spread-aware manual close: longs sell on bid, shorts buy back on ask
    if t["direction"] == "LONG" and p.get("bid") is not None:
        price = p["bid"]
    elif t["direction"] == "SHORT" and p.get("ask") is not None:
        price = p["ask"]
    if p.get("action") == "partial":
        qty = float(p.get("qty") or 0)
        if 0 < qty < float(t["quantity"]):
            re_.partial_close_replay_trade(trade_id, qty, float(price), day, p.get("bar_time"))
            st.toast(f"Partial close {qty:g} @ {price}", icon="✂️")
    else:
        re_.close_replay_trade(trade_id, float(price), day, p.get("bar_time"))
        st.toast(f"Closed trade #{trade_id} @ {price}", icon="🔒")
    st.rerun()


def _save_screenshot(p, day: str):
    import base64
    from datetime import datetime
    from pathlib import Path
    b64 = p.get("png_base64")
    if not b64:
        return
    folder = Path(__file__).resolve().parent.parent / "data" / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"replay_{day}_{datetime.now().strftime('%H%M%S')}.png"
    path.write_bytes(base64.b64decode(b64))
    st.session_state["replay_last_screenshot"] = str(path)
    st.toast(f"Screenshot saved: {path.name}", icon="📷")


# ── Panels ────────────────────────────────────────────────────────────────
def _trade_panel(sess: dict, day: str, open_trades: list[dict], sym: str):
    sid, account_id = sess["id"], sess["account_id"]
    bal = re_.demo_balance(account_id)
    stats = re_.day_stats(account_id, day)
    last_px = st.session_state.get(_px_key(sid, day))

    def _mark(t):
        # mark-to-market on the closing side: longs vs bid, shorts vs ask
        if not last_px:
            return None
        return last_px["bid"] if t["direction"] == "LONG" else last_px["ask"]

    open_pnl = open_pts = 0.0
    open_r = 0.0
    if last_px:
        for t in open_trades:
            u = re_.unrealized(t, _mark(t))
            open_pnl += u["pnl"]
            open_pts += u["pts"]
            open_r += u["r"] or 0

    st.metric("Balance", f"{sym}{bal:,.2f}")
    m1, m2 = st.columns(2)
    with m1:
        st.metric("Open P&L", f"{sym}{open_pnl:+,.2f}" if open_trades and last_px else "—",
                  delta=(f"{open_pts:+.1f} pts · {open_r:+.2f}R"
                         if open_trades and last_px else None))
    with m2:
        st.metric("Closed (day)", f"{sym}{stats['pnl']:+,.2f}",
                  delta=f"{stats['r']:+.2f}R · {stats['trades']} trades" if stats["trades"] else None)
    if open_trades and not last_px:
        st.caption("Open P&L syncs on pause/fill (live value shows on the chart).")

    st.markdown("**Open position**" if open_trades else "*No open position*")
    for t in open_trades:
        with st.container(border=True):
            u = re_.unrealized(t, _mark(t)) if last_px else None
            upnl = (f" · {sym}{u['pnl']:+,.2f} ({u['pts']:+.1f}pts"
                    + (f", {u['r']:+.2f}R" if u["r"] is not None else "") + ")") if u else ""
            st.markdown(f"**#{t['id']} {t['direction']}** {t['quantity']:g} @ {t['entry_price']:.1f}{upnl}")
            st.caption(f"SL {t.get('stop_price') or '—'} · TP {t.get('take_profit') or '—'} · "
                       f"risk {sym}{t.get('planned_risk') or 0:.0f}")
            cols = st.columns(4)
            for col, (label, frac) in zip(cols, [("10%", 0.10), ("25%", 0.25),
                                                 ("50%", 0.50), ("All", 1.0)]):
                with col:
                    if st.button(label, key=f"cl_{t['id']}_{label}", use_container_width=True):
                        if frac >= 1.0:
                            _requests(sid, day).append({"req_id": _next_id("r"),
                                                        "trade_id": t["id"],
                                                        "action": "close", "qty": None})
                        else:
                            _requests(sid, day).append({"req_id": _next_id("r"),
                                                        "trade_id": t["id"], "action": "partial",
                                                        "qty": round(float(t["quantity"]) * frac, 2)})
                        st.rerun()

    pend = _pending_orders(sid, day)
    if pend:
        st.markdown("**Working orders**")
        for o in list(pend):
            with st.container(border=True):
                st.caption(f"{o['kind']} {o['side'].upper()} {o['qty']:g} @ {o['price']:.1f} · "
                           f"SL {o.get('sl') or '—'} · TP {o.get('tp') or '—'}")
                if st.button("Cancel", key=f"cxl_{o['id']}", use_container_width=True):
                    pend.remove(o)
                    st.rerun()

    closed = re_.closed_replay_trades(account_id, day)
    if closed:
        st.markdown("**Closed today**")
        for t in closed[-6:]:
            r = f" ({t['r_multiple']:+.2f}R)" if t.get("r_multiple") is not None else ""
            st.caption(f"#{t['id']} {t['direction']} {t['quantity']:g} → {sym}{t['pnl']:+,.2f}{r}")


def _calculator(sess: dict, day: str):
    sid, account_id, instrument = sess["id"], sess["account_id"], sess["instrument"]
    sym = re_.currency_symbol(account_id)
    st.caption("Sizing → arm an order → drag its lines on the chart. Fills happen at replay prices.")
    c0, c1, c2, c3, c4 = st.columns([1.1, 1, 1, 1, 1])
    with c0:
        mode = st.radio("Risk mode", [f"Fixed {sym.strip()}", "% of balance (compounding)"],
                        index=0 if md.get_setting("replay_sizing_mode", "fixed") == "fixed" else 1,
                        key="replay_risk_mode")
        mode_key = "fixed" if mode.startswith("Fixed") else "pct"
        if mode_key != md.get_setting("replay_sizing_mode", "fixed"):
            md.save_setting("replay_sizing_mode", mode_key)
    with c1:
        if mode_key == "fixed":
            risk_val = st.number_input(f"Risk {sym.strip()}", 10.0, 100000.0,
                                       float(md.get_setting("replay_risk_fixed", 100.0)), 10.0)
            md.save_setting("replay_risk_fixed", risk_val)
        else:
            pct = st.number_input("Risk %", 0.1, 10.0,
                                  float(md.get_setting("replay_risk_pct", 0.5)), 0.1)
            md.save_setting("replay_risk_pct", pct)
    with c2:
        side = st.selectbox("Side", ["buy", "sell"])
        kind = st.selectbox("Type", ["market", "stop", "limit"])
    with c3:
        feats = di.get_day(day, instrument)
        default_entry = float(feats["sr_long_entry"] if (feats and side == "buy")
                              else feats["sr_short_entry"] if feats else 0) or 0.0
        entry = st.number_input("Entry (ignored for market)", 0.0, 1000000.0, default_entry, 1.0)
        sl = st.number_input("Stop loss", 0.0, 1000000.0,
                             float(feats["sr_long_stop"] if (feats and side == "buy")
                                   else feats["sr_short_stop"] if feats else 0) or 0.0, 1.0)
    with c4:
        tp = st.number_input("Take profit (0 = none)", 0.0, 1000000.0, 0.0, 1.0)
        risk_amt = re_.risk_amount(account_id, mode_key,
                                   float(md.get_setting("replay_risk_fixed", 100.0)),
                                   float(md.get_setting("replay_risk_pct", 0.5)))
        ref_price = entry if kind != "market" else (entry or sl)
        qty = re_.position_size(risk_amt, ref_price, sl) if sl else 0.0
        rr = (abs(tp - ref_price) / abs(ref_price - sl)) if (tp and sl and ref_price != sl) else None
        st.metric("Size", f"{qty:g}",
                  delta=f"{sym}{risk_amt:.0f} risk" + (f" · {rr:.1f}R TP" if rr else ""))

    has_plan = bool(st.session_state.get(f"replay_plan:{sid}:{day}"))
    if has_plan:
        st.caption("📖 Pre-trade plan ready — it will be linked to the next fill.")
    if st.button("🎯 Arm order", type="primary", disabled=qty <= 0):
        _pending_orders(sid, day).append({
            "id": _next_id("o"), "kind": kind, "side": side,
            "price": ref_price, "sl": sl or None, "tp": tp or None,
            "qty": qty, "_risk": risk_amt, "draggable": True,
        })
        st.rerun()


def _pretrade_plan(sess: dict, day: str):
    """Pause → pick playbook, tick rules, write the plan → arm the trade.
    The saved plan (journal entry + playbook score) links to the next fill."""
    from database import fetch_all, execute
    from utils.playbook_logic import evaluate_trade_risk
    from utils.trade_ops import save_journal_entry

    sid, account_id = sess["id"], sess["account_id"]
    plan_key = f"replay_plan:{sid}:{day}"
    existing = st.session_state.get(plan_key)
    if existing:
        st.success("Plan saved and armed — it will attach to the next fill. "
                   "Save again to replace it.")

    playbooks = fetch_all("SELECT * FROM playbooks ORDER BY name")
    c1, c2 = st.columns([1, 2])
    with c1:
        pb_options = {"(no playbook)": None} | {p["name"]: p["id"] for p in playbooks}
        pb_name = st.selectbox("Playbook", list(pb_options), key=f"plan_pb_{day}")
        pb_id = pb_options[pb_name]
        rules_met, risk_score, risk_info = {}, None, None
        if pb_id:
            rules = fetch_all(
                "SELECT * FROM playbook_rules WHERE playbook_id=? ORDER BY sort_order, id", (pb_id,))
            st.markdown("**Rules**")
            for r in rules:
                icon = {"required": "🔴", "optional": "🟡", "bonus": "🟢"}.get(r["rule_type"], "")
                rules_met[r["id"]] = st.checkbox(f"{icon} {r['name']}",
                                                 key=f"plan_rule_{day}_{r['id']}")
            if rules:
                risk_info = evaluate_trade_risk(pb_id, rules_met)
                risk_score = risk_info.get("risk_score") if isinstance(risk_info, dict) else None
                if isinstance(risk_info, dict):
                    lvl = risk_info.get("risk_level", "normal")
                    st.metric("Playbook score", f"{risk_score:.0f}%" if risk_score is not None else "—",
                              delta=f"risk: {lvl}")
                    for w in risk_info.get("warnings", []) or []:
                        st.warning(w)
    with c2:
        analysis = st.text_area("Market analysis / context", height=90, key=f"plan_an_{day}")
        plan_txt = st.text_area("Trade plan (entry, stop, target, management)",
                                height=90, key=f"plan_tx_{day}")
        psych = st.text_area("Psychology check-in", height=60, key=f"plan_ps_{day}")

    if st.button("💾 Save plan & arm for next fill", type="primary"):
        entry_id = save_journal_entry(
            None, "trade", day, None, None, None, None, None, None,
            stage="pre", playbook_id=pb_id)
        execute("""UPDATE journal_entries SET pre_analysis=?, pre_plan=?, pre_psychology=?,
                   account_id=? WHERE id=?""",
                (analysis, plan_txt, psych, account_id, entry_id))
        st.session_state[plan_key] = {
            "entry_id": entry_id, "playbook_id": pb_id,
            "rules_met": rules_met, "risk_score": risk_score,
        }
        st.success("Plan saved — arm your order in the Calculator tab; the plan links on fill.")


def _review_tab(sess: dict, day: str, sym: str):
    from utils.trade_ops import save_journal_entry, get_journal_entry_for_date
    from database import execute
    sid, account_id = sess["id"], sess["account_id"]
    closed = re_.closed_replay_trades(account_id, day)
    if closed:
        total = sum(float(t.get("pnl") or 0) for t in closed)
        r = sum(float(t.get("r_multiple") or 0) for t in closed)
        st.caption(f"{len(closed)} closed trade(s) today · {sym}{total:+,.2f} · {r:+.2f}R")
    shot = st.session_state.get("replay_last_screenshot")
    if shot:
        st.caption(f"📷 Last screenshot: `{shot}`")
    existing = get_journal_entry_for_date("daily", f"replay-{day}")
    with st.form(f"replay_journal_{sid}_{day}"):
        analysis = st.text_area("What did you see? (setup, context)",
                                value=(existing or {}).get("analysis") or "", height=100)
        execution = st.text_area("Execution & management",
                                 value=(existing or {}).get("execution") or "", height=80)
        lessons = st.text_area("Lessons", value=(existing or {}).get("lessons") or "", height=68)
        grade = st.selectbox("Grade", ["", "A", "B", "C", "D"],
                             index=["", "A", "B", "C", "D"].index((existing or {}).get("grade") or ""))
        if st.form_submit_button("💾 Save review"):
            eid = save_journal_entry((existing or {}).get("id"), "daily", f"replay-{day}",
                                     analysis, execution, None, lessons, grade or None, None)
            execute("UPDATE journal_entries SET account_id=? WHERE id=?", (account_id, eid))
            st.success("Saved.")
    if st.button("📔 Open main Journal"):
        st.session_state["page"] = "journal"
        st.rerun()


def _styles_editor():
    styles = _level_styles()
    cols = st.columns(len(styles))
    changed = False
    for col, (key, s) in zip(cols, styles.items()):
        with col:
            st.markdown(f"**{LEVEL_LABELS[key]}**")
            color = st.color_picker("Color", s["color"], key=f"ls_c_{key}")
            style = st.selectbox("Type", ["solid", "dotted", "dashed"],
                                 index=["solid", "dotted", "dashed"].index(s["style"]),
                                 key=f"ls_s_{key}")
            width = st.slider("Width", 1, 4, s["width"], key=f"ls_w_{key}")
            if (color, style, width) != (s["color"], s["style"], s["width"]):
                styles[key].update({"color": color, "style": style, "width": width})
                changed = True
    if changed:
        md.save_setting("replay_level_styles", json.dumps(styles))
        st.rerun()
