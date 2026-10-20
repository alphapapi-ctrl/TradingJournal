"""Resolve explicit trade links for shared reviews, evidence and psychology."""
import json
import database as db


def _candidate_ids(trade, entries=()):
    ids = {row['candidate_id'] for row in db.fetch_all(
        'SELECT DISTINCT candidate_id FROM fill_events WHERE trade_id=? AND account_id=?',
        (trade['id'], trade['account_id']))}
    ids.update(row['candidate_id'] for row in entries if row.get('candidate_id'))
    return ids


def entries_for_trade(trade, include_position=True):
    """No symbol/date guesses: direct links, shared position, or confirmed fills."""
    candidate_ids = _candidate_ids(trade)
    direct = db.fetch_all("SELECT candidate_id FROM journal_entries WHERE trade_id=? AND candidate_id IS NOT NULL", (trade['id'],))
    candidate_ids.update(row['candidate_id'] for row in direct)
    clauses, params = ['j.trade_id=?'], [trade['id']]
    if include_position and trade.get('position_id'):
        clauses.append('j.position_id=?')
        params.append(trade['position_id'])
    if candidate_ids:
        ids = sorted(candidate_ids)
        marks = ','.join('?' for _ in ids)
        clauses += [f'j.candidate_id IN ({marks})', f'j.id IN (SELECT journal_entry_id FROM candidates WHERE id IN ({marks}) AND account_id=?)']
        params += ids + ids + [trade['account_id']]
    return db.fetch_all(f"""SELECT j.* FROM journal_entries j WHERE ({' OR '.join(clauses)})
        AND (j.account_id IS NULL OR j.account_id=?)
        AND NOT EXISTS (SELECT 1 FROM pattern_entries p WHERE p.legacy_journal_entry_id=j.id)
        ORDER BY j.id DESC""", (*params, trade['account_id']))


def patterns_for_trade(trade, entries=None):
    entries = entries if entries is not None else entries_for_trade(trade)
    entry_ids = {entry['id'] for entry in entries}
    candidate_ids = _candidate_ids(trade, entries)
    rows = db.fetch_all("SELECT * FROM pattern_entries WHERE account_id=? AND status!='archived' ORDER BY event_date DESC,id DESC", (trade['account_id'],))
    return [row for row in rows if trade['id'] in json.loads(row.get('trade_ids') or '[]')
        or (trade.get('position_id') and row.get('position_id') == trade['position_id'])
        or (row.get('candidate_id') and row['candidate_id'] in candidate_ids)
        or row.get('context_journal_entry_id') in entry_ids]


def history_for_trade(trade, entries=None):
    entries = entries if entries is not None else entries_for_trade(trade)
    entry_ids = [row['id'] for row in entries]
    candidates = _candidate_ids(trade, entries)
    pattern_ids = [row['id'] for row in patterns_for_trade(trade, entries)]
    result = {}
    for table in ('evidence', 'management_events'):
        clauses, params = ['trade_id=?'], [trade['id']]
        for field, values in [('journal_entry_id', entry_ids), ('candidate_id', sorted(candidates))] + ([('pattern_entry_id', pattern_ids)] if table == 'evidence' else []):
            if values:
                clauses.append(f"{field} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        order = 'observed_at,id' if table == 'management_events' else 'captured_at,id'
        result[table] = db.fetch_all(f"SELECT * FROM {table} WHERE {' OR '.join(clauses)} ORDER BY {order}", params)
    session_ids = {row['session_id'] for row in entries if row.get('session_id')}
    for candidate_id in candidates:
        candidate = db.fetch_one('SELECT session_id FROM candidates WHERE id=? AND account_id=?', (candidate_id, trade['account_id']))
        if candidate:
            session_ids.add(candidate['session_id'])
    result['sessions'] = [row for session_id in sorted(session_ids) if (row := db.fetch_one('SELECT * FROM trading_sessions WHERE id=? AND account_id=?', (session_id, trade['account_id'])))]
    return result


def open_trade_detail(trade_id, account_id=None, section='📔 Plan & Review', position_scope=False):
    import streamlit as st
    st.session_state.update(page='trades', detail_trade_id=trade_id,
        _trades_pending_tab='detail', _detail_section=section,
        _detail_scope='Merged position' if position_scope else 'This trade')
    if account_id is not None:
        st.session_state['detail_account_id'] = account_id


def linked_entries(entry_id, field, record_id):
    """Validate manual plan linkage through the same revision-protected API as MCP."""
    from journal_mcp import records
    table = {'trade_id': 'trades', 'position_id': 'positions'}[field]
    target = records.get_record(table, record_id)
    if field == 'position_id' and target.get('account_id') is None:
        owners = db.fetch_all('SELECT DISTINCT account_id FROM trades WHERE position_id=?', (record_id,))
        if len(owners) != 1 or owners[0]['account_id'] is None:
            raise ValueError('Position must belong to one account before linking a plan')
        target['account_id'] = owners[0]['account_id']
    entry = records.get_record('journal_entries', entry_id)
    if entry.get('account_id') not in (None, target['account_id']):
        raise ValueError('Plan and execution belong to different accounts')
    return records.save_record('journal_entries', {field: record_id, 'account_id': target['account_id']}, entry_id, expected_revision=entry['revision'])
