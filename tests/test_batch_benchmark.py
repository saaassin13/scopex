import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
b=module('benchmark_task_batch');r=module('replay_text_report')

class BatchMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.a={'cases_sha256':'same','dataset_id':'fixed','all_delivered':True,
                'runtime':{'scopex_commit':'same','max_active_tasks':1},'batch_elapsed_s':12}
        self.b=copy.deepcopy(self.a);self.b['runtime']['max_active_tasks']=2;self.b['batch_elapsed_s']=8
    def test_matched_durations_are_not_automatically_quality_acceptance(self):
        result=b.compare(self.a,self.b)
        self.assertEqual(result['speedup'],1.5)
        self.assertEqual(result['status'],'TIMING_ONLY_QUALITY_PENDING')
    def test_manual_quality_confirmation_still_requires_timing_gain(self):
        self.b['batch_elapsed_s']=14
        self.assertEqual(b.compare(self.a,self.b,quality_confirmed=True)['status'],'NO_SPEEDUP')
    def test_failed_task_cannot_make_a_faster_batch_pass(self):
        self.b['all_delivered']=False
        with self.assertRaises(ValueError):b.compare(self.a,self.b)
    def test_different_data_or_output_code_cannot_be_compared(self):
        for field in ('data','code'):
            second=copy.deepcopy(self.b)
            if field=='data':second['cases_sha256']='different'
            else:second['runtime']['scopex_commit']='different'
            with self.assertRaises(ValueError):b.compare(self.a,second)
    def test_loopback_only_no_credentials(self):
        for value in ('https://example.com','http://192.168.1.1:8787','http://user:password@127.0.0.1:8787'):
            with self.assertRaises(ValueError):b.endpoint(value)
    def test_cases_require_explicit_readonly_and_unique_ids(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'cases.json'
            p.write_text(json.dumps({'cases':[]}))
            with self.assertRaises(ValueError):b.load_cases(p)
            p.write_text(json.dumps({'read_only':True,'dataset_id':'fixed','cases':[{'id':'a','message':'fixed window'},{'id':'a','message':'other'}]}))
            with self.assertRaises(ValueError):b.load_cases(p)
    def test_replay_rejects_mismatched_task_identity(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/'task.json').write_text(json.dumps({'id':'t','session_key':'s','state':'FAILED'}))
            (p/'evidence.json').write_text(json.dumps({'task_id':'other','session_key':'s','items':[]}))
            with self.assertRaises(ValueError):r.load_saved(p)
    def test_replay_preserves_reference_identity(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/'task.json').write_text(json.dumps({'id':'t','session_key':'s','state':'FAILED'}))
            (p/'evidence.json').write_text(json.dumps({'task_id':'t','session_key':'s','items':[{'ref':'E1','source':'log','raw':'fact'}]}))
            task,catalog=r.load_saved(p)
            self.assertEqual(catalog.get('E1').raw,'fact')
            self.assertEqual(task['state'],'FAILED')
