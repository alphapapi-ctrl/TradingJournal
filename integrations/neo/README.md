# Neo skill and MCP bridge backup

Neo Trading **0.5.0**, with trading-rule baseline **0.1.0**. This directory
backs up the plugin source from the separate local NEO project. It includes the
complete skill, linked references, scoped MCP bridge, local marketplace manifest
and bridge tests. The journal server implementation remains at this repository's
root.

The workflow uses native pattern definitions, occurrences, append-only follow-ups,
reports and explicit legacy migration. The bridge discovers the connected
server's current tool schemas when a chat retains older wrappers. It preserves
structured conflicts and performs no automatic retries or migration.

## Restore or update the local plugin

1. Copy `plugins/neo-trading/` into the NEO project's `plugins/neo-trading/`
   directory. Preserve the project's account bindings, configuration and source
   documents. The backup does not supply or replace those local files.
2. Ensure that project's `.agents/plugins/marketplace.json` contains the local
   marketplace entry shown in this directory's manifest. Its source path is
   relative to the NEO project root.
3. Refresh the existing `neo-trading@neo-local` installation using the plugin
   installer. On a host with the Codex CLI this is:

   ```powershell
   codex plugin add neo-trading@neo-local
   ```

The skill reads the real NEO project's current configuration and journal
bindings. New reflections use `pattern_entries`; reusable definitions use
`psychology_patterns`. Existing `custom_fields.neo_pattern` records are legacy
sources, migrated only on explicit request after a dry-run preview. Routine
psychology remains in linked daily/candidate entries. A demo account must be
selected before recording genuine observations.

The plugin uses the existing journal connection. Copying this package does not
start another MCP service or change the journal database. Do not copy an old
plugin-cache directory over the installed package.

## Verify the backup

Run from the TradingJournal repository root with its virtual environment and
both requirements files installed:

```powershell
$env:TRADING_JOURNAL_ROOT = (Get-Location).Path
.\.venv\Scripts\python.exe -m unittest discover -s integrations\neo\tests -p 'test_*.py' -v
```

The 12 unit tests cover transport, live argument guards, tool scope, structured
errors and no automatic retries. One HTTP integration test exercises all five
native pattern tools, image upload/readback, revisions, idempotency and migration
preview/commit/repeat against a disposable database under `data/`. It starts a
temporary loopback server and stops it at completion. Without
`TRADING_JOURNAL_ROOT`, the integration test is skipped. `NEO_BRIDGE_PATH` can
override the bridge under test; otherwise tests use this backed-up plugin.

All 13 tests passed for 0.5.0. Installed-bridge read-only checks also confirmed
the remote journal's schema 1.2.0, 38 tools, account-scoped pending queries and
pattern reports. No personal records were created or migrated by those checks.

Local database files, account bindings, capability dumps, installation paths,
screenshots, source PDFs/DOCX files and runtime state are excluded from this
backup. Future local plugin edits should be copied here, reviewed and tested
before committing a refreshed backup.
