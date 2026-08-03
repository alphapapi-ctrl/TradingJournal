"""
Trading Journal — Main Streamlit Application
"""
import streamlit as st
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from database import init_db, fetch_all

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Trading Journal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Init DB ───────────────────────────────────────────────────────────────────
init_db()

# ── FTP sync thread — starts once per server process ─────────────────────────
from utils.ftp_sync import start_sync_thread
start_sync_thread()

# Ensure all trades have accounts assigned
from utils.accounts import ensure_default_accounts
ensure_default_accounts()

# ── Theme — load from disk, inject CSS variables + component overrides ────────
from utils.theme import get_theme, get_full_css
_current_theme = get_theme()
st.markdown(f"<style>{get_full_css(_current_theme)}</style>", unsafe_allow_html=True)

# ── Pages ─────────────────────────────────────────────────────────────────────
PAGES = {
    "📔  Journal":          "journal",
    "🔬  Stock Analysis":   "stock_analysis",
    "📋  Trades":           "trades",
    "📊  Reports":          "statistics",
    "📖  Playbook":         "playbooks",
    "🎓  Reference":        "reference",
    "⏪  Replay":           "replay",
    "⚙️  Settings":         "settings",
}

if "page" not in st.session_state:
    st.session_state["page"] = "journal"

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    from utils.theme import _PALETTES
    p = _PALETTES.get(_current_theme, _PALETTES["dark"])

    st.markdown(f"""
    <div style="padding:12px 4px 20px;">
        <div style="font-family:'JetBrains Mono',monospace;font-size:1.25rem;
                    font-weight:600;color:{p['--accent']};">📈 TJ</div>
        <div style="font-size:0.62rem;color:{p['--text-faint']};letter-spacing:2.5px;
                    text-transform:uppercase;margin-top:2px;">Trading Journal</div>
    </div>
    """, unsafe_allow_html=True)

    for label, key in PAGES.items():
        if st.session_state["page"] == key:
            st.markdown(f"""
            <div style="background:{p['--bg-active']};border:1px solid {p['--border2']};
                        border-radius:6px;padding:7px 14px;margin:2px 0;
                        color:{p['--accent']};font-size:0.88rem;font-weight:600;">
                {label}
            </div>""", unsafe_allow_html=True)
        else:
            if st.button(label, key=f"nav_{key}", use_container_width=True):
                st.session_state["page"] = key
                st.rerun()

    st.divider()

    # Live stats
    total_n  = fetch_all("SELECT COUNT(*) as n FROM trades WHERE status='closed'")[0]["n"]
    wins_n   = fetch_all("SELECT COUNT(*) as n FROM trades WHERE status='closed' AND pnl>0")[0]["n"]
    open_n   = fetch_all("SELECT COUNT(*) as n FROM trades WHERE status='open'")[0]["n"]
    pnl_row  = fetch_all("SELECT ROUND(SUM(pnl - ABS(COALESCE(commission,0))),2) as p FROM trades WHERE status='closed'")[0]["p"]
    acc_n    = fetch_all("SELECT COUNT(*) as n FROM accounts")[0]["n"]
    net_pnl  = float(pnl_row or 0)
    wr       = f"{wins_n/total_n*100:.0f}%" if total_n > 0 else "—"
    pnl_col  = p["--accent"] if net_pnl >= 0 else p["--danger"]
    open_html = f"<div>Open: <span style='color:{p['--warning']};'>{open_n}</span></div>" if open_n > 0 else ""

    st.markdown(f"""
    <div style="font-size:0.75rem;color:{p['--text-faint']};line-height:2.0;">
        <div>Accounts: <span style="color:{p['--text-secondary']};">{acc_n}</span></div>
        <div>Trades: <span style="color:{p['--text-secondary']};">{total_n}</span></div>
        <div>Win rate: <span style="color:{p['--accent']};">{wr}</span></div>
        <div>Net P&L: <span style="color:{pnl_col};">{net_pnl:+,.2f}</span></div>
        {open_html}
    </div>""", unsafe_allow_html=True)

    name_rows = fetch_all("SELECT value FROM app_settings WHERE key='trader_name'")
    if name_rows and name_rows[0]["value"]:
        st.markdown(f"""
        <div style="margin-top:10px;font-size:0.75rem;color:{p['--text-faint']};">
            👤 {name_rows[0]['value']}
        </div>""", unsafe_allow_html=True)

# ── Router ────────────────────────────────────────────────────────────────────
page = st.session_state.get("page", "dashboard")

if page == "dashboard":
    from pages.dashboard import show; show()
elif page == "import_trades":
    # Consolidated into the Trades page (Add Trade tab)
    st.session_state["page"] = "trades"
    st.session_state["_trades_pending_tab"] = 1
    from pages.trades import show; show()
elif page == "trades":
    from pages.trades import show; show()
elif page == "trade_detail":
    # Consolidated into the Trades page (Trade Detail tab)
    st.session_state["page"] = "trades"
    st.session_state["_trades_pending_tab"] = 4
    from pages.trades import show; show()
elif page == "stock_analysis":
    from pages.stock_analysis import show; show()
elif page == "playbooks":
    from pages.playbooks import show; show()
elif page == "journal":
    from pages.journal import show; show()
elif page == "statistics":
    from pages.statistics import show; show()
elif page == "risk_calculator":
    # Consolidated into the Trades page (Risk Calculator tab)
    st.session_state["page"] = "trades"
    from pages.trades import show; show()
elif page == "reference":
    from pages.reference import show; show()
elif page == "replay":
    from pages.replay import show; show()
elif page == "settings":
    from pages.settings import show; show()
else:
    # Stale/removed page in session state (e.g. ea_dashboard, ai_analysis) — go home
    st.session_state["page"] = "journal"
    from pages.journal import show; show()
