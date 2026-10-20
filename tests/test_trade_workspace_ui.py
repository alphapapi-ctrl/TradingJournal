"""Trade workspace navigation and editing against disposable journals."""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from streamlit.testing.v1 import AppTest
import database as db
from journal_mcp import neo, patterns, records
from utils.trade_context import entries_for_trade, history_for_trade, patterns_for_trade, linked_entries
from utils.trade_ops import merge_trades_into_position

ROOT = Path(__file__).resolve().parents[1]
NOW = '2026-10-18T10:00:00+10:00'


class TradeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='workspace-ui-', dir=ROOT / 'data')
        self.addCleanup(temp.cleanup)
        self.addCleanup(setattr, db, 'DB_PATH', db.DB_PATH)
        db.DB_PATH = self.path = Path(temp.name) / 'journal.db'
        db.init_db()
        self.account = records.save_record('accounts', {'name': 'Workspace demo', 'currency': 'USD', 'account_type': 'Demo'})['id']
        self.other = records.save_record('accounts', {'name': 'Other account', 'currency': 'USD', 'account_type': 'Demo'})['id']
        self.playbook = records.save_record('playbooks', {'name': 'Stock swing'})['id']
        self.trade = self.new_trade()

    def new_trade(self, **changes):
        return records.save_record('trades', {'account_id': self.account, 'symbol': 'SYNTH', 'direction': 'LONG',
            'entry_time': NOW, 'quantity': 10, 'entry_price': 100, 'playbook_id': self.playbook, **changes})

    def entry(self, **changes):
        return records.save_record('journal_entries', {'entry_type': 'trade', 'entry_date': '2026-10-18',
            'stage': 'pre', 'account_id': self.account, 'trade_id': self.trade['id'], 'playbook_id': self.playbook,
            'pre_plan': 'Original swing plan', 'custom_fields': {'preserve': 'metadata'}, **changes})

    def reflection(self, key, **changes):
        return patterns.save_pattern_entry({'entry_key': key, 'account_id': self.account, 'event_date': '2026-10-18',
            'timezone': 'Australia/Brisbane', 'situation': key, **changes}, key)

    def capture(self, entry, **changes):
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), 'blue').save(buffer, 'PNG')
        return neo.upload_evidence(base64.b64encode(buffer.getvalue()).decode(), {'journal_entry_id': entry['id'],
            'filename': 'swing.png', 'mime_type': 'image/png', 'stage': 'pre', 'captured_at': NOW,
            'symbol': 'SYNTH', 'timeframe': '1d', 'source': 'Synthetic swing chart', **changes})['evidence']

    def app(self, source):
        app = AppTest.from_string(f"import database as db\nfrom pathlib import Path\ndb.DB_PATH=Path({str(self.path)!r})\n" + source, default_timeout=25).run()
        self.assertFalse(app.exception, str(app.exception))
        return app

    def detail(self):
        return self.app(f"""
from unittest.mock import patch
import streamlit as st
from pages import trade_detail
if not st.session_state.get('_test_started'):
    st.session_state['detail_trade_id'] = {self.trade['id']}
    st.session_state['_test_started'] = True
with patch.object(trade_detail, '_price_history_for', return_value=(None,None)):
    trade_detail.show(embedded=True)
""")

    def test_journal_has_only_overview_daily_weekly_and_calendar_opens_detail(self):
        from datetime import date
        today = date.today().isoformat()
        db.execute("UPDATE trades SET entry_time=?,exit_time=?,status='closed',exit_price=101 WHERE id=?", (today + 'T09:00:00', today + 'T10:00:00', self.trade['id']))
        app = self.app("""
from unittest.mock import patch
from pages import journal
with patch.object(journal, '_open_positions_section'):
    journal.show()
""")
        self.assertEqual([tab.label for tab in app.tabs], ['🗓️ Overview', '📅 Daily', '📆 Weekly'])
        app.selectbox(key='cal_ov_day_sel').select(today).run()
        app.button(key=f"goto_review_{self.trade['id']}").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['page'], 'trades')
        self.assertEqual(app.session_state['detail_trade_id'], self.trade['id'])
        self.assertEqual(app.session_state['detail_account_id'], self.account)

    def test_full_trades_page_renders_without_duplicate_widgets(self):
        self.entry()
        app = self.app("""
from unittest.mock import patch
from pages import trades, trade_detail
with patch.object(trade_detail, '_price_history_for', return_value=(None,None)), patch.object(trades, '_position_live_metrics', return_value=None):
    trades.show()
""")
        labels = [tab.label for tab in app.tabs]
        for expected in ['📊 Trade Notes', '📋 Pre-Trade Plan', 'Psychology Patterns', '📷 Evidence & History', '🧠 Psychology']:
            self.assertIn(expected, labels)
        app.selectbox(key='trades_review_account_patterns').select(self.account).run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(sum(area.label == 'Situation' for area in app.text_area), 2)

    def test_stock_playbook_review_reuses_plan_and_keeps_pre_fields_and_metadata(self):
        entry = self.entry()
        self.capture(entry)
        app = self.detail()
        self.assertTrue(any(tab.label == '📷 Evidence & History' for tab in app.tabs))
        key = f"trade_2026-10-18_post_{self.trade['id']}_None"
        app.text_area(key='analysis_' + key).set_value('Swing review')
        app.text_area(key='psych_' + key).set_value('Calm and followed the plan')
        app.button(key='save_' + key).click().run()
        self.assertFalse(app.exception, str(app.exception))
        saved = records.get_record('journal_entries', entry['id'])
        self.assertEqual((saved['analysis'], saved['pre_plan'], saved['playbook_id']), ('Swing review', 'Original swing plan', self.playbook))
        self.assertEqual(json.loads(saved['custom_fields']), {'preserve': 'metadata'})
        self.assertEqual(db.fetch_one("SELECT COUNT(*) n FROM journal_entries WHERE entry_type='trade'")['n'], 1)
        self.assertEqual(len(app.get('imgs')), 1)
        plans = self.app(f'from utils.planning_ui import show_plans\nshow_plans({self.account})')
        self.assertIn(f"open_plan_{entry['id']}", [button.key for button in plans.button])

    def test_direct_preplan_edit_and_new_review_inherit_account(self):
        app = self.detail()
        app.button(key=f"add_pre_btn_{self.trade['id']}").click().run()
        app.text_area(key=f"i_plan_{self.trade['id']}").set_value('New plan')
        app.button(key=f"save_pre_{self.trade['id']}").click().run()
        self.assertFalse(app.exception, str(app.exception))
        entry = db.fetch_one('SELECT * FROM journal_entries WHERE trade_id=?', (self.trade['id'],))
        self.assertEqual((entry['account_id'], entry['pre_plan']), (self.account, 'New plan'))
        # A review on a different trade starts a fresh, correctly owned record.
        second = self.new_trade()
        app = self.detail()
        app.selectbox(key='td_trade_sel').select(second['id']).run()
        key = f"trade_2026-10-18_post_{second['id']}_None"
        app.text_area(key='analysis_' + key).set_value('New review')
        app.button(key='save_' + key).click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(db.fetch_one('SELECT account_id FROM journal_entries WHERE trade_id=?', (second['id'],))['account_id'], self.account)

    def test_native_candidate_fill_review_keeps_single_record_and_plan_versions(self):
        session = neo.save_session({'session_key': 'workspace', 'account_id': self.account, 'exchange_date': '2026-10-17',
            'local_date': '2026-10-18', 'timezone': 'Australia/Brisbane', 'label': 'Test', 'baseline_version': 'test'})
        candidate = neo.save_candidate({'candidate_key': 'native', 'session_id': session['id'], 'symbol': 'SYNTH', 'direction': 'LONG',
            'plan': {'entry_price': 100, 'stop_price': 98, 'baseline_version': 'test', 'playbook_id': self.playbook}, 'reason': 'Initial plan'})
        neo.record_fill({'candidate_id': candidate['id'], 'trade_id': self.trade['id'], 'source': 'Synthetic', 'source_fill_id': 'one',
            'filled_at': NOW, 'kind': 'entry', 'price': 100, 'quantity': 10, 'commission': 0, 'currency': 'USD'})
        # A candidate can be associated through confirmed fills rather than one direct execution.
        db.execute('UPDATE journal_entries SET trade_id=NULL WHERE id=?', (candidate['journal_entry_id'],))
        entry = records.get_record('journal_entries', candidate['journal_entry_id'])
        self.assertIsNone(entry['trade_id'])
        versions = db.fetch_all('SELECT * FROM candidate_plans')
        self.reflection('native reflection', candidate_id=candidate['id'])
        app = self.detail()
        key = f"trade_2026-10-18_post_{self.trade['id']}_None"
        app.text_area(key='analysis_' + key).set_value('Native review')
        app.button(key='save_' + key).click().run()
        self.assertFalse(app.exception, str(app.exception))
        saved = records.get_record('journal_entries', entry['id'])
        self.assertEqual(saved['pre_plan'], entry['pre_plan'])
        self.assertEqual(saved['analysis'], 'Native review')
        self.assertEqual(db.fetch_all('SELECT * FROM candidate_plans'), versions)
        self.assertEqual(len(entries_for_trade(self.trade)), 1)
        queue = self.app(f'from utils.trade_review_ui import show_notes\nshow_notes({self.account})')
        self.assertTrue(any('1/1' in item.value for item in queue.markdown))
        self.assertEqual(len(patterns_for_trade(self.trade)), 1)

    def test_merged_position_review_uses_shared_record_including_legacy_owner(self):
        second = self.new_trade()
        position_id = merge_trades_into_position([self.trade['id'], second['id']], playbook_id=self.playbook)
        entry = self.entry(trade_id=None, position_id=position_id)
        app = self.detail()
        app.selectbox(key=f"review_scope_{self.trade['id']}").select('Merged position').run()
        self.assertFalse(app.exception, str(app.exception))
        key = f'trade_2026-10-18_post_None_{position_id}'
        app.text_area(key='analysis_' + key).set_value('Shared position review')
        app.button(key='save_' + key).click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(records.get_record('journal_entries', entry['id'])['analysis'], 'Shared position review')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM journal_entries')['n'], 1)
        with self.assertRaisesRegex(ValueError, 'different accounts'):
            merge_trades_into_position([self.trade['id'], self.new_trade(account_id=self.other)['id']])

    def test_evidence_patterns_are_linked_and_isolated_even_for_same_symbol(self):
        entry = self.entry()
        capture = self.capture(entry)
        event = neo.append_management_event({'event_key': 'swing', 'journal_entry_id': entry['id'], 'observed_at': NOW,
            'decision': 'Hold swing', 'reason': 'Following plan', 'action_status': 'observed'})
        linked = self.reflection('linked', trade_ids=[self.trade['id']])
        self.reflection('unlinked account reflection')
        unrelated = self.new_trade()
        foreign = self.new_trade(account_id=self.other)
        for trade in [unrelated, foreign]:
            other_entry = self.entry(account_id=trade['account_id'], trade_id=trade['id'])
            self.capture(other_entry)
            self.reflection(f"other-{trade['id']}", account_id=trade['account_id'], trade_ids=[trade['id']])
        history = history_for_trade(self.trade)
        self.assertEqual([row['id'] for row in history['evidence']], [capture['id']])
        self.assertEqual([row['id'] for row in history['management_events']], [event['id']])
        self.assertEqual([row['id'] for row in patterns_for_trade(self.trade)], [linked['id']])
        app = self.detail()
        self.assertEqual(app.selectbox(key=f"trade_pattern_{self.trade['id']}_selected").options[0].split(' · ')[0], f"#{linked['id']}")
        self.assertEqual(len(app.get('imgs')), 1)

    def test_trade_reflection_creation_and_shared_position_event(self):
        second = self.new_trade()
        position_id = merge_trades_into_position([self.trade['id'], second['id']])
        self.entry(trade_id=None, position_id=position_id)
        app = self.detail()
        situation = next(area for area in app.text_area if area.label == 'Situation')
        situation.set_value('Waited for my stock swing trigger')
        next(button for button in app.button if button.label == 'Save reflection').click().run()
        self.assertFalse(app.exception, str(app.exception))
        reflection = db.fetch_one('SELECT * FROM pattern_entries')
        self.assertEqual(json.loads(reflection['trade_ids']), [self.trade['id']])
        next(field for field in app.text_input if field.label == 'Decision').set_value('Hold position')
        next(area for area in app.text_area if area.label == 'Reason and observed changes').set_value('Shared position plan remains valid')
        next(button for button in app.button if button.label == 'Save event').click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM management_events')['n'], 1)
        self.assertEqual(len(history_for_trade(records.get_record('trades', second['id']))['management_events']), 1)

    def test_unassigned_plan_link_opens_correct_account_and_templates_still_editable(self):
        entry = self.entry(trade_id=None)
        app = self.app(f'from utils.planning_ui import show_plans\nshow_plans({self.account})')
        app.selectbox(key=f"link_sel_{entry['id']}").select(self.trade['id']).run()
        app.button(key=f"do_link_{entry['id']}").click().run()
        self.assertFalse(app.exception, str(app.exception))
        app.button(key=f"open_plan_{entry['id']}").click().run()
        self.assertEqual(app.session_state['detail_account_id'], self.account)
        with self.assertRaisesRegex(ValueError, 'different accounts'):
            linked_entries(entry['id'], 'trade_id', self.new_trade(account_id=self.other)['id'])
        templates = self.app('from utils.planning_ui import show_templates\nshow_templates()')
        next(field for field in templates.text_input if field.label == 'Name').set_value('Swing review')
        next(button for button in templates.button if button.label == '💾 Save').click().run()
        self.assertFalse(templates.exception)
        self.assertEqual(db.fetch_one("SELECT name FROM journal_templates WHERE name='Swing review'")['name'], 'Swing review')


if __name__ == '__main__':
    unittest.main()
