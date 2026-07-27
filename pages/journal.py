"""
Page: Journal — daily, weekly, per-trade with pre/post split and pre-trade planning entries
"""
import streamlit as st
from datetime import date, timedelta
import calendar as _cal
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all, execute
from utils.trade_ops import (
    get_journal_entries, save_journal_entry, delete_journal_entry,
    get_templates, save_template, get_trades
)
from utils.playbook_logic import get_playbooks, get_playbook
from utils.accounts import get_accounts
from utils.theme import get_theme, _PALETTES

GRADE_OPTIONS = ["", "A+", "A", "B+", "B", "C+", "C", "D", "F"]


def _p():
    t = get_theme()
    return _PALETTES.get(t, _PALETTES["dark"])


def show():
    st.header("📔 Journal")

    # ── Account filter ─────────────────────────────────────────────────────────
    accounts = get_accounts()
    p = _p()

    if accounts:
        acc_col, _ = st.columns([2, 5])
        with acc_col:
            acc_options = {f"{a['name']}  ({a['broker']})": a["id"] for a in accounts}
            acc_list = ["All Accounts"] + list(acc_options.keys())
            if "journal_account" not in st.session_state:
                st.session_state["journal_account"] = "All Accounts"
            sel_label = st.selectbox(
                "Account", acc_list,
                index=acc_list.index(st.session_state["journal_account"])
                      if st.session_state["journal_account"] in acc_list else 0,
                key="journal_acc_sel",
                label_visibility="collapsed",
            )
            st.session_state["journal_account"] = sel_label
            account_id = acc_options.get(sel_label)
    else:
        account_id = None

    st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)

    tab_overview, tab_daily, tab_weekly, tab_trade, tab_pretrade, tab_templates = st.tabs([
        "🗓️ Overview", "📅 Daily", "📆 Weekly", "📊 Trade Notes", "📋 Pre-Trade Plan", "🗂️ Templates"
    ])

    # Switch to requested tab if a button set one
    if "_pending_tab" in st.session_state:
        _js_switch_tab(st.session_state.pop("_pending_tab"))

    with tab_overview:  _calendar_overview(account_id)
    with tab_daily:     _daily_journal(account_id)
    with tab_weekly:    _weekly_journal(account_id)
    with tab_trade:     _trade_journal(account_id)
    with tab_pretrade:  _pretrade_journal(account_id)
    with tab_templates: _template_manager()


# ── helpers ───────────────────────────────────────────────────────────────────

# Tab indices: 0=Overview, 1=Daily, 2=Weekly, 3=Trade Notes, 4=Pre-Trade Plan, 5=Templates
def _js_switch_tab(index: int):
    import streamlit.components.v1 as _c
    _c.html(
        f"""<script>
        setTimeout(function(){{
            var tabs = window.parent.document.querySelectorAll('button[data-baseweb="tab"]');
            if (tabs[{index}]) tabs[{index}].click();
        }}, 120);
        </script>""",
        height=0,
        scrolling=False,
    )


def _get_entry(entry_type, entry_date, trade_id=None, stage=None):
    where = "entry_type=? AND entry_date=?"
    params = [entry_type, str(entry_date)]
    if trade_id:
        where += " AND trade_id=?"
        params.append(trade_id)
    if stage:
        where += " AND stage=?"
        params.append(stage)
    rows = fetch_all(
        f"SELECT * FROM journal_entries WHERE {where} ORDER BY created_at DESC LIMIT 1", params
    )
    return rows[0] if rows else None


def _get_position_entry(position_id, stage=None):
    where = "entry_type='trade' AND position_id=?"
    params = [position_id]
    if stage:
        where += " AND stage=?"
        params.append(stage)
    rows = fetch_all(
        f"SELECT * FROM journal_entries WHERE {where} ORDER BY created_at DESC LIMIT 1", params
    )
    return rows[0] if rows else None


def _get_default_template(template_type):
    templates = get_templates(template_type)
    return next((t for t in templates if t["is_default"]), templates[0] if templates else None)


def _acc_where(account_id, prefix=""):
    """Return (where_clause_fragment, params) for account filtering on trades table."""
    if account_id:
        return f"{prefix}account_id=?", [account_id]
    return "1=1", []


# ── Calendar Overview ─────────────────────────────────────────────────────────

def _calendar_overview(account_id=None):
    p = _p()

    # Month/year selector
    today = date.today()
    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        year = st.number_input("Year", min_value=2000, max_value=today.year + 1,
                               value=today.year, step=1, key="cal_ov_year")
    with c2:
        month = st.selectbox("Month", list(range(1, 13)),
                             index=today.month - 1,
                             format_func=lambda m: _cal.month_name[m],
                             key="cal_ov_month")

    month_start = date(int(year), int(month), 1)
    _, num_days  = _cal.monthrange(int(year), int(month))
    month_end    = date(int(year), int(month), num_days)

    # Pull all closed trades for the month
    acc_frag, acc_params = _acc_where(account_id)
    trades_month = fetch_all(
        f"""SELECT id, symbol, direction, pnl, entry_time
            FROM trades
            WHERE status='closed'
              AND DATE(entry_time) BETWEEN ? AND ?
              AND {acc_frag}
            ORDER BY entry_time""",
        [str(month_start), str(month_end)] + acc_params,
    )

    # Days on which any trade (open or closed) was ENTERED
    entered_rows = fetch_all(
        f"""SELECT DATE(entry_time) as d, COUNT(*) as n
            FROM trades
            WHERE DATE(entry_time) BETWEEN ? AND ?
              AND {acc_frag}
            GROUP BY DATE(entry_time)""",
        [str(month_start), str(month_end)] + acc_params,
    )
    day_entered = {r["d"]: r["n"] for r in entered_rows}

    # Pull all journal entries for the month
    journal_month = fetch_all(
        """SELECT id, entry_type, entry_date, trade_id, stage, grade
           FROM journal_entries
           WHERE entry_date BETWEEN ? AND ?
           ORDER BY entry_date""",
        [str(month_start), str(month_end)],
    )

    # Build per-day lookup
    from collections import defaultdict
    day_trades   = defaultdict(list)   # date_str -> [trade rows]
    day_journal  = defaultdict(list)   # date_str -> [journal rows]
    trade_has_post = {}                # trade_id -> bool

    for t in trades_month:
        d = (t["entry_time"] or "")[:10]
        day_trades[d].append(t)

    # Also find which positions have a post journal entry
    pos_reviewed = set()
    pos_rows = fetch_all(
        "SELECT DISTINCT position_id FROM journal_entries WHERE stage='post' AND position_id IS NOT NULL"
    )
    for r in pos_rows:
        pos_reviewed.add(r["position_id"])

    for j in journal_month:
        day_journal[j["entry_date"]].append(j)
        if j.get("stage") == "post":
            if j.get("trade_id"):
                trade_has_post[j["trade_id"]] = True
            if j.get("position_id"):
                # Mark all trades in this position as reviewed
                pt_rows = fetch_all(
                    "SELECT id FROM trades WHERE position_id=?", (j["position_id"],)
                )
                for pt in pt_rows:
                    trade_has_post[pt["id"]] = True

    # Month summary bar
    total_trades = len(trades_month)
    if total_trades:
        net_pnl  = sum(float(t.get("pnl") or 0) for t in trades_month)
        wins     = sum(1 for t in trades_month if float(t.get("pnl") or 0) > 0)
        daily_entries  = sum(1 for j in journal_month if j["entry_type"] == "daily")
        trade_reviewed = sum(1 for t in trades_month if trade_has_post.get(t["id"]))
        pnl_col = p["--accent"] if net_pnl >= 0 else p["--danger"]

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Trades", total_trades)
        c2.metric("Win Rate", f"{wins/total_trades*100:.0f}%")
        c3.metric("Net P&L", f"{net_pnl:+,.2f}")
        c4.metric("Daily Entries", f"{daily_entries} / {len(set(d[:7] for d in day_trades))} days")
        c5.metric("Trades Reviewed", f"{trade_reviewed} / {total_trades}")
    else:
        st.caption(f"No closed trades in {_cal.month_name[int(month)]} {year}.")

    st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)

    # Legend
    st.markdown(
        f'<div style="display:flex;gap:16px;font-size:0.72rem;color:{p["--text-muted"]};margin-bottom:8px;">'
        f'<span>🟢 Profitable + journaled</span>'
        f'<span>🟡 Profitable, not journaled</span>'
        f'<span>🔴 Loss + journaled</span>'
        f'<span>🟠 Loss, not journaled</span>'
        f'<span>🔷 Trade entered</span>'
        f'<span>⬜ No trades</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Day-of-week headers
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hdr = st.columns(7)
    for i, d in enumerate(dow_names):
        hdr[i].markdown(
            f'<div style="text-align:center;font-size:0.7rem;font-weight:600;'
            f'color:{p["--text-muted"]};padding:4px 0 2px;text-transform:uppercase;letter-spacing:1px;">{d}</div>',
            unsafe_allow_html=True,
        )

    # Calendar grid
    start_dow = month_start.weekday()  # 0=Mon
    day = 1
    for week in range(6):
        if day > num_days:
            break
        cols = st.columns(7)
        for dow in range(7):
            if week == 0 and dow < start_dow:
                cols[dow].markdown(
                    f'<div style="min-height:90px;background:{p["--bg-app"]};border-radius:6px;"></div>',
                    unsafe_allow_html=True,
                )
                continue
            if day > num_days:
                cols[dow].markdown(
                    f'<div style="min-height:90px;background:{p["--bg-app"]};border-radius:6px;"></div>',
                    unsafe_allow_html=True,
                )
                continue

            d_str = f"{year}-{int(month):02d}-{day:02d}"
            t_list = day_trades.get(d_str, [])
            j_list = day_journal.get(d_str, [])

            has_daily  = any(j["entry_type"] == "daily" for j in j_list)
            n_trades   = len(t_list)
            reviewed   = sum(1 for t in t_list if trade_has_post.get(t["id"]))
            net        = sum(float(t.get("pnl") or 0) for t in t_list)
            is_today   = (date(int(year), int(month), day) == date.today())

            if n_trades == 0:
                # No closed trades — grey; still flag daily journal / trade entries
                n_entered = day_entered.get(d_str, 0)
                bg = p["--bg-card2"]
                border = p["--accent"] if has_daily else p["--border"]
                j_icon = "📝" if has_daily else ""
                entered_icon = f'🔷<span style="font-size:0.6rem;color:{p["--text-faint"]};">{n_entered}</span>' if n_entered else ""
                today_ring = f"outline:2px solid {p['--warning']};" if is_today else ""
                cols[dow].markdown(
                    f'<div style="min-height:90px;background:{bg};border:1px solid {border};'
                    f'border-radius:6px;padding:5px 7px;{today_ring}">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                    f'<span style="color:{p["--text-faint"]};font-size:0.7rem;font-weight:700;">{day}</span>'
                    f'<span style="font-size:0.6rem;">{entered_icon}</span>'
                    f'</div>'
                    f'<div style="font-size:0.8rem;margin-top:4px;">{j_icon}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                is_pos = net >= 0
                journaled = has_daily and reviewed == n_trades
                partial   = has_daily or reviewed > 0

                if is_pos and journaled:
                    bg, border = "rgba(0,200,150,0.15)", p["--accent"]
                    dot = "🟢"
                elif is_pos and partial:
                    bg, border = "rgba(0,200,150,0.08)", p["--accent"]
                    dot = "🟡"
                elif is_pos:
                    bg, border = "rgba(245,166,35,0.12)", p["--warning"]
                    dot = "🟡"
                elif journaled:
                    bg, border = "rgba(255,75,110,0.15)", p["--danger"]
                    dot = "🔴"
                elif partial:
                    bg, border = "rgba(255,75,110,0.10)", p["--danger"]
                    dot = "🟠"
                else:
                    bg, border = "rgba(255,75,110,0.08)", p["--danger"]
                    dot = "🟠"

                vc = p["--accent"] if is_pos else p["--danger"]
                review_txt = (
                    f'<div style="color:{p["--text-faint"]};font-size:0.6rem;">✅ {reviewed}/{n_trades} reviewed</div>'
                    if n_trades > 0 else ""
                )
                today_ring = f"outline:2px solid {p['--warning']};" if is_today else ""

                entered_icon = "🔷" if day_entered.get(d_str) else ""
                cols[dow].markdown(
                    f'<div style="min-height:90px;background:{bg};border:1px solid {border};'
                    f'border-radius:6px;padding:5px 7px;{today_ring}">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                    f'<span style="color:{p["--text-muted"]};font-size:0.7rem;font-weight:700;">{day}</span>'
                    f'<span style="font-size:0.65rem;">{entered_icon}{dot}</span>'
                    f'</div>'
                    f'<div style="font-family:\'JetBrains Mono\';font-weight:700;color:{vc};'
                    f'font-size:0.75rem;margin-top:3px;">{net:+,.0f}</div>'
                    f'<div style="color:{p["--text-muted"]};font-size:0.62rem;">{n_trades} trade{"s" if n_trades>1 else ""}</div>'
                    f'{review_txt}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            day += 1

    # ── Day drill-down ─────────────────────────────────────────────────────────
    st.divider()
    st.markdown("#### Day Detail")

    # Collect trading days for the month
    trading_days = sorted(day_trades.keys(), reverse=True)
    all_days = sorted(
        [f"{year}-{int(month):02d}-{d:02d}" for d in range(1, num_days + 1)],
        reverse=True,
    )

    sel_day = st.selectbox(
        "Select day",
        all_days,
        index=0,
        key="cal_ov_day_sel",
        format_func=lambda d: f"{d}{'  ⚡ ' + str(len(day_trades[d])) + ' trade(s)' if d in day_trades else '  (no trades)'}",
    )

    if sel_day:
        t_list = day_trades.get(sel_day, [])
        j_list = day_journal.get(sel_day, [])
        has_daily = any(j["entry_type"] == "daily" for j in j_list)

        col_a, col_b = st.columns([3, 1])
        with col_a:
            if t_list:
                net = sum(float(t.get("pnl") or 0) for t in t_list)
                reviewed = sum(1 for t in t_list if trade_has_post.get(t["id"]))
                pnl_col = p["--accent"] if net >= 0 else p["--danger"]
                st.markdown(
                    f'<div style="font-size:0.85rem;color:{p["--text-muted"]};">'
                    f'<b style="color:{pnl_col};">{net:+,.2f}</b> &nbsp;·&nbsp; '
                    f'{len(t_list)} trade{"s" if len(t_list)>1 else ""} &nbsp;·&nbsp; '
                    f'{reviewed}/{len(t_list)} reviewed</div>',
                    unsafe_allow_html=True,
                )
                for t in t_list:
                    t_pnl = float(t.get("pnl") or 0)
                    has_post = trade_has_post.get(t["id"])
                    badge_col = p["--accent"] if has_post else p["--warning"]
                    badge_txt = "✅ Reviewed" if has_post else "⚠️ Not reviewed"
                    pnl_col2 = p["--accent"] if t_pnl >= 0 else p["--danger"]
                    st.markdown(
                        f'<div style="display:flex;align-items:center;justify-content:space-between;'
                        f'padding:7px 12px;margin:3px 0;background:{p["--bg-card"]};'
                        f'border:1px solid {p["--border"]};border-radius:6px;">'
                        f'<div>'
                        f'<span style="font-weight:600;color:{p["--text-primary"]};">#{t["id"]} {t["symbol"]}</span>'
                        f'<span style="font-size:0.72rem;color:{p["--text-muted"]};margin-left:8px;">{t["direction"]}</span>'
                        f'</div>'
                        f'<div style="display:flex;align-items:center;gap:12px;">'
                        f'<span style="font-family:\'JetBrains Mono\';color:{pnl_col2};font-weight:600;">{t_pnl:+,.2f}</span>'
                        f'<span style="font-size:0.7rem;color:{badge_col};border:1px solid {badge_col};'
                        f'border-radius:4px;padding:1px 6px;">{badge_txt}</span>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if not has_post:
                        if st.button(f"Write review for #{t['id']} {t['symbol']}",
                                     key=f"goto_review_{t['id']}"):
                            st.session_state["trade_journal_selected"] = t["id"]
                            st.session_state["_pending_tab"] = 3  # Trade Notes
                            st.rerun()
            else:
                st.caption("No trades on this day.")

        with col_b:
            daily_entry = next((j for j in j_list if j["entry_type"] == "daily"), None)
            if daily_entry:
                grade_txt = f" · Grade: **{daily_entry['grade']}**" if daily_entry.get("grade") else ""
                st.success(f"📝 Daily journal entry exists{grade_txt}")
            else:
                st.warning("📝 No daily journal entry")
                if st.button("Write daily entry", key=f"goto_daily_{sel_day}"):
                    st.session_state["daily_date_prefill"] = sel_day
                    st.session_state["_pending_tab"] = 1  # Daily tab
                    st.rerun()

    # ── Menaker Category Breakdown ─────────────────────────────────────────────
    _menaker_category_stats(month_start, month_end, account_id, p)

    # ── Open Positions ─────────────────────────────────────────────────────────
    _open_positions_section(account_id, p)


# ── Menaker Category Breakdown ────────────────────────────────────────────────

def _menaker_category_stats(month_start, month_end, account_id=None, p=None):
    if p is None:
        p = _p()

    acc_frag, acc_params = _acc_where(account_id, prefix="t.")
    cat_rows = fetch_all(
        f"""SELECT je.trade_category, COUNT(*) as cnt,
                   SUM(CASE WHEN t.pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(t.pnl) as total_pnl
            FROM journal_entries je
            JOIN trades t ON (je.trade_id = t.id OR je.position_id = t.position_id)
            WHERE je.stage='post' AND je.trade_category IS NOT NULL
              AND DATE(t.entry_time) BETWEEN ? AND ?
              AND {acc_frag}
            GROUP BY je.trade_category""",
        [str(month_start), str(month_end)] + acc_params,
    )

    if not cat_rows:
        return

    st.divider()
    st.markdown("#### 📊 Trade Categories *(Menaker Framework)*")

    cat_data = {r["trade_category"]: r for r in cat_rows}
    total = sum(r["cnt"] for r in cat_rows)

    cols = st.columns(4)
    for i, (cat_num, label_short, color) in enumerate([
        (1, "Type 1\nOn-plan + Win",  "#00c896"),
        (2, "Type 2\nOn-plan + Stop", "#4a9eff"),
        (3, "Type 3\nOff-plan + Loss","#ff4b6e"),
        (4, "Type 4\nOff-plan + Win", "#f5a623"),
    ]):
        row = cat_data.get(cat_num)
        cnt = row["cnt"] if row else 0
        pnl = row["total_pnl"] if row else 0
        pct = cnt / total * 100 if total else 0
        with cols[i]:
            st.markdown(
                f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border"]};'
                f'border-left:4px solid {color};border-radius:8px;padding:10px 12px;min-height:90px;">'
                f'<div style="font-size:0.68rem;color:{p["--text-muted"]};white-space:pre-line;line-height:1.4;">{label_short}</div>'
                f'<div style="font-size:1.5rem;font-weight:700;color:{color};margin:4px 0 2px;">{cnt}</div>'
                f'<div style="font-size:0.7rem;color:{p["--text-faint"]};">{pct:.0f}% of categorised</div>'
                f'<div style="font-family:\'JetBrains Mono\';font-size:0.72rem;color:{p["--text-muted"]};margin-top:2px;">P&L {pnl:+,.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Goal callout
    t3 = cat_data.get(3, {}).get("cnt", 0)
    t4 = cat_data.get(4, {}).get("cnt", 0)
    off_plan = t3 + t4
    on_plan  = cat_data.get(1, {}).get("cnt", 0) + cat_data.get(2, {}).get("cnt", 0)
    if off_plan > 0:
        goal_pct = off_plan / total * 100 if total else 0
        st.markdown(
            f'<div style="margin-top:8px;font-size:0.8rem;color:{p["--text-muted"]};">'
            f'Off-plan trades (Type 3+4): <b style="color:{p["--warning"]};">{off_plan} ({goal_pct:.0f}%)</b> — '
            f'<span style="color:{p["--text-faint"]};">Goal: reduce these by 50% month-over-month. '
            f'On-plan rate: {on_plan/(on_plan+off_plan)*100:.0f}%</span></div>',
            unsafe_allow_html=True,
        )


# ── Open Positions ────────────────────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def _live_quote(symbol: str, exchange_hint: str, since_iso: str):
    """
    Resolve a journal symbol to yfinance, return (yf_symbol, last_price, div_per_share_since).
    Returns None if no data found. Cached 10 min.
    """
    import yfinance as yf
    import pandas as pd

    sym = symbol.strip().upper()
    if "." in sym:
        candidates = [sym]
    elif exchange_hint.upper() in ("ASX", "AU"):
        candidates = [f"{sym}.AX", sym]
    else:
        candidates = [sym, f"{sym}.AX"]

    for cand in candidates:
        try:
            t = yf.Ticker(cand)
            h = t.history(period="5d")
            if h is None or h.empty:
                continue
            price = float(h["Close"].iloc[-1])
            div_ps = 0.0
            try:
                divs = t.dividends
                if divs is not None and len(divs) and since_iso:
                    # Entitled only if held BEFORE the ex-date — buying on the
                    # ex-date itself earns nothing, so the ex-DATE must be
                    # strictly after the entry DATE (compare at day level).
                    since_ts = pd.Timestamp(since_iso[:10])
                    if divs.index.tz is not None:
                        since_ts = since_ts.tz_localize(divs.index.tz)
                    div_ps = float(divs[divs.index.normalize() > since_ts].sum())
            except Exception:
                pass
            return cand, price, div_ps
        except Exception:
            continue
    return None


def _trade_exchange_hint(raw_data) -> str:
    import json as _json
    try:
        d = _json.loads(raw_data) if raw_data else None
        if isinstance(d, dict):
            return d.get("exchange", "") or ""
    except Exception:
        pass
    return ""


def _open_positions_section(account_id=None, p=None):
    if p is None:
        p = _p()

    acc_frag, acc_params = _acc_where(account_id)
    open_trades = fetch_all(
        f"""SELECT id, symbol, direction, broker, quantity, entry_price, entry_time,
                   commission, position_id, account_id, raw_data
            FROM trades
            WHERE status='open' AND {acc_frag}
            ORDER BY entry_time DESC""",
        acc_params,
    )

    st.divider()
    st.markdown(f"#### 📂 Open Positions ({len(open_trades)})")

    if not open_trades:
        st.caption("No open positions.")
        return

    # ── Live quotes: unrealised P&L + dividends since entry ──────────────────
    quotes = {}   # trade_id -> (yf_sym, price, div_ps)
    with st.spinner("Fetching live prices…"):
        for t in open_trades:
            q = _live_quote(t["symbol"], _trade_exchange_hint(t.get("raw_data")),
                            (t.get("entry_time") or "")[:10])
            if q:
                quotes[t["id"]] = q

    total_unreal = total_divs = 0.0
    priced = 0
    for t in open_trades:
        q = quotes.get(t["id"])
        if not q:
            continue
        _, price, div_ps = q
        ep, qty = float(t.get("entry_price") or 0), float(t.get("quantity") or 0)
        sign = 1 if t["direction"] == "LONG" else -1
        total_unreal += (price - ep) * qty * sign
        total_divs   += div_ps * qty * sign
        priced += 1

    if priced:
        net = total_unreal + total_divs
        net_col = p["--accent"] if net >= 0 else p["--danger"]
        div_txt = (f' <span style="color:{p["--text-faint"]};">(incl. '
                   f'{total_divs:+,.2f} dividends)</span>') if abs(total_divs) > 0.005 else ""
        missing = len(open_trades) - priced
        miss_txt = (f' <span style="color:{p["--text-faint"]};">· {missing} unpriced</span>'
                    if missing else "")
        st.markdown(
            f'<div style="font-size:0.9rem;margin-bottom:8px;color:{p["--text-muted"]};">'
            f'Open P&L: <b style="font-family:\'JetBrains Mono\';color:{net_col};">{net:+,.2f}</b>'
            f'{div_txt}{miss_txt}</div>',
            unsafe_allow_html=True,
        )

    # Check which open trades have any journal entry (pre or post)
    open_ids = [t["id"] for t in open_trades]
    if open_ids:
        placeholders = ",".join("?" * len(open_ids))
        journaled_rows = fetch_all(
            f"SELECT DISTINCT trade_id FROM journal_entries WHERE trade_id IN ({placeholders})",
            open_ids,
        )
        journaled_trade_ids = {r["trade_id"] for r in journaled_rows}

        # Also check position-level journal entries
        pos_ids = list({t["position_id"] for t in open_trades if t.get("position_id")})
        journaled_pos_ids = set()
        if pos_ids:
            pp = ",".join("?" * len(pos_ids))
            pos_j_rows = fetch_all(
                f"SELECT DISTINCT position_id FROM journal_entries WHERE position_id IN ({pp})",
                pos_ids,
            )
            journaled_pos_ids = {r["position_id"] for r in pos_j_rows}
    else:
        journaled_trade_ids = set()
        journaled_pos_ids = set()

    today = date.today()

    # Build display rows
    rows_data = []
    for t in open_trades:
        try:
            entry_dt = date.fromisoformat((t["entry_time"] or "")[:10])
            days_held = (today - entry_dt).days
            entry_date_str = entry_dt.strftime("%d %b %Y")
        except Exception:
            days_held = 0
            entry_date_str = (t["entry_time"] or "")[:10]

        has_journal = (
            t["id"] in journaled_trade_ids or
            (t.get("position_id") and t["position_id"] in journaled_pos_ids)
        )
        journal_badge = "✅ Has notes" if has_journal else "⚠️ No notes"
        badge_col = p["--accent"] if has_journal else p["--warning"]

        direction_col = p["--accent"] if t["direction"] == "LONG" else p["--danger"]

        rows_data.append((t, entry_date_str, days_held, journal_badge, badge_col, direction_col, has_journal))

    # Render as styled cards
    for t, entry_date_str, days_held, journal_badge, badge_col, direction_col, has_journal in rows_data:
        ep = float(t.get("entry_price") or 0)
        qty = float(t.get("quantity") or 0)
        comm = float(t.get("commission") or 0)

        q = quotes.get(t["id"])
        if q:
            _, last_price, div_ps = q
            sign = 1 if t["direction"] == "LONG" else -1
            unreal = (last_price - ep) * qty * sign
            div_amt = div_ps * qty * sign
            open_pnl = unreal + div_amt
            pnl_col = p["--accent"] if open_pnl >= 0 else p["--danger"]
            last_cell = (
                f'<div style="text-align:right;">'
                f'<div style="font-size:0.7rem;color:{p["--text-muted"]};">Last</div>'
                f'<div style="font-family:\'JetBrains Mono\';font-size:0.8rem;color:{p["--text-primary"]};">{last_price:,.4f}</div>'
                f'</div>'
                f'<div style="text-align:right;">'
                f'<div style="font-size:0.7rem;color:{p["--text-muted"]};">Open P&L</div>'
                f'<div style="font-family:\'JetBrains Mono\';font-size:0.8rem;font-weight:700;color:{pnl_col};">{open_pnl:+,.2f}</div>'
                f'</div>'
            )
            if abs(div_amt) > 0.005:
                last_cell += (
                    f'<div style="text-align:right;">'
                    f'<div style="font-size:0.7rem;color:{p["--text-muted"]};">Divs</div>'
                    f'<div style="font-family:\'JetBrains Mono\';font-size:0.78rem;color:{p["--accent"]};">{div_amt:+,.2f}</div>'
                    f'</div>'
                )
        else:
            last_cell = (
                f'<div style="text-align:right;">'
                f'<div style="font-size:0.7rem;color:{p["--text-muted"]};">Last</div>'
                f'<div style="font-size:0.8rem;color:{p["--text-faint"]};">—</div>'
                f'</div>'
            )

        col_main, col_action = st.columns([6, 1])
        with col_main:
            st.markdown(
                f'<div style="display:flex;align-items:center;justify-content:space-between;'
                f'padding:8px 14px;background:{p["--bg-card"]};border:1px solid {p["--border"]};'
                f'border-radius:8px;">'
                f'<div style="display:flex;align-items:center;gap:16px;">'
                f'<div>'
                f'<span style="font-weight:700;font-size:0.9rem;color:{p["--text-primary"]};">#{t["id"]} {t["symbol"]}</span>'
                f'<span style="font-size:0.72rem;color:{direction_col};font-weight:600;margin-left:8px;">{t["direction"]}</span>'
                f'<span style="font-size:0.7rem;color:{p["--text-faint"]};margin-left:8px;">{t.get("broker","")}</span>'
                f'</div>'
                f'</div>'
                f'<div style="display:flex;align-items:center;gap:20px;">'
                f'<div style="text-align:right;">'
                f'<div style="font-size:0.7rem;color:{p["--text-muted"]};">Qty</div>'
                f'<div style="font-family:\'JetBrains Mono\';font-size:0.8rem;color:{p["--text-primary"]};">{qty:g}</div>'
                f'</div>'
                f'<div style="text-align:right;">'
                f'<div style="font-size:0.7rem;color:{p["--text-muted"]};">Entry</div>'
                f'<div style="font-family:\'JetBrains Mono\';font-size:0.8rem;color:{p["--text-primary"]};">{ep:.4f}</div>'
                f'</div>'
                f'{last_cell}'
                f'<div style="text-align:right;">'
                f'<div style="font-size:0.7rem;color:{p["--text-muted"]};">Days held</div>'
                f'<div style="font-size:0.78rem;color:{p["--text-primary"]};">{days_held}d</div>'
                f'</div>'
                f'<div>'
                f'<span style="font-size:0.7rem;color:{badge_col};border:1px solid {badge_col};'
                f'border-radius:4px;padding:2px 7px;">{journal_badge}</span>'
                f'</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with col_action:
            btn_label = "Add note" if has_journal else "Write plan"
            if st.button(btn_label, key=f"open_goto_{t['id']}", use_container_width=True):
                st.session_state["trade_journal_selected"] = t["id"]
                st.session_state["_pending_tab"] = 3  # Trade Notes
                st.rerun()


# ── Daily ─────────────────────────────────────────────────────────────────────

def _daily_journal(account_id=None):
    p = _p()
    col1, col2 = st.columns([2, 1])
    with col1:
        prefill = st.session_state.pop("daily_date_prefill", None)
        default_date = date.fromisoformat(prefill) if prefill else date.today()
        selected_date = st.date_input("Date", value=default_date, key="daily_date")
    with col2:
        templates = get_templates("daily")
        template  = None
        if templates:
            sel_t    = st.selectbox("Template", ["None"] + [t["name"] for t in templates], key="daily_tmpl")
            template = next((t for t in templates if t["name"] == sel_t), None) if sel_t != "None" else None

    entry = _get_entry("daily", selected_date)
    st.subheader(f"{selected_date.strftime('%A, %d %B %Y')}")

    # Day's trades summary
    acc_frag, acc_params = _acc_where(account_id)
    day_trades = fetch_all(
        f"SELECT id, symbol, direction, pnl FROM trades WHERE status='closed' AND DATE(entry_time)=? AND {acc_frag}",
        [str(selected_date)] + acc_params,
    )
    if day_trades:
        import pandas as pd
        df = pd.DataFrame(day_trades)
        df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").round(2)
        total_pnl = df["pnl"].sum()
        wins = (df["pnl"] > 0).sum()
        pnl_col = "#00c896" if total_pnl >= 0 else "#ff4b6e"
        st.markdown(f"""
        <div style="display:flex;gap:16px;margin-bottom:8px;font-size:0.85rem;">
            <span>📊 <b>{len(day_trades)}</b> trades</span>
            <span>✅ <b>{wins}</b> wins</span>
            <span style="color:{pnl_col};font-weight:700;">P&L: {total_pnl:+.2f}</span>
        </div>""", unsafe_allow_html=True)

    _journal_form("daily", selected_date, entry, template)

    with st.expander("📚 Past Daily Entries"):
        for ep in get_journal_entries("daily")[:20]:
            grade_badge = f" — **{ep['grade']}**" if ep.get("grade") else ""
            with st.expander(f"📅 {ep['entry_date']}{grade_badge} mood {ep.get('mood','?')}/10"):
                if ep.get("analysis"): st.markdown(ep["analysis"][:400])
                if st.button("Delete", key=f"del_daily_{ep['id']}"):
                    delete_journal_entry(ep["id"]); st.rerun()


# ── Weekly ────────────────────────────────────────────────────────────────────

def _weekly_journal(account_id=None):
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())

    col1, col2 = st.columns([2, 1])
    with col1:
        selected_week = st.date_input("Week starting (Monday)", value=week_start, key="weekly_date")
    with col2:
        templates = get_templates("weekly")
        template  = None
        if templates:
            sel_t    = st.selectbox("Template", ["None"] + [t["name"] for t in templates], key="weekly_tmpl")
            template = next((t for t in templates if t["name"] == sel_t), None) if sel_t != "None" else None

    week_end = selected_week + timedelta(days=6)
    entry    = _get_entry("weekly", selected_week)
    st.subheader(f"{selected_week.strftime('%d %b')} — {week_end.strftime('%d %b %Y')}")

    # Week stats scoped to account
    import pandas as pd
    acc_frag, acc_params = _acc_where(account_id)
    week_trades = fetch_all(
        f"SELECT pnl FROM trades WHERE status='closed' AND entry_time>=? AND entry_time<=? AND {acc_frag}",
        [str(selected_week), str(week_end) + " 23:59:59"] + acc_params,
    )
    if week_trades:
        df = pd.DataFrame(week_trades)
        df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0)
        wins = (df["pnl"] > 0).sum()
        wr   = wins / len(df) * 100
        net  = df["pnl"].sum()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Trades", len(df))
        c2.metric("Win Rate", f"{wr:.0f}%")
        c3.metric("Net P&L", f"{net:+.2f}")
        c4.metric("Best Day", f"{df['pnl'].max():+.2f}")

    _journal_form("weekly", selected_week, entry, template)

    with st.expander("📚 Past Weekly Entries"):
        for ep in get_journal_entries("weekly")[:10]:
            with st.expander(f"📆 Week of {ep['entry_date']}"):
                if ep.get("analysis"): st.markdown(ep["analysis"][:300])
                if st.button("Delete", key=f"del_weekly_{ep['id']}"):
                    delete_journal_entry(ep["id"]); st.rerun()


# ── Shared journal form ───────────────────────────────────────────────────────

# Menaker trade category definitions
_CATEGORY_OPTIONS = [
    "— Not set —",
    "Type 1 — On-plan ✅ + Profit",
    "Type 2 — On-plan ✅ + Stop (predefined loss)",
    "Type 3 — Off-plan ❌ + Loss (sloppy/impulse/revenge)",
    "Type 4 — Off-plan ❌ + Profit (lucky but not sustainable)",
]
_CATEGORY_VALUES = [None, 1, 2, 3, 4]
_CATEGORY_COLORS = {1: "#00c896", 2: "#4a9eff", 3: "#ff4b6e", 4: "#f5a623"}
_CATEGORY_DESCRIPTIONS = {
    1: "You followed your plan and it worked. This is the trade you want to replicate.",
    2: "You followed your plan but were stopped out at your predefined level. Not fun, but this is disciplined trading.",
    3: "Sloppy, impulsive, revenge or FOMO trade that lost money. These are the trades to eliminate.",
    4: "Off-plan trade that happened to make money. Not sustainable — it masks poor process.",
}


def _journal_form(entry_type, entry_date, entry=None, template=None,
                  trade_id=None, position_id=None, playbook_id=None, stage="post"):
    t = template or {}
    e = entry   or {}
    p = _p()

    # ── Trade Category (Menaker framework) — only for trade entries ───────────
    trade_category = None
    if entry_type == "trade" and stage == "post":
        st.markdown("#### 🏷️ Trade Category *(Menaker Framework)*")
        cur_cat = e.get("trade_category")
        cat_idx = _CATEGORY_VALUES.index(cur_cat) if cur_cat in _CATEGORY_VALUES else 0
        cat_sel = st.radio(
            "Categorise this trade",
            _CATEGORY_OPTIONS,
            index=cat_idx,
            key=f"cat_{entry_type}_{entry_date}_{stage}_{trade_id}_{position_id}",
            horizontal=False,
            label_visibility="collapsed",
        )
        cat_idx_sel = _CATEGORY_OPTIONS.index(cat_sel)
        trade_category = _CATEGORY_VALUES[cat_idx_sel]
        if trade_category and trade_category in _CATEGORY_DESCRIPTIONS:
            col = _CATEGORY_COLORS[trade_category]
            st.markdown(
                f'<div style="font-size:0.8rem;color:{col};padding:4px 10px;border-left:3px solid {col};'
                f'background:{col}18;border-radius:4px;margin-bottom:8px;">'
                f'{_CATEGORY_DESCRIPTIONS[trade_category]}</div>',
                unsafe_allow_html=True,
            )
        # Autopsy prompt for Type 3/4
        if trade_category in (3, 4):
            st.markdown(
                f'<div style="background:{p["--bg-card2"]};border:1px solid {p["--border"]};'
                f'border-left:3px solid {p["--warning"]};border-radius:6px;padding:8px 12px;'
                f'font-size:0.82rem;color:{p["--text-muted"]};margin-bottom:6px;">'
                f'<b>Post-trade autopsy</b> — Identify the sequence of events that led to this trade. '
                f'What was the trigger? What did you feel in your body beforehand? '
                f'What would on-plan behaviour have looked like?</div>',
                unsafe_allow_html=True,
            )
        st.markdown("---")

    analysis   = st.text_area("📊 Analysis",   value=e.get("analysis")   or t.get("analysis_template",   ""), height=180, key=f"analysis_{entry_type}_{entry_date}_{stage}")
    execution  = st.text_area("⚡ Execution",  value=e.get("execution")  or t.get("execution_template",  ""), height=150, key=f"exec_{entry_type}_{entry_date}_{stage}")
    psychology = st.text_area("🧠 Psychology", value=e.get("psychology") or t.get("psychology_template", ""), height=150, key=f"psych_{entry_type}_{entry_date}_{stage}")
    lessons    = st.text_area("💡 Lessons",    value=e.get("lessons", ""),                                    height=80,  key=f"lessons_{entry_type}_{entry_date}_{stage}")

    c1, c2 = st.columns(2)
    grade_idx = GRADE_OPTIONS.index(e.get("grade", "")) if e.get("grade") in GRADE_OPTIONS else 0
    grade = c1.selectbox("Grade", GRADE_OPTIONS, index=grade_idx, key=f"grade_{entry_type}_{entry_date}_{stage}")
    mood  = c2.slider("Mood (1-10)", 1, 10, value=int(e.get("mood") or 5), key=f"mood_{entry_type}_{entry_date}_{stage}")

    if st.button("💾 Save Entry", type="primary", key=f"save_{entry_type}_{entry_date}_{stage}"):
        eid = save_journal_entry(
            entry_id=e.get("id"),
            entry_type=entry_type, entry_date=str(entry_date),
            analysis=analysis, execution=execution,
            psychology=psychology, lessons=lessons,
            grade=grade, mood=mood,
            trade_id=trade_id, position_id=position_id,
            stage=stage, playbook_id=playbook_id,
            trade_category=trade_category,
        )
        st.success("Entry saved!")
        return True, eid
    return False, e.get("id")


# ── Trade Journal (post-trade with pre-trade column) ──────────────────────────

def _trade_journal(account_id=None):
    p = _p()
    st.subheader("Trade Notes")
    st.caption("Positions (merged trades) are shown first and journalled as a single unit.")

    acc_frag, acc_params = _acc_where(account_id)
    raw_trades = fetch_all(
        f"""SELECT * FROM trades WHERE status IN ('open','closed') AND {acc_frag}
            ORDER BY status='closed', entry_time DESC LIMIT 500""",
        acc_params,
    )
    if not raw_trades:
        st.info("No trades found for the selected account.")
        return

    # ── Build reviewed sets ───────────────────────────────────────────────────
    all_trade_ids = [t["id"] for t in raw_trades]
    reviewed_trade_ids = set()
    if all_trade_ids:
        ph = ",".join("?" * len(all_trade_ids))
        rows = fetch_all(
            f"SELECT DISTINCT trade_id FROM journal_entries WHERE stage='post' AND trade_id IN ({ph})",
            all_trade_ids,
        )
        reviewed_trade_ids = {r["trade_id"] for r in rows}

    # Position-level reviews propagate to their constituent trades
    reviewed_pos_ids = set()
    pos_review_rows = fetch_all(
        "SELECT DISTINCT position_id FROM journal_entries WHERE stage='post' AND position_id IS NOT NULL"
    )
    reviewed_pos_ids = {r["position_id"] for r in pos_review_rows}

    def is_reviewed(t):
        return (t["id"] in reviewed_trade_ids or
                (t.get("position_id") and t["position_id"] in reviewed_pos_ids))

    # ── Build unified selector items ──────────────────────────────────────────
    # positions that have at least one trade in raw_trades
    trade_pos_ids = {t["position_id"] for t in raw_trades if t.get("position_id")}
    positions_in_scope = {}
    for pos_id in trade_pos_ids:
        pos_trades = [t for t in raw_trades if t.get("position_id") == pos_id]
        if not pos_trades:
            continue
        pos_row = fetch_all("SELECT * FROM positions WHERE id=?", (pos_id,))
        if pos_row:
            positions_in_scope[pos_id] = {"pos": pos_row[0], "trades": pos_trades}

    # Items: ("pos", position_id) or ("trade", trade_id)
    items = []
    seen_trade_ids = set()
    # Positions first, sorted by most recent trade entry_time
    for pos_id, pd_ in sorted(
        positions_in_scope.items(),
        key=lambda kv: max((t.get("entry_time") or "") for t in kv[1]["trades"]),
        reverse=True,
    ):
        items.append(("pos", pos_id))
        for t in pd_["trades"]:
            seen_trade_ids.add(t["id"])

    # Individual trades not part of any position
    for t in raw_trades:
        if t["id"] not in seen_trade_ids:
            items.append(("trade", t["id"]))

    # ── Filter ────────────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns([3, 1, 1])
    with col3:
        filter_opt = st.selectbox("Show", ["All", "Not reviewed", "Reviewed"], key="tj_filter")

    def item_reviewed(item):
        kind, iid = item
        if kind == "pos":
            return iid in reviewed_pos_ids
        return iid in reviewed_trade_ids

    if filter_opt == "Not reviewed":
        items = [i for i in items if not item_reviewed(i)]
    elif filter_opt == "Reviewed":
        items = [i for i in items if item_reviewed(i)]

    if not items:
        st.caption("No items match the current filter.")
        return

    # Coverage summary
    n_positions = len(positions_in_scope)
    n_ind_trades = len([t for t in raw_trades if t["id"] not in seen_trade_ids])
    n_units = n_positions + n_ind_trades
    n_rev = sum(1 for pos_id in positions_in_scope if pos_id in reviewed_pos_ids)
    n_rev += sum(1 for t in raw_trades if t["id"] not in seen_trade_ids and t["id"] in reviewed_trade_ids)
    pct = n_rev / n_units * 100 if n_units else 0
    bar_col = p["--accent"] if pct >= 80 else (p["--warning"] if pct >= 50 else p["--danger"])
    st.markdown(
        f'<div style="margin:4px 0 10px;font-size:0.78rem;color:{p["--text-muted"]};">'
        f'Journal coverage: <b style="color:{bar_col};">{n_rev}/{n_units}</b> '
        f'units reviewed ({pct:.0f}%) '
        f'<span style="color:{p["--text-faint"]};">· {n_positions} position{"s" if n_positions!=1 else ""}, '
        f'{n_ind_trades} individual trade{"s" if n_ind_trades!=1 else ""}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Selector ──────────────────────────────────────────────────────────────
    def item_label(item):
        kind, iid = item
        rev = "✅" if item_reviewed(item) else "⚠️"
        if kind == "pos":
            pd_ = positions_in_scope[iid]
            pos = pd_["pos"]
            tr  = pd_["trades"]
            pnl = sum(float(t.get("pnl") or 0) for t in tr)
            dt  = min((t.get("entry_time") or "")[:10] for t in tr)
            return f"{rev} POS-{iid}  {pos['symbol']} {pos['direction']}  {dt}  ({len(tr)} trades)  {pnl:+.2f}"
        else:
            t = next((t for t in raw_trades if t["id"] == iid), None)
            if not t: return str(iid)
            if t.get("status") == "open":
                return f"{rev} 🔓 #{iid}  {t['symbol']} {t['direction']}  {(t.get('entry_time') or '')[:10]}  (open)"
            pnl = float(t.get("pnl") or 0)
            return f"{rev} #{iid}  {t['symbol']} {t['direction']}  {(t.get('entry_time') or '')[:10]}  {pnl:+.2f}"

    # Handle prefill from calendar drill-down (trade_id → find its item)
    prefill_trade_id = st.session_state.pop("trade_journal_selected", None)
    default_index = 0
    if prefill_trade_id:
        for idx, item in enumerate(items):
            kind, iid = item
            if kind == "trade" and iid == prefill_trade_id:
                default_index = idx
                break
            if kind == "pos":
                if any(t["id"] == prefill_trade_id for t in positions_in_scope[iid]["trades"]):
                    default_index = idx
                    break

    with col1:
        selected_item = st.selectbox(
            "Select position or trade", items,
            format_func=item_label,
            index=default_index,
            key="tj_item_sel",
        )

    with col2:
        templates = get_templates("trade")
        template  = None
        if templates:
            sel_t    = st.selectbox("Template", ["None"] + [t["name"] for t in templates], key="post_tmpl")
            template = next((t for t in templates if t["name"] == sel_t), None) if sel_t != "None" else None

    st.divider()

    sel_kind, sel_id = selected_item

    if sel_kind == "pos":
        _journal_position(sel_id, positions_in_scope[sel_id], template, p)
    else:
        _journal_single_trade(sel_id, raw_trades, template, p)


def _journal_position(pos_id, pd_, template, p):
    """Journal UI for a merged position."""
    pos    = pd_["pos"]
    trades = sorted(pd_["trades"], key=lambda t: t.get("entry_time") or "")
    total_pnl = sum(float(t.get("pnl") or 0) for t in trades)
    pnl_col = p["--accent"] if total_pnl >= 0 else p["--danger"]
    open_dt  = min((t.get("entry_time") or "")[:10] for t in trades)
    close_dt = max((t.get("exit_time")  or t.get("entry_time") or "")[:10] for t in trades)

    # Position header
    st.markdown(
        f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border2"]};'
        f'border-left:4px solid {p["--accent"]};border-radius:8px;padding:12px 16px;margin-bottom:12px;">'
        f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">'
        f'<span style="font-size:1rem;font-weight:700;color:{p["--text-primary"]};">POS-{pos_id}</span>'
        f'<span style="color:{p["--text-muted"]};">{pos["symbol"]} {pos["direction"]}</span>'
        f'<span style="color:{p["--text-faint"]};font-size:0.8rem;">{open_dt} → {close_dt}</span>'
        f'<span style="font-family:\'JetBrains Mono\';font-weight:700;color:{pnl_col};margin-left:auto;">'
        f'Net P&L: {total_pnl:+,.2f}</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Constituent trades table
    with st.expander(f"📊 {len(trades)} constituent trades", expanded=True):
        for t in trades:
            t_pnl = float(t.get("pnl") or 0)
            t_col = p["--accent"] if t_pnl >= 0 else p["--danger"]
            comm  = abs(float(t.get("commission") or 0))
            st.markdown(
                f'<div style="display:flex;gap:16px;align-items:center;padding:5px 8px;'
                f'margin:2px 0;background:{p["--bg-card2"]};border-radius:5px;font-size:0.82rem;">'
                f'<span style="color:{p["--text-faint"]};width:36px;">#{t["id"]}</span>'
                f'<span style="color:{p["--text-primary"]};width:60px;">{(t.get("entry_time") or "")[:10]}</span>'
                f'<span style="color:{p["--text-muted"]};">'
                f'Entry: {t.get("entry_price") or "—"}  →  Exit: {t.get("exit_price") or "—"}</span>'
                f'<span style="font-family:\'JetBrains Mono\';color:{t_col};margin-left:auto;font-weight:600;">'
                f'{t_pnl:+.2f}</span>'
                f'{"<span style=\"color:" + p["--text-faint"] + ";font-size:0.75rem;\"> comm " + f"{comm:.2f}" + "</span>" if comm else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )

    entry_date = open_dt
    pre_entry  = _get_position_entry(pos_id, stage="pre")
    post_entry = _get_position_entry(pos_id, stage="post")

    # Unassigned pre-trade plans
    unassigned = fetch_all(
        "SELECT * FROM journal_entries WHERE entry_type='trade' AND stage='pre' AND trade_id IS NULL AND position_id IS NULL ORDER BY entry_date DESC LIMIT 20"
    )
    if unassigned:
        with st.expander(f"🔗 Link an existing pre-trade plan ({len(unassigned)} unassigned)"):
            for u in unassigned:
                pb_name = f" · {fetch_all('SELECT name FROM playbooks WHERE id=?', (u['playbook_id'],))[0]['name']}" if u.get("playbook_id") else ""
                st.markdown(f"**#{u['id']}** {u['entry_date']}{pb_name}")
                if u.get("pre_analysis") or u.get("pre_plan"): st.caption((u.get("pre_analysis") or u.get("pre_plan"))[:150])
                if st.button(f"Link #{u['id']} to POS-{pos_id}", key=f"link_pre_pos_{u['id']}_{pos_id}"):
                    execute("UPDATE journal_entries SET position_id=? WHERE id=?", (pos_id, u["id"]))
                    st.success("Linked!"); st.rerun()
                st.divider()

    col_pre, col_post = st.columns(2)

    with col_pre:
        st.markdown("### 📋 Pre-Trade Plan")
        if pre_entry:
            st.markdown(
                f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border"]};'
                f'border-left:3px solid {p["--accent"]};border-radius:8px;padding:12px;font-size:0.85rem;">',
                unsafe_allow_html=True,
            )
            if pre_entry.get("pre_analysis"):    st.markdown(f"**Analysis:**\n{pre_entry['pre_analysis']}")
            if pre_entry.get("pre_plan"):         st.markdown(f"**Plan:**\n{pre_entry['pre_plan']}")
            if pre_entry.get("pre_accuracy_check") and "Not answered" not in pre_entry["pre_accuracy_check"]:
                st.markdown(f"**Accuracy check:** {pre_entry['pre_accuracy_check']}")
            if pre_entry.get("pre_risk_type") and "Not answered" not in pre_entry["pre_risk_type"]:
                st.markdown(f"**Risk type:** {pre_entry['pre_risk_type']}")
            if pre_entry.get("pre_body_state"):   st.markdown(f"**Body state:** {pre_entry['pre_body_state']}")
            if pre_entry.get("pre_psychology"):   st.markdown(f"**Mindset:**\n{pre_entry['pre_psychology']}")
            if pre_entry.get("pre_risk_notes"):   st.markdown(f"**Risk Notes:**\n{pre_entry['pre_risk_notes']}")
            if pre_entry.get("playbook_id"):
                pbs = fetch_all("SELECT name FROM playbooks WHERE id=?", (pre_entry["playbook_id"],))
                if pbs: st.markdown(f"📖 Playbook: **{pbs[0]['name']}**")
            st.markdown("</div>", unsafe_allow_html=True)
            if st.button("✏️ Edit Pre-Trade Plan", key=f"edit_pre_pos_{pos_id}"):
                st.session_state[f"edit_pre_pos_{pos_id}"] = True
            if st.session_state.get(f"edit_pre_pos_{pos_id}"):
                _pre_trade_form_position(pos_id, entry_date, pre_entry)
        else:
            st.caption("No pre-trade plan recorded for this position.")
            if st.button("➕ Add Pre-Trade Plan", key=f"add_pre_pos_{pos_id}"):
                st.session_state[f"edit_pre_pos_{pos_id}"] = True
            if st.session_state.get(f"edit_pre_pos_{pos_id}"):
                _pre_trade_form_position(pos_id, entry_date, None)

    with col_post:
        st.markdown("### 📝 Post-Trade Review")
        _journal_form("trade", entry_date, post_entry, template, position_id=pos_id, stage="post")


def _pre_trade_psych_check(key_suffix, existing_body="", existing_accuracy="", existing_risk=""):
    """Render Menaker pre-trade psychology check-in fields. Returns (body_state, accuracy_check, risk_type)."""
    p = _p()
    with st.expander("🧠 Pre-Trade Psychology Check *(Menaker)*", expanded=not existing_body):
        st.markdown(
            f'<div style="font-size:0.78rem;color:{p["--text-muted"]};margin-bottom:8px;">'
            f'<b>Before you hit the button</b> — check your inner state, not just the chart.</div>',
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        with c1:
            accuracy_check = st.radio(
                "Am I being…",
                ["— Not answered —", "✅ Accurate (following my plan)", "⚡ Trying to make money (impulse)"],
                index=["— Not answered —", "✅ Accurate (following my plan)", "⚡ Trying to make money (impulse)"].index(existing_accuracy)
                       if existing_accuracy in ["— Not answered —", "✅ Accurate (following my plan)", "⚡ Trying to make money (impulse)"] else 0,
                key=f"accuracy_{key_suffix}",
                label_visibility="visible",
            )
            risk_type = st.radio(
                "This is…",
                ["— Not answered —", "✅ Smart risk (defined, on-plan)", "⚠️ Sloppy risk (unclear, reactive)"],
                index=["— Not answered —", "✅ Smart risk (defined, on-plan)", "⚠️ Sloppy risk (unclear, reactive)"].index(existing_risk)
                       if existing_risk in ["— Not answered —", "✅ Smart risk (defined, on-plan)", "⚠️ Sloppy risk (unclear, reactive)"] else 0,
                key=f"risk_type_{key_suffix}",
                label_visibility="visible",
            )
        with c2:
            body_state = st.text_area(
                "Body scan — what do you notice?",
                value=existing_body,
                height=120,
                placeholder="Breathing rate? Tense? Leaning forward? Calm? Energy level?",
                key=f"body_{key_suffix}",
            )
    return body_state, accuracy_check, risk_type


def _pre_trade_form_position(pos_id, entry_date, existing):
    e = existing or {}
    playbooks  = get_playbooks()
    pb_options = {"None": None} | {pb["name"]: pb["id"] for pb in playbooks}
    current_pb = next((pb["name"] for pb in playbooks if pb["id"] == e.get("playbook_id")), None) if e.get("playbook_id") else None
    sel_pb = st.selectbox("Playbook", list(pb_options.keys()),
                           index=list(pb_options.keys()).index(current_pb) if current_pb in pb_options else 0,
                           key=f"inline_pb_pos_{pos_id}")
    pre_analysis   = st.text_area("Market Analysis / Setup", value=e.get("pre_analysis",""),   height=100, key=f"i_analysis_pos_{pos_id}")
    pre_plan       = st.text_area("Trade Plan",              value=e.get("pre_plan",""),        height=100, key=f"i_plan_pos_{pos_id}")

    body_state, accuracy_check, risk_type = _pre_trade_psych_check(
        key_suffix=f"pos_{pos_id}",
        existing_body=e.get("pre_body_state",""),
        existing_accuracy=e.get("pre_accuracy_check",""),
        existing_risk=e.get("pre_risk_type",""),
    )

    pre_psychology = st.text_area("Mindset / Additional Notes", value=e.get("pre_psychology",""), height=80, key=f"i_psych_pos_{pos_id}")
    pre_risk_notes = st.text_area("Risk / Sizing Notes",        value=e.get("pre_risk_notes",""), height=60, key=f"i_risk_pos_{pos_id}")
    if st.button("💾 Save Pre-Trade Plan", type="primary", key=f"save_pre_pos_{pos_id}"):
        pb_id = pb_options.get(sel_pb)
        if e.get("id"):
            execute("""UPDATE journal_entries SET pre_analysis=?, pre_plan=?, pre_psychology=?,
                       pre_risk_notes=?, playbook_id=?, pre_body_state=?, pre_accuracy_check=?,
                       pre_risk_type=?, updated_at=datetime('now') WHERE id=?""",
                    (pre_analysis, pre_plan, pre_psychology, pre_risk_notes, pb_id,
                     body_state, accuracy_check, risk_type, e["id"]))
        else:
            execute("""INSERT INTO journal_entries
                       (entry_type, entry_date, position_id, stage, playbook_id,
                        pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                        pre_body_state, pre_accuracy_check, pre_risk_type)
                       VALUES ('trade',?,?,'pre',?,?,?,?,?,?,?,?)""",
                    (entry_date, pos_id, pb_id, pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                     body_state, accuracy_check, risk_type))
        st.session_state.pop(f"edit_pre_pos_{pos_id}", None)
        st.success("Pre-trade plan saved!")
        st.rerun()


def _journal_single_trade(trade_id, raw_trades, template, p):
    """Journal UI for an individual (unmerged) trade."""
    trade = next((t for t in raw_trades if t["id"] == trade_id), None)
    if not trade:
        return

    pnl = float(trade.get("pnl") or 0)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Symbol",    trade["symbol"])
    c2.metric("Direction", trade["direction"])
    c3.metric("Entry",     f"{trade.get('entry_price',0):.5f}" if trade.get("entry_price") else "—")
    c4.metric("Exit",      f"{trade.get('exit_price',0):.5f}"  if trade.get("exit_price")  else "—")
    c5.metric("P&L",       f"{pnl:+.2f}")

    entry_date = (trade.get("entry_time") or str(date.today()))[:10]
    pre_entry  = _get_entry("trade", entry_date, trade_id=trade_id, stage="pre")
    post_entry = _get_entry("trade", entry_date, trade_id=trade_id, stage="post")

    unassigned = fetch_all(
        "SELECT * FROM journal_entries WHERE entry_type='trade' AND stage='pre' AND trade_id IS NULL AND position_id IS NULL ORDER BY entry_date DESC LIMIT 20"
    )
    if unassigned:
        with st.expander(f"🔗 Link an existing pre-trade plan ({len(unassigned)} unassigned)"):
            for u in unassigned:
                st.markdown(f"**#{u['id']}** {u['entry_date']}")
                if u.get("pre_analysis") or u.get("pre_plan"): st.caption((u.get("pre_analysis") or u.get("pre_plan"))[:150])
                if st.button(f"Link #{u['id']} to this trade", key=f"link_pre_{u['id']}_{trade_id}"):
                    execute("UPDATE journal_entries SET trade_id=? WHERE id=?", (trade_id, u["id"]))
                    st.success("Linked!"); st.rerun()
                st.divider()

    col_pre, col_post = st.columns(2)

    with col_pre:
        st.markdown("### 📋 Pre-Trade Plan")
        if pre_entry:
            st.markdown(
                f'<div style="background:{p["--bg-card"]};border:1px solid {p["--border"]};'
                f'border-left:3px solid {p["--accent"]};border-radius:8px;padding:12px;font-size:0.85rem;">',
                unsafe_allow_html=True,
            )
            if pre_entry.get("pre_analysis"):    st.markdown(f"**Analysis:**\n{pre_entry['pre_analysis']}")
            if pre_entry.get("pre_plan"):         st.markdown(f"**Plan:**\n{pre_entry['pre_plan']}")
            if pre_entry.get("pre_accuracy_check") and "Not answered" not in pre_entry["pre_accuracy_check"]:
                st.markdown(f"**Accuracy check:** {pre_entry['pre_accuracy_check']}")
            if pre_entry.get("pre_risk_type") and "Not answered" not in pre_entry["pre_risk_type"]:
                st.markdown(f"**Risk type:** {pre_entry['pre_risk_type']}")
            if pre_entry.get("pre_body_state"):   st.markdown(f"**Body state:** {pre_entry['pre_body_state']}")
            if pre_entry.get("pre_psychology"):   st.markdown(f"**Mindset:**\n{pre_entry['pre_psychology']}")
            if pre_entry.get("pre_risk_notes"):   st.markdown(f"**Risk Notes:**\n{pre_entry['pre_risk_notes']}")
            if pre_entry.get("playbook_id"):
                pbs = fetch_all("SELECT name FROM playbooks WHERE id=?", (pre_entry["playbook_id"],))
                if pbs: st.markdown(f"📖 Playbook: **{pbs[0]['name']}**")
            st.markdown("</div>", unsafe_allow_html=True)
            if st.button("✏️ Edit Pre-Trade Plan", key=f"edit_pre_{trade_id}"):
                st.session_state[f"edit_pre_{trade_id}"] = True
            if st.session_state.get(f"edit_pre_{trade_id}"):
                _pre_trade_form_inline(trade, pre_entry)
        else:
            st.caption("No pre-trade plan recorded for this trade.")
            if st.button("➕ Add Pre-Trade Plan", key=f"add_pre_{trade_id}"):
                st.session_state[f"edit_pre_{trade_id}"] = True
            if st.session_state.get(f"edit_pre_{trade_id}"):
                _pre_trade_form_inline(trade, None)

    with col_post:
        st.markdown("### 📝 Post-Trade Review")
        _journal_form("trade", entry_date, post_entry, template, trade_id=trade_id, stage="post")


def _pre_trade_form_inline(trade, existing):
    e = existing or {}
    entry_date = (trade.get("entry_time") or str(date.today()))[:10]
    playbooks  = get_playbooks()
    pb_options = {"None": None} | {pb["name"]: pb["id"] for pb in playbooks}
    current_pb = None
    if e.get("playbook_id"):
        current_pb = next((pb["name"] for pb in playbooks if pb["id"] == e["playbook_id"]), None)
    sel_pb = st.selectbox("Playbook", list(pb_options.keys()),
                           index=list(pb_options.keys()).index(current_pb) if current_pb in pb_options else 0,
                           key=f"inline_pb_{trade['id']}")
    pre_analysis   = st.text_area("Market Analysis / Setup", value=e.get("pre_analysis",""),   height=100, key=f"i_analysis_{trade['id']}")
    pre_plan       = st.text_area("Trade Plan",              value=e.get("pre_plan",""),        height=100, key=f"i_plan_{trade['id']}")

    body_state, accuracy_check, risk_type = _pre_trade_psych_check(
        key_suffix=f"trade_{trade['id']}",
        existing_body=e.get("pre_body_state",""),
        existing_accuracy=e.get("pre_accuracy_check",""),
        existing_risk=e.get("pre_risk_type",""),
    )

    pre_psychology = st.text_area("Mindset / Additional Notes", value=e.get("pre_psychology",""), height=80, key=f"i_psych_{trade['id']}")
    pre_risk_notes = st.text_area("Risk / Sizing Notes",        value=e.get("pre_risk_notes",""), height=60, key=f"i_risk_{trade['id']}")
    if st.button("💾 Save Pre-Trade Plan", type="primary", key=f"save_pre_{trade['id']}"):
        pb_id = pb_options.get(sel_pb)
        if e.get("id"):
            execute("""UPDATE journal_entries SET pre_analysis=?, pre_plan=?, pre_psychology=?,
                       pre_risk_notes=?, playbook_id=?, pre_body_state=?, pre_accuracy_check=?,
                       pre_risk_type=?, updated_at=datetime('now') WHERE id=?""",
                    (pre_analysis, pre_plan, pre_psychology, pre_risk_notes, pb_id,
                     body_state, accuracy_check, risk_type, e["id"]))
        else:
            execute("""INSERT INTO journal_entries
                       (entry_type, entry_date, trade_id, stage, playbook_id,
                        pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                        pre_body_state, pre_accuracy_check, pre_risk_type)
                       VALUES ('trade',?,?,'pre',?,?,?,?,?,?,?,?)""",
                    (entry_date, trade["id"], pb_id,
                     pre_analysis, pre_plan, pre_psychology, pre_risk_notes,
                     body_state, accuracy_check, risk_type))
        st.session_state.pop(f"edit_pre_{trade['id']}", None)
        st.success("Pre-trade plan saved!")
        st.rerun()


# ── Pre-Trade Planning (unassigned) ──────────────────────────────────────────

def _pretrade_journal(account_id=None):
    st.subheader("📋 Pre-Trade Plans")
    st.markdown("Write your trade plan **before** entering a trade. Once you take the trade, link it from the Trade Notes tab.")
    tab_new, tab_existing = st.tabs(["✏️ New Plan", "📂 Existing Plans"])
    with tab_new:      _new_pretrade_plan(account_id)
    with tab_existing: _list_pretrade_plans(account_id)


def _new_pretrade_plan(account_id=None):
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        plan_date = st.date_input("Date", value=date.today(), key="pt_date")
    with col2:
        playbooks  = get_playbooks()
        pb_options = {"None": None} | {pb["name"]: pb["id"] for pb in playbooks}
        sel_pb     = st.selectbox("Playbook", list(pb_options.keys()), key="pt_pb")
        pb_id      = pb_options.get(sel_pb)
    with col3:
        accounts    = get_accounts()
        acc_options = {"— None —": None} | {a["name"]: a["id"] for a in accounts}
        default_acc = next((a["name"] for a in accounts if a["id"] == account_id), "— None —")
        sel_acc     = st.selectbox("Account", list(acc_options.keys()),
                                   index=list(acc_options.keys()).index(default_acc),
                                   key="pt_account")
        plan_acc_id = acc_options.get(sel_acc)

    if pb_id:
        pb = get_playbook(pb_id)
        if pb and pb.get("rules"):
            with st.expander("📖 Playbook rules checklist", expanded=True):
                for rule in pb["rules"]:
                    badge = {"required":"🔴","optional":"🟡","bonus":"🟢"}.get(rule["rule_type"],"⚪")
                    grp = f"  ⛓ _{rule['rule_group']} (any one)_" if rule.get("rule_group") else ""
                    st.checkbox(f"{badge} {rule['name']}{grp}", key=f"pt_rule_{rule['id']}")

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        pre_analysis   = st.text_area("📊 Market Analysis & Setup", height=150, key="pt_analysis",
                                       placeholder="What's the macro context? What setup are you seeing?")
        pre_plan       = st.text_area("🎯 Trade Plan", height=150, key="pt_plan",
                                       placeholder="Entry trigger, stop loss level, take profit target, position size, invalidation point...")
    with col2:
        pre_psychology = st.text_area("🧠 Mindset Check", height=150, key="pt_psychology",
                                       placeholder="How are you feeling? Any emotional biases to watch for?")
        pre_risk_notes = st.text_area("⚖️ Risk Notes", height=150, key="pt_risk",
                                       placeholder="Planned risk %, lot size rationale, any special considerations...")

    if st.button("💾 Save Pre-Trade Plan", type="primary"):
        execute("""INSERT INTO journal_entries
                   (entry_type, entry_date, stage, playbook_id,
                    pre_analysis, pre_plan, pre_psychology, pre_risk_notes, account_id)
                   VALUES ('trade',?,'pre',?,?,?,?,?,?)""",
                (str(plan_date), pb_id, pre_analysis, pre_plan, pre_psychology, pre_risk_notes, plan_acc_id))
        st.success("✅ Pre-trade plan saved! Come back after the trade to link it and write your post-trade review.")
        st.rerun()


def _list_pretrade_plans(account_id=None):
    plans = fetch_all(
        """SELECT j.*, p.name as playbook_name, a.name as account_name
           FROM journal_entries j
           LEFT JOIN playbooks p ON j.playbook_id = p.id
           LEFT JOIN accounts  a ON j.account_id  = a.id
           WHERE j.entry_type='trade' AND j.stage='pre'
           ORDER BY j.entry_date DESC, j.created_at DESC"""
    )
    # Journal account filter: show plans for that account + plans with no account set
    if account_id:
        plans = [p for p in plans if p.get("account_id") in (None, account_id)]
    if not plans:
        st.caption("No pre-trade plans yet.")
        return

    unassigned = [p for p in plans if not p.get("trade_id")]
    assigned   = [p for p in plans if p.get("trade_id")]

    if unassigned:
        st.markdown(f"**Unassigned plans ({len(unassigned)}) — waiting to be linked:**")
        for plan in unassigned:
            pb_badge  = f" · 📖 {plan['playbook_name']}" if plan.get("playbook_name") else ""
            acc_badge = f" · 🏦 {plan['account_name']}" if plan.get("account_name") else ""
            with st.expander(f"📋 {plan['entry_date']}{pb_badge}{acc_badge}  _(unassigned)_"):
                if plan.get("pre_analysis"): st.markdown(f"**Analysis:** {plan['pre_analysis'][:300]}")
                if plan.get("pre_plan"):     st.markdown(f"**Plan:** {plan['pre_plan'][:200]}")
                # Trades offered for linking: prefer the plan's own account, else journal filter.
                # Open trades included — swing plans link right after entry.
                link_acc = plan.get("account_id") or account_id
                acc_frag, acc_params = _acc_where(link_acc)
                trades = fetch_all(
                    f"""SELECT * FROM trades WHERE status IN ('open','closed') AND {acc_frag}
                        ORDER BY status='closed', entry_time DESC LIMIT 200""",
                    acc_params,
                )
                if trades:
                    def trade_label(x):
                        t = next((t for t in trades if t["id"] == x), None)
                        if not t: return str(x)
                        badge = "🔓 OPEN " if t.get("status") == "open" else ""
                        return f"{badge}#{x} {t['symbol']} {t['direction']} {(t.get('entry_time') or '')[:10]}"
                    trade_id = st.selectbox(
                        "Link to trade", [None] + [t["id"] for t in trades],
                        format_func=lambda x: "— select —" if x is None else trade_label(x),
                        key=f"link_sel_{plan['id']}",
                    )
                    col1, col2 = st.columns(2)
                    if col1.button("🔗 Link", key=f"do_link_{plan['id']}", disabled=trade_id is None):
                        execute("UPDATE journal_entries SET trade_id=? WHERE id=?", (trade_id, plan["id"]))
                        st.success("Linked!"); st.rerun()
                    if col2.button("🗑️ Delete", key=f"del_pre_{plan['id']}"):
                        delete_journal_entry(plan["id"]); st.rerun()

    if assigned:
        st.divider()
        st.markdown(f"**Linked plans ({len(assigned)}):**")
        for plan in assigned[:10]:
            pb_badge  = f" · {plan['playbook_name']}" if plan.get("playbook_name") else ""
            acc_badge = f" · 🏦 {plan['account_name']}" if plan.get("account_name") else ""
            trade_info = f" → Trade #{plan['trade_id']}" if plan.get("trade_id") else ""
            with st.expander(f"✅ {plan['entry_date']}{pb_badge}{acc_badge}{trade_info}"):
                if plan.get("pre_analysis"): st.markdown(f"**Analysis:** {plan['pre_analysis'][:200]}")


# ── Template Manager ──────────────────────────────────────────────────────────

def _template_manager():
    st.subheader("Journal Templates")
    templates = get_templates()

    col1, col2 = st.columns([1, 2])
    with col1:
        st.markdown("**Templates**")
        for t in templates:
            type_icon = {"daily":"📅","weekly":"📆","trade":"📊"}.get(t["template_type"],"📋")
            star = " ⭐" if t["is_default"] else ""
            if st.button(f"{type_icon} {t['name']}{star}", key=f"tmpl_{t['id']}", use_container_width=True):
                st.session_state["editing_template_id"] = t["id"]

    with col2:
        eid  = st.session_state.get("editing_template_id")
        tmpl = next((t for t in templates if t["id"] == eid), None) if eid else None

        with st.form("template_form"):
            t_name       = st.text_input("Name",    value=tmpl["name"]              if tmpl else "")
            t_type       = st.selectbox("Type",     ["trade","daily","weekly"],
                                         index=["trade","daily","weekly"].index(tmpl["template_type"]) if tmpl else 0)
            t_analysis   = st.text_area("Analysis Template",   value=tmpl.get("analysis_template","")   if tmpl else "", height=120)
            t_execution  = st.text_area("Execution Template",  value=tmpl.get("execution_template","")  if tmpl else "", height=120)
            t_psychology = st.text_area("Psychology Template", value=tmpl.get("psychology_template","") if tmpl else "", height=120)
            t_default    = st.checkbox("Default for type", value=bool(tmpl and tmpl["is_default"]))

            c1, c2 = st.columns(2)
            if c1.form_submit_button("💾 Save", type="primary"):
                if t_name:
                    save_template(eid, t_name, t_type, t_analysis, t_execution, t_psychology, t_default)
                    st.success("Saved!"); st.session_state.pop("editing_template_id", None); st.rerun()
            if c2.form_submit_button("➕ New"):
                st.session_state.pop("editing_template_id", None); st.rerun()
