"""Evidence and immutable workflow history in the journal's web interface."""
import base64
from datetime import datetime
from uuid import uuid4

import streamlit as st
import database as db
from journal_mcp import neo


def _show_stage(captures, stage, title, prefix):
    from utils.journal_display import date_time
    st.markdown(f'**{title} ({len(captures)})**')
    if not captures:
        st.caption(f'No {title.lower()} screenshots attached yet.')
        return
    capture = captures[-1]
    if len(captures) > 1:
        by_id = {capture['id']: capture for capture in captures}
        selected = st.selectbox(f'{title} screenshot', list(by_id), index=len(captures) - 1,
            format_func=lambda key: f"{date_time(by_id[key]['captured_at'])} · {by_id[key]['timeframe']} · {by_id[key]['filename']}",
            key=f"{prefix}_{stage}")
        capture = by_id[selected]
    metadata, content = neo.evidence_bytes(capture['id'])
    st.image(content, use_container_width=True, caption=f"{metadata['symbol']} · {metadata['timeframe']} · {date_time(metadata['captured_at'])}")
    st.caption(f"{metadata['filename']} · {metadata['source']}")
    if metadata.get('chart_context'):
        st.caption(metadata['chart_context'])
    st.download_button('Download screenshot', content, metadata['filename'], metadata['mime_type'],
        key=f"{prefix}_download_{metadata['id']}")


def show_trade_screenshots(trade, evidence=None):
    """Each screenshot appears once, grouped by its place in the trade process."""
    from utils.trade_context import history_for_trade
    evidence = history_for_trade(trade)['evidence'] if evidence is None else evidence
    st.markdown('#### Pre / post-trade screenshots')
    prefix = f"trade_screenshot_{trade['id']}"
    for column, stage, title in zip(st.columns(2), ('pre', 'post'), ('Pre-trade', 'Post-trade')):
        with column:
            _show_stage([row for row in evidence if row['stage']==stage], stage, title, prefix)
    management = [row for row in evidence if row['stage']=='management']
    if management:
        with st.expander('During-trade screenshots'):
            _show_stage(management, 'management', 'During-trade', prefix)


def show(account_id=None, trade=None):
    from utils.trade_context import entries_for_trade, history_for_trade
    history = None
    if trade is not None:
        account_id = trade['account_id']
        entries = entries_for_trade(trade)
        history = history_for_trade(trade, entries)
        sessions = history['sessions']
    else:
        entries = db.fetch_all("SELECT * FROM journal_entries" + (" WHERE account_id=?" if account_id else "") + " ORDER BY id DESC LIMIT 200", (account_id,) if account_id else ())
        sessions = db.fetch_all("SELECT * FROM trading_sessions" + (" WHERE account_id=?" if account_id else "") + " ORDER BY id DESC LIMIT 50", (account_id,) if account_id else ())
    if sessions:
        with st.expander("Session handovers"):
            for session in sessions:
                st.markdown(f"**{session['label']} · exchange {session['exchange_date']} · local {session['local_date']} · {session['timezone']}**")
                if session['review_block']:
                    st.warning(f"Stop and review: {session['review_reason']}")
                if session.get('restart_decision'):
                    st.caption(f"Restart decision: {session['restart_decision']}")
                from utils.journal_display import show_note
                show_note(session['handover'])
    if not entries:
        st.info("Save a plan or review for this trade before attaching evidence.")
        if trade is not None and st.button('Start screenshot attachments', key=f"evidence_create_{trade['id']}"):
            from journal_mcp import records
            records.save_record('journal_entries', {'entry_type': 'trade', 'entry_date': trade['entry_time'][:10], 'stage': 'pre', 'trade_id': trade['id'], 'account_id': account_id, 'symbol': trade['symbol']})
            st.rerun()
        # Explicit trade-linked captures/events may predate a narrative record.
        if history:
            show_trade_screenshots(trade, history['evidence'])
            _show_events(history['management_events'])
        return
    by_id = {entry['id']: entry for entry in entries}
    entry_id = list(by_id)[0]
    if len(by_id) > 1:
        entry_id = st.selectbox('Attach new items to', list(by_id), format_func=lambda key: f"{'Position' if by_id[key].get('position_id') else 'Trade'} {'plan' if by_id[key]['stage']=='pre' else 'review'} · {by_id[key]['entry_date']}", key=f"trade_evidence_entry_{trade['id']}" if trade is not None else 'neo_entry')
    entry = by_id[entry_id]
    candidate_id = entry.get("candidate_id")
    capture_trade_id = None
    if trade is not None and (entry.get('trade_id') == trade['id'] or (candidate_id and db.fetch_one(
            'SELECT id FROM fill_events WHERE candidate_id=? AND trade_id=?', (candidate_id, trade['id'])))):
        capture_trade_id = trade['id']
    if candidate_id:
        candidate = db.fetch_one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
        st.caption(f"Planned setup: {candidate['symbol']} {candidate['direction'].lower()} · {candidate['status'].capitalize()}")
        with st.expander("Original plan and revisions"):
            for revision in db.fetch_all("SELECT * FROM candidate_plans WHERE candidate_id=? ORDER BY version", (candidate_id,)):
                st.markdown(f"**Version {revision['version']} · {revision['reason']}**")
                st.caption(f"Baseline {revision['baseline_version']} · playbook {revision.get('playbook_version') or 'unassigned'} · template {revision.get('template_version') or 'unassigned'}")
                from utils.journal_display import show_note
                show_note(revision['plan'])
    evidence = history["evidence"] if history else db.fetch_all("SELECT * FROM evidence WHERE journal_entry_id=? ORDER BY id", (entry_id,))
    if trade is not None:
        show_trade_screenshots(trade, evidence)
    else:
        _show_captures(evidence)
    with st.expander("Attach a screenshot"):
        with st.form(f"neo_upload_{entry_id}"):
            upload = st.file_uploader("PNG, JPEG or WebP (up to 8 MiB)", type=["png", "jpg", "jpeg", "webp"])
            stage = st.selectbox('Screenshot stage', ['pre', 'management', 'post'], format_func=lambda key: {'pre':'Pre-trade', 'management':'During-trade', 'post':'Post-trade'}[key])
            symbol = st.text_input("Exact contract / chart symbol", value=entry.get("symbol") or (trade['symbol'] if trade is not None else ''))
            timeframe = st.text_input("Chart timeframe")
            captured_at = st.text_input("Capture time with UTC offset", value=datetime.now().astimezone().isoformat(timespec="seconds"))
            source = st.text_input("Source / feed")
            context = st.text_input("Chart / indicator context")
            save = st.form_submit_button("Save screenshot")
        if save:
            if upload is None:
                st.error("Choose an image first.")
            else:
                try:
                    _ensure_owner(entry, trade)
                    result = neo.upload_evidence(base64.b64encode(upload.getvalue()).decode(), {"journal_entry_id": entry_id, "candidate_id": candidate_id, "trade_id": capture_trade_id, "filename": upload.name, "mime_type": upload.type, "stage": stage, "captured_at": captured_at, "symbol": symbol, "timeframe": timeframe, "source": source, "chart_context": context})
                    st.success(f"Saved screenshot #{result['evidence']['id']}")
                    st.rerun()
                except (ValueError, TypeError) as exc:
                    st.error(str(exc))
    events = history["management_events"] if history else db.fetch_all("SELECT * FROM management_events WHERE journal_entry_id=? ORDER BY observed_at,id", (entry_id,))
    _show_events(events)
    with st.expander("Record a management decision"):
        with st.form(f"neo_event_{entry_id}"):
            observed_at = st.text_input("Decision time with UTC offset", value=datetime.now().astimezone().isoformat(timespec="seconds"))
            decision = st.text_input("Decision")
            reason = st.text_area("Reason and observed changes")
            rule = st.text_input("Applicable rule")
            action_status = st.selectbox('Action status', ['observed', 'proposed', 'executed', 'cancelled'], format_func=str.capitalize)
            include_levels = st.checkbox("Record stop / target levels")
            before_stop = st.number_input("Previous stop", min_value=0.0)
            after_stop = st.number_input("New stop", min_value=0.0)
            before_target = st.number_input("Previous target", min_value=0.0)
            after_target = st.number_input("New target", min_value=0.0)
            evidence_ids = st.multiselect("Supporting screenshots for this journal entry", [capture["id"] for capture in evidence if capture['journal_entry_id'] == entry_id])
            save_event = st.form_submit_button("Save event")
        if save_event:
            data = {"event_key": uuid4().hex, "journal_entry_id": entry_id, "candidate_id": candidate_id, "trade_id": capture_trade_id, "observed_at": observed_at, "decision": decision, "reason": reason, "rule": rule, "action_status": action_status, "evidence_ids": evidence_ids}
            if include_levels:
                data.update(before_stop=before_stop, after_stop=after_stop, before_target=before_target, after_target=after_target)
            try:
                _ensure_owner(entry, trade)
                neo.append_management_event(data)
                st.rerun()
            except (ValueError, TypeError) as exc:
                st.error(str(exc))


def _show_captures(evidence):
    for stage, title in [('pre', 'Pre-trade'), ('management', 'During-trade'), ('post', 'Post-trade')]:
        captures = [row for row in evidence if row['stage']==stage]
        if captures:
            _show_stage(captures, stage, title, 'journal_capture')


def _show_events(events):
    from utils.journal_display import date_time
    st.markdown('#### Management decisions')
    if not events:
        st.caption('No management decisions recorded yet.')
    for event in events:
        with st.container(border=True):
            st.markdown(f"**{event['decision']}**")
            st.caption(f"{date_time(event['observed_at'])} · {event['action_status'].capitalize()}")
            st.write(event['reason'])
            if event.get('rule'):
                st.write(f"Applicable rule: {event['rule']}")
            for key, title in [('stop', 'Stop'), ('target', 'Target')]:
                before, after = event.get('before_'+key), event.get('after_'+key)
                if before is not None or after is not None:
                    st.write(f"{title}: {before if before is not None else 'Not recorded'} → {after if after is not None else 'Not recorded'}")


def _ensure_owner(entry, trade):
    if trade is not None and entry.get('account_id') is None:
        from journal_mcp import records
        records.save_record('journal_entries', {'account_id': trade['account_id']}, entry['id'], expected_revision=entry['revision'])
