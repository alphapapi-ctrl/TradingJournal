"""Native pattern reflections and pending practice work in the journal."""
import json
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st
from journal_mcp import patterns


def show(account_id=None):
    st.subheader("Psychology patterns")
    if account_id is None:
        st.info("Choose an account above to view or record pattern reflections.")
        return
    definitions = patterns.list_records("psychology_patterns", {"include_archived": True}, limit=500)["records"]
    labels = {item["id"]: item["label"] for item in definitions}
    today = datetime.now(ZoneInfo("Australia/Brisbane")).date()
    with st.expander("Record a pattern occurrence"):
        with st.form("pattern_create"):
            event_date = st.date_input("Event date", today)
            timezone = st.text_input("Event timezone", "Australia/Brisbane")
            definition_id = st.selectbox("Pattern", [None] + list(labels), format_func=lambda key: labels.get(key, "Unclassified"))
            situation = st.text_area("Situation")
            trigger = st.text_area("Trigger")
            emotion = st.text_area("Emotion / body state (your words)")
            thought = st.text_area("Automatic thought")
            action = st.text_area("Action taken")
            better = st.text_area("Better response to practise")
            urge = st.selectbox("Urge response", ["unknown", "resisted", "acted_on", "no_urge"])
            adherence = st.selectbox("Plan adherence", ["unknown", "followed", "deviated", "not_applicable"])
            breach = st.selectbox("Actual rule breach", ["unknown", "no", "yes"])
            submit = st.form_submit_button("Save reflection")
        if submit:
            data = {"entry_key": uuid4().hex, "account_id": account_id, "event_date": str(event_date), "timezone": timezone,
                    "pattern_id": definition_id, "urge_response": urge, "plan_adherence": adherence, "rule_breach": breach,
                    "source": "Journal web interface", "recorded_by": "user"}
            reflections = {"situation": situation, "trigger": trigger, "emotion_body_state": emotion, "automatic_thought": thought, "action_taken": action, "better_response": better}
            data.update({key: value for key, value in reflections.items() if value.strip()})
            data["field_provenance"] = {key: {"kind": "user_report"} for key, value in reflections.items() if value.strip()}
            try:
                patterns.save_pattern_entry(data, "ui-pattern-" + data["entry_key"])
                st.rerun()
            except (patterns.PatternError, ValueError) as exc:
                st.error(str(exc))
    pending = patterns.list_records("pattern_entries", {"account_id": account_id, "follow_up_status": "pending"}, limit=100)
    st.caption(f"Pending reflections: {pending['total']}")
    entries = patterns.list_records("pattern_entries", {"account_id": account_id}, limit=100)["records"]
    if entries:
        by_id = {entry["id"]: entry for entry in entries}
        selected = st.selectbox("Reflection", list(by_id), format_func=lambda key: f"#{key} · {by_id[key]['event_date']} · {by_id[key]['title'] or labels.get(by_id[key]['pattern_id'], 'Unclassified')}")
        entry = patterns.details("pattern_entries", selected)
        st.json(entry)
        with st.form(f"pattern_followup_{selected}"):
            note = st.text_area("Follow-up note")
            followup_status = st.selectbox("Follow-up status", ["pending", "reviewed", "not_required"], index=["pending", "reviewed", "not_required"].index(entry["follow_up_status"]))
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
    with st.expander("Pattern report"):
        start = st.date_input("Report from", today - timedelta(days=29))
        end = st.date_input("Report through", today)
        if st.button("Generate pattern report"):
            try:
                report = patterns.generate_pattern_report([account_id], str(start), str(end))
                st.json(report)
                st.download_button("Download report", json.dumps(report, indent=2), "pattern-report.json", "application/json")
            except (patterns.PatternError, ValueError) as exc:
                st.error(str(exc))
