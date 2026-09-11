"""Regression for the reported ID rewrite. Local mock HTTP; no OpenClaw/model."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / 'scripts/poc02_preflight.py'
spec = importlib.util.spec_from_file_location('preflight_ids', FILE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
OLD_ID = 'call_scopex_preflight'


def replay_payload(call_id=p.TOOL_ID, marker=p.MARKER):
    return {'messages': [
        {'role': 'system', 'content': 'Synthetic wiring test only.'},
        {'role': 'user', 'content': 'Print test marker.'},
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': call_id, 'type': 'function', 'function': {'name': 'exec',
                'arguments': json.dumps({'command': "printf 'SCOPEX_SANDBOX_WIRE_CHECK\\n'"})}}]},
        {'role': 'tool', 'tool_call_id': call_id, 'content': marker},
    ]}


class ToolIdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ref = {'model': 'test-qwen', 'request': {'temperature': .1,
            'max_tokens': 2048, 'chat_template_kwargs': {'enable_thinking': False}}}

    def test_fixture_id_survives_observed_sanitization(self):
        self.assertEqual(re.sub(r'[^A-Za-z0-9]', '', p.TOOL_ID), p.TOOL_ID)
        self.assertEqual(p.TOOL_ID, 'callscopexpreflight')

    def test_reported_replay_is_accepted_with_new_fixture(self):
        detail = p.check_tool_return(replay_payload('callscopexpreflight'))
        self.assertEqual(detail['problems'], [])
        self.assertTrue(detail['marker_matched_expected_id'])
        self.assertEqual(detail['matching_result_count'], 1)

    def test_reproduce_old_fixture_false_negative(self):
        with patch.object(p, 'TOOL_ID', OLD_ID):
            detail = p.check_tool_return(replay_payload('callscopexpreflight'))
        self.assertTrue(detail['marker_present_in_tool_results'])
        self.assertFalse(detail['marker_matched_expected_id'])
        self.assertIn('tool_call_id mismatch or missing tool result', detail['problems'])

    def test_unrelated_id_with_marker_still_rejected(self):
        detail = p.check_tool_return(replay_payload('callunrelated'))
        self.assertTrue(detail['problems'])
        self.assertFalse(detail['marker_matched_expected_id'])

    def test_no_fuzzy_id_match(self):
        detail = p.check_tool_return(replay_payload('call_scopex_preflight'))
        self.assertTrue(detail['problems'])

    def test_missing_marker_is_separate_failure(self):
        detail = p.check_tool_return(replay_payload(marker='exec failed'))
        self.assertEqual(detail['problems'], ['expected tool result missing marker'])
        self.assertEqual(detail['matching_result_count'], 1)

    def test_marker_only_in_prompt_is_not_evidence(self):
        payload = {'messages': [{'role': 'user', 'content': p.MARKER}]}
        detail = p.check_tool_return(payload)
        self.assertTrue(detail['problems'])
        self.assertFalse(detail['marker_present_in_tool_results'])

    def test_marker_in_tool_arguments_is_not_evidence(self):
        payload = replay_payload()
        payload['messages'].pop()
        detail = p.check_tool_return(payload)
        self.assertTrue(detail['problems'])
        self.assertFalse(detail['marker_present_in_tool_results'])

    def test_duplicate_same_id_results_rejected(self):
        payload = replay_payload()
        payload['messages'].append(copy.deepcopy(payload['messages'][-1]))
        detail = p.check_tool_return(payload)
        self.assertEqual(detail['problems'], ['duplicate tool results for expected ID'])

    def test_marker_on_different_result_does_not_rescue_failure(self):
        payload = replay_payload(marker='no expected output')
        payload['messages'].append({'role': 'tool', 'tool_call_id': 'other', 'content': p.MARKER})
        detail = p.check_tool_return(payload)
        self.assertTrue(detail['marker_present_in_tool_results'])
        self.assertTrue(detail['problems'])

    def test_summary_does_not_dump_content_or_arbitrary_id(self):
        payload = replay_payload('invalid TOKEN=SECRET', marker='SECRET')
        detail = p.check_tool_return(payload)
        self.assertNotIn('SECRET', json.dumps(detail))
        self.assertEqual(detail['returned_tool_call_ids'], ['<invalid-or-omitted>'])

    def test_input_messages_not_mutated(self):
        payload = replay_payload()
        original = copy.deepcopy(payload)
        p.check_tool_return(payload)
        self.assertEqual(payload, original)

    def http_replay(self, stream, response_id=None, return_id=None, marker=p.MARKER):
        """Simulate only the observed alphanumeric ID rewrite, not an Agent."""
        old_context = patch.object(p, 'TOOL_ID', response_id or p.TOOL_ID)
        with old_context:
            server = p.Receiver(self.root, self.ref, 'TEST_LOCAL_KEY')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                def post(payload):
                    req = urllib.request.Request(
                        f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
                        data=json.dumps(payload).encode(),
                        headers={'Authorization': 'Bearer TEST_LOCAL_KEY',
                                 'Content-Type': 'application/json'})
                    try:
                        with opener.open(req, timeout=3) as res:
                            return res.status, res.read().decode()
                    except urllib.error.HTTPError as exc:
                        try:
                            return exc.code, exc.read().decode()
                        finally:
                            exc.close()
                base = {'model': self.ref['model'], **self.ref['request'], 'stream': stream,
                    'messages': replay_payload()['messages'][:2],
                    'tools': [{'type': 'function', 'function': {'name': name}} for name in p.TOOLS]}
                status, first = post(base)
                self.assertEqual(status, 200)
                if stream:
                    events = [json.loads(line[6:]) for line in first.splitlines()
                              if line.startswith('data: ') and line != 'data: [DONE]']
                    reply = events[0]['choices'][0]['delta']
                else:
                    reply = json.loads(first)['choices'][0]['message']
                emitted = reply['tool_calls'][0]['id']
                sanitized = re.sub(r'[^A-Za-z0-9]', '', emitted)
                second = {**base, **replay_payload(return_id or sanitized, marker)}
                status, answer = post(second)
                self.assertEqual(len(server.records), 2)
                self.assertNotIn('TEST_LOCAL_KEY', (self.root / 'wire-02-request.json').read_text())
                return status, answer, server.records[1]
            finally:
                server.shutdown(); thread.join(timeout=3); server.server_close()

    def test_http_reproduces_old_422_despite_marker(self):
        code, answer, detail = self.http_replay(True, response_id=OLD_ID)
        self.assertEqual(code, 422)
        self.assertIn('tool_call_id mismatch', answer)
        self.assertTrue(detail['tool_return']['marker_present_in_tool_results'])

    def test_http_stream_round_trip_with_id_sanitization(self):
        code, answer, detail = self.http_replay(True)
        self.assertEqual(code, 200)
        self.assertIn(p.SYNTHETIC, answer)
        self.assertIn('data: [DONE]', answer)
        self.assertEqual(detail['tool_return']['problems'], [])

    def test_http_nonstream_round_trip_with_id_sanitization(self):
        code, answer, detail = self.http_replay(False)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(answer)['choices'][0]['message']['content'], p.SYNTHETIC)
        self.assertTrue(detail['tool_return']['marker_matched_expected_id'])

    def test_http_wrong_id_is_not_accepted_to_make_test_pass(self):
        code, answer, detail = self.http_replay(True, return_id='unrelated')
        self.assertEqual(code, 422)
        self.assertTrue(detail['tool_return']['problems'])

    def test_http_matching_id_missing_marker_still_fails(self):
        code, answer, detail = self.http_replay(True, marker='command failed')
        self.assertEqual(code, 422)
        self.assertIn('expected tool result missing marker', answer)
        self.assertFalse(detail['tool_return']['marker_matched_expected_id'])


if __name__ == '__main__':
    unittest.main()
