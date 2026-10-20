"""Trade workspace navigation and editing against disposable journals."""
import base64
import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from mcp import Client
from streamlit.testing.v1 import AppTest
import database as db
from journal_mcp import neo, patterns, records
from journal_mcp.server import create_server
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
        self.capture(self.entry())
        app = self.app("""
from unittest.mock import patch
from pages import trades, trade_detail
with patch.object(trade_detail, '_price_history_for', return_value=(None,None)), patch.object(trades, '_position_live_metrics', return_value=None):
    trades.show()
""")
        labels = [tab.label for tab in app.tabs]
        self.assertNotIn('📊 Trade Notes', labels)
        for expected in ['Prepare & Record', '🧩 Positions', '🔍 Trade Detail', 'Psychology Patterns']:
            self.assertIn(expected, labels)
        app.selectbox(key='trades_review_account_patterns').select(self.account).run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(sum(area.label == 'Situation' for area in app.text_area), 2)

    def test_detail_process_is_one_page_with_readable_reflections_and_history(self):
        entry = self.entry(pre_plan=json.dumps({'entry_price': 100, 'stop_price': 98, 'checklist': {'location': 'Confirmed'}}))
        for stage in ('pre', 'management', 'post'):
            self.capture(entry, stage=stage)
        neo.append_management_event({'event_key': 'readable', 'journal_entry_id': entry['id'], 'observed_at': NOW,
            'decision': 'Keep the original stop', 'reason': 'Structure is intact', 'action_status': 'observed',
            'before_stop': 98, 'after_stop': 98, 'rule': 'Respect invalidation'})
        reflection = self.reflection('readable reflection', trade_ids=[self.trade['id']],
            emotion_body_state='Shoulders relaxed', better_response='Wait for the next trigger', urge_response='resisted',
            plan_adherence='followed', rule_breach='no', field_provenance={'emotion_body_state': {'kind': 'user_report'}})
        patterns.append_pattern_followup({'pattern_entry_id': reflection['id'], 'followup_key': 'practised',
            'note': 'Practised the pause', 'response_practised': True, 'response_helpful': True,
            'observed_at': NOW}, 'readable-followup')
        app = self.detail()
        self.assertFalse(app.tabs)
        self.assertFalse(app.get('json'))
        headings = [item.value for item in app.subheader]
        expected = ['Trade overview', 'Setup & playbook', 'Pre-trade plan', 'Execution & management', 'Post-trade review', 'Psychology patterns']
        self.assertEqual([title for title in headings if title in expected], expected)
        prose = '\n'.join(item.value for item in app.markdown)
        for value in ['Entry price: 100', 'Location: Confirmed', 'Keep the original stop', 'Structure is intact',
            'Respect invalidation', 'Shoulders relaxed', 'Wait for the next trigger', 'Practised the pause', 'Resisted the urge']:
            self.assertIn(value, prose)
        self.assertNotIn('field_provenance', prose)
        self.assertEqual(len(app.get('imgs')), 3)

    def test_pattern_report_is_readable_and_keeps_unknowns_out_of_rates(self):
        definition = patterns.save_psychology_pattern({'pattern_key': 'pause', 'label': 'Pause after a missed move',
            'practice_response': 'Take one breath'}, 'pause-definition')
        for key, urge in [('one','resisted'), ('two','acted_on'), ('three','unknown')]:
            self.reflection(key, pattern_id=definition['id'], urge_response=urge)
        app = self.app(f'from utils.pattern_ui import show\nshow({self.account})')
        next(button for button in app.button if button.label == 'Generate pattern report').click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertFalse(app.get('json'))
        prose = '\n'.join(item.value for item in app.markdown)
        self.assertIn('Urges resisted: 1 of 2 classified occurrences (50%)', prose)
        self.assertEqual(next(item.value for item in app.metric if item.label == 'Logged occurrences'), '3')
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM pattern_entries')['n'], 3)

    def test_preparation_copies_sizing_into_one_plan_without_recording_a_fill(self):
        app = self.app('from pages.trades import _prepare_and_record\n_prepare_and_record()')
        app.selectbox(key='rc_asset_type').select('CFD').run()
        app.number_input(key='rc_balance').set_value(10000)
        app.number_input(key='rc_entry').set_value(3000)
        app.number_input(key='rc_stop').set_value(2997)
        app.run()
        app.button(key='rc_create_plan').click().run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertIn('0.33 lots', app.text_area(key='pt_plan').value)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM journal_entries')['n'], 0)
        app.text_area(key='pt_analysis').set_value('Wait for a confirmed setup')
        next(button for button in app.button if button.label == '💾 Save Pre-Trade Plan').click().run()
        self.assertFalse(app.exception, str(app.exception))
        entry = db.fetch_one('SELECT * FROM journal_entries')
        self.assertEqual(entry['symbol'], 'XAUUSD.A')
        self.assertEqual(json.loads(entry['custom_fields'])['position_sizing']['quantity'], 0.33)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM journal_entries')['n'], 1)
        self.assertEqual(db.fetch_one('SELECT COUNT(*) n FROM trades')['n'], 1)
        app.button(key='rc_create_plan').click().run()
        app.text_input(key='pt_symbol').set_value('DIFFERENT')
        next(button for button in app.button if button.label == '💾 Save Pre-Trade Plan').click().run()
        self.assertFalse(app.exception, str(app.exception))
        changed = db.fetch_one('SELECT * FROM journal_entries ORDER BY id DESC LIMIT 1')
        self.assertEqual(changed['symbol'], 'DIFFERENT')
        self.assertIsNone(changed['custom_fields'], 'A changed symbol must not retain another contract’s sizing assumptions')

    def test_stock_playbook_review_reuses_plan_and_keeps_pre_fields_and_metadata(self):
        entry = self.entry()
        self.capture(entry)
        app = self.detail()
        self.assertFalse(app.tabs)
        self.assertFalse(app.get('json'))
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
        self.assertTrue(any(item.value == 'No pre-trade screenshots attached yet.' for item in app.caption))
        self.assertTrue(any(item.value == 'No post-trade screenshots attached yet.' for item in app.caption))
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

    def test_mcp_trade_detail_fields_and_menaker_round_trip_with_ui(self):
        entry = self.entry()
        review = {'stage': 'post', 'pre_analysis': 'Setup analysis', 'pre_plan': 'Original swing plan',
            'pre_playbook_notes': 'Rules checked', 'pre_psychology': 'Ready', 'pre_risk_notes': 'Defined risk',
            'pre_body_state': 'Relaxed', 'pre_accuracy_check': 'Accurate', 'pre_risk_type': 'Smart risk',
            'analysis': 'Review analysis', 'execution': 'Planned execution', 'psychology': 'Calm',
            'lessons': 'Repeat the process', 'grade': 'A', 'mood': 8, 'trade_category': 2}

        async def check_mcp():
            async with Client(create_server()) as client:
                schema = (await client.call_tool('journal_schema', {'entity': 'journal_entries'})).structured_content
                fields = schema['entities']['journal_entries']['fields']
                self.assertTrue(all(fields[key]['writable'] for key in review))
                self.assertEqual(fields['trade_category']['enum'], [1, 2, 3, 4])
                self.assertIn('Menaker', fields['trade_category']['description'])
                saved = await client.call_tool('save_record', {'entity': 'journal_entries', 'record_id': entry['id'],
                    'expected_revision': entry['revision'], 'data': review})
                self.assertFalse(saved.is_error)
                read = (await client.call_tool('get_record', {'entity': 'journal_entries', 'record_id': entry['id']})).structured_content
                for key, value in review.items():
                    self.assertEqual(read[key], value)
                invalid = await client.call_tool('save_record', {'entity': 'journal_entries', 'record_id': entry['id'], 'data': {'trade_category': 5}})
                self.assertTrue(invalid.is_error)
        asyncio.run(asyncio.wait_for(check_mcp(), timeout=20))
        app = self.detail()
        suffix = f"trade_2026-10-18_post_{self.trade['id']}_None"
        self.assertEqual(app.selectbox(key='grade_' + suffix).value, 'A')
        self.assertTrue(app.radio(key='cat_' + suffix).value.startswith('Type 2'))
        self.assertEqual(app.text_area(key='analysis_' + suffix).value, review['analysis'])
        app.selectbox(key='grade_' + suffix).select('B+')
        app.radio(key='cat_' + suffix).set_value('Type 1 — On-plan ✅ + Profit')
        app.button(key='save_' + suffix).click().run()
        self.assertFalse(app.exception, str(app.exception))

        async def read_ui_update():
            async with Client(create_server()) as client:
                result = await client.call_tool('get_record', {'entity': 'journal_entries', 'record_id': entry['id']})
                self.assertEqual(result.structured_content['grade'], 'B+')
                self.assertEqual(result.structured_content['trade_category'], 1)
                self.assertEqual(result.structured_content['pre_plan'], review['pre_plan'])
        asyncio.run(asyncio.wait_for(read_ui_update(), timeout=20))

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
        self.assertEqual(len(entries_for_trade(self.trade)), 1)
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

    def test_overview_screenshots_compare_pre_post_and_select_multiple_captures(self):
        entry = self.entry()
        first = self.capture(entry, filename='before-first.png')
        latest = self.capture(entry, filename='before-latest.png', captured_at='2026-10-18T10:01:00+10:00')
        post = self.capture(entry, filename='after.png', stage='post')
        post_latest = self.capture(entry, filename='after-latest.png', stage='post', captured_at='2026-10-18T11:01:00+10:00')
        management = self.capture(entry, filename='management.png', stage='management')
        unrelated = self.capture(self.entry(trade_id=self.new_trade()['id']), filename='unrelated.png')
        app = self.detail()
        overview = app
        self.assertEqual(len(overview.get('imgs')), 3)
        self.assertTrue(any(item.value == '**Pre-trade (2)**' for item in overview.markdown))
        self.assertTrue(any(item.value == '**Post-trade (2)**' for item in overview.markdown))
        selector = app.selectbox(key=f"trade_screenshot_{self.trade['id']}_pre")
        self.assertEqual(selector.value, latest['id'])
        self.assertEqual(len(selector.options), 2)
        self.assertEqual(len(overview.get('download_button')), 3)
        captions = '\n'.join(item.value for item in overview.caption)
        self.assertIn('before-latest.png', captions)
        self.assertIn('after-latest.png', captions)
        self.assertEqual(captions.count(management['filename']), 1)
        self.assertNotIn(unrelated['filename'], captions)
        selector.select(first['id']).run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any('before-first.png' in item.value for item in app.caption))
        self.assertEqual(app.selectbox(key=f"trade_screenshot_{self.trade['id']}_post").value, post_latest['id'])
        app.selectbox(key=f"trade_screenshot_{self.trade['id']}_post").select(post['id']).run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any('after.png' in item.value for item in app.caption))
        self.assertEqual(app.selectbox(key=f"trade_screenshot_{self.trade['id']}_pre").value, first['id'])

    def test_screenshots_render_when_streamlit_rejects_removed_image_option(self):
        entry = self.entry()
        self.capture(entry)
        self.capture(entry, stage='post')
        app = self.app("""
from unittest.mock import patch
import streamlit as st
from pages import trade_detail
original_image = st.image
# Reproduce the remote image API: use_column_width is no longer accepted.
def current_image(image, caption=None, *, use_container_width=None):
    return original_image(image, caption=caption, use_container_width=use_container_width)
with patch.object(st, 'image', current_image), patch.object(trade_detail, '_price_history_for', return_value=(None,None)):
    trade_detail.show(embedded=True)
""")
        self.assertEqual(len(app.get('imgs')), 2)

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
