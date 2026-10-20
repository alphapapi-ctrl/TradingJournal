# 📈 Trading Journal

A Streamlit trading journal focused on stocks & ETF swing/position trading — broker imports,
position-based journalling, playbooks with OR-group rules, Menaker trade categorisation,
live open P&L (including dividends) and process-compliance reporting.

## Features

- **Journal** (default page) — monthly calendar overview with journal coverage, trade-entry
  markers, day drill-down, open positions with live P&L + dividends (yfinance), daily/weekly
  entries. Its only tabs are Overview, Daily and Weekly.
- **Menaker framework** — categorise every trade Type 1–4 (on/off-plan × win/loss),
  pre-trade psychology check (accuracy, smart vs sloppy risk, body scan), autopsy prompts
- **Stock Analysis** — technicals (EMA 21/50, SMA 200, RSI, OBV, volume-spike/capitulation
  scan, weekly demand zones) with a generalised fundamental checks framework and ETF-aware
  filtering
- **Risk Calculator** — select normal stocks or IC Markets Gold/US500 CFDs; size
  shares or lots from cash risk, with saved setups and pre-trade plans. CFD sizing
  uses contract size and lot steps without tick values. See [CFD sizing](CFD_SIZING.md).
  Kelly criterion remains available from your measured edge.
- **Add Trade** — auto-detected imports: TradingView paper CSV, IBKR Activity Statement,
  CMC Markets, MT5 HTML/CSV, IC Markets; manual entry with account assignment
- **Trades / Trade Detail** — merge trades into positions (with playbook propagation),
  partial close / full close / reopen, edit any trade (position totals auto-recompute),
  real price chart with entry/exit marked, EMA 20/50/200, daily/weekly toggle.
  Trades also contains Trade Notes, Pre-Trade Plan and Psychology Patterns. Trade Notes
  opens the shared Trade Detail editor for plans and reviews; Trade Detail includes
  screenshots, management history and linked psychology reflections for any playbook.
  Overview shows pre/post-trade screenshots side by side, with selection for multiple captures.
  Journal templates are managed under Settings → Journal Templates.
- **Playbooks** — required/optional/bonus rules with weights, OR-groups (any-one-satisfies),
  risk thresholds with position-size multipliers
- **Reports** — general performance, Menaker category trends, journal discipline
  (grades, mood, pre-plan & review compliance), playbook compliance, risk-threshold outcomes

## Getting Started

**Easiest:** double-click `launch.bat` (or the *Trading Journal* desktop shortcut —
create it once with `powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1`).
It starts the app from the repo venv and opens your browser; if the app is already
running it just opens the browser.

Manual start:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## DAX Reference & Replay Trainer

Two discretionary-trading modules built on the School Run App tick/parquet caches:

- **🎓 DAX Reference** — browse historical DAX days with strategy filters
  (gap size/direction, gap-closed-in-hindsight, open vs prior session / overnight
  range, 1st/2nd 15-min bar construction, CPI/NFP/FOMC news days). Filtered days
  appear as mini-chart cards; click one to load it on a multi-timeframe
  (1/5/15m) chart with school-run entry/stop levels and a day-info box.
  The **Data** tab points at the School Run App data folder, runs cache updates,
  rebuilds the day index and holds candle color settings.
- **⏪ Replay Trainer** — bar-by-bar replay (pause/play, speed, skip, next/random
  day) with simulated trading: position-size calculator (fixed € or %-of-balance
  compounding), market/stop/limit orders with draggable entry/SL/TP lines,
  partial/full closes, automatic SL/TP fills, R-multiple tracking and chart
  screenshots. Trades are written to a **Replay Demo** account so they show up
  in Reports, Journal and Playbooks like any other account.

News calendar dates (2016–2026) were sourced from bls.gov / federalreserve.gov;
edit `data/reference/us_news_events.csv` to extend (regenerate with
`scripts/build_news_csv.py`).

A local SQLite database is created at `data/journal.db` on first run.
All data stays on your machine — the `data/` directory is git-ignored because it contains
your trades, journal entries and any stored credentials.

## AI access over the local network (MCP)

The optional MCP server gives compatible AI applications access to journal records,
trade/position management, playbooks, reports, imports/exports, settings, and
replay/reference data. It supports hosted or local models through your AI application.
The server runs alongside the journal with no authentication, private LAN access,
below-normal priority, one tool at a time, and no background polling or model runtime.

On the journal machine, run `launch_mcp.bat -Install -Background` once. Connect
your AI application's **Streamable HTTP** MCP client to
`http://JOURNAL-PC:8765/mcp`, replacing `JOURNAL-PC` with its actual hostname/IP.
Use **Settings → MCP / AI Access** to check server/database status, install the
quiet Windows login task, and view connection/setup requirements. Or run
`scripts/setup_mcp_task.ps1 -ConnectionHost mt5tools -StartNow` in PowerShell on
the journal computer. Startup preferences stay in its ignored `data/mcp/` folder.
See [MCP_SETUP.md](MCP_SETUP.md) for deployment, supported tools, resource
measurements, connection checks, and stopping the service.

Neo trading workflows add durable screenshots, management/fill history, versioned
candidate plans, overnight sessions, balance/risk snapshots and instrument/FX
sizing. Use **Trades → Trade Detail → Evidence & History** to review captures and decisions.
See [NEO_MCP.md](NEO_MCP.md) for the workflow and input contracts. Existing records
are preserved through an additive startup migration.

Native psychology patterns separate reusable definitions, occurrences and
follow-ups, with auditable edits, account-scoped recurrence/response reports,
direct screenshot links and explicit legacy migration. Use **Trades → Psychology
Patterns**. See [PATTERN_MCP.md](PATTERN_MCP.md); progression and clean-day policies
remain outside this feature.

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
