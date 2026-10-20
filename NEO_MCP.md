# Neo journal workflow

Native psychology-pattern records and reports are now available alongside this
workflow. See [PATTERN_MCP.md](PATTERN_MCP.md) for schema version 1.2.0, dedicated
tools, pending-work retrieval and explicit migration from legacy reflections.

Implemented from the requirements reviewed on 18 October 2026. These tools record
plans, observations and confirmed executions; they do not place broker orders.
Existing accounts, journal entries, imports and boolean playbook calls continue
to work. No demo account, balance, instrument specification or trading policy is
created automatically.

## Using the workflow

1. Call `journal_schema` and list existing `accounts`. Select the actual account.
2. `save_session` creates a keyed overnight session and its daily journal entry.
   Supply exchange date and Brisbane/local date separately, IANA timezone, label
   and baseline version. Resume using the same ID/key even across midnight.
3. Append `save_account_snapshot`, `save_risk_regime` and `save_instrument_spec`
   using verified data. These are immutable, timestamped records. A new snapshot
   or policy supersedes an earlier one by explicit selection, not by changing it.
4. `save_candidate` creates an idea and its pre-trade entry. Supply stable
   candidate key, session, exact symbol/contract, direction, plan and reason.
   It creates no executed trade. Plan changes append numbered revisions.
5. Use `evaluate_playbook(..., evidence=...)` to keep pass/fail/unknown/caution
   evidence with source/time. `size_candidate` persists sizing inputs and results
   in another plan revision. Move the candidate to assessed/pending only when
   required checks and risk inputs are ready. Cancelled ideas remain in history.
6. Upload actual screenshots through `upload_evidence`. Use `read_evidence` for
   chunked image bytes or a temporary download link. List with
   `list_records(entity="evidence", filters={"candidate_id": ID})`.
7. Append `append_management_event` with observed time, before/after stop/target,
   decision, applicable rule, evidence IDs and observed/proposed/executed/cancelled
   status. Proposed stop/exit decisions do not change trades or execute orders.
8. Reuse confirmed imported/manual journal trades. `record_fill` links individual
   fills to a candidate and trade, deduplicated by account + source + source fill
   ID. Entry and exit quantities, commissions and verified gross account-currency
   P&L remain separate. A correction uses a new source ID and `corrects_fill_id`.
   The first entry links the legacy journal entry to the trade as well.
9. Use `trade_action` to record actual full/partial journal closes, then
   `reconcile_candidate` to compare fills with quantities, commissions and P&L.
   Resolve its issues before marking the candidate closed. Journal commissions
   include commission plus other fees; swap uses signed credit/debit. Candidate
   totals reconcile costs across partial-close slices without duplicating fees.
10. Save session handover, review block/reasons and next action using
    `save_session`. Clearing a block requires a recorded restart decision.
    A newly created session inherits an unresolved block on that account.

## Input contracts

All workflow timestamps use ISO 8601 with an explicit UTC offset. Monetary limits
and fills' P&L/commissions are in account currency. Fill prices are in the
instrument's quote currency. All write tools accept optional `idempotency_key`;
reuse the same key and exactly the same arguments after an uncertain outcome.
A key reused with changed input fails. Cached responses are the original result;
read the current record after a later operation.

`save_record` and `trade_action` accept `expected_revision`. Session and candidate
updates require it. Revisions advance on web-interface changes as well as MCP
writes. On conflict, read, merge and retry with a new key. `custom_fields` remains
a whole-field replacement; merge namespaces before patching it.

New entities are discoverable and paginated through `journal_schema`,
`list_records`, `get_record`, and existing exports. Dedicated tools perform their
writes. History tables reject update/delete operations. Parent entries/accounts/
trades with durable workflow history cannot be deleted; retain the audit trail.

### Session data

Required on creation: `session_key`, `account_id`, `exchange_date`, `local_date`,
`timezone`, `label`, `baseline_version`. Mutable fields: `label`, `status`
(`active`/`completed`), `review_block` (0/1), `review_reason`, `handover` (object),
`restart_decision`. Session identity/date/account/original baseline stay fixed.

### Candidate data

Required on creation: `candidate_key`, `session_id`, `symbol`, `direction`
(`LONG`/`SHORT`), `plan`, `reason`. Plan fields: `entry_price`, `stop_price`,
`target_price`, `risk_pct`, `planned_cash_risk`, `baseline_version`, `playbook_id`,
`template_id`, `checklist`, `notes`. Only baseline version is mandatory for an
incomplete idea. Generated `sizing` is written by `size_candidate`.

Candidate lifecycle is draft → assessed → pending → open → closed, with blocked
and cancelled states. Returning an unfilled idea to draft permits reassessment.
Confirmed fills establish open status, including actual off-plan executions.
An idea/order intent never silently becomes a trade. Every plan stores baseline,
playbook and template versions plus snapshots of the referenced rules/templates;
subsequent edits do not rewrite historical criteria. Original pre-trade text is
preserved separately from revisions and management history.

### Risk snapshots and instrument data

- Account snapshot: `account_id`, `balance`, optional `equity`, `currency`,
  `source`, `observed_at`, `trading_day_timezone`, `trading_day_boundary` (`HH:MM`).
- Risk regime: `account_id`, `mode` (`demo`/`live`/`prop`), `risk_pct`, `source`,
  `effective_at`, optional `max_trade_risk`, `max_combined_risk`, `max_daily_loss`,
  `notes`. Limits are cash amounts. Prop policies require all three limits.
  Demo mode requires a Demo account. Live/prop have no inherited demo percentage.
- Instrument spec: `symbol`, `version`, `quote_currency`, `contract_multiplier`,
  `quantity_step`, `min_quantity`, `source`, `verified_at`, optional `max_quantity`.
  Default `sizing_method: point_value` retains required `point_value`, `tick_size`
  and `tick_value`. CFD `sizing_method: contract_size` derives point value directly
  from the contract multiplier and does not require tick values. If known ticks
  are supplied, both must be positive and tick value must equal point value ×
  tick size. Do not multiply point value by the contract size again. See
  [the stock/CFD calculator and contract-size mode](CFD_SIZING.md).

`size_candidate` needs candidate/snapshot/regime/spec IDs, `expected_revision`
and `observed_at`. Optional FX uses account-currency units per quote-currency
unit and requires source/time when currencies differ. Cost allowance is an
account-currency round-trip amount per contract. Configured combined-risk and
daily-loss limits require explicit existing open risk/daily loss. Quantity rounds
down to the permitted step; below minimum returns zero and an instrument maximum
caps quantity. Unknown tick values do not block contract-size sizing or force
decimal entry/stop prices onto a whole-point grid. A risk override requires
a reason, applies to that candidate only, and does not change the regime.

When creating an executed trade, persist `instrument_spec_id` and
`sizing_assumptions` (copy the candidate sizing object). The journal validates
known tick increments and quantity minimum/step/maximum, and retains account
currency, point value and FX basis. Tick validation is omitted for contract-size
specs with unknown ticks; monetary P&L still uses the saved contract/FX basis.
Full and partial close calculations use those economics. A verified broker P&L
override remains supported. Legacy trades without a spec retain their prior
price × quantity behaviour; no MNQ specification is guessed for them. Broker
imports retain broker-reported P&L and require explicit instrument/fill linkage.

Fills may also include `other_costs` (positive fees) and `swap` (signed
credit/debit), all in account currency. `reconcile_candidate` reports verified
gross realised P&L, commissions and other costs allocated
to realised quantities, net realised P&L and monetary R against the earliest
recorded cash risk. Open-risk, current balance, FX freshness and broker fills
must still be verified by the caller. It flags missing exit P&L, excess/out-of-order
exits, quantity/fee/swap/P&L differences. No broker sync or P&L is fabricated.

### Checklist behaviour

Four-state evidence maps rule IDs to `{status, evidence?, source?, observed_at?}`.
Missing evidence remains unknown. OR groups pass with any known pass; all known
fails block; unresolved required groups yield incomplete readiness. Unknown or
caution evidence prevents a full compliance score, without treating an absent
optional preference as a mandatory rule.

Minimum guard examples, with one required check and optional divergence:

| Evidence | Required readiness | Score | Guard result |
| --- | --- | --- | --- |
| Required pass, divergence fail | ready | 70 | normal, multiplier 1 |
| Required pass, divergence pass | ready | 100 | normal, multiplier 1 |
| Required fail, divergence pass | blocked | 40 | configured failure consequence |
| Required unknown/caution | incomplete | unavailable | unresolved, no sizing permission |
| Required pass, divergence unknown | ready | unavailable | normal if no guard needs divergence |

Scores show the existing 60/30/10 required/optional/bonus composition, including
the legacy full allocation for empty categories. They are not probabilities or
instructions to increase/reduce a configured risk percentage. Guards apply their
consequence when a minimum/trigger requirement is **not met**. Boolean
`rules_met` calls retain legacy semantics: omitted booleans mean unmet; use the
four-state interface for incomplete evidence.

## Evidence access and the web interface

Trades → **Trade Detail** follows the process on one page: overview, setup/playbook,
pre-trade plan, execution/management, post-trade review and psychology. The execution
section displays linked images and management decisions, with optional session
context and recorded plan revisions. It can attach screenshots
and record decisions. Images are validated PNG/JPEG/WebP, up to 8 MiB and 40 MP;
their SHA-256 hash and context accompany the stored bytes.

This workspace supports all playbooks. It resolves explicit trade, shared position
and confirmed candidate-fill links; it does not match unrelated entries by symbol
or date. Plans and reviews share the Trade Detail workspace; the redundant Trade Notes
tab has been removed. Existing narrative fields and immutable plan revisions are retained.
Pre/post-trade screenshots are shown side by side once, with during-trade captures
in a separate group. When a stage has several captures, select one to compare or download.
Management decisions and psychology reflections are presented as readable notes.

`read_evidence` returns base64 chunks and an expiring bearer-capability
`download_path`. Resolve the path against the configured journal server. An HTTP
download requires that capability and passes the existing private-LAN and host/
origin checks. This is per-link access control, **not account authentication**:
any client allowed to call this intentionally unauthenticated LAN MCP can request
evidence. No local file paths or external screenshot URLs are fetched by upload.
Images are stored as database BLOBs and included in SQLite online backups.

## Rollout and remaining integration checks

The migration is additive and runs at app/server startup. Existing data is
retained. Install updated `requirements-mcp.txt`, deploy the source package to
the journal host, restart the MCP and Streamlit processes, then refresh the client
tool list. No running remote service is changed by building the package.

Tests use synthetic specifications and temporary databases, including actual MCP
calls, screenshot storage/read-back, UI display, sizing and partial-close
calculations. They do not verify MNQ contract specifications, connect TradingView,
select the user's Neo demo account, reconcile real broker fills or establish
live/prop rules. Those original integration checks remain to be performed using
the actual account/feed and supplied evidence.
