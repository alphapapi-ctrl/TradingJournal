# 📈 Trading Journal

A Streamlit trading journal focused on stocks & ETF swing/position trading — broker imports,
position-based journalling, playbooks with OR-group rules, Menaker trade categorisation,
live open P&L (including dividends) and process-compliance reporting.

## Features

- **Journal** (default page) — monthly calendar overview with journal coverage, trade-entry
  markers, day drill-down, open positions with live P&L + dividends (yfinance), daily/weekly
  entries, per-trade & per-position notes with pre/post split
- **Menaker framework** — categorise every trade Type 1–4 (on/off-plan × win/loss),
  pre-trade psychology check (accuracy, smart vs sloppy risk, body scan), autopsy prompts
- **Stock Analysis** — technicals (EMA 21/50, SMA 200, RSI, OBV, volume-spike/capitulation
  scan, weekly demand zones) and a crude Buffett/Burry fundamental prequalification with
  traffic-light checks; ETF-aware
- **Risk Calculator** — stocks-first position sizing with live prices, creates linked
  pre-trade journal entries; Kelly criterion from your measured edge
- **Add Trade** — auto-detected imports: TradingView paper CSV, IBKR Activity Statement,
  CMC Markets, MT5 HTML/CSV, IC Markets; manual entry with account assignment
- **Trades / Trade Detail** — merge trades into positions (with playbook propagation),
  partial close / full close / reopen, edit any trade (position totals auto-recompute),
  real price chart with entry/exit marked, EMA 20/50/200, daily/weekly toggle
- **Playbooks** — required/optional/bonus rules with weights, OR-groups (any-one-satisfies),
  risk thresholds with position-size multipliers
- **Reports** — general performance, Menaker category trends, journal discipline
  (grades, mood, pre-plan & review compliance), playbook compliance, risk-threshold outcomes

## Getting Started

```bash
pip install -r requirements.txt
streamlit run app.py
```

A local SQLite database is created at `data/journal.db` on first run.
All data stays on your machine — the `data/` directory is git-ignored because it contains
your trades, journal entries and any stored credentials.

## Playbook Logic

Rules have three types:
- 🔴 **Required** — 60% of the score. Not meeting these triggers risk warnings.
- 🟡 **Optional** — 30% of the score.
- 🟢 **Bonus** — 10% of the score.

Rules sharing an **OR Group** name collapse into a single requirement satisfied when any
one of them is met (e.g. "3-monthly rebalance OR mid-cycle stop management").

**Risk Thresholds** define what happens when rules aren't met:
- `reduced` — take smaller position (size multiplier)
- `no_trade` — the setup should not be taken

## Project Structure

```
TradingJournal/
├── app.py                  # Main Streamlit app + nav
├── database.py             # SQLite schema, migrations & helpers
├── brokers/
│   ├── importers.py        # Auto-detection + MT5/IC Markets parsers
│   ├── tradingview.py      # TradingView paper-trading CSV (FIFO pairing)
│   ├── ibkr.py             # IBKR Activity Statement CSV
│   └── cmc_markets.py      # CMC Markets CSV
├── pages/
│   ├── journal.py          # Calendar overview, daily/weekly/trade notes, pre-trade plans
│   ├── stock_analysis.py   # yfinance technicals + fundamentals
│   ├── risk_calculator.py  # Position sizing + Kelly
│   ├── import_trades.py    # Add Trade (imports + manual)
│   ├── trades.py           # Trade list, merge, account assignment
│   ├── trade_detail.py     # Drill-down, manage (close/partial/edit), chart
│   ├── statistics.py       # Reports
│   ├── playbooks.py
│   └── settings.py         # Accounts, risk defaults, display, AI (local LLM), data, FTP
└── utils/
    ├── trade_ops.py        # CRUD, positions, close/partial/reopen, recompute
    ├── playbook_logic.py   # Rule evaluation incl. OR-groups, risk scoring
    ├── stock_data.py       # yfinance wrappers, technical & fundamental checks
    ├── statistics.py       # Performance calculations
    ├── accounts.py
    └── theme.py            # Dark/light theming
```
