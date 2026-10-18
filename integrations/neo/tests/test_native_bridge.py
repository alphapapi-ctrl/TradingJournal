"""Disposable HTTP acceptance test; set TRADING_JOURNAL_ROOT and use its venv.

NEO_BRIDGE_PATH can select a staged bridge; by default use the Neo source skill.
Without TRADING_JOURNAL_ROOT this integration test is skipped during unit checks.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(os.environ.get('TRADING_JOURNAL_ROOT', '.')).resolve()
BRIDGE_PATH = Path(os.environ.get('NEO_BRIDGE_PATH', Path(__file__).resolve().parents[1] / 'plugins/neo-trading/skills/neo-trading-workflow/scripts/journal_mcp.py'))
spec = importlib.util.spec_from_file_location('native_bridge', BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
NOW = '2026-10-18T12:00:00+10:00'


@unittest.skipUnless(os.environ.get('TRADING_JOURNAL_ROOT'), 'Set TRADING_JOURNAL_ROOT to test with its disposable MCP server')
class NativeBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='neo-bridge-test-', dir=ROOT / 'data')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1', 0))
            port = port_socket.getsockname()[1]
        cls.url = f'http://127.0.0.1:{port}/mcp'
        log = (cls.directory / 'server.log').open('w', encoding='utf-8')
        cls.addClassCleanup(log.close)
        cls.server = subprocess.Popen([sys.executable, str(ROOT / 'mcp_server.py'), '--host', '127.0.0.1', '--port', str(port), '--database', str(cls.directory / 'disposable.db')], cwd=ROOT, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def stop():
            cls.server.terminate()
            cls.server.wait(timeout=15)
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 30
        while True:
            if cls.server.poll() is not None:
                raise RuntimeError((cls.directory / 'server.log').read_text())
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.3):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError('Disposable MCP did not start')
                time.sleep(.1)
        cls.client = bridge.JournalMCP(cls.url)

    def test_complete_native_pattern_workflow(self):
        from PIL import Image
        client = self.client
        tools = client.list_tools()
        self.assertEqual(len(tools), 38)
        for name in ('save_psychology_pattern', 'save_pattern_entry', 'append_pattern_followup', 'generate_pattern_report', 'migrate_pattern_entries'):
            self.assertIn(name, bridge.ALLOWED_TOOLS)
            self.assertIn(name, tools)
        self.assertIn('$defs', tools['save_pattern_entry']['inputSchema'])
        self.assertEqual(client.call('journal_schema', {})['schema_version'], '1.2.0')
        account = client.call('save_record', {'entity': 'accounts', 'data': {'name': 'Disposable bridge demo', 'account_type': 'Demo', 'currency': 'USD'}, 'idempotency_key': 'test-account'})['id']
        definition = client.call('save_psychology_pattern', {'data': {'pattern_key': 'neo-test-pattern', 'system_key': 'neo', 'label': 'Synthetic test trigger', 'practice_response': 'Synthetic test response'}, 'idempotency_key': 'test-definition'})
        args = {'data': {'entry_key': 'neo-test-event', 'account_id': account, 'pattern_id': definition['id'], 'event_date': '2026-10-18', 'timezone': 'Australia/Brisbane', 'situation': 'Synthetic acceptance test only', 'action_taken': 'Waited', 'urge_response': 'resisted', 'plan_adherence': 'followed', 'rule_breach': 'no', 'follow_up_status': 'pending', 'field_provenance': {'situation': {'kind': 'user_report', 'source': 'Synthetic fixture'}}}, 'idempotency_key': 'test-entry'}
        entry = client.call('save_pattern_entry', args)
        self.assertEqual(client.call('save_pattern_entry', args)['id'], entry['id'])
        self.assertIsNone(entry['occurred_at'])
        self.assertEqual(client.call('list_records', {'entity': 'pattern_entries', 'filters': {'account_id': account, 'follow_up_status': 'pending'}})['total'], 1)
        patched = client.call('save_pattern_entry', {'pattern_entry_id': entry['id'], 'expected_revision': entry['revision'], 'data': {'better_response': 'Keep following the planned response'}, 'idempotency_key': 'test-entry-edit'})
        with self.assertRaises(bridge.JournalError) as conflict:
            client.call('save_pattern_entry', {'pattern_entry_id': entry['id'], 'expected_revision': entry['revision'], 'data': {'situation': 'Stale edit'}, 'idempotency_key': 'test-stale'})
        self.assertEqual(conflict.exception.payload['error']['code'], 'revision_conflict')
        self.assertEqual(conflict.exception.payload['error']['current_revision'], patched['revision'])
        with self.assertRaises(bridge.JournalError) as conflict:
            client.call('save_pattern_entry', {**args, 'data': {**args['data'], 'situation': 'Changed same-key request'}})
        self.assertEqual(conflict.exception.payload['error']['code'], 'idempotency_conflict')
        with self.assertRaises(bridge.JournalError):
            client.call('save_pattern_entry', {'data': {'account_id': 'wrong type', 'invented_field': True}, 'idempotency_key': 'test-invalid-nested'})
        self.assertEqual(client.call('list_records', {'entity': 'pattern_entries'})['total'], 1)

        # Exercise the real CLI image path, including direct native owner and revision advance.
        image_path = self.directory / 'synthetic.png'
        Image.new('RGB', (8, 8), 'white').save(image_path)
        metadata = {'data': {'pattern_entry_id': entry['id'], 'filename': image_path.name, 'mime_type': 'image/png', 'stage': 'management', 'captured_at': NOW, 'symbol': 'SYNTH', 'timeframe': '1m', 'source': 'Synthetic fixture'}, 'idempotency_key': 'test-image'}
        args_path = self.directory / 'image-args.json'
        output = self.directory / 'image-result.json'
        args_path.write_text(json.dumps(metadata), encoding='utf-8')
        result = subprocess.run([sys.executable, str(BRIDGE_PATH), '--url', self.url, 'call', 'upload_evidence', '--args-file', str(args_path), '--image', str(image_path), '--out', str(output)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(output.read_text())['evidence']
        self.assertNotIn('content_base64', result.stdout)
        downloaded = client.call('read_evidence', {'evidence_id': evidence['id']})
        self.assertEqual(base64.b64decode(downloaded['content']), image_path.read_bytes())
        current = client.call('get_record', {'entity': 'pattern_entries', 'record_id': entry['id']})
        self.assertGreater(current['revision'], patched['revision'])
        self.assertIn(evidence['id'], current['evidence_ids'])
        follow_args = {'data': {'pattern_entry_id': entry['id'], 'followup_key': 'test-practice', 'note': 'Synthetic practice completed', 'response_practised': True, 'response_helpful': True, 'parent_update': {'follow_up_status': 'reviewed', 'next_session_intention': 'Synthetic intention'}}, 'expected_revision': current['revision'], 'idempotency_key': 'test-followup'}
        followup = client.call('append_pattern_followup', follow_args)
        self.assertEqual(client.call('append_pattern_followup', follow_args)['id'], followup['id'])
        self.assertEqual(client.call('list_records', {'entity': 'pattern_entries', 'filters': {'account_id': account, 'follow_up_status': 'pending'}})['total'], 0)
        self.assertEqual(client.call('list_records', {'entity': 'pattern_followups', 'filters': {'account_id': account}})['total'], 1)
        report = client.call('generate_pattern_report', {'account_ids': [account], 'start_date': '2026-10-18', 'end_date': '2026-10-18', 'include_examples': True})
        self.assertEqual(report['total'], 1)
        self.assertEqual(report['totals']['occurrences'], 1)
        self.assertEqual(client.call('list_records', {'entity': 'journal_entries'})['total'], 0)
        self.assertEqual(client.call('list_records', {'entity': 'trades'})['total'], 0)

        # Migration preview/commit uses only an explicitly seeded legacy fixture.
        legacy = client.call('save_record', {'entity': 'journal_entries', 'data': {'account_id': account, 'entry_type': 'daily', 'entry_date': '2026-10-18', 'stage': 'post', 'psychology': 'Situation: Synthetic legacy observation', 'custom_fields': {'neo_pattern': {'pattern_key': 'legacy-fixture', 'follow_up_status': 'pending'}}}, 'idempotency_key': 'test-legacy'})
        selection = {'source_ids': [legacy['id']], 'namespace': True, 'timezone': 'Australia/Brisbane'}
        preview = client.call('migrate_pattern_entries', {'selection': selection, 'dry_run': True})
        self.assertEqual(client.call('list_records', {'entity': 'pattern_entries'})['total'], 1)
        migrated = client.call('migrate_pattern_entries', {'selection': selection, 'dry_run': False, 'idempotency_key': 'test-migration'})
        self.assertEqual(len(migrated['mappings']), 1, migrated)
        self.assertEqual(client.call('list_records', {'entity': 'pattern_entries'})['total'], 2)
        again = client.call('migrate_pattern_entries', {'selection': selection, 'dry_run': False, 'idempotency_key': 'test-migration-repeat'})
        self.assertEqual(again['mappings'], migrated['mappings'])
        self.assertEqual(client.call('list_records', {'entity': 'pattern_entries'})['total'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
