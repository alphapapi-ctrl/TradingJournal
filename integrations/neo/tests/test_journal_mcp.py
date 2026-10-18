import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(os.environ.get('NEO_BRIDGE_PATH', Path(__file__).resolve().parents[1] / 'plugins/neo-trading/skills/neo-trading-workflow/scripts/journal_mcp.py'))
spec = importlib.util.spec_from_file_location('neo_bridge', path)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class Response(io.BytesIO):
    def __init__(self, body, content_type='application/json', status=200, session=None):
        super().__init__(body.encode())
        self.status = status
        self.headers = {'Content-Type': content_type}
        if session:
            self.headers['Mcp-Session-Id'] = session


def connected_client():
    client = object.__new__(bridge.JournalMCP)
    client.url = bridge.DEFAULT_URL
    client.timeout = 1
    client.headers = {}
    client.request_id = 0
    client.tools = {}
    return client


class BridgeTests(unittest.TestCase):
    def test_json_response(self):
        self.assertEqual(bridge.decode_response(Response('{"id":4,"result":{}}'), 4)['id'], 4)

    def test_stream_ignores_notifications_and_handles_multiline_event(self):
        data = ': keepalive\n\ndata: {"method":"notice"}\n\ndata: {"jsonrpc":"2.0",\ndata: "id":4,"result":{"ok":true}}\n\n'
        self.assertTrue(bridge.decode_response(Response(data, 'text/event-stream'), 4)['result']['ok'])

    def test_missing_stream_result_fails(self):
        with self.assertRaises(bridge.JournalError):
            bridge.decode_response(Response('data: {"id":5}\n\n', 'text/event-stream'), 4)

    def test_lost_write_is_not_retried(self):
        client = connected_client()
        with patch.object(bridge.urllib.request, 'urlopen', side_effect=OSError('connection lost')) as request:
            with self.assertRaises(bridge.JournalError):
                client.rpc('tools/call', {'name':'save_candidate','arguments':{'idempotency_key':'same-key'}})
            self.assertEqual(request.call_count, 1)

    def test_live_required_argument_guard(self):
        client = connected_client()
        client.tools = {'size_candidate': {'inputSchema': {'required':['candidate_id','expected_revision'], 'properties':{'candidate_id':{},'expected_revision':{}}}}}
        with patch.object(client, 'rpc') as request:
            with self.assertRaises(bridge.JournalError):
                client.call('size_candidate', {'candidate_id':1})
            request.assert_not_called()

    def test_unknown_tool_is_not_called(self):
        client = connected_client()
        with patch.object(client, 'rpc') as request:
            with self.assertRaises(bridge.JournalError):
                client.call('broker_order', {})
            request.assert_not_called()

    def test_tool_errors_never_become_success(self):
        with self.assertRaises(bridge.JournalError):
            bridge.unpack({'isError':True,'content':[{'type':'text','text':'revision conflict'}]})

    def test_all_native_pattern_tools_are_scoped_and_called(self):
        names = {'save_psychology_pattern', 'save_pattern_entry', 'append_pattern_followup', 'generate_pattern_report', 'migrate_pattern_entries'}
        client = connected_client()
        for name in names:
            with self.subTest(name=name):
                self.assertIn(name, bridge.ALLOWED_TOOLS)
                client.tools = {name: {'inputSchema': {'properties': {'data': {}}}}}
                with patch.object(client, 'rpc', return_value={'structuredContent': {'saved': True}}) as request:
                    self.assertEqual(client.call(name, {'data': {}}), {'saved': True})
                    request.assert_called_once_with('tools/call', {'name': name, 'arguments': {'data': {}}})

    def test_structured_conflict_preserves_code_revision_and_link_details(self):
        payload = {'error': {'code': 'revision_conflict', 'message': 'Stale revision', 'current_revision': 4, 'pattern_entry_id': 2}, 'schema_version': '1.2.0'}
        with self.assertRaises(bridge.JournalError) as raised:
            bridge.unpack({'isError': True, 'structuredContent': payload, 'content': []})
        self.assertEqual(raised.exception.payload, payload)
        self.assertIn('current_revision=4', str(raised.exception))

    def test_structured_error_cli_output_is_failure_and_machine_readable(self):
        import contextlib
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            args = Path(directory) / 'args.json'
            output = Path(directory) / 'result.json'
            args.write_text('{}', encoding='utf-8')
            payload = {'error': {'code': 'validation_failure', 'message': 'Invalid observation'}}
            client = connected_client()
            with patch.object(bridge, 'JournalMCP', return_value=client), patch.object(client, 'list_tools', return_value={}), patch.object(client, 'call', side_effect=bridge.JournalError('validation_failure', payload)), patch.object(bridge.sys, 'argv', ['bridge', 'call', 'save_pattern_entry', '--args-file', str(args), '--out', str(output)]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    bridge.main()
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8')), payload)

    def test_session_and_request_id_handling(self):
        client = connected_client()
        with patch.object(bridge.urllib.request, 'urlopen', return_value=Response('{"id":1,"result":{"ok":true}}', session='session-1')):
            self.assertTrue(client.rpc('tools/list', {})['ok'])
        self.assertEqual(client.headers['Mcp-Session-Id'], 'session-1')
        with patch.object(bridge.urllib.request, 'urlopen', return_value=Response('{"id":99,"result":{}}')):
            with self.assertRaises(bridge.JournalError):
                client.rpc('tools/list', {})

    def test_tool_discovery_paginates(self):
        client = connected_client()
        with patch.object(client, 'rpc', side_effect=[{'tools':[{'name':'first'}],'nextCursor':'next'}, {'tools':[{'name':'second'}]}]) as request:
            self.assertEqual(set(client.list_tools()), {'first','second'})
            self.assertEqual(request.call_args_list[1].args[1], {'cursor':'next'})


if __name__ == '__main__':
    unittest.main()
