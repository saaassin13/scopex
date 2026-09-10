"""CPU-only harness tests. Mock responses never count as model/Spark evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'poc01.py'
spec = importlib.util.spec_from_file_location('poc01', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def response(content=None, calls=None, reason=None):
    msg = {'role': 'assistant', 'content': content}
    if calls:
        msg['tool_calls'] = calls
        msg['reasoning_content'] = 'mock planning text'
    return {'choices': [{'message': msg, 'finish_reason': reason or ('tool_calls' if calls else 'stop')}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}


def call(name='read_file', arguments='{"path":"input.log"}', id='call_1'):
    return {'id': id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


class FakeClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def request(self, endpoint, payload=None, timeout=None):
        self.requests.append(copy.deepcopy(payload))
        value = next(self.outputs)
        if isinstance(value, BaseException):
            raise value
        return value


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.suite = self.base / 'suite'
        self.cases = p.init_cases(self.suite)
        self.cfg = {'model': 'mock-model', 'timeout_s': 3, 'sla_s': 2, 'max_rounds': 6,
                    'max_tool_calls': 8, 'max_file_bytes': 262144,
                    'request': {'temperature': 0.1, 'max_tokens': 100}}

    def tearDown(self):
        self.temp.cleanup()

    def case(self, name):
        return next(c for c in self.cases if c['id'] == name)

    def run_one(self, name, outputs):
        client = FakeClient(outputs)
        result = p.run_case(self.case(name), self.suite, self.cfg, client, self.base / 'out')
        return result, client

    def test_fixture_count_and_no_overwrite(self):
        self.assertEqual(len(self.cases), 8)
        with self.assertRaises(p.ProbeError):
            p.init_cases(self.suite)

    def test_direct_and_tool_share_bytes_and_answer(self):
        a, b = self.case('direct-basic'), self.case('tool-basic')
        self.assertEqual(a['root'], b['root'])
        self.assertEqual(a['files'], b['files'])
        self.assertEqual(a['expected'], b['expected'])
        self.assertEqual(len(a['expected']['records']), 3)

    def test_strict_json_grading(self):
        for actual in ['true', '1.0', '```json\n1\n```', '1 extra', 'NaN']:
            self.assertFalse(p.exact_json(actual, 1))
        self.assertFalse(p.exact_json('{"a":1,"a":1}', {'a': 1}))
        self.assertTrue(p.exact_json(' { "a": 1 } ', {'a': 1}))
        self.assertFalse(p.exact_json('[2,1]', [1, 2]))

    def test_direct_correct(self):
        result, client = self.run_one('direct-basic', [response(json.dumps(self.case('direct-basic')['expected']))])
        self.assertTrue(result['within_sla'])
        self.assertNotIn('tools', client.requests[0])
        self.assertIsNone(result['ttft_s'])

    def test_wrong_answer_is_failure(self):
        result, _ = self.run_one('direct-basic', [response('{"records":[]}')])
        self.assertFalse(result['correct'])

    def test_empty_is_valid_answer(self):
        result, _ = self.run_one('direct-empty', [response('{"records":[]}')])
        self.assertTrue(result['correct'])

    def test_tool_success_and_reasoning_preserved(self):
        result, client = self.run_one('tool-basic', [response(calls=[call()]),
                    response(json.dumps(self.case('tool-basic')['expected']))])
        self.assertTrue(result['correct'])
        messages = client.requests[1]['messages']
        self.assertEqual(messages[-1]['tool_call_id'], 'call_1')
        self.assertEqual(messages[-2]['reasoning_content'], 'mock planning text')
        self.assertEqual(result['successful_reads'], ['input.log'])
        self.assertTrue((self.base / 'out/01-request.json').exists())

    def test_hallucinated_tool_answer_not_success(self):
        result, _ = self.run_one('tool-basic', [response(json.dumps(self.case('tool-basic')['expected']))])
        self.assertFalse(result['correct'])
        self.assertFalse(result['grade']['required_reads'])

    def test_chain_two_reads_and_listing(self):
        output = [response(calls=[call('list_files', '{}')]),
                  response(calls=[call(arguments='{"path":"part_a.log"}', id='a'),
                                  call(arguments='{"path":"part_b.log"}', id='b')]),
                  response(json.dumps(self.case('chain')['expected']))]
        result, _ = self.run_one('chain', output)
        self.assertTrue(result['correct'])
        self.assertEqual(result['tool_calls'], 3)

    def test_unknown_tool_can_recover_but_is_recorded(self):
        result, _ = self.run_one('tool-basic', [response(calls=[call('shell', '{}')]),
                    response(calls=[call(id='ok')]), response(json.dumps(self.case('tool-basic')['expected']))])
        self.assertTrue(result['correct'])
        self.assertEqual(result['tool_errors'], 1)

    def test_repeat_stops(self):
        result, _ = self.run_one('tool-basic', [response(calls=[call()])] * 3)
        self.assertFalse(result['correct'])
        self.assertIn('repeated_identical_call', result['error'])

    def test_token_limit_not_success(self):
        result, _ = self.run_one('direct-empty', [response('{"records":[]}', reason='length')])
        self.assertFalse(result['correct'])
        self.assertIn('token_limit', result['error'])

    def test_duplicate_call_id_is_protocol_failure(self):
        result, _ = self.run_one('tool-basic', [response(calls=[call(), call()])])
        self.assertIn('tool_call_id', result['error'])

    def test_path_traversal_absolute_and_symlink_denied(self):
        root = self.suite / 'data/basic'
        for name in ['../../cases.json', '/etc/passwd']:
            with self.assertRaises(p.ProbeError):
                p.read_text(root, name, 262144)
        (root / 'leak').symlink_to(self.suite / 'cases.json')
        with self.assertRaises(p.ProbeError):
            p.read_text(root, 'leak', 262144)

    def test_size_cap_no_silent_truncation(self):
        with self.assertRaisesRegex(p.ProbeError, 'file_too_large'):
            p.read_text(self.suite / 'data/basic', 'input.log', 5)

    def test_images_are_actual_base64_and_swap_changes_order(self):
        _, messages, _ = p.prepare(self.case('vision'), self.suite, self.cfg)
        _, swapped, _ = p.prepare(self.case('vision-swap'), self.suite, self.cfg)
        a = messages[-1]['content']
        b = swapped[-1]['content']
        self.assertTrue(a[1]['image_url']['url'].startswith('data:image/png;base64,iVBOR'))
        self.assertEqual(a[1], b[2])
        self.assertNotEqual(a[1], b[1])
        self.assertNotIn('expected', json.dumps(messages))

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'POSIX required')
    def test_hard_timeout_and_result_persisted(self):
        class SlowClient:
            def request(self, *args, **kwargs):
                time.sleep(1)
        self.cfg['timeout_s'] = 0.05
        result = p.run_case(self.case('chat'), self.suite, self.cfg, SlowClient(), self.base / 'out')
        self.assertEqual(result['status'], 'timeout')
        self.assertLess(result['wall_s'], 0.5)
        self.assertTrue((self.base / 'out/result.json').exists())

    def test_interrupt_is_saved(self):
        result, _ = self.run_one('chat', [KeyboardInterrupt()])
        self.assertEqual(result['status'], 'interrupted')
        self.assertFalse(result['correct'])

    def test_report_counts_failures_and_unrun(self):
        out = self.base / 'report'
        out.mkdir()
        result = {'case': 'chat', 'correct': False, 'within_sla': False, 'wall_s': 2,
                  'status': 'error', 'rounds': 1, 'tool_calls': 0}
        p.report(out, [result], [1, 2], False)
        text = (out / 'summary.md').read_text()
        self.assertIn('未运行 1 次', text)
        self.assertIn('| chat | 1 | 0/1 | 0/1 |', text)

    def test_config_rejects_external_url_and_request_override(self):
        path = self.base / 'config.json'
        base = {'base_url': 'http://127.0.0.1:8000/v1', 'model': 'mock'}
        p.dump(path, base)
        p.load_config(path)
        for url in ['https://example.com/v1', 'http://127.0.0.1:8000/v1?api_key=x', 'http://a:b@localhost/v1']:
            p.dump(path, {**base, 'base_url': url})
            with self.assertRaises(p.ProbeError):
                p.load_config(path)
        p.dump(path, {**base, 'request': {'messages': []}})
        with self.assertRaises(p.ProbeError):
            p.load_config(path)

    def test_round_and_call_limits(self):
        self.cfg['max_rounds'] = 1
        result, _ = self.run_one('tool-basic', [response(calls=[call()])])
        self.assertIn('round_limit', result['error'])
        self.cfg['max_rounds'] = 6
        self.cfg['max_tool_calls'] = 1
        result, _ = self.run_one('tool-basic', [response(calls=[call(), call(id='second')])])
        self.assertIn('tool_call_limit', result['error'])

    def test_response_and_tool_finish_must_be_valid(self):
        result, _ = self.run_one('chat', [{'choices': []}])
        self.assertIn('protocol_error', result['error'])
        result, _ = self.run_one('tool-basic', [response(None, reason='tool_calls')])
        self.assertIn('protocol_error', result['error'])

    def test_redirect_is_refused(self):
        with self.assertRaises(p.ProbeError):
            p.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://example.com')

    def test_truth_is_outside_tool_root(self):
        root = self.suite / 'data/basic'
        result = p.execute_tool(root, 'list_files', {}, 262144)
        self.assertEqual(result['files'], ['input.log'])
        with self.assertRaises(p.ProbeError):
            p.execute_tool(root, 'read_file', {'path': '../../cases.json'}, 262144)

    def test_real_import_copies_bytes_and_protects_truth(self):
        from types import SimpleNamespace
        source = self.base / 'original.txt'
        source.write_text('real fixture\n', encoding='utf-8')
        prompt = self.base / 'prompt.txt'
        prompt.write_text('Return JSON records.', encoding='utf-8')
        expected = self.base / 'expected.json'
        p.dump(expected, {'records': []})
        args = SimpleNamespace(name='original', suite=self.suite, file=str(source),
                               prompt_file=str(prompt), expected_file=str(expected))
        p.add_log(args, self.cfg)
        target = self.suite / 'real-original'
        self.assertEqual(p.digest(source), p.digest(target / 'data/input.log'))
        self.assertEqual(len(p.loads((target / 'cases.json').read_text())), 2)
        self.assertFalse((target / 'data/expected.json').exists())
        with self.assertRaises(p.ProbeError):
            p.add_log(args, self.cfg)

    def test_cli_complete_run_persists_plan_and_manifest(self):
        config = self.base / 'config.json'
        p.dump(config, {**self.cfg, 'base_url': 'http://127.0.0.1:8000/v1'})
        client = FakeClient([response('{"status":"SCOPEX_READY"}')] * 2)
        with patch.object(p, 'ROOT', self.base), patch.object(p, 'Client', return_value=client):
            code = p.main(['--config', str(config), '--suite', str(self.suite), 'run',
                           '--case', 'chat', '--repeat', '2', '--label', 'test'])
        self.assertEqual(code, 0)
        output = next((self.base / 'runs').iterdir())
        self.assertTrue((output / 'manifest.json').exists())
        self.assertEqual(len(p.loads((output / 'results.json').read_text())), 2)
        self.assertEqual(len(p.loads((output / 'plan.json').read_text())['attempts']), 2)

    def test_cli_stops_on_failure_and_does_not_hide_unrun(self):
        config = self.base / 'config.json'
        p.dump(config, {**self.cfg, 'base_url': 'http://127.0.0.1:8000/v1'})
        client = FakeClient([response('{}')])
        with patch.object(p, 'ROOT', self.base), patch.object(p, 'Client', return_value=client):
            code = p.main(['--config', str(config), '--suite', str(self.suite), 'run',
                           '--case', 'chat', '--repeat', '2'])
        self.assertEqual(code, 1)
        output = next((self.base / 'runs').iterdir())
        self.assertIn('未运行 1 次', (output / 'summary.md').read_text())

    def test_http_local_integration(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"mock-model"}]}')
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.seen = data
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(response('{"status":"SCOPEX_READY"}')).encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = {**self.cfg, 'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_key_env': 'SCOPEX_TEST_UNUSED'}
            # Invalid proxy must not affect local request routing.
            with patch.dict('os.environ', {'HTTP_PROXY': 'http://invalid.example:1'}):
                client = p.Client(cfg)
                self.assertEqual(client.request('/models')['data'][0]['id'], 'mock-model')
                result = p.run_case(self.case('chat'), self.suite, cfg, client, self.base / 'out')
            self.assertTrue(result['correct'])
            self.assertFalse(server.seen['stream'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
