# Neo journal operations

Workflow version 0.5.0. The trading-rule baseline remains 0.1.0 and demo default risk remains 1%. Use the [pre-session report format](pre-session.md) for technical preparation and daily narrative mapping; its long/short scenarios are not executed trades. Read the project's TradingView layout registry for current access state, observed study versions/inputs and timezone offsets; chart viewing is not proof of an image upload or a verified execution.

## Current tools and connection

Use the connected trading_journal MCP. The configured endpoint is http://mt5tools:8765/mcp, verified from local configuration and a live MCP tools/list response on 18 October 2026. It exposes 38 tools with schema 1.2.0, including five native pattern tools; verify the live catalogue/capabilities rather than assuming the installed wrappers refreshed. Do not register a duplicate connection.

Prefer native tools whose current schema includes the required arguments. This chat may retain old definitions: for example, its evaluate_playbook wrapper may accept rules_met but not evidence. For stale or missing wrappers use the scoped [MCP bridge](../scripts/journal_mcp.py), which fetches the server's live schema, validates top-level argument names and calls the same MCP tools. It does not add a new service, credentials, retries, polling or broker execution.

Use the bundled workspace Python resolved through load_workspace_dependencies. Resolve paths from the skill directory. Bridge operations are:

    python scripts/journal_mcp.py list --out <local-json-file>
    python scripts/journal_mcp.py describe save_session
    python scripts/journal_mcp.py call <tool> --args-file <exact-arguments.json> --out <result.json>
    python scripts/journal_mcp.py call upload_evidence --args-file <metadata-arguments.json> --image <actual-image-file> --out <result.json>

Those paths are relative to the skill directory. The CLI saves full results locally and prints a small success result; it does not print image bytes. On tool/protocol errors it exits nonzero. Structured pattern errors are preserved in the output JSON, including `error.code`, link/conflict details and `current_revision` when returned; stderr gives a compact failure. Nested typed validation and relationship checks remain authoritative on the server. Never interpret an error output file as a saved record. The upload arguments file contains data and an idempotency_key; --image supplies content_base64 from actual bytes. Network permission may be needed to reach the configured LAN host. Use normal host approvals; never switch endpoints or relax permissions to bypass a rejection. The bridge targets the currently unauthenticated private-LAN MCP; if authentication changes, reconnect through the supported integration rather than embedding secrets.

## Discover and protect writes

Start with journal_schema and list_records(accounts). Read project neo-config.json and journal-bindings.json, then verify the referenced names, IDs, types and versions. Page relevant list results until next_offset is null. New entities work through list_records/get_record even if a cached list_records description still lists only the older entities. Read only the selected account's relevant history.

Use expected_revision from the latest record for supported updates and stable idempotency_key values for logical writes. Generic save_record and trade_action now support both; session/candidate updates and size_candidate require the current revision. Revision is server-managed. Never send it as an editable data field.

On conflict, fetch the latest record, merge only the intended fields and reassess the action before submitting with a current revision. On a timeout or lost response, reconcile by stable key; retry the exact same payload and key only after checking its outcome. A changed request is a new logical operation, not a retry. UI writes also advance revisions. Read back each saved logical operation.

Custom fields remain whole-field replacements. The four native pattern entities also reject generic CRUD writes; use their dedicated tools. Preserve unrelated namespaces when they are still needed, but use native records for the supported Neo concepts. The new history entities are read-only through generic CRUD: do not try save_record on sessions, candidates, plans, events, evidence, snapshots, regimes or specs.

## Tool routing

| Operation | Dedicated tool | Readable entity |
| --- | --- | --- |
| Start/resume session and save handover/review block | save_session | trading_sessions |
| Create candidate or revise plan/status | save_candidate | candidates and candidate_plans |
| Append management decision | append_management_event | management_events |
| Record confirmed individual fill | record_fill | fill_events |
| Capture current balance/equity | save_account_snapshot | account_snapshots |
| Store explicit account risk policy | save_risk_regime | risk_regimes |
| Record verified contract economics | save_instrument_spec | instrument_specs |
| Size and retain assumptions as a new plan version | size_candidate | candidate_plans |
| Upload actual chart image | upload_evidence | evidence |
| Retrieve image bytes/download capability | read_evidence | evidence metadata |
| Check fills, fees, P&L and original monetary R | reconcile_candidate | report response; no trade mutation |
| Save reusable psychology definition/practice response | save_psychology_pattern | psychology_patterns |
| Save one meaningful pattern occurrence | save_pattern_entry | pattern_entries and pattern_revisions |
| Append practice note/correction and optional atomic parent patch | append_pattern_followup | pattern_followups |
| Review patterns with explicit accounts/date basis | generate_pattern_report | report response; no mutation |
| Preview/explicitly migrate legacy reflections | migrate_pattern_entries | retained legacy sources and native mappings |

These are journal operations. They do not place, modify or close broker orders.

## Sessions and linked narrative

Create through save_session(data=..., idempotency_key=...). Required data: session_key, account_id, exchange_date, local_date, timezone (IANA), label and baseline_version. Optional session data includes active/completed status, review_block 0/1, review_reason, handover object and restart_decision. Updates additionally take session_id and expected_revision at the tool's top level.

The tool creates the linked daily journal entry. Use its returned journal_entry_id for preparation/post narrative and template/version metadata; do not create a duplicate main daily entry. A meaningful supporting pattern reflection is a native `pattern_entries` occurrence; see [psychology and pattern journalling](psychology.md). Unknown account selection is a local preparation draft, not an invented account ID. Keep a stable key through overnight trading and distinguish market date from Brisbane local date.

Load the relevant prior session and open candidates before resuming. A review block carries across new sessions. Clearing it requires the user's restart decision, not merely a new date or chat. Include open trade/position IDs, unfinished candidates, missing reviews, pending policy decisions, evidence IDs, risk-override scope and next action in handover. Completing the session does not clear a block.

## Candidate and immutable plan lifecycle

Create through save_candidate with data {candidate_key, session_id, symbol, direction, plan, reason}, plus an idempotency key. Direction is LONG/SHORT. Plan fields described by the live tool include entry_price, stop_price, target_price, risk_pct, planned_cash_risk, baseline_version, playbook_id, template_id, checklist and notes. Omit unresolved numbers; keep their absence visible in the assessment.

The tool creates a linked pre-trade entry and initial immutable plan. Later changes use candidate_id, expected_revision and data containing plan/reason or status. Keep the original plan: a revision is appended rather than a retrospective rewrite. Plan history captures playbook/template versions and snapshots. Read candidate_plans and the linked narrative entry when reviewing what was known before entry.

Supported statuses: draft, assessed, blocked, pending, cancelled, open, closed. The narrative classification incomplete maps to draft with missing evidence recorded; do not submit an unsupported incomplete status. Only confirmed fills establish open, not a manual signal/status shortcut.

Use the linked journal entry for pre_analysis, pre_plan, pre_playbook_notes, pre_psychology, pre_risk_notes, pre_body_state and pre_accuracy_check. Pre and post share this entry, preserving pre fields. The entry now supports session_id, candidate_id, template_id, template_version, baseline_version and playbook_version as real fields. Apply the actual bound template and versions, not a hard-coded placeholder. Stage is pre/post, not candidate execution status.

## Current balance and percentage risk

Before sizing, resolve the selected account and actual current balance/currency. Store a fresh source-backed account snapshot with account_id, balance, optional equity, currency, source, observed_at, trading_day_timezone and trading_day_boundary in HH:MM. Snapshot source/time and the actual account day boundary must come from evidence; do not assume initial balance or midnight.

Use save_risk_regime for explicit account policy: account_id, mode demo/live/prop, risk_pct, source, effective_at and optional max_trade_risk, max_combined_risk, max_daily_loss, notes. The limit fields are CASH in account currency. Do not put percentage values into them. The user-approved Neo demo default is 1%; it can be recorded for the confirmed demo account. Do not manufacture limits or activate a different regime when a tool requires one. Prop regimes require all limits; the user's live/prop policy remains undefined.

The regime history is retained. Verify the intended account, mode and effective regime rather than choosing the highest ID globally. Explicit per-trade risk changes use the sizing override and reason; do not silently change the account regime.

Use save_instrument_spec only for verified exact-contract data: symbol, version, quote_currency, contract_multiplier, point_value, tick_size, tick_value, quantity_step, min_quantity, source and verified_at. point_value already includes the contract multiplier; tick_value equals point_value x tick_size. A generic MNQ label is not proof that a broker symbol/contract/feed or its economics match.

Use size_candidate with candidate_id, snapshot_id, regime_id, instrument_spec_id, expected_revision and offset-aware observed_at. Optional inputs: risk_pct_override and override_reason; fx_rate, fx_source and fx_observed_at; costs_per_contract; existing_open_risk and daily_loss. FX is account-currency units per quote-currency unit. Round-trip cost allowance and exposure/loss inputs are in account currency. Provide open risk and daily loss where a configured cap requires them. Do not substitute zero for missing exposure/cost evidence.

The tool uses the current-balance snapshot, checks limits, rounds quantity down and persists a plan revision with sizing provenance. Never multiply point_value by contract_multiplier again or round up to minimum quantity. Read the returned plan and current candidate revision before another update. The legacy risk_calculator remains useful for an explicit preview, but lacks this persisted candidate context.

## Four-state evidence and playbook readiness

Prefer evaluate_playbook(playbook_id=..., evidence=...) and omit rules_met. Map live rule IDs to objects with status pass/fail/unknown/caution, evidence, source and observed_at. Missing evidence remains unknown. Do not invent evidence to complete a checklist. Store this mapping and assessment in the candidate plan.

Read readiness, evidence_complete, score_components and compliance_score/risk_score separately. A required unknown/caution is incomplete; known required failure is blocked. All required checks passed can be ready even when optional divergence is unknown and the composition score is null. Absent reversal divergence alone is not a required failure. If it was assessed and absent, record optional fail rather than unknown.

Readiness is checklist information; account sizing/news/review gates still apply. Scores are neither probability nor percentage-risk instructions. Retain the existing minimum guard: min_required_pct 100, min_optional_pct 0, no trigger rule, consequence no_trade/0 on failure; passing minima defaults normal/1. Re-test after any actual rule/threshold change.

assign_playbook still accepts legacy booleans for exactly one confirmed trade OR position. Before assignment, use the four-state assessment; derive booleans only for an assessment whose required checks are known. Keep unknown optional evidence out of the legacy map, preserve the rich evidence in the plan and label any legacy composition score separately. Never patch calculated rule/score/linkage fields directly.

## Management decisions and actual fills

Append management decisions with data {event_key, journal_entry_id, observed_at, decision, reason, action_status}, plus optional candidate_id, trade_id, fill_event_id, before_stop, after_stop, before_target, after_target, rule and evidence_ids. Status is observed/proposed/executed/cancelled. Stable keys and idempotency protect retries.

Events are immutable and do not change broker orders or the journal stop/target fields. Append an execution event when an earlier proposal is acted on. For a confirmed stop/target change, patch only the relevant journal trade fields with revision/idempotency protection. Never treat an exit proposal as a fill.

Use record_fill only for a confirmed execution linked to an existing journal trade: data {candidate_id, trade_id, source, source_fill_id, filled_at, kind entry/exit, price, quantity, commission, currency}, optionally other_costs, swap, verified exit pnl and corrects_fill_id. Source fill IDs deduplicate; corrections append instead of overwriting history. Positive other_costs are fees, swap is signed, and exit pnl is VERIFIED GROSS P&L in account currency. Do not derive verified gross P&L from an unverified quote-currency calculation.

record_fill neither creates nor closes the journal trade. Reuse the correct imported trade; only create a trade from a confirmed actual entry if one is missing. For a confirmed exit, check whether an import already closed/partially closed it. Use trade_action only for the remaining journal state change, with current revision, stable key and verified fee/P&L interpretation; do not apply an exit twice. The trade_action pnl override's gross/net treatment must be confirmed before deriving an override from fill totals.

Call reconcile_candidate after recording fills and after journal-close updates. It checks entry/exit quantities, partial-fill costs, journal P&L and original monetary R. It requires verified gross account-currency P&L for each exit, allocates entry commissions pro rata to realised quantity, reports gaps and does not invent FX/P&L or mutate trades. A reconciliation gap is an unresolved item, not permission to fabricate a value.

Post narrative uses analysis, execution, psychology, lessons and optional adopted grade, stage post, with the original pre fields and candidate plan preserved. User emotions come from the user. Use generate_report only for requested account/date performance review; keep currencies separate and flag missing reconciliation.

## Screenshot upload and retrieval

Use upload_evidence with actual PNG/JPEG/WebP base64 bytes, maximum 8 MiB, and metadata {journal_entry_id OR pattern_entry_id, filename, mime_type, stage pre/management/post, captured_at, symbol, timeframe, source}, optionally candidate_id, trade_id and chart_context. At least one consistent owner is required. Native pattern evidence needs no dummy journal entry; upload advances its revision, so fetch current state before editing. Use the real capture time, exact feed/contract and observed indicator context. Retain the evidence ID, hash and linkage returned by the journal, and verify metadata via get_record/list_records. A local filename alone is not an upload.

Keep local captures as optional working copies under project evidence/YYYY-MM-DD/<candidate-key>/. Use actual TradingView/browser exports or user-provided images with their provenance. Pending TradingView access does not prevent uploading a real user-provided screenshot.

read_evidence accepts evidence_id, offset and max_bytes (up to 256 KiB). Follow next_offset, decode actual chunks, verify the returned SHA-256/byte count, and only then treat a reconstructed local image as complete. Inspect actual response fields rather than guessing a base64 field name. Its one-hour bearer download_path is on the MCP host: resolve against the configured host only if needed and treat it as temporary. Retain durable evidence IDs/hashes, not expiring token URLs in handovers. Do not expose or persist download tokens unnecessarily. The service remains private-LAN MCP access, rather than per-user authentication.

## Legacy entries, fallback and remaining gaps

Existing legacy custom_fields.neo histories are preserved. Read them for previous review blocks, original plans and evidence paths. Do not automatically backfill fabricated sessions/fills/snapshots or re-upload missing image files. If a legacy candidate is explicitly migrated, link the original entry, mark reconstruction provenance/time and avoid counting it as a second executed trade.

If native tools or their schemas are stale, use live discovery/bridge rather than downgrading. If the server/network is unavailable, save a clearly UNSAVED draft under project sessions/YYYY-MM-DD/ with the intended tool arguments, stable keys and error; preserve immutable history and merge existing namespaces only when patching a legacy field. Reconcile by key and revision after reconnecting.

No demo account, genuine account snapshot, risk regime, exact instrument spec, native session or candidate has been selected/created by this upgrade. Append-only workflow history cannot be deleted through generic CRUD, so capability validation is read-only. First-session write/upload/fill checks remain pending real account data. TradingView access and detailed exit/live-prop policies remain separate setup items.

## Psychology and native pattern entries

Use [psychology and pattern journalling](psychology.md) for the native contracts, prompts, states, provenance, report denominators and explicit legacy migration. Reusable definitions are `psychology_patterns`, occurrences are `pattern_entries`, later notes are append-only `pattern_followups` and edits retain `pattern_revisions`. Create/patch through `save_psychology_pattern` and `save_pattern_entry`; append practice/result/corrections through `append_pattern_followup`. All writes require stable idempotency keys; definition/occurrence updates and explicit follow-up parent updates require current revisions. Arrays and objects replace atomically. Errors distinguish validation, links/accounts, stale revisions and idempotency conflicts; fetch and reassess rather than automatically retrying.

Routine psychology remains in the linked daily/candidate entry. New meaningful reflections use native storage and optional pattern-template provenance, with real account/context links. Preserve user observations separately from interpretation and urge/action separately from adherence/breach. Query account-specific pending entries independently of handover, then merge native IDs and one intention into `neo_psychology` without clearing review blocks.

`generate_pattern_report` needs explicit accounts and dates; inspect date basis, distinct counts, excluded/unknown groups, zero/null denominators and logging coverage before drawing conclusions. `migrate_pattern_entries` is for explicitly requested legacy migration: preview first, verify source/template IDs and fallback timezone, then commit with a stable key and inspect per-source results. Existing legacy entries stay readable until migrated. This skill update itself does not migrate history or create personal pattern records. Clean-day/progression rules remain deferred.
