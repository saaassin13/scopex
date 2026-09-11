"""Offline preparation tests. No OpenClaw, Docker daemon, or model is required."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/poc02_prepare.py'
spec = importlib.util.spec_from_file_location('poc02_prepare', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def fake_inventory(folder, cli_path=None):
    return {'machine': 'aarch64', 'python': '3.10.12', 'host_cli_found': False,
            'host_cli': None, 'help': {}, 'docker': {'status': 'not_found'}, 'config_loaded': False}


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run, self.suite = self.root / 'run', self.root / 'suite'
        self.out, self.stage = self.root / 'prep', self.root / 'input'
        self.log = b'2030-01-01 12:00:00:001 [demo.cpp:1] [ERROR] TEST_ONLY  \r\n'
        self.cfg = {'base_url': 'http://127.0.0.1:8000/v1', 'model': 'test-model',
                    'sla_s': 120, 'timeout_s': 180, 'api_key': 'NEVER_EXPORT_SECRET',
                    'notes': 'NEVER_EXPORT_SECRET', 'request': {'temperature': .1, 'max_tokens': 2048,
                    'chat_template_kwargs': {'enable_thinking': False}}}
        self.cases = [{'id': mode + '-real-test', 'mode': mode, 'root': 'data', 'files': ['input.log'],
                       'prompt': 'Extract the error records as JSON.',
                       'expected': {'secret_answer': 'DO_NOT_MOUNT_EXPECTED'}} for mode in ('direct', 'tool')]
        self.results = [{'case': c['id'], 'status': 'correct', 'correct': True, 'within_sla': True,
                         'input_sha256': {'input.log': p.sha(self.log)}} for c in self.cases]
        put(self.run / 'config.json', self.cfg)
        put(self.run / 'cases.json', self.cases)
        put(self.run / 'results.json', self.results)
        put(self.run / 'plan.json', {'warmup': False, 'attempts': [r['case'] for r in self.results]})
        put(self.suite / 'cases.json', self.cases)
        (self.suite / 'data').mkdir()
        (self.suite / 'data/input.log').write_bytes(self.log)

    def prepare(self):
        return p.prepare(self.run, self.suite, self.out, self.stage, inventory_fn=fake_inventory)

    def test_exact_input_only_and_original_unchanged(self):
        original = {f: f.read_bytes() for f in self.root.rglob('*') if f.is_file()}
        self.prepare()
        self.assertEqual([f.name for f in self.stage.iterdir()], ['input.log'])
        self.assertEqual((self.stage / 'input.log').read_bytes(), self.log)
        for file, data in original.items():
            self.assertEqual(file.read_bytes(), data)
        self.assertNotIn('DO_NOT_MOUNT_EXPECTED', (self.out / 'task.txt').read_text())
        self.assertNotIn('NEVER_EXPORT_SECRET', (self.out / 'reference.json').read_text())

    def test_stage_is_not_claimed_isolated(self):
        summary = self.prepare()
        ref = p.read_json(self.out / 'reference.json')
        for key in ('isolation_verified', 'wire_request_verified', 'agent_run_started'):
            self.assertIs(ref[key], False)
        self.assertIn('PREPARED_NOT_RUN', summary)

    def test_reject_existing_output(self):
        self.out.mkdir()
        with self.assertRaises(p.PrepareError):
            self.prepare()
        self.assertFalse(self.stage.exists())

    def test_reject_existing_stage(self):
        self.stage.mkdir()
        with self.assertRaises(p.PrepareError):
            self.prepare()
        self.assertFalse(self.out.exists())

    def test_reject_stage_inside_suite(self):
        self.stage = self.suite / 'public'
        with self.assertRaises(p.PrepareError):
            self.prepare()

    def test_reject_stage_ancestor_of_evidence(self):
        self.stage = self.root / 'parent'
        self.out = self.stage / 'private'
        with self.assertRaises(p.PrepareError):
            self.prepare()

    def test_hash_mismatch(self):
        (self.suite / 'data/input.log').write_bytes(b'changed')
        with self.assertRaises(p.PrepareError):
            self.prepare()
        self.assertFalse(self.out.exists())

    def test_manifest_changed(self):
        cases = [dict(c) for c in self.cases]
        cases[0]['prompt'] = 'different rules'
        put(self.suite / 'cases.json', cases)
        with self.assertRaises(p.PrepareError):
            self.prepare()

    def test_warmup_not_accepted(self):
        put(self.run / 'plan.json', {'warmup': True, 'attempts': [r['case'] for r in self.results]})
        with self.assertRaises(p.PrepareError):
            self.prepare()

    def test_incomplete_run_not_accepted(self):
        put(self.run / 'plan.json', {'warmup': False, 'attempts': [r['case'] for r in self.results] * 2})
        with self.assertRaises(p.PrepareError):
            self.prepare()

    def test_format_failure_not_promoted(self):
        self.results[1].update(status='wrong_answer_or_missing_evidence', correct=False, within_sla=False)
        put(self.run / 'results.json', self.results)
        with self.assertRaises(p.PrepareError):
            self.prepare()

    def test_pair_must_match(self):
        self.cases[1]['expected'] = {'another': 'answer'}
        put(self.run / 'cases.json', self.cases)
        put(self.suite / 'cases.json', self.cases)
        with self.assertRaises(p.PrepareError):
            self.prepare()

    def test_symlink_input_denied(self):
        original = self.suite / 'data/input.log'
        original.rename(self.root / 'secret.log')
        original.symlink_to(self.root / 'secret.log')
        with self.assertRaises(p.PrepareError):
            self.prepare()

    def test_parent_symlink_denied(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.suite, target_is_directory=True)
        with self.assertRaises(p.PrepareError):
            p.plain_path(alias / 'data/input.log')

    def test_url_rejects_remote_and_credentials(self):
        for url in ('http://example.com/v1', 'http://key@127.0.0.1/v1',
                    'http://127.0.0.1/v1?key=secret', 'http://127.0.0.1/else'):
            with self.subTest(url=url), self.assertRaises(p.PrepareError):
                p.validate_reference({**self.cfg, 'base_url': url})

    def test_nothink_must_be_boolean_false(self):
        for value in (True, 'false', 0, None):
            cfg = {**self.cfg, 'request': {'chat_template_kwargs': {'enable_thinking': value}}}
            with self.subTest(value=value), self.assertRaises(p.PrepareError):
                p.validate_reference(cfg)

    def test_unknown_request_fields_not_exported(self):
        cfg = {**self.cfg, 'request': {**self.cfg['request'], 'api_key': 'secret'}}
        with self.assertRaises(p.PrepareError):
            p.validate_reference(cfg)

    def test_duplicate_keys_and_nan_rejected(self):
        for text in ('{"key":1,"key":2}', '{"key":NaN}'):
            with self.assertRaises(p.PrepareError):
                p.loads(text)

    def test_clean_environment(self):
        with patch.dict(os.environ, {'SCOPEX_API_KEY': 'secret', 'ANTHROPIC_API_KEY': 'secret',
                                     'OPENCLAW_CONFIG_PATH': '/prod/config', 'NODE_OPTIONS': '--bad',
                                     'HTTPS_PROXY': 'https://example.com'}):
            env = p.clean_help_env('/isolated')
        for key in ('SCOPEX_API_KEY', 'ANTHROPIC_API_KEY', 'NODE_OPTIONS', 'HTTPS_PROXY'):
            self.assertNotIn(key, env)
        self.assertTrue(env['OPENCLAW_CONFIG_PATH'].startswith('/isolated/'))

    def test_capture_no_shell_interpolation(self):
        result = p.capture([sys.executable, '-c', 'import sys;print(sys.argv[1])', '$(echo unsafe)'],
                           dict(os.environ), self.root)
        self.assertEqual(result['returncode'], 0)
        self.assertIn('$(echo unsafe)', result['text'])

    def test_capture_timeout(self):
        result = p.capture([sys.executable, '-c', 'import time;time.sleep(5)'],
                           dict(os.environ), self.root, seconds=.05)
        self.assertTrue(result['timeout'])
        self.assertNotEqual(result['returncode'], 0)

    def test_cli_help_without_inputs_or_runtime(self):
        result = p.capture([sys.executable, str(SCRIPT), '--help'], dict(os.environ), self.root)
        self.assertEqual(result['returncode'], 0)
        self.assertIn('--baseline-run', result['text'])

    def test_missing_binary(self):
        result = p.capture(['/this/path/does/not/exist'], {}, self.root)
        self.assertIsNone(result['returncode'])
        self.assertEqual(result['error_type'], 'FileNotFoundError')

    def test_remote_docker_not_contacted(self):
        calls = []
        def fake_capture(argv, *args, **kwargs):
            calls.append(argv)
            return {'returncode': 0, 'timeout': False, 'truncated': False,
                    'text': '"tcp://remote.example:2376"'}
        with patch.object(p.shutil, 'which', side_effect=lambda n: '/usr/bin/docker' if n == 'docker' else None), \
             patch.object(p, 'capture', side_effect=fake_capture), \
             patch.dict(os.environ, {'DOCKER_HOST': '', 'DOCKER_CONTEXT': 'remote-test'}):
            info = p.inventory(self.root)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:3], ['context', 'inspect'])
        self.assertEqual(info['docker']['status'], 'remote_or_unknown_context_not_queried')

    def test_summary_does_not_dump_help_secrets(self):
        info = fake_inventory(self.root)
        info['help']['version'] = {'returncode': 0, 'text': 'OpenClaw 2026.9.9 apiKey=TOPSECRET'}
        summary = p.render_summary(p.validate_reference(self.cfg) | {'baseline_attempts': 2, 'input_bytes': 10}, info, self.stage)
        self.assertIn('2026.9.9', summary)
        self.assertNotIn('TOPSECRET', summary)


if __name__ == '__main__':
    unittest.main()
