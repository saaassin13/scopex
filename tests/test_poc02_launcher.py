"""Static sg/env/sh metadata parsing; never run a discovered program."""
import importlib.util
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('locate', Path(__file__).resolve().parents[1] / 'scripts/poc02_locate.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class LauncherTests(unittest.TestCase):
    def test_sg_quoted_command(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', '/usr/bin/node /opt/openclaw/dist/index.js gateway --token SECRET'])
        self.assertEqual(got, ['/usr/bin/sg', '/usr/bin/node', '/opt/openclaw/dist/index.js'])

    def test_sg_flattened_show(self):
        got = p.summarize_unit('WorkingDirectory=!/home/test\nExecStart={ path=/usr/bin/sg ; argv[]=/usr/bin/sg docker -c /usr/bin/node /opt/openclaw/dist/index.js gateway --token SECRET ; ignore_errors=no ; pid=123 ; }')
        self.assertEqual(got['launcher'], 'sg')
        self.assertEqual(got['entry_parse'], 'wrapped_paths_candidate')
        self.assertEqual(got['program_paths'], ['/usr/bin/sg', '/usr/bin/node', '/opt/openclaw/dist/index.js'])
        self.assertNotIn('SECRET', json.dumps(got))

    def test_sg_optional_c_and_login(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', '-', 'docker', '/opt/openclaw/openclaw.mjs gateway'])
        self.assertEqual(got, ['/usr/bin/sg', '/opt/openclaw/openclaw.mjs'])

    def test_sg_exec_env_nested(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', 'exec /usr/bin/env TOKEN=SECRET /usr/bin/node /opt/openclaw/dist/index.js'])
        self.assertEqual(got, ['/usr/bin/sg', '/usr/bin/env', '/usr/bin/node', '/opt/openclaw/dist/index.js'])

    def test_sg_nested_shell(self):
        inner = 'exec /usr/bin/node /opt/openclaw/dist/index.js gateway'
        body = '/bin/bash -lc ' + shlex.quote(inner)
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', body])
        self.assertEqual(got, ['/usr/bin/sg', '/bin/bash', '/usr/bin/node', '/opt/openclaw/dist/index.js'])

    def test_cd_literal_and_exec(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', 'cd /opt/openclaw && exec /usr/bin/node dist/index.js --token SECRET'])
        self.assertIn('/opt/openclaw/dist/index.js', got)
        self.assertNotIn('SECRET', json.dumps(got))

    def test_assignment_value_not_a_path(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', 'TOKEN=/secret/file.js /usr/bin/node /opt/openclaw/dist/index.js'])
        self.assertNotIn('/secret/file.js', got)
        self.assertIn('/opt/openclaw/dist/index.js', got)

    def test_no_arbitrary_flag_value(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', '/usr/bin/node /opt/openclaw/dist/index.js --token /secret/key.js'])
        self.assertNotIn('/secret/key.js', got)

    def test_shell_pipeline_unknown(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', 'echo SECRET | /usr/bin/node /opt/openclaw/dist/index.js'])
        self.assertEqual(got, ['/usr/bin/sg'])

    def test_no_eval_or_substitution(self):
        for command in ('eval /usr/bin/node /opt/openclaw/dist/index.js', '$(echo /usr/bin/node) /opt/openclaw/dist/index.js', '$SECRET /opt/openclaw/dist/index.js'):
            with self.subTest(command=command):
                self.assertEqual(p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', command]), ['/usr/bin/sg'])

    def test_node_known_options(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', '/usr/bin/node --no-warnings --disable-warning=ExperimentalWarning /opt/openclaw/dist/index.js --token SECRET'])
        self.assertIn('/opt/openclaw/dist/index.js', got)

    def test_node_eval_not_script(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', '/usr/bin/node --eval /secret/code.js'])
        self.assertEqual(got, ['/usr/bin/sg', '/usr/bin/node'])

    def test_no_relative_path_from_systemctl_marker(self):
        got = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', '/usr/bin/node dist/index.js'], '!/home/test')
        self.assertEqual(got, ['/usr/bin/sg', '/usr/bin/node'])

    def test_shell_unmatched_quote_stops(self):
        self.assertEqual(p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', "'/usr/bin/node"]), ['/usr/bin/sg'])

    def test_wrapper_only_label(self):
        got = p.summarize_unit('ExecStart={ path=/usr/bin/sg ; argv[]=/usr/bin/sg docker -c openclaw gateway ; ignore_errors=no ; }')
        self.assertEqual(got['entry_parse'], 'wrapper_only')

    def test_no_execute_and_find_package_from_wrapped_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'dist').mkdir()
            (root / 'dist/index.js').write_text('DO NOT EXECUTE')
            (root / 'openclaw.mjs').write_text('DO NOT EXECUTE')
            (root / 'package.json').write_text(json.dumps({'name': 'openclaw', 'version': '2026.9.1', 'bin': {'openclaw': 'openclaw.mjs'}}))
            with patch.object(p.subprocess, 'run', side_effect=AssertionError('must not execute')), patch.object(p.glob, 'iglob', return_value=iter(())), patch.object(p.shutil, 'which', return_value=None):
                paths = p.wrapped_program_paths(['/usr/bin/sg', 'docker', '-c', f'/usr/bin/node {root}/dist/index.js --token SECRET'])
                got = p.installed([], [{'units': [{'program_paths': paths}]}], home=root / 'home')
            self.assertTrue(any(x['package_root'] == str(root) for x in got['packages']))
            self.assertNotIn('SECRET', json.dumps(got))


if __name__ == '__main__':
    unittest.main()
