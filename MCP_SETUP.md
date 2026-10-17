# Connect an AI assistant to the trading journal

The journal now includes an optional MCP server. Run it **on the machine that
hosts the journal and its database**. Your AI application connects over the
local network and can use either a hosted model or a model on another LAN PC.
No model runs on the journal/MT5 machine as part of this service.

## Start on the journal machine

Update/copy the application files first. Keep the machine's existing `data/`
folder, database, settings, and virtual environment in place. The deployment
package for this change contains application code only, with no personal data.

From the journal folder, run this once to install the optional dependencies:

```powershell
.\launch_mcp.bat -Install -Background
```

Subsequent starts:

```powershell
.\launch_mcp.bat -Background
```

For a visible foreground terminal, double-click `launch_mcp.bat` or omit
`-Background`. Stop a foreground server with Ctrl+C. Stop the background server:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_mcp.ps1
```

The server uses the existing `data/journal.db`, matching the current browser
application. It can run alongside Streamlit, or independently. Starting it
initialises the normal journal schema if needed. This does not create accounts
or import demo trades. The existing browser launcher continues to start the
browser journal only; use the MCP launcher separately when needed.

The MCP launcher expects the journal's `.venv\Scripts\python.exe` to exist.
For a new installation, first create the virtual environment with
`python -m venv .venv`. The `-Install` option then installs both the journal's
existing dependencies and the additional MCP dependencies.

## Connect from your main trading PC

In your AI application's MCP settings, add a **Streamable HTTP** server:

| Setting | Value |
| --- | --- |
| Name | Trading Journal |
| URL | `http://JOURNAL-PC:8765/mcp` |
| Authentication | None |

Replace `JOURNAL-PC` with the journal machine's actual LAN hostname or IP.
Port 8765 is separate from the browser journal's port. The exact configuration
file format depends on your AI application; the URL and transport above are the
connection details it needs. A compatible application running on your main PC
can use these tools while talking to a hosted LLM, then switch to local inference.
A cloud-only chat service needs a local bridge if it cannot reach your LAN.

Check `http://JOURNAL-PC:8765/health` from the main PC's browser. It should show
`"status":"ok"`. To verify an actual MCP connection from a PC with the MCP
dependency installed:

```powershell
.\.venv\Scripts\python.exe .\scripts\check_mcp.py --url http://JOURNAL-PC:8765/mcp
```

To sample server memory and CPU over five idle seconds:

```powershell
.\.venv\Scripts\python.exe .\scripts\check_mcp.py --url http://JOURNAL-PC:8765/mcp --sample-seconds 5
```

To also verify record counts and generate a full report without editing records,
append `--verify-journal`. On the same machine, omit `--url` to use
`http://127.0.0.1:8765/mcp`.

If Windows Firewall blocks the connection, permit TCP 8765 for the private LAN.
An administrator can create a rule restricted to the local subnet:

```powershell
New-NetFirewallRule -DisplayName "Trading Journal MCP (LAN)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress LocalSubnet -Profile Private
```

No authentication is enabled, as requested. HTTP clients must have a private,
loopback, or link-local address. Proxy headers are not trusted. The server also
checks the requested hostname and browser origin; it accepts the machine's
detected hostnames and addresses. A hostname alias can be added explicitly:

```powershell
.\launch_mcp.bat -Background -AllowedHost journal-server
```

Browser-based MCP clients may also need an explicit origin and client-specific
CORS handling; ordinary desktop MCP clients do not send an Origin header.
There is no permissive browser CORS policy in this server. The launcher supports
`-AllowedOrigin http://YOUR-CLIENT:PORT` for the Origin validation allowlist.

### This Codex app (local trial)

The local trial is connected under the name `trading_journal` using **stdio**.
Codex launches `mcp_server.py --transport stdio` itself, so the local connection
does not depend on a background HTTP service surviving a Codex restart.
Its configuration lives in your personal Codex configuration, outside this
repository; it is not deployed through Git. Restart Codex after changing the
connection to load its tools.

To configure the same connection manually on another local installation:

```powershell
codex mcp add trading_journal -- C:\Users\pc\TradingJournal\.venv\Scripts\python.exe C:\Users\pc\TradingJournal\mcp_server.py --transport stdio
```

Adjust the absolute paths for another installation. The local connection has a
five-minute tool timeout, matching the helper limit. For a future LAN deployment,
run the HTTP server on the journal machine using `launch_mcp.bat -Background`
and change the Codex connection to its `http://JOURNAL-PC:8765/mcp` URL.

After restarting Codex, try: "Use the trading journal tools to list my accounts
and generate a performance and journal-discipline report. Don't change records."
See the [official Codex MCP configuration documentation](https://developers.openai.com/codex/mcp/)
for client configuration and restart behaviour.

## What the AI can do

The server advertises 22 tools with descriptions and input schemas:

| Capability | Tools |
| --- | --- |
| Discover fields and browse/search records | `journal_schema`, `list_records`, `get_record` |
| Add/edit/delete records | `save_record`, `delete_record` |
| Close, partially close, reopen journal trades | `trade_action` |
| Merge/unmerge positions | `position_action` |
| Assign playbooks, check rules/OR groups and risk thresholds | `assign_playbook`, `evaluate_playbook` |
| Performance, Menaker categories, discipline, playbook compliance, risk outcomes, open holdings | `generate_report`, `save_report` |
| Broker imports, CSV/JSON exports, read export files | `import_trades`, `export_records`, `read_export` |
| Journal settings, risk sizing | `settings`, `risk_calculator` |
| Stock quotes/history, technical/fundamental analysis, substantial holders | `market_analysis` |
| Reference day filters, bars, index rebuild, cache maintenance | `reference_data` |
| Replay sessions and simulated trades | `replay_action` |
| Manual MT5 FTP sync, database backup, resource diagnostics | `ftp_sync`, `backup_database`, `diagnostics` |

Record editing covers accounts, trades, daily/weekly/trade journal entries,
all pre/post analysis and psychology fields, grades and categories, templates,
playbooks/rules/thresholds, saved risk setups, and replay session state.
Positions are created by merging trades. Import history and indexed reference
features are generated records and can be inspected but not arbitrarily edited.

Start by asking the assistant to inspect the schema and list existing accounts.
For example: "Find my open trades in account 2 and produce a performance and
journal-discipline report for September 2026."

Updates are patches: omitted fields remain unchanged; null clears a nullable
field. Related writes are transactional. Deleting trades/positions/accounts
retains journal text and removes its invalid links. Account deletion retains
trading history. Journal operations do not place orders with MT5 or brokers.
Replay uses simulated Demo accounts and historical bar validation.

Broker import accepts CSV/HTML text, or base64 for binary/UTF-16 exports, with an
existing account ID. `dry_run` defaults to true; repeat with false to save.
The existing TradingView, IBKR, CMC, MT5 and IC Markets parsers are used.
Broker IDs are matched within the account and broker, so the same ticket number
in another account does not overwrite its trades. Dividends are opt-in.

Reports return calculated data and can be saved as Markdown/JSON. CSV/JSON
exports are paginated. Saved outputs live under `data/mcp/exports/`; retrieve
them through `/exports/FILENAME`, `journal://exports/FILENAME`, or `read_export`.
Backups use SQLite's online backup under `data/mcp/backups/` and include
committed changes in the WAL. They cover the database, not external market caches,
screenshots, or file-based preferences. Output files remain until you remove
them; no automatic cleanup runs in the background.

The browser remains the interactive chart/replay surface. MCP exposes its
underlying data and operations; it does not manipulate browser tabs, chart
dragging, playback animation, or screenshots. Optional market/FTP/School Run
features require the same data sources and configuration as the browser journal.

## Resource use and calculation limits

- One server process and **one tool operation at a time**; no model runtime,
  polling, scheduled analysis, auto-sync, or automatic cache updates.
- Below-normal Windows process priority and one computational-library thread.
- Imports and market/replay/reference queries use short-lived helper processes
  so their libraries and caches are released when they finish. Cache-update
  process tracking stays in the server when that maintenance is explicitly requested.
- Default page size 100, maximum 500. Imports: 8 MiB / 5,000 rows. Reports:
  20,000 trades, with equity curves sampled to at most 500 points. Larger work
  requires narrower filters or paginated requests; totals are not silently truncated.
- Reports filter inclusive dates using **entry time**, matching the existing
  journal's general reports. Amounts are grouped by account currency with no
  currency conversion. Net P&L uses `pnl - abs(commission) - abs(swap)`.
- MCP drawdown includes the initial zero-equity peak, so an initial losing
  streak is counted. Undefined ratios return null. These differ from the older
  browser report's initial-peak handling and ratio sentinels. Categories and
  discipline use the latest applicable review, counting each trade once.
- Short-lived workers have a five-minute limit. Large cache rebuilds/data
  downloads remain heavier maintenance work; request them when MT5 has headroom.

In an isolated test on the development PC, the server used about **80 MiB** at
baseline and **86 MiB** after a 5,000-trade report. The report took about
**0.09 seconds**; a two-second idle sample used **0.015 CPU seconds**, including
diagnostic requests. These are development measurements, not guarantees for
the MT5 machine. `diagnostics` and `check_mcp.py` report its actual usage.

## Other launch options and verification

```powershell
# Bind only a specific network interface / choose a different port
.\launch_mcp.bat -BindAddress 192.168.1.20 -Port 8765

# Same-machine clients that launch an MCP subprocess can use stdio instead
.\.venv\Scripts\python.exe .\mcp_server.py --transport stdio

# Explicit database selection (normally leave this unset)
.\launch_mcp.bat -Background -Database D:\JournalData\journal.db

# Integration tests: temporary databases only
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Background server logs are in `data/mcp/server.stdout.log` and
`data/mcp/server.stderr.log`. The launcher avoids starting a second instance on
the same port, and the stop script checks process identity before stopping it.
When `-Database` selects another location, logs and PID state are stored in the
`mcp` folder beside that database; pass the same `-Database` to the stop script.
Restart MCP after changing application code. Browser pages may need a refresh
to display changes made through MCP; existing sidebar statistics can remain
cached for up to 30 seconds.

The implementation uses the [official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
with stateless Streamable HTTP/JSON responses and support for older MCP clients.
