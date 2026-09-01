"""
Page: Settings — account config, preferences, data management
"""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import fetch_all, execute
from utils.app_settings import get_settings_dict, set_many_settings, set_setting
from utils.seed_data import seed_all


def get_settings() -> dict:
    return get_settings_dict()

def save_setting(key: str, value):
    set_setting(key, value)

def save_all_settings(data: dict):
    set_many_settings(data)


def show():
    st.header("⚙️ Settings")

    tab_accounts, tab_risk, tab_display, tab_ai, tab_data, tab_ftp, tab_network = st.tabs([
        "🏦 Accounts", "⚖️ Risk Defaults", "🎨 Display", "🤖 AI", "🗄️ Data", "🔗 MT5 FTP", "🌐 Network"
    ])

    settings = get_settings()

    with tab_accounts:
        _accounts_tab()

    with tab_risk:
        _risk_settings(settings)

    with tab_display:
        _display_settings(settings)

    with tab_ai:
        _ai_settings(settings)

    with tab_data:
        _data_management()

    with tab_ftp:
        _ftp_settings(settings)

    with tab_network:
        _network_settings()



# ── Prop firm status bar ─────────────────────────────────────────────────────

def _prop_status_bar(acc: dict, stats: dict):
    """Inline status bar for prop firm accounts showing limit usage."""
    from database import fetch_all as _fa
    from datetime import date

    balance    = float(acc.get("initial_balance") or 0)
    max_loss   = acc.get("prop_max_loss_pct")
    daily_firm = acc.get("prop_daily_loss_pct")
    daily_self = acc.get("prop_personal_daily_loss_pct")
    profit_tgt = acc.get("prop_profit_target_pct")

    if not balance:
        st.caption("Set an initial balance to track prop firm limits.")
        return

    # Total drawdown
    net_pnl = float(stats.get("net_pnl") or 0)
    drawdown_pct = (net_pnl / balance * 100) if balance else 0

    # Today's P&L
    today = date.today().isoformat()
    today_row = _fa(
        "SELECT COALESCE(SUM(pnl),0) as p FROM trades "
        "WHERE account_id=? AND DATE(exit_time)=? AND status='closed'",
        (acc["id"], today)
    )
    today_pnl  = float(today_row[0]["p"] if today_row else 0)
    today_pct  = (today_pnl / balance * 100) if balance else 0

    cols = st.columns(4)

    # Total P&L vs profit target
    if profit_tgt:
        prog = min(max(drawdown_pct / profit_tgt, 0), 1) if drawdown_pct > 0 else 0
        cols[0].metric("Progress to Target",
                       f"{drawdown_pct:+.1f}%",
                       f"Target: {profit_tgt}%")

    # Total drawdown vs max loss
    if max_loss:
        used_pct = min(abs(min(drawdown_pct, 0)) / max_loss * 100, 100)
        cols[1].metric("Max Loss Used",
                       f"{abs(min(drawdown_pct, 0)):.1f}% / {max_loss}%",
                       f"{100 - used_pct:.0f}% remaining",
                       delta_color="inverse")

    # Daily P&L vs firm limit
    if daily_firm:
        daily_used = abs(min(today_pct, 0)) / daily_firm * 100
        cols[2].metric("Daily Loss (Firm)",
                       f"{today_pct:.1f}% / -{daily_firm}%",
                       "⛔ LIMIT HIT" if today_pct <= -daily_firm else f"{100 - daily_used:.0f}% remaining",
                       delta_color="inverse")

    # Daily P&L vs personal guardrail
    if daily_self:
        hit = today_pct <= -daily_self
        cols[3].metric("Daily Loss (Mine)",
                       f"{today_pct:.1f}% / -{daily_self}%",
                       "🛑 STOP TRADING" if hit else "Within limit",
                       delta_color="inverse")

    # Warning banners
    if daily_self and today_pct <= -daily_self:
        st.error(f"🛑 **Personal guardrail hit** — you've lost {abs(today_pct):.1f}% today (limit: {daily_self}%). Stop trading for today.")
    elif daily_firm and today_pct <= -daily_firm:
        st.error(f"⛔ **Firm daily limit breached** — account may be at risk.")
    if max_loss and drawdown_pct <= -max_loss:
        st.error(f"⛔ **Max loss breached** — total drawdown {abs(drawdown_pct):.1f}% exceeds {max_loss}%.")


# ── Accounts Tab ──────────────────────────────────────────────────────────────

def _accounts_tab():
    from utils.accounts import get_accounts, create_account, update_account, delete_account, get_account_stats
    from database import fetch_all as _fa

    st.subheader("Trading Accounts")
    st.caption("Manage accounts and link imported trades to them.")

    accounts = get_accounts()

    ACCT_TYPES = ["Personal", "Prop Firm", "EA"]

    # ── Account list ──────────────────────────────────────────────────────────
    for acc in accounts:
        stats   = get_account_stats(acc["id"])
        pnl     = stats["net_pnl"]
        pnl_col = "var(--accent,#00c896)" if pnl >= 0 else "var(--danger,#ff4b6e)"
        default_badge = " ⭐" if acc["is_default"] else ""
        wr_str = f"{stats['win_rate']:.0f}% WR" if stats["closed"] else "no trades"
        acc_type = acc.get("account_type") or "Personal"
        type_badge = f" · {acc_type}" if acc_type != "Personal" else ""

        with st.expander(
            f"**{acc['name']}**{default_badge}{type_badge}  ·  {acc['broker']}  ·  "
            f"{stats['closed']} trades  ·  P&L {pnl:+,.2f}  ·  {wr_str}"
        ):
            # ── Prop firm status bar ──────────────────────────────────────────
            if acc_type == "Prop Firm":
                _prop_status_bar(acc, stats)

            with st.form(f"edit_acc_{acc['id']}"):
                c1, c2 = st.columns(2)
                name     = c1.text_input("Account Name",   value=acc["name"])
                broker   = c2.text_input("Broker",         value=acc["broker"])
                c3, c4   = st.columns(2)
                acc_num  = c3.text_input("Account Number", value=acc.get("account_number") or "")
                currency = c4.selectbox("Currency", ["AUD","USD","EUR","GBP","CAD","JPY"],
                                         index=["AUD","USD","EUR","GBP","CAD","JPY"].index(
                                             acc.get("currency","AUD"))
                                         if acc.get("currency","AUD") in ["AUD","USD","EUR","GBP","CAD","JPY"] else 0)
                c5, c6   = st.columns(2)
                balance  = c5.number_input("Initial Balance",
                                            value=float(acc.get("initial_balance") or 0),
                                            min_value=0.0, step=100.0)
                is_def   = c6.checkbox("Set as default account",
                                        value=bool(acc["is_default"]))

                acc_type_sel = st.selectbox(
                    "Account Type",
                    ACCT_TYPES,
                    index=ACCT_TYPES.index(acc_type) if acc_type in ACCT_TYPES else 0,
                )

                # Prop firm fields — only shown when type is Prop Firm
                prop_profit = prop_max = prop_daily = prop_personal = None
                if acc_type_sel == "Prop Firm":
                    st.markdown("**Prop Firm Rules**")
                    pc1, pc2 = st.columns(2)
                    prop_profit = pc1.number_input(
                        "Profit Target (%)",
                        value=float(acc.get("prop_profit_target_pct") or 10.0),
                        min_value=0.0, max_value=100.0, step=0.5,
                        help="Firm's profit target to pass the challenge/stay funded"
                    )
                    prop_max = pc2.number_input(
                        "Max Loss (%)",
                        value=float(acc.get("prop_max_loss_pct") or 10.0),
                        min_value=0.0, max_value=100.0, step=0.5,
                        help="Firm's maximum total drawdown before account is breached"
                    )
                    pc3, pc4 = st.columns(2)
                    prop_daily = pc3.number_input(
                        "Firm Daily Loss Limit (%)",
                        value=float(acc.get("prop_daily_loss_pct") or 5.0),
                        min_value=0.0, max_value=100.0, step=0.5,
                        help="Firm's maximum daily loss before account is breached"
                    )
                    prop_personal = pc4.number_input(
                        "My Daily Loss Guardrail (%)",
                        value=float(acc.get("prop_personal_daily_loss_pct") or 3.0),
                        min_value=0.0, max_value=100.0, step=0.5,
                        help="Your personal stop — stop trading for the day when this is hit"
                    )

                notes = st.text_area("Notes", value=acc.get("notes") or "", height=60)

                ca, cb = st.columns(2)
                if ca.form_submit_button("💾 Save"):
                    update_account(acc["id"], name, broker, currency, balance,
                                   acc_num, notes, is_def,
                                   account_type=acc_type_sel,
                                   prop_profit_target_pct=prop_profit,
                                   prop_max_loss_pct=prop_max,
                                   prop_daily_loss_pct=prop_daily,
                                   prop_personal_daily_loss_pct=prop_personal)
                    st.success("Updated!"); st.rerun()
                if cb.form_submit_button("🗑️ Delete Account"):
                    delete_account(acc["id"])
                    st.rerun()

            # Trade count summary
            brokers_in_acc = _fa(
                "SELECT broker, COUNT(*) as n FROM trades WHERE account_id=? GROUP BY broker",
                (acc["id"],)
            )
            if brokers_in_acc:
                st.caption("Trades: " + "  ·  ".join(
                    f"{r['broker']}: {r['n']}" for r in brokers_in_acc
                ))

    st.divider()

    # ── Add new account ───────────────────────────────────────────────────────
    with st.expander("➕ Add New Account"):
        with st.form("new_account"):
            c1, c2  = st.columns(2)
            name    = c1.text_input("Account Name*", placeholder="e.g. ICMarkets Prop 1")
            broker  = c2.text_input("Broker*",        placeholder="e.g. IC Markets")
            c3, c4  = st.columns(2)
            acc_num = c3.text_input("Account Number", placeholder="e.g. 142331")
            currency = c4.selectbox("Currency", ["AUD","USD","EUR","GBP","CAD","JPY"])
            c5, c6  = st.columns(2)
            balance  = c5.number_input("Initial Balance", value=10000.0, min_value=0.0, step=100.0)
            is_def   = c6.checkbox("Set as default")

            acc_type_new = st.selectbox("Account Type", ACCT_TYPES)

            new_prop_profit = new_prop_max = new_prop_daily = new_prop_personal = None
            if acc_type_new == "Prop Firm":
                st.markdown("**Prop Firm Rules**")
                nc1, nc2 = st.columns(2)
                new_prop_profit = nc1.number_input("Profit Target (%)",  value=10.0, min_value=0.0, max_value=100.0, step=0.5)
                new_prop_max    = nc2.number_input("Max Loss (%)",        value=10.0, min_value=0.0, max_value=100.0, step=0.5)
                nc3, nc4 = st.columns(2)
                new_prop_daily    = nc3.number_input("Firm Daily Loss Limit (%)",  value=5.0, min_value=0.0, max_value=100.0, step=0.5)
                new_prop_personal = nc4.number_input("My Daily Loss Guardrail (%)", value=3.0, min_value=0.0, max_value=100.0, step=0.5)

            notes = st.text_area("Notes", height=60)

            if st.form_submit_button("Create Account", type="primary"):
                if not name or not broker:
                    st.error("Name and Broker are required.")
                else:
                    create_account(name, broker, currency, balance, acc_num, notes, is_def,
                                   account_type=acc_type_new,
                                   prop_profit_target_pct=new_prop_profit,
                                   prop_max_loss_pct=new_prop_max,
                                   prop_daily_loss_pct=new_prop_daily,
                                   prop_personal_daily_loss_pct=new_prop_personal)
                    st.success(f"Account '{name}' created!")
                    st.rerun()

    # ── Unassigned trades ─────────────────────────────────────────────────────
    unassigned = _fa(
        "SELECT broker, COUNT(*) as n FROM trades WHERE account_id IS NULL GROUP BY broker"
    )
    if unassigned:
        st.divider()
        st.subheader("Unassigned Trades")
        st.caption("These trades have no account. Select an account to assign them to.")
        for row in unassigned:
            acc_options = {a["name"]: a["id"] for a in accounts}
            if not acc_options:
                st.warning("Create an account first.")
                break
            c1, c2 = st.columns([3, 2])
            c1.markdown(f"**{row['broker']}** — {row['n']} trades without an account")
            sel = c2.selectbox("Assign to", list(acc_options.keys()),
                                key=f"assign_{row['broker']}")
            if st.button(f"Assign {row['broker']} trades", key=f"do_assign_{row['broker']}"):
                from database import execute as _ex
                _ex("UPDATE trades SET account_id=? WHERE broker=? AND account_id IS NULL",
                    (acc_options[sel], row["broker"]))
                st.success(f"Assigned {row['n']} trades to {sel}")
                st.rerun()


# ── Risk Settings ─────────────────────────────────────────────────────────────

def _risk_settings(settings):
    st.subheader("Risk Management Defaults")
    st.caption("These defaults are used in the Risk Calculator and Dashboard session limits.")

    with st.form("risk_settings"):
        col1, col2 = st.columns(2)

        risk_pct = col1.slider("Default Risk per Trade (%)",
                                min_value=0.1, max_value=5.0,
                                value=float(settings.get("risk_pct", 1.0)),
                                step=0.1)
        daily_loss_pct = col2.slider("Daily Loss Limit (%)",
                                      min_value=0.5, max_value=10.0,
                                      value=float(settings.get("daily_loss_pct", 3.0)),
                                      step=0.5)
        weekly_loss_pct = col1.slider("Weekly Loss Limit (%)",
                                       min_value=1.0, max_value=20.0,
                                       value=float(settings.get("weekly_loss_pct", 6.0)),
                                       step=0.5)
        max_trades_day = col2.number_input("Max Trades per Day",
                                            value=int(settings.get("max_trades_day", 5)),
                                            min_value=1, max_value=50, step=1)
        max_open_positions = col1.number_input("Max Open Positions",
                                                value=int(settings.get("max_open_positions", 3)),
                                                min_value=1, max_value=20, step=1)
        default_rr = col2.number_input("Default Min R:R Target",
                                        value=float(settings.get("default_rr", 2.0)),
                                        min_value=0.5, max_value=10.0, step=0.5)
        prop_firm_mode = st.checkbox(
            "Prop Firm Mode",
            value=settings.get("prop_firm_mode", "false") == "true",
            help="Enables stricter drawdown tracking and rule compliance warnings"
        )

        if st.form_submit_button("💾 Save Risk Settings", type="primary"):
            save_all_settings({
                "risk_pct":            risk_pct,
                "daily_loss_pct":      daily_loss_pct,
                "weekly_loss_pct":     weekly_loss_pct,
                "max_trades_day":      max_trades_day,
                "max_open_positions":  max_open_positions,
                "default_rr":          default_rr,
                "prop_firm_mode":      str(prop_firm_mode).lower(),
            })
            st.success("Risk settings saved!")


# ── Display Settings ──────────────────────────────────────────────────────────

def _display_settings(settings):
    from utils.theme import get_theme, set_theme, _PALETTES

    st.subheader("🎨 Theme")
    st.markdown(
        "Choose how the app looks. The Streamlit light/dark toggle in the top-right menu "
        "controls Streamlit's own chrome; these three options control the **app's colour scheme** "
        "independently — pick whichever combination suits your monitor."
    )

    current_theme = get_theme()
    THEME_OPTIONS  = ["dark", "mid", "light"]
    THEME_LABELS   = {
        "dark":  "🌑  Dark  — deep navy/black, easy on the eyes at night",
        "mid":   "🌒  Mid   — slate blue-grey, a softer dark for long sessions",
        "light": "🌕  Light — clean white/grey, bright monitors or daytime trading",
    }

    # Radio — no form needed, change is instant
    chosen = st.radio(
        "App colour scheme",
        options=THEME_OPTIONS,
        format_func=lambda x: THEME_LABELS[x],
        index=THEME_OPTIONS.index(current_theme),
        key="theme_radio",
        horizontal=False,
    )

    # Build colour swatches
    p = _PALETTES[chosen]
    border_col = p["--border"]
    swatch_keys = ["--bg-app", "--bg-card", "--bg-active", "--accent", "--danger", "--text-primary"]
    swatches_html = "".join(
        f'<div style="width:24px;height:24px;border-radius:5px;background:{p[k]};'
        f'border:1px solid {border_col};" title="{k}"></div>'
        for k in swatch_keys
    )
    st.markdown(f"""
    <div style="display:flex;gap:8px;margin:12px 0 4px;align-items:center;">
        <span style="font-size:0.75rem;color:{p['--text-muted']};">Preview:</span>
        {swatches_html}
        <span style="font-size:0.8rem;padding:3px 10px;background:{p['--bg-card']};
                     border:1px solid {border_col};border-radius:5px;
                     color:{p['--text-primary']};">Aa</span>
        <span style="font-size:0.8rem;padding:3px 10px;background:{p['--accent']};
                     border-radius:5px;color:#fff;font-weight:700;">Button</span>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([1, 4])
    if col1.button("✅ Apply Theme", type="primary", use_container_width=True):
        set_theme(chosen)
        st.success(f"Theme set to **{chosen}**. Reloading…")
        st.rerun()

    if chosen != current_theme:
        st.caption(f"↑ Click **Apply Theme** to switch from `{current_theme}` → `{chosen}`")

    st.divider()

    # ── Display preferences ────────────────────────────────────────────────────
    st.subheader("Display Preferences")

    with st.form("display_settings"):
        col1, col2 = st.columns(2)

        trader_name = col1.text_input(
            "Trader Name / Alias",
            value=settings.get("trader_name", ""),
            placeholder="e.g. John D."
        )
        timezone = col2.selectbox(
            "Timezone",
            ["UTC", "UTC+1 (London)", "UTC+2 (Europe)", "UTC+10 (Sydney/AEST)",
             "UTC+11 (AEDT)", "UTC-5 (EST)", "UTC-4 (EDT)", "UTC-8 (PST)"],
            index=["UTC", "UTC+1 (London)", "UTC+2 (Europe)", "UTC+10 (Sydney/AEST)",
                   "UTC+11 (AEDT)", "UTC-5 (EST)", "UTC-4 (EDT)", "UTC-8 (PST)"].index(
                settings.get("timezone", "UTC"))
                  if settings.get("timezone") in ["UTC", "UTC+1 (London)", "UTC+2 (Europe)",
                     "UTC+10 (Sydney/AEST)", "UTC+11 (AEDT)", "UTC-5 (EST)", "UTC-4 (EDT)", "UTC-8 (PST)"] else 0
        )
        pnl_currency = col1.selectbox(
            "P&L Currency Symbol",
            ["$", "€", "£", "¥", "A$"],
            index=["$","€","£","¥","A$"].index(settings.get("pnl_currency", "$"))
                  if settings.get("pnl_currency") in ["$","€","£","¥","A$"] else 0
        )
        decimal_places = col2.selectbox(
            "Price Decimal Places",
            [3, 4, 5],
            index=[3, 4, 5].index(int(settings.get("decimal_places", 5)))
                  if int(settings.get("decimal_places", 5)) in [3, 4, 5] else 2
        )
        date_format = col1.selectbox(
            "Date Format",
            ["YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY"],
            index=["YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY"].index(
                settings.get("date_format", "YYYY-MM-DD"))
                  if settings.get("date_format") in ["YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY"] else 0
        )
        show_commissions = col2.checkbox(
            "Deduct commissions from displayed P&L",
            value=settings.get("show_commissions", "true") == "true"
        )
        equity_period = col1.selectbox(
            "Equity Curve Period (Dashboard)",
            ["All Time", "Last 90 days", "Last 30 days", "This Month"],
            index=["All Time", "Last 90 days", "Last 30 days", "This Month"].index(
                settings.get("dashboard_equity_period", "All Time"))
                  if settings.get("dashboard_equity_period") in
                     ["All Time", "Last 90 days", "Last 30 days", "This Month"] else 0
        )

        if st.form_submit_button("💾 Save Preferences", type="primary"):
            save_all_settings({
                "trader_name":             trader_name,
                "timezone":                timezone,
                "pnl_currency":            pnl_currency,
                "decimal_places":          decimal_places,
                "date_format":             date_format,
                "show_commissions":        str(show_commissions).lower(),
                "dashboard_equity_period": equity_period,
            })
            st.success("Preferences saved!")


# ── Data Management ───────────────────────────────────────────────────────────

def _ai_settings(settings: dict):
    """Local LLM configuration — framework only; generate buttons come later."""
    st.subheader("AI — Local LLM")
    st.caption("Configure a locally-hosted LLM for journal/trade analysis. "
               "The connection settings are stored now; AI generate buttons will be added to pages later.")

    provider = st.selectbox(
        "Provider",
        ["Ollama", "LM Studio", "OpenAI-compatible (custom)"],
        index=["Ollama", "LM Studio", "OpenAI-compatible (custom)"].index(
            settings.get("ai_provider", "Ollama"))
            if settings.get("ai_provider", "Ollama") in ["Ollama", "LM Studio", "OpenAI-compatible (custom)"] else 0,
        key="ai_provider_sel",
    )

    default_urls = {
        "Ollama":                      "http://localhost:11434",
        "LM Studio":                   "http://localhost:1234/v1",
        "OpenAI-compatible (custom)":  "http://localhost:8000/v1",
    }
    base_url = st.text_input(
        "Base URL",
        value=settings.get("ai_base_url") or default_urls[provider],
        key="ai_base_url_inp",
        help="Ollama uses its native API; LM Studio and custom servers use the OpenAI-compatible /v1 endpoint",
    )
    model = st.text_input(
        "Model name",
        value=settings.get("ai_model", ""),
        placeholder="e.g. llama3.1:8b / qwen2.5:14b / local-model",
        key="ai_model_inp",
    )

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("🔌 Test Connection", key="ai_test_btn"):
            import urllib.request, json as _json
            try:
                if provider == "Ollama":
                    url = base_url.rstrip("/") + "/api/tags"
                else:
                    url = base_url.rstrip("/") + "/models"
                with urllib.request.urlopen(url, timeout=5) as resp:
                    data = _json.loads(resp.read().decode())
                if provider == "Ollama":
                    models = [m["name"] for m in data.get("models", [])]
                else:
                    models = [m.get("id", "?") for m in data.get("data", [])]
                st.success(f"✅ Connected — {len(models)} model(s) available")
                if models:
                    st.caption("Available: " + ", ".join(models[:10]))
            except Exception as e:
                st.error(f"Connection failed: {e}")
    with c2:
        if st.button("💾 Save AI Settings", type="primary", key="ai_save_btn"):
            save_all_settings({
                "ai_provider": provider,
                "ai_base_url": base_url.strip(),
                "ai_model":    model.strip(),
            })
            st.success("AI settings saved.")

    st.divider()
    st.markdown("**Planned AI features** *(buttons to be added later)*")
    st.markdown(
        "- Journal entry summarisation & pattern detection across entries\n"
        "- Pre-trade plan review against playbook rules\n"
        "- Menaker category trend commentary (Type 3/4 reduction tracking)\n"
        "- Stock analysis narrative from the technical + fundamental data"
    )


def _data_management():
    st.subheader("Data Management")

    # Stats
    trades_count   = fetch_all("SELECT COUNT(*) as n FROM trades")[0]["n"]
    journals_count = fetch_all("SELECT COUNT(*) as n FROM journal_entries")[0]["n"]
    pb_count       = fetch_all("SELECT COUNT(*) as n FROM playbooks")[0]["n"]
    import_count   = fetch_all("SELECT COUNT(*) as n FROM import_history")[0]["n"]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Trades",       trades_count)
    col2.metric("Journal Entries", journals_count)
    col3.metric("Playbooks",    pb_count)
    col4.metric("Imports",      import_count)

    st.divider()

    # Export
    st.markdown("**Export Data**")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📤 Export Trades (CSV)", use_container_width=True):
            import pandas as pd
            trades = fetch_all("SELECT * FROM trades")
            if trades:
                import io
                df = pd.DataFrame(trades)
                csv = df.to_csv(index=False)
                st.download_button(
                    "⬇️ Download trades.csv",
                    data=csv,
                    file_name="trades_export.csv",
                    mime="text/csv",
                )
    with col2:
        if st.button("📤 Export Journal (CSV)", use_container_width=True):
            import pandas as pd
            entries = fetch_all("SELECT * FROM journal_entries")
            if entries:
                df = pd.DataFrame(entries)
                csv = df.to_csv(index=False)
                st.download_button(
                    "⬇️ Download journal.csv",
                    data=csv,
                    file_name="journal_export.csv",
                    mime="text/csv",
                )

    st.divider()

    # Demo data
    st.markdown("**Demo Data**")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🌱 Reload Demo Data", use_container_width=True):
            st.session_state["confirm_reload_demo"] = True

    if st.session_state.get("confirm_reload_demo"):
        st.warning("This will ADD demo trades/journals on top of existing data. Are you sure?")
        c1, c2 = st.columns(2)
        if c1.button("Yes, Add Demo Data", type="primary"):
            seed_all(clear_first=False)
            st.session_state.pop("confirm_reload_demo")
            st.success("Demo data added!")
            st.rerun()
        if c2.button("Cancel"):
            st.session_state.pop("confirm_reload_demo")
            st.rerun()

    st.divider()

    # Danger zone
    st.markdown("**⚠️ Danger Zone**")
    with st.expander("🗑️ Delete Data (irreversible)", expanded=False):
        st.warning("These actions permanently delete data and cannot be undone.")

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Delete All Trades", type="primary", use_container_width=True):
                st.session_state["confirm_delete_trades"] = True
        with col2:
            if st.button("Delete All Journals", type="primary", use_container_width=True):
                st.session_state["confirm_delete_journals"] = True
        with col3:
            if st.button("Delete Everything", type="primary", use_container_width=True):
                st.session_state["confirm_delete_all"] = True

        if st.session_state.get("confirm_delete_trades"):
            c1, c2 = st.columns(2)
            if c1.button("✅ Confirm Delete Trades"):
                execute("DELETE FROM trades")
                execute("DELETE FROM positions")
                execute("DELETE FROM import_history")
                st.session_state.pop("confirm_delete_trades")
                st.success("All trades deleted.")
                st.rerun()
            if c2.button("Cancel##dt"):
                st.session_state.pop("confirm_delete_trades")

        if st.session_state.get("confirm_delete_journals"):
            c1, c2 = st.columns(2)
            if c1.button("✅ Confirm Delete Journals"):
                execute("DELETE FROM journal_entries")
                st.session_state.pop("confirm_delete_journals")
                st.success("All journal entries deleted.")
                st.rerun()
            if c2.button("Cancel##dj"):
                st.session_state.pop("confirm_delete_journals")

        if st.session_state.get("confirm_delete_all"):
            c1, c2 = st.columns(2)
            if c1.button("✅ CONFIRM DELETE ALL"):
                for table in ["trades", "positions", "playbooks", "playbook_rules",
                               "playbook_risk_rules", "journal_entries", "import_history"]:
                    execute(f"DELETE FROM {table}")
                st.session_state.pop("confirm_delete_all")
                st.success("All data deleted.")
                st.rerun()
            if c2.button("Cancel##da"):
                st.session_state.pop("confirm_delete_all")


# ── MT5 FTP Settings ──────────────────────────────────────────────────────────

def _ftp_settings(settings: dict):
    import json
    from datetime import datetime
    from utils.ftp_sync import connect_ftp, list_ftp_folders, sync_all_accounts, is_running
    from utils.accounts import get_accounts

    st.subheader("MT5 FTP Auto-Sync")
    st.caption(
        "MT5 terminals on the remote machine publish account HTML reports to an FTP server. "
        "The app polls every 5 minutes and imports any new or updated trades automatically."
    )

    # ── Sync thread status ────────────────────────────────────────────────────
    if is_running():
        st.success("✅ Sync thread is running — polling every 5 minutes")
    else:
        st.warning("⚠️ Sync thread is not running. Restart the app to start it.")

    st.divider()

    # ── Connection settings ───────────────────────────────────────────────────
    st.subheader("FTP Connection")

    with st.form("ftp_connection_settings"):
        c1, c2 = st.columns([3, 1])
        ftp_host = c1.text_input(
            "Host / IP", value=settings.get("ftp_host", ""), placeholder="e.g. 192.168.2.234"
        )
        ftp_port = c2.number_input(
            "Port", value=int(settings.get("ftp_port", 21)), min_value=1, max_value=65535, step=1
        )
        c3, c4 = st.columns(2)
        ftp_user = c3.text_input(
            "Username", value=settings.get("ftp_user", ""), placeholder="e.g. mt5ftp"
        )
        ftp_password = c4.text_input(
            "Password", value=settings.get("ftp_password", ""), type="password"
        )
        ftp_enabled = st.checkbox(
            "Enable automatic sync",
            value=settings.get("ftp_enabled", "true") == "true",
            help="Uncheck to pause polling without losing connection settings",
        )

        if st.form_submit_button("💾 Save FTP Settings", type="primary"):
            save_all_settings({
                "ftp_host":     ftp_host,
                "ftp_port":     str(ftp_port),
                "ftp_user":     ftp_user,
                "ftp_password": ftp_password,
                "ftp_enabled":  str(ftp_enabled).lower(),
            })
            st.success("FTP settings saved. Restart the app for the sync thread to pick up new credentials.")
            st.rerun()

    # ── Test connection ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("Test Connection")

    if st.button("🔌 Test FTP Connection"):
        cfg = {
            "ftp_host":     settings.get("ftp_host", ""),
            "ftp_port":     settings.get("ftp_port", "21"),
            "ftp_user":     settings.get("ftp_user", ""),
            "ftp_password": settings.get("ftp_password", ""),
        }
        if not cfg["ftp_host"] or not cfg["ftp_user"] or not cfg["ftp_password"]:
            st.error("Save host, username, and password first.")
        else:
            try:
                with st.spinner("Connecting…"):
                    ftp = connect_ftp(cfg)
                    folders = list_ftp_folders(ftp)
                    ftp.quit()
                st.success(f"✅ Connected! Found {len(folders)} folder(s) on FTP root:")
                st.code("\n".join(folders) if folders else "(no folders found)")
            except Exception as e:
                st.error(f"Connection failed: {e}")

    # ── Account → FTP folder mapping ──────────────────────────────────────────
    st.divider()
    st.subheader("Account → FTP Folder Mapping")
    st.caption(
        "MT5 publishes each account's report into a folder named after its account number. "
        "Enter the FTP folder name for each account. Leave blank to skip that account."
    )

    accounts = get_accounts()
    if not accounts:
        st.info("No accounts configured yet — add accounts in the Accounts tab first.")
    else:
        for acc in accounts:
            c1, c2, c3 = st.columns([3, 2, 1])
            c1.markdown(f"**{acc['name']}** · {acc['broker']}")
            current_folder = acc.get("ftp_folder") or ""
            new_folder = c2.text_input(
                "FTP folder",
                value=current_folder,
                placeholder=f"e.g. {acc.get('account_number') or '142331'}",
                key=f"ftp_folder_{acc['id']}",
                label_visibility="collapsed",
            )
            if c3.button("Save", key=f"save_ftp_{acc['id']}"):
                execute(
                    "UPDATE accounts SET ftp_folder=? WHERE id=?",
                    (new_folder.strip() or None, acc["id"]),
                )
                st.success(f"Saved folder '{new_folder}' for {acc['name']}")
                st.rerun()

    # ── Sync status & manual trigger ──────────────────────────────────────────
    st.divider()
    st.subheader("Sync Status")

    last_sync = settings.get("ftp_last_sync", "Never")
    last_result_raw = settings.get("ftp_last_result", "")

    col1, col2 = st.columns([2, 1])
    col1.markdown(f"**Last sync:** {last_sync}")

    if col2.button("▶ Sync Now", type="primary", use_container_width=True):
        with st.spinner("Syncing all configured accounts…"):
            results = sync_all_accounts()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_setting("ftp_last_sync", now)
        save_setting("ftp_last_result", json.dumps(results))
        st.rerun()

    if last_result_raw:
        try:
            results = json.loads(last_result_raw)
        except Exception:
            results = {}

        for key, val in results.items():
            if key.startswith("_"):
                st.warning(f"{key[1:].capitalize()}: {val}")
            elif isinstance(val, dict):
                err = val.get("error")
                new = val.get("new", 0)
                upd = val.get("updated", 0)
                if err:
                    st.error(f"**{key}** — {err}")
                else:
                    st.info(f"**{key}** — {new} new, {upd} updated")


def _network_settings():
    from utils.network_config import load_network_config, save_network_config, streamlit_launch_command

    st.subheader("Streamlit Network / Access")
    st.caption(
        "Configure where the app binds and how it starts. "
        "Saved settings are written to `data/network.json` and used by the local launcher."
    )

    cfg = load_network_config()

    with st.form("network_settings"):
        c1, c2 = st.columns(2)
        server_address = c1.text_input(
            "Server address",
            value=cfg.get("server_address", "127.0.0.1"),
            help="Use 127.0.0.1 for local-only, or 0.0.0.0 for LAN access."
        )
        server_port = c2.number_input(
            "Server port",
            value=int(cfg.get("server_port", 8503)),
            min_value=1,
            max_value=65535,
            step=1
        )

        c3, c4 = st.columns(2)
        server_headless = c3.checkbox(
            "Headless mode",
            value=bool(cfg.get("server_headless", True)),
            help="Streamlit internal headless option."
        )
        open_browser = c4.checkbox(
            "Open browser on launch",
            value=bool(cfg.get("open_browser", True)),
            help="You can turn this off on remote / RDP sessions."
        )

        st.markdown("ℹ️ Changing these values requires relaunching the app.")
        if st.form_submit_button("💾 Save Network Settings", type="primary"):
            save_network_config({
                "server_address": server_address.strip(),
                "server_port": int(server_port),
                "server_headless": bool(server_headless),
                "open_browser": bool(open_browser),
            })
            st.success("Network settings saved.")
            st.info("🔁 Restart required: close all running Streamlit instances and restart via `launch.bat` for changes to take effect.")
            st.rerun()

    st.divider()
    st.markdown("**Current launch command**")
    st.code(streamlit_launch_command(load_network_config()), language="bash")
