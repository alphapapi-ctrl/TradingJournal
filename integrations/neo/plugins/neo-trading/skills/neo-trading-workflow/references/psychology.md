# Neo psychology and pattern journalling

Psychology prompts were adopted on 18 October 2026; workflow 0.5.0 uses the native pattern tools (schema 1.2.0). Load root `journal-bindings.json` for daily/trade templates and optional pattern-template provenance; the old daily-type pattern template is not the storage entity for new reflections. Technical baseline 0.1.0, variable 1% demo risk and existing stop-and-review rules remain in force. Clean-day counting, severity repair periods and progression/promotion rules are deferred; the reset/development PDF is their future framework, not an activated policy.

## Daily prompts

Keep the initial exchange brief and expand only on meaningful responses. Do not run the full pattern questionnaire for every trade.

Before a session, load the previous session's handover, referenced native occurrences, their definitions/current practice responses and pending follow-ups for the selected account. Ask:

- How are your focus, energy, sleep/fatigue and body state? Is there pressure, frustration, boredom or another feeling you want to flag?
- Which recent pattern or lesson should we watch today?
- What discomfort do you anticipate, and what planned response will help you follow Neo?

Before a candidate, ask whether the idea comes from the setup or an urge for action, how the user feels about the planned monetary risk, and any temptation to chase, avoid or change the plan. Store answers in the linked entry's `pre_psychology` and `pre_body_state`; use `pre_risk_notes` for reported risk acceptance. Preserve existing content and original pre-trade observations. Do not turn an unanswered reflection into a fabricated answer or an extra scoring gate. Existing material risk/mindset checks and review blocks still apply.

During management, distinguish changed market evidence from an urge to remove discomfort. A short ARTT check can help: anticipate discomfort, recognise/name it, talk through what it is asking the user to do, then choose an action supported by the plan. Ask what changed, what the user feels/thinks, what they are tempted to do, what they actually did, and which rule supports the decision. Emotions do not themselves create entry/exit signals or fill records. Retain real management decisions in `append_management_event`; cross-reference a pattern entry when relevant.

After a trade/session, ask: What did you feel/think, what did you actually do, and what happened? Did you follow the plan regardless of P&L? What response helped or needs practice? Save one concrete next-session intention and any unresolved pattern-entry IDs in handover. A resisted urge or a good response can be worth recording too. Do not equate every loss with a poor trade or every urge with a rule break.

## When to make a pattern entry

Create a focused supporting entry for a meaningful emotional pattern, recurring trigger, rule-break urge, actual lapse or improvement in execution. Routine short answers belong in the existing daily/candidate entry; create a separate pattern entry only when the event merits follow-up. Reuse the native entry for the same event and append a follow-up with its actual observation time when known; a new occurrence gets its own entry key and may reference the earlier entry and reusable definition.

Use the user's pattern template headings:

1. Situation: market/account/social context and what happened.
2. Early warning condition: fatigue, low energy, pressure or another user-reported condition.
3. Trigger: the specific event that activated the pattern.
4. Emotion/body state: the user's words and physical sensations.
5. Automatic thought: the sentence the user noticed.
6. Impulse/temptation: the action they wanted to take.
7. Action taken: what they actually did, including choosing not to act.
8. Cost/risk if repeated: a labelled hypothetical concern, separate from actual P&L.
9. Root belief or fear: optional user reflection, not a diagnosis or inferred history.
10. Future regret/greed check: what they feared regretting or hoped to gain.
11. Replacement thought: a useful operating thought in the user's words.
12. Better response/proposed rule: one concrete action to practise. Label a proposed trading/risk rule as unadopted until the user explicitly adopts it.

Ask the core situation/trigger, state/thought, urge/action and better-response questions together; fill other headings as relevant during review. Unanswered sections stay null with `field_states` set to `unknown`; use `not_applicable` only when established. Do not store placeholder text as a reported feeling. Do not request sensitive personal history as a condition of journalling. A before score of 1–5 is optional and uncalibrated: retain the user's meaning if supplied, never infer a score or map it to native `mood`/`grade`. No comparison or pass threshold is defined. Do not guess expansions of HALT/FEAR shorthand.

## Native storage and follow-up

Read the live schema and selected account before writes; use [journal operations](journal-workflow.md) for bridge, revisions and idempotency. Require schema 1.2.0 capabilities `native_patterns`, `pattern_reports`, `pattern_evidence_owner` and `legacy_pattern_migration`. If wrappers are stale, use the bridge's live schemas rather than writing new custom-field workarounds. If the server is unavailable, retain a clearly UNSAVED local draft with the intended keys.

| Purpose | Tool and readable entity |
| --- | --- |
| Reusable user-confirmed pattern and current practice response | `save_psychology_pattern`; `psychology_patterns` |
| One actual occurrence, including a no-trade event | `save_pattern_entry`; `pattern_entries` |
| Later practice/result or correction for that occurrence | `append_pattern_followup`; `pattern_followups` |
| Original definition/occurrence snapshots | `get_record` / `list_records` on `pattern_revisions` |

Definitions require stable `pattern_key` and `label`; `system_key: neo` can group confirmed Neo definitions. Check existing keys before creating; similar labels alone do not justify merging. Definition `practice_response` records the current user-confirmed response. A definition is optional for an unclassified occurrence; do not invent one to complete the form.

Create an occurrence with `save_pattern_entry(data=..., idempotency_key=...)`. Data requires stable `entry_key`, existing `account_id`, local `event_date` (YYYY-MM-DD), IANA `timezone` and at least one substantive observation. A title alone is insufficient. Store actual `occurred_at` only when known, with an offset agreeing with the reported local date/timezone; never invent midnight. A selected account is required, but a session, trade or candidate is optional. Without an account, retain an unsaved draft.

Map the twelve headings directly to `situation`, `early_warning_condition`, `trigger`, `emotion_body_state`, `automatic_thought`, `impulse_temptation`, `action_taken`, `hypothetical_cost_if_repeated`, `root_belief_or_fear`, `future_regret_or_reward`, `replacement_thought` and `better_response`. Optional `observed_result` and `next_session_intention` keep the outcome and next action separate. Use `field_provenance` for each recorded reflection: `kind: user_report` or `imported`, with actual source/recorder/time when known. Put assistant interpretation only in `interpretation` with `kind: assistant_interpretation`; do not rewrite original user reports. `field_states` distinguishes `provided`, `unknown` and `not_applicable`. Text for unknown/not-applicable fields stays null. Optional source scores belong in `uncalibrated_score_note`, with no mood/readiness conversion.

Keep independent classifications explicit:

- `urge_response`: `resisted`, `acted_on`, `no_urge`, `unknown`.
- `plan_adherence`: `followed`, `deviated`, `not_applicable`, `unknown`.
- `rule_breach`: `yes`, `no`, `unknown`; include the actual rule reference when established.
- `status`: `draft`, `reviewed`, `archived`; `follow_up_status`: `pending`, `reviewed`, `not_required`. A reviewed reflection can still need follow-up.

Unreported classifications stay unknown. Record `trigger_tags`, `emotion_tags` and `behaviour_tags` only when user-confirmed. An urge, an action, a rule breach and a loss are separate observations. `proposed_rule` remains `proposed`; an `adopted` status requires explicit `adoption_decision`, `adoption_reference` and offset-aware `adopted_at`. Saving that status does not amend a playbook or risk regime.

Link only real same-account/context records: optional `session_id`, `candidate_id`, `trade_ids`, `position_id`, `parent_journal_entry_id`, `context_journal_entry_id`, `management_event_ids`, `related_pattern_entry_ids` and `evidence_ids`. Use the main daily entry as parent and candidate narrative entry as context when applicable. Preserve optional bound `template_id`/`template_version`, actual `baseline_version` and `playbook_version`; do not replace the main entry's template. Multiple trade links still represent one occurrence. No execution or dummy journal entry is required.

Use `upload_evidence` with actual image bytes and `data.pattern_entry_id`. A journal owner may also be supplied only when consistent. Read back the durable evidence ID/hash and current occurrence: uploading atomically appends the evidence link and advances its revision. Do not reuse the pre-upload revision for the next edit.

Every logical write has a stable idempotency key; an identical retry returns its original result, while changed input under that key conflicts. Definition/occurrence updates include their ID and latest `expected_revision` at the tool's top level. Omitted fields preserve values; explicit null clears nullable text, arrays to [] and provenance/state objects to {}. Arrays and objects replace atomically, so fetch and merge before changing one member. Generic `save_record` cannot write any of the four pattern entities. Read back before claiming success and after a retry before editing again.

For a later practice note, use `append_pattern_followup` with `data.pattern_entry_id`, stable `followup_key` and actual `note`. Optional actual `observed_at`, `response_practised`, `response_helpful`, `observed_result`, evidence and intention remain user reports. Corrections append with `corrects_followup_id`; never overwrite an old note. To close pending work or change the parent's intention atomically, include `data.parent_update` and the parent's latest top-level `expected_revision`. A follow-up-local intention does not update the parent's intention automatically. A genuinely new occurrence uses `save_pattern_entry`, not a follow-up on an older event.

## Retrieval, handover and review

Load pending work independently of handover using `list_records(entity="pattern_entries", filters={"account_id": actual_account_id, "follow_up_status": "pending"})`; page through `next_offset`. Fetch referenced occurrences with `get_record`, including definition, history and follow-ups. `pattern_followups` supports exact account filtering too. Archived occurrences are excluded by default; request them explicitly for a historical review. Tag filters require all supplied tags. Keep date-only events distinct from known event times.

Merge `neo_psychology` into the latest native session handover through revision-protected `save_session`. It contains native `pattern_entry_ids`, `pending_pattern_entry_ids` and `next_session_intention`; preserve other handover keys, deferred progression status and every active review block. If saving handover fails, retain the already-saved occurrence ID and report that unfinished step rather than recreating the occurrence. Routine prompts remain in the linked daily/candidate narrative, with original pre fields preserved.

Use `generate_pattern_report` for a requested review with explicit `account_ids`, inclusive `start_date`/`end_date` and relevant filters. Default date basis is the stored local `event_date`; `session_exchange_date` excludes entries lacking linked exchange dates and reports those IDs/counts. Inspect distinct occurrence/session/trade counts, recurrence, latest practice response, pending work, helpful user-reported follow-ups and missing provenance/classification/logging coverage. Request bounded examples only when useful; page detail/pending/example rows using `next_offset` while aggregates cover all matching records.

Resistance rate is resisted / (resisted + acted_on), excluding and showing no-urge/unknown. Adherence is followed / (followed + deviated), excluding and showing unknown/not-applicable. A zero denominator is null. Rates describe logged occurrences, not all trades or days. Tag groups overlap; tags and follow-ups must not inflate total events or linked trades. An empty sample or unlogged days do not demonstrate improvement, resolution or clean days. Do not attribute P&L to a pattern.

## Existing workaround entries

Existing `journal_entries` with `custom_fields.neo_pattern` or the verified pattern-template binding remain legacy sources. Read them for unresolved work, alongside native pending entries, until explicitly migrated; do not create new legacy reflections. Legacy metadata `pattern_key` identifies an occurrence, not a reusable native definition. Resolve already-migrated references using the returned mapping or exact `legacy_journal_entry_id` filter to avoid counting the old source twice.

When the user requests migration, first call `migrate_pattern_entries` with explicit `selection.source_ids`, verified database-specific `template_ids` and/or `namespace: true`, a confirmed fallback IANA timezone and `dry_run: true`. Never hard-code the local pattern template ID or promote routine daily psychology. Preview mappings, conflicts, unmapped fields and unresolved related IDs. Full legacy text is retained; uncertain mappings need review and must not be guessed.

Commit only the requested selection with `dry_run: false` and a stable `idempotency_key`, then inspect every per-source result. Sources/templates remain intact; markers link them to native IDs. Valid sources can succeed while others report conflicts. Repeated selections reuse mappings; `reconciliation_required` after a source edit means review history and explicitly patch the native record, not overwrite it by rerunning migration. Keep old handover references resolvable; do not silently migrate or fabricate personal history during setup.

## Scope and sources

Prompts draw from `Trading yourself.docx`, `Trading_Pattern_Journal_Entry_Template.docx`, the reset/development plan and the course to-do list, with source hashes/reviews in root `source-manifest.json`. The user has now adopted these psychology prompts and pattern journalling, not all claims or policies in the source documents. Do not infer emotions from price/P&L, psychological diagnoses, childhood causes, win probabilities or a strategy edge. TradeZella is not required; Bookmap stays excluded. No course purchase, accountability message, monitoring schedule, clean-day counter or live/prop progression is authorized by this feature.
