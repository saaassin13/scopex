"""v2 regression: synthetic records only. No real model/native tools are run."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import poc02_run as p
import poc02_review as r


def envelope(meta=None, text='{"records":[]}'):
    return json.dumps({'meta': meta or {}, 'payloads': [{'text': text}]})


def pair(cid, offset, result, name='read', result_id=None, path='/agent/input.log'):
    args = {'path': path, 'offset': offset} if name == 'read' else {'command': 'echo not executed'}
    return [{'role': 'assistant', 'tool_calls': [{'id':cid, 'function':{'name':name,'arguments':json.dumps(args)}}]},
            {'role':'tool','tool_call_id':result_id or cid,'content':result}]


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.source=b'first  \nsecond\nsecond\nlast\n'
        self.pages=pair('a',1,'first  \nsecond\n\n[Read output capped at 32KB for this call. Use offset=3 to continue.]')+pair('b',3,'second\nlast\n')

    def test_replay_warning_not_failure(self):
        out=p.cli_outcome(envelope({'replayInvalid':True,'livenessState':'working'}))
        self.assertEqual(out['blockers'],[]); self.assertTrue(out['warnings'])
        self.assertFalse(out['automatic_replay_allowed'])

    def test_errors_aborts_fallback_still_fail(self):
        for flags in ({'error':{}},{'aborted':True},{'timeoutPhase':'model'},
                      {'timedOut':True},{'executionTrace':{'fallbackUsed':True}}):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                p.extract_answer(envelope({'replayInvalid':True,**flags}))

    def test_pending_liveness_blocked(self):
        for state in ('abandoned','blocked','paused'):
            with self.subTest(state=state), self.assertRaises(ValueError):
                p.extract_answer(envelope({'livenessState':state}))

    def test_bad_flags_types_not_silently_accepted(self):
        for field in ('aborted','replayInvalid'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                p.extract_answer(envelope({field:'false'}))

    def test_malformed_meta(self):
        for meta in ([], 'text'):
            with self.assertRaises(ValueError): p.extract_answer(json.dumps({'meta':meta,'payloads':[{'text':'{}'}]}))

    def test_no_final_text_still_fails(self):
        with self.assertRaises(ValueError): p.extract_answer(envelope({'replayInvalid':True},''))

    def test_last_payload_not_best_payload(self):
        obj={'payloads':[{'text':'{"records":[]}'},{'text':'wrong'}],'meta':{'replayInvalid':True}}
        self.assertEqual(p.extract_answer(json.dumps(obj)),('wrong',2))

    def test_full_paged_coverage(self):
        got=p.read_coverage(self.pages,self.source)
        self.assertEqual(got['covered_lines'],4);self.assertEqual(got['status'],'verified_paged_read')

    def test_missing_page(self):
        self.assertEqual(p.read_coverage(self.pages[:2],self.source)['status'],'review_required')

    def test_wrong_result_id(self):
        pages=copy.deepcopy(self.pages);pages[-1]['tool_call_id']='wrong'
        self.assertEqual(p.read_coverage(pages,self.source)['covered_lines'],2)

    def test_wrong_page_offset(self):
        pages=self.pages[:2]+pair('b',2,'second\nlast\n')
        self.assertEqual(p.read_coverage(pages,self.source)['status'],'review_required')

    def test_footer_offset_must_match(self):
        pages=copy.deepcopy(self.pages);pages[1]['content']=pages[1]['content'].replace('offset=3','offset=4')
        self.assertEqual(p.read_coverage(pages,self.source)['status'],'review_required')

    def test_duplicate_source_lines_not_collapsed(self):
        pages=pair('a',1,'first  \nsecond\nlast\n')
        self.assertNotEqual(p.read_coverage(pages,self.source)['status'],'verified_paged_read')

    def test_trailing_whitespace_not_silently_trimmed(self):
        pages=copy.deepcopy(self.pages);pages[1]['content']=pages[1]['content'].replace('first  ','first')
        self.assertEqual(p.read_coverage(pages,self.source)['status'],'review_required')

    def test_eof_error_not_evidence_but_does_not_erase_good_reads(self):
        pages=self.pages+pair('c',6,'Offset 6 is beyond end of file (4 lines total).')
        got=p.read_coverage(pages,self.source)
        self.assertTrue(got['pages'][-1]['past_eof']);self.assertEqual(got['status'],'verified_paged_read')

    def test_exec_return_cannot_spoof_read(self):
        pages=pair('exec',1,self.source.decode(),name='exec')
        self.assertEqual(p.read_coverage(pages,self.source)['covered_lines'],0)

    def test_source_path_must_match(self):
        pages=pair('a',1,self.source.decode(),path='/different')
        self.assertEqual(p.read_coverage(pages,self.source)['covered_lines'],0)

    def test_conflicting_history_requires_review(self):
        pages=self.pages+pair('a',1,'different')
        self.assertEqual(p.read_coverage(pages,self.source)['status'],'review_required')

    def test_repeated_history_not_duplicated(self):
        self.assertEqual(p.read_coverage(self.pages*3,self.source)['covered_lines'],4)

    def test_structured_text_and_crlf(self):
        pages=pair('a',1,[{'type':'text','text':self.source.decode().replace('\n','\r\n')}])
        self.assertEqual(p.read_coverage(pages,self.source)['status'],'verified_paged_read')

    def test_original_task_text_unchanged_by_default(self):
        self.assertEqual(p.task_text('KEEP\nRULES'), 'KEEP\nRULES\n需要读取的文件：/agent/input.log\n')

    def test_strategy_is_explicit_and_no_case_answers(self):
        base=p.task_text('KEEP'); changed=p.task_text('KEEP','filter-first')
        self.assertTrue(changed.startswith(base));self.assertNotEqual(changed,base)
        for secret in ('mainwindow','124','213','expected.json','grep -n'):
            self.assertNotIn(secret, p.FILTER_FIRST_GUIDANCE)
        with self.assertRaises(ValueError): p.task_text('KEEP','unknown')

    def response(self, answer='{"records":[]}', finish='stop'):
        ev={'choices':[{'index':0,'delta':{'content':answer},'finish_reason':None}]}
        end={'choices':[{'index':0,'delta':{},'finish_reason':finish}]}
        return ('data: '+json.dumps(ev)+'\n\ndata: '+json.dumps(end)+'\n\ndata: [DONE]\n\n').encode()

    def test_sse_reconstruction(self):
        self.assertEqual(r.final_response(self.response(),'text/event-stream'),'{'+'"records":[]}')

    def test_length_or_incomplete_stream_blocked(self):
        for raw in (self.response(finish='length'),self.response().replace(b'data: [DONE]\n\n',b'')):
            with self.assertRaises(ValueError): r.final_response(raw,'text/event-stream')

    def test_nonstream_reconstruction(self):
        raw=json.dumps({'choices':[{'index':0,'message':{'content':'{}'},'finish_reason':'stop'}]}).encode()
        self.assertEqual(r.final_response(raw,'application/json'),'{}')

    def fixture(self):
        run=self.root/'run';run.mkdir(); stage=self.root/'input';stage.mkdir()
        (stage/'input.log').write_bytes(self.source)
        ref={'staging_directory':str(stage),'input_sha256':p.sha(self.source),'sla_s':120}
        raw=self.response(); body=json.dumps({'messages':self.pages}).encode()
        (run/'wire-01-request.json').write_bytes(body);(run/'wire-01-response.bin').write_bytes(raw)
        (run/'agent.stdout.txt').write_text(envelope({'error':None,'aborted':False,'replayInvalid':True,
            'livenessState':'working','executionTrace':{'fallbackUsed':False}}))
        row={'index':1,'forwarded':True,'http_status':200,'response_complete':True,'wire':{'problems':[]},
             'request_sha256':p.sha(body),'response_bytes':len(raw),'content_type':'text/event-stream'}
        result={'preflight':str(self.root/'pf'),'input_sha256':ref['input_sha256'],'returncode':0,'stop':None,
                'agent_task_attempted':True,'synthetic_response':False,'sandbox':{'problems':[]},'wall_s':133.3608,
                'wire':[row],'errors':[r.LEGACY_ERROR],'status':'NOT_COMPLETED'}
        p.save(run/'result.json',result)
        return run,ref,result

    def test_review_reclassifies_without_writing_or_inference(self):
        run,ref,_=self.fixture()
        originals={str(f):f.read_bytes() for f in self.root.rglob('*') if f.is_file()}
        with patch.object(p,'bundle',return_value=(ref,{}, {'expected':{'records':[]}},'')), \
             patch.object(p,'connect',side_effect=AssertionError('no network')), \
             patch.object(p.subprocess,'Popen',side_effect=AssertionError('no subprocess')):
            got=r.review(run)
        self.assertEqual(got['review_status'],'CORRECT_OVER_SLA')
        self.assertTrue(got['strict_correct']);self.assertFalse(got['within_sla'])
        self.assertEqual(originals,{str(f):f.read_bytes() for f in self.root.rglob('*') if f.is_file()})

    def test_other_error_not_erased(self):
        run,ref,result=self.fixture();result['errors'].append('owned sandbox stop failed: fixture')
        (run/'result.json').write_text(json.dumps(result))
        with patch.object(p,'bundle',return_value=(ref,{}, {'expected':{'records':[]}},'')):
            self.assertEqual(r.review(run)['review_status'],'REVIEW_BLOCKED')

    def test_replay_warning_does_not_hide_timeout(self):
        run,ref,result=self.fixture();result['stop']='timeout'
        (run/'result.json').write_text(json.dumps(result))
        with patch.object(p,'bundle',return_value=(ref,{}, {'expected':{'records':[]}},'')):
            self.assertFalse(r.review(run)['strict_correct'])

    def test_hash_change_stops_review(self):
        run,ref,_=self.fixture();(run/'wire-01-request.json').write_text('{}')
        with patch.object(p,'bundle',return_value=(ref,{}, {'expected':{'records':[]}},'')),self.assertRaises(ValueError):
            r.review(run)

    def test_read_symlink_and_cap(self):
        path=self.root/'big';path.write_bytes(b'1234')
        with self.assertRaises(ValueError):r.bounded_bytes(path,3)
        link=self.root/'alias';link.symlink_to(path)
        with self.assertRaises(ValueError):r.bounded_bytes(link)


if __name__=='__main__':unittest.main()
