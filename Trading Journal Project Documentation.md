# Trading Journal — Project Documentation
**Status:** Active development · Local Streamlit app · Python / SQLite  
**Last updated:** May 2026

---

## Overview

A locally-hosted trading journal built with Streamlit, SQLite, and Plotly. Designed for a single trader running multiple broker accounts. Core features: trade import, playbook rule management, journal entries with pre/post trade split, statistics, risk calculator, and optional AI analysis via Claude or ChatGPT.

---

## Project Structure

```
trading_journal/
├── app.py                        # Main entry point, routing, sidebar, theme injection
├── requirements.txt
├── .streamlit/
│   └── config.toml               # showSidebarNavigation = false
├── data/
│   ├── trading_journal.db        # SQLite database
│   └── theme.json                # Persisted theme choice (dark/mid/light)
├── brokers/
│   ├── __init__.py
│   ├── importers.py              # Auto-detect + MT5 HTML/CSV + IC Markets CSV parsers
│   └── cmc_markets.py            # CMC Markets statement CSV parser (FIFO lot matching)
├── pages/
│   ├── dashboard.py              # Main dashboard with calendar, stats panel, equity curve
│   ├── import_trades.py          # File upload + broker detection + account assignment
│   ├── trades.py                 # Trade list with account/symbol/status filters
│   ├── trade_detail.py           # Single trade drill-down with chart, playbook, journal
│   ├── playbooks.py              # Playbook CRUD + rule management + risk thresholds
│   ├── journal.py                # Daily/weekly/trade journal + pre-trade planning
│   ├── statistics.py             # General stats + playbook compliance + threshold outcomes
│   ├── risk_calculator.py        # Position sizer, R-multiple, Monte Carlo, session limits
│   ├── ai_analysis.py            # Claude/ChatGPT analysis with quick prompts + chat
│   └── settings.py               # Accounts, profile, risk defaults, theme, data management
└── utils/
    ├── accounts.py               # Account CRUD, stats, ensure_default_accounts()
    ├── playbook_logic.py         # Rule evaluation, risk scoring, compliance stats
    ├── trade_ops.py              # Trade/position/journal CRUD
    ├── statistics.py             # P&L stats, equity curve, R-multiples, monthly breakdown
    ├── seed_data.py              # Demo data seeder (60 trades, 2 playbooks, journals)
    └── theme.py                  # Three palettes (dark/mid/light), CSS generation, chart colours
```

---

## Database Schema

### `accounts`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| name | TEXT | e.g. "CMC Markets AUD" |
| broker | TEXT | e.g. "CMC Markets" |
| account_number | TEXT | Optional |
| currency | TEXT | AUD / USD / EUR etc. |
| initial_balance | REAL | Used for % P&L calculations |
| is_default | INTEGER | 1 = default account |
| notes | TEXT | |
| created_at | TEXT | |

### `trades`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| account_id | INTEGER | FK → accounts |
| broker_trade_id | TEXT | Dedup key from broker |
| broker | TEXT | |
| symbol | TEXT | |
| direction | TEXT | LONG / SHORT |
| entry_price | REAL | |
| exit_price | REAL | |
| entry_time | TEXT | ISO datetime string |
| exit_time | TEXT | |
| quantity | REAL | Lots / shares |
| pnl | REAL | Gross P&L |
| commission | REAL | |
| swap | REAL | |
| raw_data | TEXT | JSON original broker data |
| position_id | INTEGER | FK → positions (if merged) |
| playbook_id | INTEGER | FK → playbooks |
| playbook_rules_met | TEXT | JSON {rule_id: bool} |
| risk_score | REAL | 0–100 quality score |
| status | TEXT | open / closed |

### `positions`
Merged trade groups — multiple entries/exits treated as one position. Stores weighted avg entry/exit, total qty, total P&L.

### `playbooks`
Named trade setups with symbol filter, direction, description.

### `playbook_rules`
Rules belonging to a playbook. Types: `required` (60% of score), `optional` (30%), `bonus` (10%). Each has a weight (default 1.0) for relative importance within its type.

### `playbook_risk_rules`
Thresholds that fire when rules aren't met. Two trigger modes:
- **Percentage-based:** fires when `required_pct < min_required_pct` or `optional_pct < min_optional_pct`
- **Specific rule:** fires when a named rule is (or isn't) met regardless of overall score
- Outcome: `normal` / `reduced` (with size multiplier) / `no_trade`

### `journal_entries`
| Column | Notes |
|--------|-------|
| entry_type | daily / weekly / trade |
| stage | pre / post (for trade entries) |
| trade_id | Optional — links to a trade |
| playbook_id | For pre-trade plans before a trade exists |
| pre_analysis, pre_plan, pre_psychology, pre_risk_notes | Pre-trade fields |
| analysis, execution, psychology, lessons | Post-trade fields |
| grade | A+ / A / B+ etc. |
| mood | 1–10 |

### `journal_templates`
Reusable text templates for analysis/execution/psychology sections. Per type (daily/weekly/trade), one can be set as default.

### `app_settings`
Key-value store for: account_balance, risk_pct, daily_loss_pct, weekly_loss_pct, max_trades_day, pnl_currency, date_format, trader_name etc.

---

## Broker Importers

### CMC Markets CSV
- **Format:** Cash ledger with transaction types CB (cash buy), CS (cash sell), JG (journal — dividends, crypto, fees), RG/PG (settlement flows)
- **Logic:** FIFO lot matching — each CB opens a lot per symbol, each CS consumes oldest lot(s). Partial sells split proportionally.
- **Handles:** Partial sells, multiple buys before a sell, dividends (stored as income entries), crypto via `]CYP` prefix, open positions (unsold lots kept as `status=open`)
- **Auto-detect:** By filename pattern `Statement-XXXXXX-*.csv` or presence of CB/CS type codes in content

### MT5 HTML / CSV
- Parses MetaTrader 5 account history HTML report or tab/comma-delimited CSV deals export
- Pairs `in`/`out` deal rows by symbol to form round-trip trades

### IC Markets CSV
- Standard MT4/MT5 trade history CSV with Open Time, Close Time, Open Price, Close Price, Symbol, Profit, Commission, Swap columns

### Auto-detection order
CMC Markets → MT5 HTML → MT5 CSV → IC Markets CSV (fallback)

---

## Theme System

Three palettes: **dark** (navy/black), **mid** (slate blue-grey), **light** (white/grey).

- Stored in `data/theme.json`
- `utils/theme.py` exports `get_full_css(theme)` — returns ~6000 chars of CSS overriding every Streamlit component including the header bar, sidebar, metrics, tabs, dataframes, inputs, expanders
- Chart font/grid colours exported separately via `get_chart_font_color(theme)` / `get_chart_grid_color(theme)` and applied to every Plotly figure
- All inline HTML in dashboard/cards uses CSS custom properties (`var(--bg-card)`, `var(--accent)` etc.) so theme switch cascades instantly
- Theme picker: 3 radio buttons in Settings → Display tab. Apply button calls `set_theme()` + `st.rerun()`

---

## Dashboard Layout

```
Account dropdown
──────────────────────────────────────────
KPI row (7 metrics, all-time for selected account)
──────────────────────────────────────────
Calendar [Monthly | Weekly heatmap]     ← view toggle
  Monthly: month selector → summary bar (P&L $+%, trades, WR, avg/day, best/worst day)
           + 7-column calendar grid with per-day: $PnL, %account, N trades, W wins L losses
  Weekly:  16-week rolling heatmap with hover detail
──────────────────────────────────────────
Period Stats Panel (scoped to selected month or All Time)
  8 KPI cards + Monthly P&L bars + P&L by Symbol
──────────────────────────────────────────
Equity Curve (full width, with drawdown overlay)
──────────────────────────────────────────
Win/Loss ring  |  Recent trades + journal  |  Streaks & records
```

---

## Playbook Scoring

```
Score = (req_met_weighted / req_total_weighted) × 60
      + (opt_met_weighted / opt_total_weighted) × 30  
      + (bonus_met_weighted / bonus_total_weighted) × 10
```

Risk thresholds are checked in order of `min_required_pct` descending. First matching threshold wins. Specific-rule triggers take priority over percentage-based.

---

## AI Analysis Page

- Supports **Claude** (claude-opus-4-5 / sonnet / haiku) and **ChatGPT** (gpt-4o / mini / turbo)
- API key entered in sidebar, never stored to disk
- **Quick Analysis:** 7 one-click prompts (Performance Review, Psychology, Playbook Compliance, Loss Analysis, Best Setups, Weekly Plan, Risk Management)
- **Chat:** Multi-turn conversation. Full stats + journal context injected on first message, subsequent turns use conversation history
- **Prompt Library:** Saved/editable custom prompts with run button
- Context includes: all-time stats, by-symbol breakdown, monthly P&L, playbook compliance, last 15 journal entries

---

## Known Issues / Incomplete Items

### Bugs / edge cases to verify
- [ ] CMC FIFO matching: untested with stock splits, bonus shares, rights issues
- [ ] CMC crypto qty is stored as 1.0 (qty not in CMC's ledger description) — P&L is correct but quantity is wrong
- [ ] Statistics page: account filter only wired to General tab; Playbook Compliance and Threshold Outcomes tabs ignore account selection
- [ ] Dashboard account balance uses `initial_balance` from accounts table — needs to be entered manually in Settings → Accounts; doesn't auto-calculate

### Not yet built (from original spec)
- [ ] **AI context completeness:** pre-trade plans and full journal text not yet included in AI context builder — only stats + truncated journal entries
- [ ] **Risk calculator open positions awareness:** position sizer doesn't query current open trades to warn about existing exposure
- [ ] **Chart/screenshot attachments** on trade journal entries
- [ ] **MT5 live connection panel** — Windows-only, requires `MetaTrader5` package; stub exists in Settings but not wired
- [ ] **Performance targets vs actuals** — weekly/monthly goal setting
- [ ] **Session/day analysis** — do you perform better morning vs afternoon, Monday vs Friday etc.
- [ ] **Trade correlation analysis** — consecutive loss patterns, performance after big wins/losses

### Architecture decisions deferred
- [ ] Multi-user support (currently single-user SQLite)
- [ ] Cloud deployment (decided: stay local for now)
- [ ] Multiple account equity curves on same chart (cross-account view)

---

## Priority List (suggested)

### P1 — Data accuracy
1. Fix statistics page to respect account filter on all tabs
2. Verify CMC P&L calculations against broker statements
3. Auto-calculate running balance from initial_balance + cumulative P&L

### P2 — Core usability
4. Wire AI context to include pre-trade plans
5. Risk calculator: show current open exposure when sizing
6. Add account filter to Trade Detail page

### P3 — Features
7. Session/time-of-day performance analysis
8. Chart image upload on trade journal
9. MT5 live pull (Windows)
10. Performance targets

---

## Running Locally

```bash
# Install
pip install -r requirements.txt

# Run
streamlit run app.py

# First run auto-seeds demo data (60 trades, 2 playbooks, journals)
# Import real data: Import Trades page → upload CMC/MT5/IC Markets file
```

### requirements.txt
```
streamlit>=1.32.0
pandas>=2.0.0
numpy>=1.24.0
plotly>=5.18.0
sqlalchemy>=2.0.0
anthropic>=0.20.0
openai>=1.0.0
# MetaTrader5>=5.0.45  # Windows only, optional
```

---

## Key Design Decisions

**SQLite over Postgres:** Single-user local app, no need for connection pooling. File-based DB is trivially backed up (just copy the `.db` file).

**No ORM:** Raw SQL via `sqlite3` with thin `fetch_all` / `fetch_one` / `execute` helpers in `database.py`. Keeps it readable and avoids dependency overhead.

**FIFO lot matching for CMC:** CMC exports a cash ledger not a trade list. FIFO is the standard ATO method for CGT calculations and matches how most Australian retail brokers account for trades.

**CSS custom properties for theming:** All inline HTML uses `var(--bg-card)` etc. rather than hardcoded hex. This means a single `st.markdown()` CSS injection at app startup is the only place colours are defined, and the theme switch propagates everywhere instantly without page re-renders.

**account_id on every trade:** Even though it's currently single-user, every trade is tagged to an account from day one. This makes the account dropdown filter trivial and leaves the door open for multi-account or multi-user scenarios without a schema migration.

---

## CMC Data Notes (Account: Statement-420453)

- Date range: 2022-03-19 to 2026-04-18
- 73 trades imported (67 closed, 6 open)
- Closed P&L: **-$2,850.97 AUD**
- Win rate: **31.3%** (21 wins, 46 losses)
- Symbols traded: ASX equities (S32, BHP, AGL, REH, CBA etc.), US-listed ETFs (AG:US, PAAS:US, SILJ:US), crypto (UNI, LINK, AAVE, AXS, ENS, LRC, RNDR, SAND)
- 6 open positions remain (not yet sold as of statement date)
- Dividends: parsed and stored with `trade_type='dividend'` (not included in P&L stats by default)