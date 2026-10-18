# Native psychology patterns

Schema/capability version **1.2.0** adds five tools to the existing journal MCP
(38 tools total). This implements the supplied psychology-pattern requirements
without introducing clean-day counters, progression rules, diagnoses, risk
changes or broker operations. All acceptance tests use disposable databases.

## Records and discovery

- `psychology_patterns`: reusable definitions identified by stable `pattern_key`,
  with label, optional system grouping, description, confirmed tags and current
  practice response. Similar labels are never merged automatically.
- `pattern_entries`: individual occurrences identified by stable `entry_key`.
  A definition is optional, so a partial or unclassified no-trade event is valid.
- `pattern_followups`: append-only practice notes, results and corrections.
- `pattern_revisions`: immutable snapshots of definition/occurrence edits.

Use `journal_schema`, `get_record` and paginated `list_records` to inspect these
entities. Schema responses include `schema_version`, capability flags and
machine-readable create/patch/filter contracts, enums, relationship rules,
array replacement and date/sort semantics. Native write tool schemas expose
typed nested data. Generic CRUD cannot bypass the pattern write tools.

## Writes

| Tool | Contract |
| --- | --- |
| `save_psychology_pattern` | `data`, required `idempotency_key`, optional `pattern_id`, `expected_revision`. Create requires `pattern_key` and `label`. Updates require current revision. |
| `save_pattern_entry` | `data`, required `idempotency_key`, optional `pattern_entry_id`, `expected_revision`. Create requires `entry_key`, existing `account_id`, local `event_date`, IANA `timezone` and at least one substantive observation. |
| `append_pattern_followup` | Typed `data`, required `idempotency_key`, optional `expected_revision`. Data requires parent `pattern_entry_id`, stable `followup_key` and `note`. |

Omitted patch fields preserve saved values. Explicit null clears nullable fields.
Arrays replace atomically; null clears them to `[]`. Provenance/state objects also
replace atomically; null clears to `{}`. Keys and an occurrence's account remain
fixed. Empty create observations and clearing all substantive observations fail.

Every logical write requires a stable idempotency key. An identical retry returns
the original result, even if a later operation has changed the record. Changed
input under that key conflicts. Read back the current record before a subsequent
edit. Definition/occurrence updates require `expected_revision`; a stale write
returns the current revision and changes nothing.

Pattern execution errors return `isError: true` with structured `error` containing
`code`, `message` and available conflict/link details. Codes distinguish
`validation_failure`, `missing_link`, `account_mismatch`, `revision_conflict` and
`idempotency_conflict`. MCP input-schema validation can also reject malformed
arguments before the tool executes. Saves return IDs, revision and saved state;
get/read-back includes history, links, definition and follow-ups.

### Reflection and classification

All twelve reflection fields are nullable: `situation`, `early_warning_condition`,
`trigger`, `emotion_body_state`, `automatic_thought`, `impulse_temptation`,
`action_taken`, `hypothetical_cost_if_repeated`, `root_belief_or_fear`,
`future_regret_or_reward`, `replacement_thought`, `better_response`.

Additional optional fields include `observed_result`, `next_session_intention`,
`setup_description`, `title` and a separate `interpretation`. No emotion or tag
is inferred from prose or trading results. `field_provenance` names fields with
`kind: user_report | imported | assistant_interpretation`, and optional source,
recorder and actual observation time. Assistant interpretation belongs in
`interpretation`, not the user's original reflection fields. Revisions preserve
prior observations. `field_states` distinguishes `provided`, `unknown` and
`not_applicable`; unknown/not-applicable fields retain null text, not placeholder
emotion categories.

Behaviour classifications remain separate:

| Field | States |
| --- | --- |
| `urge_response` | resisted, acted_on, no_urge, unknown |
| `plan_adherence` | followed, deviated, not_applicable, unknown |
| `rule_breach` | yes, no, unknown |
| `status` | draft, reviewed, archived |
| `follow_up_status` | pending, reviewed, not_required |

Behaviour defaults are unknown. A resisted urge does not imply a rule breach;
an acted-on urge does not automatically imply deviation. A reviewed reflection
can still have pending follow-up. Confirmed trigger/emotion/behaviour tags are
free-form labels, with no mandatory taxonomy.

Keep optional source ratings in `uncalibrated_score_note`. Numeric ratings and
automatic mood/grade/readiness mappings are not introduced. Proposed rules use
`proposed_rule` and `proposed_rule_status`. An adopted proposal requires an
explicit `adoption_decision`, `adoption_reference` and offset-aware `adopted_at`;
recording it does not modify a playbook or risk regime.

### Context and evidence

Occurrences may link existing session, candidate, trade IDs, position, parent
daily entry, context journal entry, related occurrences, management events and
evidence. Links must exist and match the selected account/context. Multiple
trades are references to one occurrence. Conflicts roll back the complete write.

`upload_evidence` accepts `pattern_entry_id` as an owner instead of
`journal_entry_id`. No dummy daily entry is required. Both owners may be supplied
when their account/context is consistent. Existing journal-owned calls continue
working. PNG/JPEG/WebP size/byte/hash validation, retrieval and expiring download
capabilities are unchanged. A pattern upload atomically adds its evidence ID to
the occurrence and advances its revision/history. Reusing existing evidence IDs
does not create another upload.

Event dates are local `YYYY-MM-DD` observations, not creation dates. An actual
`occurred_at` is optional and must include an offset and agree with the stored
date/timezone. Date-only events keep null time. Server audit timestamps remain
separate. Template ID/version and baseline/playbook versions are supported;
template versions are captured when a template is newly linked.

## Follow-ups and new-chat retrieval

A follow-up can retain actual `observed_at`, response practised/helpful booleans,
result, intention, evidence and provenance. Unknown observation time stays null.
Use `corrects_followup_id` to append a correction; originals remain readable and
reports exclude superseded follow-ups. A later occurrence gets a new entry key
referencing the same definition, not a disguised follow-up.

For an atomic parent change, provide `parent_update` with workflow status,
follow-up status and/or next-session intention, plus the parent's current
`expected_revision`. The returned parent state shows exactly what changed.
Follow-up-local intention text does not silently replace the parent's intention
without this explicit patch.

Retrieve pending work independently of handovers:

```json
{"entity":"pattern_entries","filters":{"account_id":123,"follow_up_status":"pending"},"limit":100}
```

Use an actual existing account ID. Follow-ups also support an exact `account_id`
filter. Occurrence lists sort by event date, known UTC event time, then ID;
date-only entries sort first on their reported date. Follow-up lists sort by
known observation time and ID, with unknown time first. Archived entries are
excluded by default; request `status: archived` or `include_archived: true`.
Tag filters require all requested confirmed tags.

Session handover can retain a `neo_psychology` object containing native
`pattern_entry_ids`, `pending_pattern_entry_ids` and `next_session_intention`.
Resolve migrated legacy IDs with the migration mapping or exact
`legacy_journal_entry_id` filter. Merge this object into the current handover
using `save_session` and its revision, preserving other keys and review blocks.
Pattern tools never clear a session review block or start background monitoring.

## Reports

`generate_pattern_report` requires explicit `account_ids`, inclusive `start_date`
and `end_date`. Optional filters cover account/session/symbol/definition,
workflow/follow-up state, behaviour and confirmed tags. Default date basis is
stored `event_date`; `session_exchange_date` uses the linked exchange date and
returns excluded IDs/counts for missing session date context.

The report contains distinct occurrence/session/trade totals and IDs; recurrence
by stable definition and period; latest practice response; tag/behaviour
breakdowns; useful user-reported follow-ups; pending work; prior equal-length
period comparison; missing classifications/provenance and logging coverage.

Resistance rate is resisted / (resisted + acted_on). No-urge and unknown counts
are shown and excluded. Plan adherence is followed / (followed + deviated),
excluding and showing unknown/not-applicable. Zero denominators return null.
These denominators cover logged occurrences, not all trades or trading days.
Tag groups can overlap; overall occurrence and linked-trade counts do not grow
because of tags or follow-ups. Archived records requested explicitly have their
own summary. There is no P&L attribution in this release.

Aggregates cover all matching records. Detail entries, pending-work rows and
examples page using `limit` (1–500), `offset` and `next_offset`. Examples require
`include_examples: true` and text is bounded to 500 characters per field. Narrow
filters if a report would exceed 20,000 occurrences. Days without logs and an
empty prior sample are missing coverage, not proof of improvement or resolution.

## Legacy migration

`migrate_pattern_entries` defaults to `dry_run: true`. Selection requires explicit
source IDs, database-specific pattern template IDs and/or `namespace: true` for
`custom_fields.neo_pattern`, plus a confirmed timezone for missing legacy timezone
metadata. Explicit source IDs must still have the known namespace or a supplied
pattern-template binding. Ordinary daily psychology is never automatically
promoted to an occurrence. No template ID, account or definition is hard-coded.

Preview returns proposed mappings, counts, conflicts, unmapped headings/metadata
and unresolved old related IDs. Full original reflection/source fields are
retained in `legacy_reflection_text`; uncertain mapping sets a review flag.
Only recognised headings are mapped. Definitions and emotion tags are not
created by sentiment analysis or guessed grouping.

Commit requires `dry_run: false` and `idempotency_key`. Each source/target/marker
write is atomic; valid sources can migrate while other selected sources are
reported as conflicts. Inspect the returned per-source state. Repeat selection
under another logical key returns existing mappings, without duplicate events.
After source edits, migration reports `reconciliation_required`; it never
overwrites a native edit automatically. Review the source/native history and use
an explicit native patch to reconcile.

Sources, templates and unrelated custom-field namespaces stay intact. The source
gets a native-ID marker and the native record retains source ID/revision and
migration ID. Pattern reports count native IDs once; migrated sources no longer
count as additional daily entries or trading reviews in the journal views.
Unmigrated sources remain visible. Preview does not write even an idempotency log.

## Deployment

Pull this commit, install both requirements files using the journal's virtual
environment, and restart the journal and MCP processes. The additive startup
migration runs transactionally. Its evidence-table upgrade retains existing image
IDs, metadata and BLOBs, and a failed upgrade rolls back. No existing psychology
entries migrate automatically and no live history has been created by tests.

Confirm `journal_schema.schema_version == "1.2.0"`, capabilities and all 38 tools
after deployment. Refresh/restart the client connection if it retained an older
catalogue. Legacy templates and the current Neo plugin can continue using their
existing format until native schema/save/read-back behaviour is verified and
that workflow is explicitly switched to native patterns.
