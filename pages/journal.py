"""
Page: Journal — calendar overview, daily and weekly entries.
"""
import streamlit as st
from datetime import date, timedelta
import calendar as _cal
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all
from utils.trade_ops import (
    get_journal_entries, save_journal_entry, delete_journal_entry,
    get_templates
)
from utils.accounts import get_accounts
from utils.theme import get_theme, _PALETTES

GRADE_OPTIONS = ["", "A+", "A", "B+", "B", "C+", "C", "D", "F"]


def _p():
    t = get_theme()
    return _PALETTES.get(t, _PALETTES["dark"])


def _flash(msg):
    """Queue a confirmation message that survives st.rerun() (shown as a toast next run)."""
    st.session_state["_journal_flash"] = msg


def show():
    st.header("📔 Journal")
    if st.session_state.get("_journal_flash"):
        st.toast(st.session_state.pop("_journal_flash"), icon="✅")

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

    tab_overview, tab_daily, tab_weekly = st.tabs(["🗓️ Overview", "📅 Daily", "📆 Weekly"])
    if "_pending_tab" in st.session_state:
        index = st.session_state.pop("_pending_tab")
        if index in (0, 1, 2):
            _js_switch_tab(index)
    with tab_overview: _calendar_overview(account_id)
    with tab_daily: _daily_journal(account_id)
    with tab_weekly: _weekly_journal(account_id)


# ── helpers ───────────────────────────────────────────────────────────────────

# Tab indices: 0=Overview, 1=Daily, 2=Weekly
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
    where = "entry_type=? AND entry_date=? AND NOT EXISTS(SELECT 1 FROM pattern_entries p WHERE p.legacy_journal_entry_id=journal_entries.id)"
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
           AND NOT EXISTS(SELECT 1 FROM pattern_entries p WHERE p.legacy_journal_entry_id=journal_entries.id)
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

    today_str = str(date.today())
    sel_day = st.selectbox(
        "Select day",
        all_days,
        index=all_days.index(today_str) if today_str in all_days else 0,
        key="cal_ov_day_sel",
        format_func=lambda d: (
            f"{d}  ⚡ {len(day_trades[d])} trade(s)" if d in day_trades
            else f"{d}  🔷 {day_entered[d]} entered" if d in day_entered
            else f"{d}  (no trades)"
        ),
    )

    if sel_day:
        j_list = day_journal.get(sel_day, [])
        has_daily = any(j["entry_type"] == "daily" for j in j_list)

        # All trading activity touching this day: entries (any status) + exits
        day_acts = fetch_all(
            f"""SELECT * FROM trades
                WHERE (DATE(entry_time)=? OR DATE(exit_time)=?) AND {acc_frag}
                ORDER BY id""",
            [sel_day, sel_day] + acc_params,
        )
        day_plans = fetch_all(
            "SELECT * FROM journal_entries WHERE entry_type='trade' AND stage='pre' AND entry_date=? "
            "ORDER BY id",
            (sel_day,),
        )

        col_a, col_b = st.columns([3, 1])
        with col_a:
            if day_acts:
                closed_today = [t for t in day_acts
                                if (t.get("exit_time") or "")[:10] == sel_day and t.get("status") == "closed"]
                net = sum(float(t.get("pnl") or 0) for t in closed_today)
                reviewed = sum(1 for t in closed_today if trade_has_post.get(t["id"]))
                pnl_col = p["--accent"] if net >= 0 else p["--danger"]
                st.markdown(
                    f'<div style="font-size:0.85rem;color:{p["--text-muted"]};">'
                    f'<b style="color:{pnl_col};">{net:+,.2f}</b> realised &nbsp;·&nbsp; '
                    f'{len(day_acts)} trade{"s" if len(day_acts)>1 else ""} active &nbsp;·&nbsp; '
                    f'{len(closed_today)} closed &nbsp;·&nbsp; '
                    f'{reviewed}/{len(closed_today)} reviewed</div>',
                    unsafe_allow_html=True,
                )
                for t in day_acts:
                    entered_today = (t.get("entry_time") or "")[:10] == sel_day
                    exited_today  = (t.get("exit_time") or "")[:10] == sel_day and t.get("status") == "closed"
                    is_open       = t.get("status") == "open"
                    if entered_today and exited_today:
                        act_txt = "🔷🔶 In & out"
                    elif entered_today and is_open:
                        act_txt = "🔷 Entered (still open)"
                    elif entered_today:
                        act_txt = "🔷 Entered"
                    else:
                        act_txt = "🔶 Closed"
                    t_pnl = float(t.get("pnl") or 0)
                    has_post = trade_has_post.get(t["id"])
                    badge_col = p["--accent"] if has_post else p["--warning"]
                    badge_txt = "✅ Reviewed" if has_post else "⚠️ Not reviewed"
                    pnl_col2 = p["--accent"] if t_pnl >= 0 else p["--danger"]
                    pnl_html = ('<span style="font-size:0.72rem;color:' + p["--text-faint"] + ';">open</span>'
                                if is_open else
                                f'<span style="font-family:\'JetBrains Mono\';color:{pnl_col2};font-weight:600;">{t_pnl:+,.2f}</span>')
                    review_html = ("" if is_open else
                                   f'<span style="font-size:0.7rem;color:{badge_col};border:1px solid {badge_col};'
                                   f'border-radius:4px;padding:1px 6px;">{badge_txt}</span>')
                    st.markdown(
                        f'<div style="display:flex;align-items:center;justify-content:space-between;'
                        f'padding:7px 12px;margin:3px 0;background:{p["--bg-card"]};'
                        f'border:1px solid {p["--border"]};border-radius:6px;">'
                        f'<div>'
                        f'<span style="font-weight:600;color:{p["--text-primary"]};">#{t["id"]} {t["symbol"]}</span>'
                        f'<span style="font-size:0.72rem;color:{p["--text-muted"]};margin-left:8px;">{t["direction"]}</span>'
                        f'<span style="font-size:0.72rem;color:{p["--text-faint"]};margin-left:10px;">{act_txt}</span>'
                        f'</div>'
                        f'<div style="display:flex;align-items:center;gap:12px;">'
                        f'{pnl_html}'
                        f'{review_html}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if not is_open and not has_post:
                        if st.button(f"Write review for #{t['id']} {t['symbol']}",
                                     key=f"goto_review_{t['id']}"):
                            from utils.trade_context import open_trade_detail
                            open_trade_detail(t["id"], t.get("account_id"))
                            st.rerun()
            else:
                st.caption("No trades entered or closed on this day.")

            if day_plans:
                st.markdown("**📋 Pre-trade plans written this day:**")
                for pl in day_plans:
                    sym = pl.get("symbol") or ""
                    linked = f" → Trade #{pl['trade_id']}" if pl.get("trade_id") else " (unassigned)"
                    snippet = (pl.get("pre_analysis") or pl.get("pre_plan") or "")[:80]
                    st.markdown(
                        f'<div style="padding:6px 12px;margin:3px 0;background:{p["--bg-card2"]};'
                        f'border:1px solid {p["--border"]};border-radius:6px;font-size:0.8rem;">'
                        f'📋 <b>#{pl["id"]}</b> {sym}{linked}'
                        f'{f"<span style=\"color:{p['--text-faint']};margin-left:8px;\">{snippet}</span>" if snippet else ""}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

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
                from utils.trade_context import open_trade_detail
                open_trade_detail(t["id"], t.get("account_id"))
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

    k = f"{entry_type}_{entry_date}_{stage}_{trade_id}_{position_id}"
    analysis   = st.text_area("📊 Analysis",   value=e.get("analysis")   or t.get("analysis_template",   ""), height=180, key=f"analysis_{k}")
    execution  = st.text_area("⚡ Execution",  value=e.get("execution")  or t.get("execution_template",  ""), height=150, key=f"exec_{k}")
    psychology = st.text_area("🧠 Psychology", value=e.get("psychology") or t.get("psychology_template", ""), height=150, key=f"psych_{k}")
    lessons    = st.text_area("💡 Lessons",    value=e.get("lessons", ""),                                    height=80,  key=f"lessons_{k}")

    c1, c2 = st.columns(2)
    grade_idx = GRADE_OPTIONS.index(e.get("grade", "")) if e.get("grade") in GRADE_OPTIONS else 0
    grade = c1.selectbox("Grade", GRADE_OPTIONS, index=grade_idx, key=f"grade_{k}")
    mood  = c2.slider("Mood (1-10)", 1, 10, value=int(e.get("mood") or 5), key=f"mood_{k}")

    if st.button("💾 Save Entry", type="primary", key=f"save_{k}"):
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
        _flash("Journal entry saved!")
        st.rerun()
    if e.get("id"):
        ts = (e.get("updated_at") or e.get("created_at") or "")[:16]
        st.caption(f"✅ Saved entry #{e['id']}" + (f" · last updated {ts}" if ts else ""))
    return False, e.get("id")
