"""Offline preflight tests. No OpenClaw, Docker daemon, vLLM, or GPU required."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / 'scripts/poc02_preflight.py'
spec = importlib.util.spec_from_file_location('preflight', FILE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.stage = self.root / 'input'
        self.stage.mkdir()
        self.stage.joinpath('input.log').write_bytes(b'TEST_ONLY\n')
        self.ref = {'model': 'test-qwen', 'staging_directory': str(self.stage),
                    'base_url': 'http://127.0.0.1:8000/v1', 'timeout_s': 180,
                    'input_sha256': p.digest(self.stage / 'input.log'),
                    'request': {'temperature': 0.1, 'max_tokens': 2048,
                                'chat_template_kwargs': {'enable_thinking': False}}}
        self.runtime, self.out = self.root / 'runtime', self.root / 'audit'
        self.out.mkdir()

    def payload(self, **more):
        return {'model': self.ref['model'], **self.ref['request'],
                'messages': [{'role': 'user', 'content': 'wire check only'}],
                'tools': [{'type': 'function', 'function': {'name': name}} for name in p.TOOLS],
                **more}

    def cfg(self, ref=None):
        return p.build_config(ref or self.ref, self.runtime, self.out,
            'http://127.0.0.1:12345/v1', 'NOT_A_REAL_KEY', 'sha256:abc', 'sx123', 1001, 1001)

    def info(self):
        return {'Name': '/sxpf-sx123-one', 'Image': 'sha256:abc',
                'Config': {'User': '1001:1001'},
                'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True,
                              'Privileged': False, 'CapDrop': ['ALL'],
                              'SecurityOpt': ['no-new-privileges']},
                'Mounts': [{'Type': 'bind', 'Source': str(self.stage), 'Destination': '/agent', 'RW': False},
                           {'Type': 'bind', 'Source': str(self.runtime / 'sandboxes' / 'scope'), 'Destination': '/workspace', 'RW': True}]}

    def boundary(self, info):
        return p.check_container(info, self.stage, self.runtime / 'sandboxes', 'sxpf-sx123-', 'sha256:abc', 1001)

    def test_input_hash(self):
        self.assertEqual(p.check_input(self.ref), self.stage)

    def test_changed_input_rejected(self):
        self.stage.joinpath('input.log').write_text('changed')
        with self.assertRaises(p.CheckError): p.check_input(self.ref)

    def test_answers_not_allowed_in_stage(self):
        self.stage.joinpath('expected.json').write_text('{}')
        with self.assertRaises(p.CheckError): p.check_input(self.ref)

    def test_symlink_input_rejected(self):
        self.stage.joinpath('input.log').unlink()
        self.stage.joinpath('input.log').symlink_to(self.root / 'outside')
        with self.assertRaises(p.CheckError): p.check_input(self.ref)

    def test_config_entries_not_list(self):
        self.assertEqual(list(self.cfg()['agents']['entries']), ['sx123'])
        self.assertNotIn('list', self.cfg()['agents'])

    def test_config_does_not_point_to_vllm(self):
        cfg = self.cfg()
        self.assertEqual(cfg['models']['providers']['vllm']['baseUrl'], 'http://127.0.0.1:12345/v1')
        self.assertNotIn(self.ref['base_url'], json.dumps(cfg))

    def test_no_fallback_and_no_skills(self):
        defaults = self.cfg()['agents']['defaults']
        self.assertEqual(defaults['model']['fallbacks'], [])
        self.assertEqual(defaults['skills'], [])
        self.assertTrue(defaults['skipBootstrap'])
        self.assertFalse(defaults['compaction']['enabled'])

    def test_readonly_agent_mount_and_pinned_image(self):
        s = self.cfg()['agents']['defaults']['sandbox']
        self.assertEqual(s['mode'], 'all')
        self.assertEqual(s['workspaceAccess'], 'ro')
        self.assertEqual(s['docker']['image'], 'sha256:abc')
        self.assertEqual(s['docker']['network'], 'none')
        self.assertNotIn('binds', s['docker'])

    def test_tools_no_elevation_and_no_host_exec(self):
        t = self.cfg()['tools']
        self.assertEqual(t['allow'], ['read', 'exec', 'process'])
        self.assertEqual(t['exec']['host'], 'sandbox')
        self.assertFalse(t['elevated']['enabled'])

    def test_baseline_extra_body_unchanged(self):
        row = self.cfg()['agents']['defaults']['models']['vllm/test-qwen']
        self.assertEqual(row['params']['extra_body'], self.ref['request'])

    def test_requires_false_boolean_thinking(self):
        for value in (True, 'false', 0, None):
            ref = {**self.ref, 'request': {'max_tokens': 2048, 'chat_template_kwargs': {'enable_thinking': value}}}
            with self.subTest(value=value), self.assertRaises(p.CheckError): self.cfg(ref)

    def test_rejects_extra_body_from_unknown_source(self):
        ref = {**self.ref, 'request': {**self.ref['request'], 'apiKey': 'SECRET'}}
        with self.assertRaises(p.CheckError): self.cfg(ref)

    def test_one_limit_only(self):
        ref = {**self.ref, 'request': {**self.ref['request'], 'max_completion_tokens': 2048}}
        with self.assertRaises(p.CheckError): self.cfg(ref)

    def test_wire_matches(self):
        self.assertEqual(p.check_wire(self.payload(stream=True), self.ref)['problems'], [])

    def test_wire_detects_missing_thinking(self):
        payload = self.payload()
        payload.pop('chat_template_kwargs')
        self.assertIn('missing chat_template_kwargs', p.check_wire(payload, self.ref)['problems'])

    def test_wire_detects_model_and_temperature(self):
        x = p.check_wire(self.payload(model='other', temperature=.7), self.ref)
        self.assertIn('model mismatch', x['problems'])
        self.assertIn('temperature mismatch', x['problems'])

    def test_wire_blocks_forced_tools(self):
        self.assertIn('tool_choice is forced', p.check_wire(self.payload(tool_choice='required'), self.ref)['problems'])

    def test_wire_blocks_extra_tools(self):
        x = self.payload()
        x['tools'].append({'function': {'name': 'gateway'}})
        self.assertIn('unexpected or missing tool surface', p.check_wire(x, self.ref)['problems'])

    def test_wire_extra_thinking_keys_visible(self):
        result = p.check_wire(self.payload(chat_template_kwargs={'enable_thinking':False, 'preserve_thinking':True}), self.ref)
        self.assertTrue(result['chat_template_kwargs']['preserve_thinking'])

    def test_boundary_good(self):
        self.assertEqual(self.boundary(self.info())['problems'], [])

    def test_boundary_readwrite_log_rejected(self):
        info = self.info(); info['Mounts'][0]['RW'] = True
        self.assertIn('unexpected bind or volume', self.boundary(info)['problems'])

    def test_boundary_repo_mount_rejected(self):
        info = self.info(); info['Mounts'].append({'Type':'bind','Source':str(p.ROOT),'Destination':'/repo','RW':False})
        self.assertIn('unexpected bind or volume', self.boundary(info)['problems'])

    def test_boundary_root_rejected(self):
        info = self.info(); info['Config']['User'] = '0:0'
        self.assertIn('sandbox user mismatch', self.boundary(info)['problems'])

    def test_boundary_network_rejected(self):
        info = self.info(); info['HostConfig']['NetworkMode'] = 'host'
        self.assertIn('image or network mismatch', self.boundary(info)['problems'])

    def test_boundary_wrong_name_rejected(self):
        info = self.info(); info['Name']='/openclaw-production'
        self.assertIn('not this test container', self.boundary(info)['problems'])

    def test_environment_no_operator_keys(self):
        with patch.dict(os.environ, {'SCOPEX_API_KEY':'SECRET','OPENAI_API_KEY':'SECRET','NODE_OPTIONS':'BAD','HTTPS_PROXY':'BAD'}):
            env=p.clean_env(self.runtime,self.out/'openclaw.json','unix:///var/run/docker.sock')
        self.assertNotIn('SECRET',json.dumps(env)); self.assertNotIn('BAD',json.dumps(env))
        self.assertEqual(env['OPENCLAW_LOAD_SHELL_ENV'],'0')

    def test_strict_json_input(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(p.CheckError): p.loads(raw)

    def receiver_request(self, stream):
        server=p.Receiver(self.out,self.ref,'LOCAL_TEST_TOKEN')
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(payload):
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
                    data=json.dumps(payload).encode(),
                    headers={'Authorization':'Bearer LOCAL_TEST_TOKEN','Content-Type':'application/json'})
                with opener.open(req,timeout=3) as resp: return resp.read().decode()
            first=post(self.payload(stream=stream))
            self.assertIn('exec', first)
            self.assertIn(p.MARKER, first)
            # Unit test simulates native tool return. It does NOT run exec.
            second=self.payload(stream=stream)
            second['messages'].append({'role':'tool','tool_call_id':p.TOOL_ID,'content':p.MARKER})
            answer=post(second)
            self.assertEqual(len(server.records),2)
            self.assertIn(p.SYNTHETIC,answer)
            self.assertNotIn('LOCAL_TEST_TOKEN',(self.out/'wire-01-request.json').read_text())
            return answer
        finally:
            server.shutdown(); thread.join(); server.server_close()

    def test_http_stream_synthetic_no_usage(self):
        result=self.receiver_request(True)
        self.assertIn('data: [DONE]',result)
        self.assertNotIn('"usage"',result)

    def test_http_nonstream_synthetic(self):
        result=json.loads(self.receiver_request(False))
        self.assertEqual(result['choices'][0]['message']['content'],p.SYNTHETIC)

    def test_http_mismatch_stops_before_tool_reply(self):
        server=p.Receiver(self.out,self.ref,'TOKEN')
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
                data=json.dumps(self.payload(temperature=.8)).encode(),
                headers={'Authorization':'Bearer TOKEN','Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught: opener.open(req,timeout=3)
            self.assertEqual(caught.exception.code,422)
            self.assertIn('temperature mismatch',server.records[0]['problems'])
        finally:
            server.shutdown(); thread.join(); server.server_close()

    def test_complete_main_with_mock_runtime_and_docker(self):
        prepared=self.root/'prepared'; prepared.mkdir()
        p.dump(prepared/'reference.json',self.ref)
        cli=self.root/'openclaw'; cli.write_text('NOT EXECUTED'); cli.chmod(0o700)
        seen=[]; cfg={}; cfg_path=None
        def fake_command(argv, env, cwd, out, label, seconds=25):
            nonlocal cfg, cfg_path
            seen.append((label,argv))
            if label=='docker-context': return '"unix:///var/run/docker.sock"'
            if label=='image-inspect': return json.dumps({'Architecture':'arm64','Os':'linux','Id':'sha256:abc','Config':{}})
            if label=='version':
                cfg_path=Path(env['OPENCLAW_CONFIG_PATH']); cfg=p.read_json(cfg_path)
                return 'OpenClaw 2026.9.2 (3928bad)'
            if label=='config-file': return str(cfg_path)
            if label=='config-validate': return 'valid'
            if label=='wire-turn':
                provider=cfg['models']['providers']['vllm']
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for payload in (self.payload(stream=False),self.payload(stream=False,messages=[{'role':'tool','tool_call_id':p.TOOL_ID,'content':p.MARKER}])):
                    req=urllib.request.Request(provider['baseUrl']+'/chat/completions',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+provider['apiKey']})
                    with opener.open(req,timeout=3) as response: response.read()
                return 'fake CLI reply'
            if label=='sandbox-list': return 'abcdef012345'
            if label=='sandbox-inspect':
                info=self.info()
                sc=cfg['agents']['defaults']['sandbox']
                info['Name']='/'+sc['docker']['containerPrefix']+'one'
                info['Mounts'][1]['Source']=str(Path(sc['workspaceRoot'])/'scope')
                return json.dumps(info)
            if label=='sandbox-boundary': return self.ref['input_sha256']+'  /agent/input.log'
            if label.startswith('stop-'): return 'abcdef012345'
            raise AssertionError(label)
        with patch.object(p,'run_command',side_effect=fake_command), patch.object(p.os,'geteuid',return_value=1001), patch.object(p.os,'getuid',return_value=1001), patch.object(p.os,'getgid',return_value=1001), patch.object(p.Path,'home',return_value=self.root), patch.object(p.shutil,'which',return_value='/usr/bin/docker'), patch.dict(os.environ,{'DOCKER_HOST':'','DOCKER_CONTEXT':''}):
            code=p.main(['--prepared',str(prepared),'--openclaw-bin',str(cli)])
        self.assertEqual(code,0)
        result=p.read_json(next(prepared.glob('native-preflight-*/result.json')))
        self.assertEqual(result['status'],'PREFLIGHT_PASS_NOT_MODEL_EVAL')
        self.assertEqual(result['model_inference_calls'],0)
        self.assertFalse(result['agent_task_executed'])
        self.assertTrue(any(label.startswith('stop-') for label,_ in seen))

    def test_help_does_not_require_spark_or_root(self):
        result=subprocess.run([sys.executable,str(FILE),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertIn('--prepared',result.stdout)

    def test_subprocess_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            p.run_command([sys.executable,'-c','import time;time.sleep(5)'],dict(os.environ),self.root,self.out,'timeout',.05)

if __name__=='__main__': unittest.main()
