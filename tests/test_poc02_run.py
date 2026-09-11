"""Real-run wrapper tests, with local fake upstream; no OpenClaw/Docker/GPU."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / 'scripts/poc02_run.py'
spec = importlib.util.spec_from_file_location('real_runner', FILE)
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.ref = {'model': 'fixture-model', 'base_url': 'http://127.0.0.1:8000/v1',
                    'staging_directory': str(self.root / 'input'), 'sla_s': 120, 'timeout_s': 180,
                    'request': {'temperature': .1, 'max_tokens': 2048,
                                'chat_template_kwargs': {'enable_thinking': False}}}
        self.cfg = {'logging': {'file': 'old.log'}, 'models': {'providers': {'vllm': {
            'baseUrl': 'http://127.0.0.1:12345/v1', 'apiKey': 'OLD_TEMP',
            'models': [{'contextWindow': 32768}]}}}, 'agents': {
            'defaults': {'workspace': self.ref['staging_directory'],
                'sandbox': {'workspaceAccess': 'ro', 'workspaceRoot': 'old',
                            'docker': {'image': 'sha256:test', 'containerPrefix': 'old'}}},
            'entries': {'oldid': {'default': True}}},
            'tools': {'allow': ['read','exec','process']}}

    def test_loopback_url(self):
        self.assertEqual(p.endpoint('http://localhost:8000/v1'), ('http','127.0.0.1',8000))
        self.assertEqual(p.endpoint('https://[::1]/v1/'), ('https','::1',443))

    def test_remote_url_and_credentials_rejected(self):
        for u in ('http://example.com/v1','http://127.0.0.1.evil/v1',
                  'http://key@127.0.0.1/v1','http://127.0.0.1/v1?k=x','http://127.0.0.1/v2'):
            with self.subTest(u=u), self.assertRaises(ValueError): p.endpoint(u)

    def test_relocation_is_only_operational(self):
        old = copy.deepcopy(self.cfg)
        cfg = p.real_config(self.cfg,self.ref,self.root/'new',self.root/'out',
                            'http://127.0.0.1:9999/v1','NEW_TEMP','newid')
        self.assertEqual(self.cfg, old)
        self.assertEqual(p.config_signature(cfg), p.config_signature(old))
        self.assertEqual(cfg['agents']['defaults']['sandbox']['workspaceAccess'],'ro')

    def test_relocation_refuses_new_input(self):
        with self.assertRaises(ValueError):
            p.real_config(self.cfg,{**self.ref,'staging_directory':'/other'},self.root,self.root,'x','y','z')

    def test_behavioral_change_detected(self):
        cfg = copy.deepcopy(self.cfg); cfg['tools']['allow'].append('gateway')
        self.assertNotEqual(p.config_signature(cfg),p.config_signature(self.cfg))

    def test_json_duplicate_nan_denied(self):
        for text in ('{"x":1,"x":2}','{"x":NaN}'):
            with self.assertRaises(ValueError): p.load(text)

    def test_grade_strict_and_content(self):
        self.assertTrue(p.grade('{ "records": [] }',{'records':[]})['strict_match'])
        g=p.grade('```json\n{"records":[]}\n```',{'records':[]})
        self.assertFalse(g['strict_match']); self.assertTrue(g['format_only'])

    def test_wrong_values_and_bool(self):
        self.assertFalse(p.grade('{"n":true}',{'n':1})['content_match'])
        self.assertFalse(p.grade('{"v":[2,1]}',{'v':[1,2]})['content_match'])

    def test_unparseable_not_content_wrong(self):
        self.assertIsNone(p.grade('some text {}',{})['content_match'])

    def test_last_payload_only(self):
        text=json.dumps({'payloads':[{'text':'correct earlier'},{'text':'last wrong'}],'meta':{}})
        self.assertEqual(p.extract_answer(text),('last wrong',2))

    def test_reasoning_not_answer(self):
        self.assertEqual(p.extract_answer(json.dumps({'payloads':[{'text':'hidden','isReasoning':True},
            {'text':'{}'}]})),('{}',1))

    def test_incomplete_and_fallback_rejected(self):
        for meta in ({'error':{'kind':'incomplete_turn'}},{'aborted':True},
                     {'executionTrace':{'fallbackUsed':True}}):
            with self.subTest(meta=meta), self.assertRaises(ValueError):
                p.extract_answer(json.dumps({'payloads':[{'text':'{}'}],'meta':meta}))

    def test_replay_flag_is_preserved_but_not_completion_failure(self):
        text=json.dumps({'payloads':[{'text':'{}'}], 'meta':{'replayInvalid':True}})
        self.assertEqual(p.extract_answer(text), ('{}',1))
        self.assertTrue(p.cli_outcome(text)['warnings'])
        self.assertFalse(p.cli_outcome(text)['automatic_replay_allowed'])

    def test_missing_cli_envelope_rejected(self):
        for text in ('{}','[]','banner\n{"payloads":[]}', '{"payloads":[{"text":"error","isError":true}]}'):
            with self.subTest(text=text), self.assertRaises(ValueError): p.extract_answer(text)

    def trace(self,name='read',args=None,ret='line1\nline2\n',cid='call1',tool_id='call1'):
        args = args if args is not None else {'path':'/agent/input.log'}
        p.save(self.root/'wire-01-request.json',{'messages':[
            {'role':'assistant','tool_calls':[{'id':cid,'function':{'name':name,'arguments':json.dumps(args)}}]},
            {'role':'tool','tool_call_id':tool_id,'content':ret}]})

    def test_full_read_evidence(self):
        self.trace()
        e=p.evidence(self.root,b'line1\nline2\n')
        self.assertEqual(e['status'],'verified_full_read')
        self.assertEqual(e['tool_calls_seen'],1)

    def test_exec_requires_review_even_correct_output(self):
        self.trace('exec',{'command':'cat /agent/input.log'})
        self.assertEqual(p.evidence(self.root,b'line1\nline2\n')['status'],'review_required')

    def test_mismatched_id_not_read_evidence(self):
        self.trace(tool_id='other')
        self.assertEqual(p.evidence(self.root,b'line1\nline2\n')['status'],'review_required')

    def test_partial_read_not_full_evidence(self):
        self.trace(ret='line1\n')
        self.assertEqual(p.evidence(self.root,b'line1\nline2\n')['status'],'review_required')

    def test_no_calls_is_missing(self):
        self.assertEqual(p.evidence(self.root,b'text')['status'],'missing')

    def test_replayed_history_not_duplicate(self):
        self.trace()
        (self.root/'wire-02-request.json').write_bytes((self.root/'wire-01-request.json').read_bytes())
        self.assertEqual(p.evidence(self.root,b'line1\nline2\n')['same_argument_extra_calls'],0)

    def test_read_record_cap_and_symlink(self):
        f=self.root/'data'; f.write_text('{}')
        alias=self.root/'alias'; alias.symlink_to(f)
        with self.assertRaises(ValueError): p.read(alias)

    def proxy(self, stream=False, mismatch=False, gate_fail=False, bad_auth=False):
        received=[]; gate_calls=[]
        response = (b'data: {"choices":[{"delta":{"content":"REAL_FROM_TEST_UPSTREAM"},"finish_reason":null}]}\n\n'
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n') if stream else b'{"upstream": "REAL_FROM_TEST_UPSTREAM"}'
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_POST(self):
                received.append((self.rfile.read(int(self.headers['Content-Length'])),self.headers.get('Authorization')))
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream' if stream else 'application/json')
                self.send_header('Content-Length',str(len(response))); self.end_headers()
                self.wfile.write(response)
        up=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        ut=threading.Thread(target=up.serve_forever,daemon=True);ut.start()
        class Native:
            @staticmethod
            def check_wire(payload,ref): return {'problems':['mismatch'] if mismatch else []}
        def gate():
            gate_calls.append(True)
            if gate_fail: raise ValueError('bad sandbox')
        ref={**self.ref,'base_url':f'http://127.0.0.1:{up.server_port}/v1'}
        server=p.Recorder(self.root,ref,'UPSTREAM_SECRET','LOCAL_SECRET',Native,gate,6)
        server.started=time.monotonic();server.deadline=server.started+5
        th=threading.Thread(target=server.serve_forever,daemon=True);th.start()
        body=b'{ "model" : "fixture-model", "stream": '+(b'true' if stream else b'false')+b', "messages": [] }'
        try:
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',data=body,
                headers={'Authorization':'Bearer '+('WRONG' if bad_auth else 'LOCAL_SECRET'),'Content-Type':'application/json'})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            if mismatch or gate_fail or bad_auth:
                with self.assertRaises(urllib.error.HTTPError): opener.open(req,timeout=3)
                self.assertEqual(received,[])
            else:
                with opener.open(req,timeout=3) as r: actual=r.read()
                self.assertEqual(actual,response)
                self.assertEqual(received,[(body,'Bearer UPSTREAM_SECRET')])
                self.assertEqual((self.root/'wire-01-request.json').read_bytes(),body)
                self.assertEqual((self.root/'wire-01-response.bin').read_bytes(),response)
                self.assertEqual(gate_calls,[True])
                self.assertNotIn('UPSTREAM_SECRET', ''.join(f.read_text() for f in self.root.glob('wire-*.json')))
        finally:
            server.cancel();server.shutdown();th.join();server.server_close()
            up.shutdown();ut.join();up.server_close()

    def test_http_exact_body_forward(self): self.proxy()
    def test_sse_forward_without_synthetic_content(self): self.proxy(stream=True)
    def test_wire_mismatch_never_hits_upstream(self): self.proxy(mismatch=True)
    def test_boundary_failure_never_hits_upstream(self): self.proxy(gate_fail=True)
    def test_receiver_auth_denied(self): self.proxy(bad_auth=True)

    def test_models_redirect_not_followed(self):
        class Response:
            status=302
            def read(self,*a): return b''
        class Conn:
            def request(self,*a,**k): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(p,'connect',return_value=Conn()), self.assertRaises(ValueError): p.model_info(self.ref,'')

    def test_models_requires_exact_id(self):
        class Response:
            status=200
            def read(self,*a): return b'{"data":[{"id":"wrong"}]}'
        class Conn:
            def request(self,*a,**k): pass
            def getresponse(self): return Response()
            def close(self): pass
        with patch.object(p,'connect',return_value=Conn()),self.assertRaises(ValueError):p.model_info(self.ref,'')

    def test_help_needs_no_native_install(self):
        result=subprocess.run([sys.executable,str(FILE),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertIn('--preflight',result.stdout)

    def test_failed_preflight_rejected_before_reading_data(self):
        p.save(self.root/'result.json',{'status':'PREFLIGHT_FAILED'})
        p.save(self.root/'openclaw.json',self.cfg)
        with self.assertRaises(ValueError):p.bundle(self.root)

    def complete_run(self, proof=False):
        prepared=self.root/'prepared';prepared.mkdir()
        pf=prepared/'native-preflight-fixture';pf.mkdir()
        stage=Path(self.ref['staging_directory']);stage.mkdir();(stage/'input.log').write_bytes(b'line1\nline2\n')
        baseline=self.root/'baseline';baseline.mkdir();suite=self.root/'suite';suite.mkdir()
        old_cfg={**self.ref,'api_key_env':'SCOPEX_TEST_MISSING_KEY'}
        p.save(baseline/'config.json',old_cfg)
        cases=[{'mode':'tool','prompt':'Return JSON.','expected':{'n':1}}]
        p.save(baseline/'cases.json',cases);p.save(suite/'cases.json',cases)
        ref={**self.ref,'baseline_run':str(baseline),'suite':str(suite),
             'baseline_config_sha256':p.sha((baseline/'config.json').read_bytes()),
             'input_sha256':p.sha(b'line1\nline2\n'),'prompt_sha256':p.sha(b'Return JSON.'),
             'expected_sha256':p.sha(p.canonical({'n':1}).encode())}
        p.save(prepared/'reference.json',ref)
        p.save(pf/'result.json',{'status':'PREFLIGHT_PASS_NOT_MODEL_EVAL','errors':[],
            'native_exec_marker_returned':True,'sandbox':{'problems':[]},'runtime_directory':str(self.root/'oldruntime')})
        p.save(pf/'openclaw.json',self.cfg)
        cli=self.root/'openclaw';cli.touch();cli.chmod(0o700)
        test=self
        class Native:
            @staticmethod
            def check_input(r): return stage
            @staticmethod
            def clean_env(runtime,config,host):return {'OPENCLAW_CONFIG_PATH':str(config)}
            @staticmethod
            def build_config(*args):return test.cfg
            @staticmethod
            def run_command(argv,env,cwd,out,label,seconds=25):
                if label=='real-config-file':return env['OPENCLAW_CONFIG_PATH']
                if label=='real-version':return 'OpenClaw 2026.9.2 (3928bad)'
                if label=='real-sandbox-list': return 'abcdef012345'
                if label=='real-sandbox-inspect':
                    cfg=p.read(Path(env['OPENCLAW_CONFIG_PATH']))
                    prefix=cfg['agents']['defaults']['sandbox']['docker']['containerPrefix']
                    return json.dumps({'Name':'/'+prefix+'workspace-test','Image':'sha256:test'})
                if label=='real-boundary-read': return ref['input_sha256']+' /agent/input.log'
                return ''
            @staticmethod
            def check_container(*args):return {'problems':[]} # native helper simulated here

        def task(cli,config,env,runtime,out,*args):
            server=args[-1]
            if proof:
                server.gate()
                p.save(out/'wire-01-request.json',{'messages':[
                    {'role':'assistant','tool_calls':[{'id':'callread','function':{
                        'name':'read','arguments':'{"path":"/agent/input.log"}'}}]},
                    {'role':'tool','tool_call_id':'callread','content':'line1\nline2\n'}]})
            server.records=[{'forwarded':True,'http_status':200,'response_complete':True}]
            # Gate is mocked here; HTTP and sandbox-before-forward tested separately.
            (out/'agent.stdout.txt').write_text(json.dumps({'payloads':[{'text':'{"n":1}'}]}))
            return {'returncode':0,'stop':None,'wall_s':1.0}
        # Do not claim this mock verifies the actual native container/helper behavior.
        with patch.object(p.os,'geteuid',return_value=1001),patch.object(p.Path,'home',return_value=self.root), \
             patch.object(p.shutil,'which',return_value='/usr/bin/docker'),patch.object(p,'model_info',return_value={'id':'fixture-model','max_model_len':32768}), \
             patch.object(p,'run_task',side_effect=task),patch.dict(p.os.environ,{'DOCKER_HOST':'unix:///var/run/docker.sock','DOCKER_CONTEXT':''}):
            code=p.main(['--preflight',str(pf),'--openclaw-bin',str(cli)],native=Native)
        self.assertEqual(code,0 if proof else 1)
        result=p.read(next(prepared.glob('real-a-*/result.json')))
        self.assertEqual(result['strict_correct'],proof)
        self.assertFalse(result['synthetic_response'])
        self.assertEqual(result['evidence']['status'],'verified_full_read' if proof else 'missing')

    def test_completed_answer_without_gate_cannot_pass(self): self.complete_run(False)
    def test_positive_path_with_fake_native_helpers(self): self.complete_run(True)

    def test_response_usage_is_observed_not_invented(self):
        f=self.root/'response.bin'
        f.write_bytes(b'data: {"choices":[],"usage":{"completion_tokens":17}}\n\ndata: [DONE]\n\n')
        self.assertEqual(p.response_metadata(f,'text/event-stream')['usage']['completion_tokens'],17)
        f.write_bytes(b'{"choices":[]}')
        self.assertIsNone(p.response_metadata(f,'application/json')['usage'])

if __name__=='__main__':unittest.main()
