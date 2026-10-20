"""Native pattern reflections and pending practice work in the journal."""
import json
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st
from journal_mcp import patterns
from utils.journal_display import show_note, date_time, label


STATE_LABELS = {'unknown': 'Not recorded', 'resisted': 'Resisted the urge', 'acted_on': 'Acted on the urge',
    'no_urge': 'No urge', 'followed': 'Followed the plan', 'deviated': 'Deviated from the plan',
    'not_applicable': 'Not applicable', 'yes': 'Yes', 'no': 'No', 'pending': 'Follow-up needed',
    'reviewed': 'Reviewed', 'not_required': 'No follow-up needed', 'draft': 'Draft', 'archived': 'Archived'}


def _state(value):
    return STATE_LABELS.get(value, label(value))


def _reflection_field(entry, key, title):
    value = entry.get(key)
    state = (entry.get('field_states') or {}).get(key)
    if not value and not state:
        return
    st.markdown(f'**{title}**')
    if value:
        provenance = (entry.get('field_provenance') or {}).get(key, {})
        if provenance.get('kind') == 'assistant_interpretation':
            st.caption('Assistant interpretation')
        elif provenance.get('kind') == 'imported':
            st.caption('Imported reflection')
        show_note(value)
    else:
        st.caption(_state(state))


def _show_reflection(entry):
    definition = entry.get('definition') or {}
    st.markdown(f"#### {entry.get('title') or definition.get('label') or 'Pattern reflection'}")
    st.caption(' · '.join(filter(None, [date_time(entry.get('occurred_at') or entry['event_date']),
        entry.get('timezone'), entry.get('symbol'), _state(entry['follow_up_status'])])))
    for field, title in [('urge_response','Urge response'), ('plan_adherence','Plan adherence'), ('rule_breach','Rule breach')]:
        st.write(f'{title}: {_state(entry[field])}')
    happened, practice = st.columns(2)
    with happened:
        st.markdown('##### What happened')
        for field, title in [('setup_description','Setup'), ('situation','Situation'), ('early_warning_condition','Early warning'),
            ('trigger','Trigger'), ('emotion_body_state','Emotion / body state'), ('automatic_thought','Automatic thought'),
            ('impulse_temptation','Impulse / temptation'), ('action_taken','Action taken'), ('observed_result','Observed result')]:
            _reflection_field(entry, field, title)
    with practice:
        st.markdown('##### What to practise')
        for field, title in [('root_belief_or_fear','Belief / fear'), ('hypothetical_cost_if_repeated','Cost if repeated'),
            ('future_regret_or_reward','Future regret / reward'), ('replacement_thought','Replacement thought'),
            ('better_response','Better response'), ('next_session_intention','Next-session intention')]:
            _reflection_field(entry, field, title)
        if definition.get('practice_response') and definition['practice_response'] != entry.get('better_response'):
            st.markdown('**Pattern practice response**')
            show_note(definition['practice_response'])
    for key in ('trigger_tags','emotion_tags','behaviour_tags'):
        if entry.get(key):
            st.caption(f"{label(key)}: {', '.join(entry[key])}")
    for field, title in [('interpretation','Interpretation'), ('rule_reference','Rule reference'),
        ('uncalibrated_score_note','Original score note'), ('proposed_rule','Proposed rule'),
        ('adoption_decision','Rule adoption decision'), ('legacy_reflection_text','Original imported reflection')]:
        _reflection_field(entry, field, title)
    if entry.get('proposed_rule'):
        st.caption(f"Rule status: {label(entry.get('proposed_rule_status') or 'proposed')}")
        if entry.get('adoption_reference'):
            st.write(f"Adoption reference: {entry['adoption_reference']}")
    if entry.get('migration_review_required'):
        st.info('This imported reflection needs your review.')
    followups = entry.get('followups', [])
    if followups:
        st.markdown('##### Follow-up history')
        corrected = {row['corrects_followup_id'] for row in followups if row.get('corrects_followup_id')}
        for row in followups:
            with st.container(border=True):
                st.caption(date_time(row.get('observed_at')) + (' · Corrected by a later note' if row['id'] in corrected else ''))
                show_note(row['note'])
                for field, title in [('observed_result','Observed result'), ('next_session_intention','Next-session intention')]:
                    if row.get(field):
                        st.write(f"{title}: {row[field]}")
                for field, title in [('response_practised','Response practised'), ('response_helpful','Response helpful')]:
                    if row.get(field) is not None:
                        st.write(f"{title}: {'Yes' if row[field] else 'No'}")
    if entry.get('source'):
        st.caption(f"Source: {entry['source']}")


def _show_report(report):
    st.markdown(f"#### Pattern review · {report['start_date']} to {report['end_date']}")
    totals = report['totals']
    c1, c2, c3 = st.columns(3)
    c1.metric('Logged occurrences', totals['occurrences'])
    c2.metric('Linked trades', totals['distinct_trades'])
    c3.metric('Follow-ups needed', len(report['follow_up_queue']))
    for key, title in [('urge_response','Urges resisted'), ('plan_adherence','Plan followed')]:
        rate = totals[key]
        if rate['denominator']:
            st.write(f"{title}: {rate['numerator']} of {rate['denominator']} classified occurrences ({rate['rate']:.0%})")
        else:
            st.caption(f'{title}: no classified occurrences in this period.')
    st.caption('Rates use logged, classified occurrences. Unknown or not applicable responses are excluded; days without logs do not imply improvement.')
    recurrence = report['recurrence']
    if recurrence:
        st.dataframe([{'Pattern': row.get('label') or 'Unclassified', 'Occurrences': row['count'],
            'First seen': row['first_date'], 'Last seen': row['last_date'],
            'Practice response': row.get('practice_response') or 'Not recorded'} for row in recurrence], hide_index=True, use_container_width=True)
    else:
        st.info('No reflections recorded for this period.')
    quality = report['data_quality']
    if quality['unclassified'] or quality['partial']:
        st.caption(f"Data to review: {quality['unclassified']} unclassified and {quality['partial']} incomplete reflections.")


def show(account_id=None, trade=None):
    from utils.trade_context import patterns_for_trade
    if trade is not None:
        account_id = trade['account_id']
    prefix = f"trade_pattern_{trade['id']}" if trade is not None else 'patterns'
    st.subheader("Psychology patterns")
    if account_id is None:
        st.info("Choose an account above to view or record pattern reflections.")
        return
    definitions = patterns.list_records("psychology_patterns", {"include_archived": True}, limit=500)["records"]
    labels = {item["id"]: item["label"] for item in definitions}
    today = datetime.now(ZoneInfo("Australia/Brisbane")).date()
    with st.expander("Record a pattern occurrence"):
        with st.form(prefix + "_create"):
            event_date = st.date_input("Event date", today)
            timezone = st.text_input("Event timezone", "Australia/Brisbane")
            definition_id = st.selectbox("Pattern", [None] + list(labels), format_func=lambda key: labels.get(key, "Unclassified"))
            situation = st.text_area("Situation")
            trigger = st.text_area("Trigger")
            emotion = st.text_area("Emotion / body state (your words)")
            thought = st.text_area("Automatic thought")
            action = st.text_area("Action taken")
            better = st.text_area("Better response to practise")
            urge = st.selectbox("Urge response", ["unknown", "resisted", "acted_on", "no_urge"], format_func=_state)
            adherence = st.selectbox("Plan adherence", ["unknown", "followed", "deviated", "not_applicable"], format_func=_state)
            breach = st.selectbox("Actual rule breach", ["unknown", "no", "yes"], format_func=_state)
            submit = st.form_submit_button("Save reflection")
        if submit:
            data = {"entry_key": uuid4().hex, "account_id": account_id, "event_date": str(event_date), "timezone": timezone,
                    "pattern_id": definition_id, "urge_response": urge, "plan_adherence": adherence, "rule_breach": breach,
                    "source": "Journal web interface", "recorded_by": "user"}
            if trade is not None:
                data.update(trade_ids=[trade['id']], symbol=trade['symbol'])
            reflections = {"situation": situation, "trigger": trigger, "emotion_body_state": emotion, "automatic_thought": thought, "action_taken": action, "better_response": better}
            data.update({key: value for key, value in reflections.items() if value.strip()})
            data["field_provenance"] = {key: {"kind": "user_report"} for key, value in reflections.items() if value.strip()}
            try:
                patterns.save_pattern_entry(data, "ui-pattern-" + data["entry_key"])
                st.rerun()
            except (patterns.PatternError, ValueError) as exc:
                st.error(str(exc))
    if trade is not None:
        entries = patterns_for_trade(trade)
        pending_count = sum(entry['follow_up_status'] == 'pending' for entry in entries)
        st.caption('Reflections explicitly linked to this trade, its shared position or candidate.')
    else:
        entries = patterns.list_records("pattern_entries", {"account_id": account_id}, limit=100)["records"]
        pending_count = patterns.list_records("pattern_entries", {"account_id": account_id, "follow_up_status": "pending"}, limit=1)['total']
    st.caption(f"Pending reflections: {pending_count}")
    if entries:
        by_id = {entry["id"]: entry for entry in entries}
        selected = st.selectbox("Reflection", list(by_id), format_func=lambda key: f"#{key} · {by_id[key]['event_date']} · {by_id[key]['title'] or labels.get(by_id[key]['pattern_id'], 'Unclassified')}", key=prefix + "_selected")
        entry = patterns.details("pattern_entries", selected)
        _show_reflection(entry)
        with st.form(f"{prefix}_followup_{selected}"):
            note = st.text_area("Follow-up note")
            followup_status = st.selectbox("Follow-up status", ["pending", "reviewed", "not_required"], index=["pending", "reviewed", "not_required"].index(entry["follow_up_status"]), format_func=_state)
            intention = st.text_input("Next-session intention", entry.get("next_session_intention") or '')
            submit_followup = st.form_submit_button("Save follow-up")
        if submit_followup:
            try:
                key = uuid4().hex
                patterns.append_pattern_followup({"pattern_entry_id": selected, "followup_key": key, "note": note,
                                                  "source": "Journal web interface", "recorded_by": "user",
                                                  "parent_update": {"follow_up_status": followup_status, "next_session_intention": intention or None}},
                                                 "ui-followup-" + key, expected_revision=entry["revision"])
                st.rerun()
            except (patterns.PatternError, ValueError) as exc:
                st.error(str(exc))
    if trade is not None:
        return
    with st.expander("Pattern report"):
        start = st.date_input("Report from", today - timedelta(days=29))
        end = st.date_input("Report through", today)
        if st.button("Generate pattern report"):
            try:
                report = patterns.generate_pattern_report([account_id], str(start), str(end))
                st.session_state[prefix + '_report'] = report
            except (patterns.PatternError, ValueError) as exc:
                st.error(str(exc))
        report = st.session_state.get(prefix + '_report')
        if report and report['account_ids'] == [account_id] and report['start_date'] == str(start) and report['end_date'] == str(end):
            _show_report(report)
            st.download_button('Download report', json.dumps(report, indent=2), 'pattern-report.json', 'application/json')
