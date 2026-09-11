"""Offline locator tests: fake /proc and package metadata, never start OpenClaw."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/poc02_locate.py'
spec = importlib.util.spec_from_file_location('locate', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class LocateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def package(self, **values):
        self.root.joinpath('package.json').write_text(json.dumps({
            'name': 'openclaw', 'version': '2026.9.1',
            'bin': {'openclaw': 'openclaw.mjs'}, **values}))
        self.root.joinpath('openclaw.mjs').touch()

    def test_node_script_without_flag_values(self):
        self.assertEqual(p.program_paths(['/usr/bin/node', '/opt/openclaw/index.js',
            '--token', '/secret/token.js']), ['/usr/bin/node', '/opt/openclaw/index.js'])

    def test_relative_script(self):
        self.assertEqual(p.program_paths(['/usr/bin/node', 'dist/index.js'], '/opt/openclaw'),
            ['/usr/bin/node', '/opt/openclaw/dist/index.js'])

    def test_dont_treat_node_option_as_script(self):
        self.assertEqual(p.program_paths(['/usr/bin/node', '--eval', 'secret']), ['/usr/bin/node'])

    def test_unknown_launcher_not_reconstructed(self):
        self.assertEqual(p.program_paths(['/usr/bin/bash', '-c', 'token=SECRET node app.js']), ['/usr/bin/bash'])

    def test_unit_metadata_excludes_secrets(self):
        raw = ('Id=openclaw-gateway.service\nMainPID=55\nLoadState=loaded\n'
               'WorkingDirectory=/opt/openclaw\nEnvironment=TOKEN=SECRET\n'
               'ExecStart={ path=/usr/bin/node ; argv[]=/usr/bin/node /opt/openclaw/dist/index.js gateway --token SECRET ; ignore_errors=no ; }\n')
        row = p.summarize_unit(raw)
        self.assertNotIn('SECRET', json.dumps(row))
        self.assertEqual(row['program_paths'], ['/usr/bin/node', '/opt/openclaw/dist/index.js'])

    def test_unparseable_exec_is_unknown(self):
        self.assertEqual(p.summarize_unit('ExecStart=unknown')['entry_parse'], 'unknown')

    def test_package_version_and_entry(self):
        self.package()
        got = p.package_at(self.root)
        self.assertEqual(got['package_version'], '2026.9.1')
        self.assertEqual(got['entry_file'], str(self.root / 'openclaw.mjs'))

    def test_unrelated_package(self):
        self.package(name='other')
        self.assertIsNone(p.package_at(self.root))

    def test_metadata_is_not_execution(self):
        self.package(scripts={'postinstall': 'MUST_NOT_RUN'}, apiKey='SECRET')
        row = p.package_at(self.root)
        self.assertNotIn('SECRET', json.dumps(row))
        self.assertNotIn('MUST_NOT_RUN', json.dumps(row))

    def test_invalid_version_hidden(self):
        self.package(version='2026 --token SECRET')
        self.assertEqual(p.package_at(self.root)['package_version'], 'UNKNOWN')

    def test_entry_escape_rejected(self):
        self.package(bin={'openclaw': '../outside.mjs'})
        self.assertIsNone(p.package_at(self.root)['entry_file'])

    def test_invalid_json_package(self):
        self.root.joinpath('package.json').write_text('bad json')
        self.assertIsNone(p.package_at(self.root))

    def test_process_does_not_report_flag_values(self):
        folder = self.root / '555555'
        folder.mkdir()
        (folder / 'comm').write_text('openclaw-gatewa\n')
        (folder / 'cmdline').write_bytes(b'/usr/bin/node\0/opt/openclaw/dist/index.js\0--token\0SECRET\0')
        (folder / 'status').write_text('Uid:\t1000\t1000\n')
        (folder / 'cgroup').write_text('0::/user.slice/openclaw-gateway.service\n')
        (folder / 'cwd').symlink_to('/opt/openclaw')
        (folder / 'exe').symlink_to('/usr/bin/node')
        rows = p.processes(self.root)
        self.assertEqual(len(rows), 1)
        self.assertNotIn('SECRET', json.dumps(rows))
        self.assertEqual(rows[0]['service_units'], ['openclaw-gateway.service'])
        self.assertEqual(rows[0]['uid'], 1000)

    def test_generic_process_message_is_not_identity(self):
        folder = self.root / '555556'
        folder.mkdir()
        (folder / 'comm').write_text('python3\n')
        (folder / 'cmdline').write_bytes(b'python3\0some.py\0--message\0openclaw\0')
        self.assertEqual(p.processes(self.root), [])

    def test_no_service_binary(self):
        with patch.object(p.shutil, 'which', return_value=None):
            self.assertEqual(p.services()[0]['status'], 'systemctl_not_found')

    def test_only_metadata_commands(self):
        calls = []
        def command(argv):
            calls.append(argv)
            if 'list-units' in argv:
                return 'ok', 'openclaw-custom.service loaded active running\n'
            return 'ok', 'Id=openclaw-custom.service\nLoadState=loaded\nMainPID=123\n'
        with patch.object(p.shutil, 'which', return_value='/usr/bin/systemctl'), patch.object(p, 'command', side_effect=command):
            rows = p.services()
        self.assertEqual(len(rows), 2)
        for argv in calls:
            self.assertTrue('show' in argv or 'list-units' in argv)
            for banned in ('start', 'restart', 'enable', 'daemon-reload', 'status', 'cat'):
                self.assertNotIn(banned, argv)

    def test_explicit_root_discovery_without_executing(self):
        self.package()
        with patch.object(p.glob, 'iglob', return_value=iter(())), patch.object(p.shutil, 'which', return_value=None):
            result = p.installed([], [], [self.root], home=self.root / 'no-home')
        self.assertEqual(result['packages'][0]['package_root'], str(self.root))

    def test_missing_link_does_not_raise(self):
        self.assertIsNone(p.link(self.root / 'missing'))
        self.assertEqual(p.text(self.root / 'missing'), '')


if __name__ == '__main__':
    unittest.main()
