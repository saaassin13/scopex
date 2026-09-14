from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.claims import claim_set_from_dict
from scopex.finalizer.client import FinalizerResponse
from scopex.finalizer.report import ConstrainedReportComposer, validate_report_payload
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import RuntimeAudit


class FakeClient:
    def __init__(self, content: str, *, finish: str = 'stop', done: bool = True) -> None:
        self.content = content
        self.finish = finish
        self.done = done
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return FinalizerResponse(
            content=self.content,
            headers_s=0.01,
            first_content_s=0.02,
            elapsed_s=0.03,
            finish_reasons=(self.finish,),
            done_seen=self.done,
            usage={'prompt_tokens': 10, 'completion_tokens': 20},
        )


def fixture():
    catalog = EvidenceCatalog('task-1', 'agent:sx:task-1')
    catalog.add(
        source='business_facts:encoder-health',
        raw=json.dumps({
            'scopex_role': 'business_facts',
            'schema': 3,
            'source': 'encoder-health',
            'window': {'start': '2026-09-14 12:00:00', 'end': '2026-09-14 13:00:00'},
            'facts': {
                'samples_in_window': 200859,
                'sampling_gap_count': 0,
                'anomaly_event_count': 2,
                'reverse_glitch_candidate_count': 1,
                'reverse_interval_candidate_count': 1,
            },
            'top_candidates': [
                {
                    'type': 'reverse_glitch_candidate',
                    'start': '2026-09-14 12:13:21:120',
                    'count_before': 812345,
                    'count_end': 812291,
                    'pulse_delta': -54,
                    'recovery_pulses_next_3': 71,
                    'recovered': True,
                }
            ],
        }, ensure_ascii=False),
        metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'},
    )
    catalog.add(
        source='/agent-data/logs/CowDisinfect.log',
        raw='2026-09-14 12:13:21:120 [INFO] EncoderVal [812291], TurnTableSpeed [-10.0 mm/s]',
        metadata={'evidence_type': 'file_line', 'line_number': 42},
    )
    claims = claim_set_from_dict({
        'claims': [
            {
                'id': 'C1', 'kind': 'fact', 'topic': '存在两个显著编码器事件',
                'evidence_refs': ['E1'], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed',
            },
            {
                'id': 'C2', 'kind': 'inference', 'topic': '其中一次符合回退恢复毛刺形态',
                'evidence_refs': ['E1', 'E2'], 'confidence': 'medium', 'scope': 'event', 'relation': 'temporal_association',
            },
            {
                'id': 'C3', 'kind': 'unknown', 'topic': '具体物理原因未确认',
                'evidence_refs': ['E1'], 'confidence': 'unknown', 'scope': 'component', 'relation': 'unknown',
            },
        ],
        'summary_claim_ids': ['C1', 'C2', 'C3'],
    })
    return catalog, claims


class ReportComposerTests(unittest.TestCase):
    def test_valid_report_uses_existing_claims_and_evidence(self):
        catalog, claims = fixture()
        payload = {
            'version': 1,
            'conclusion': {
                'text': '12点到13点编码器存在2个显著异常事件。',
                'claim_ids': ['C1'], 'evidence_refs': ['E1'],
            },
            'facts': [
                {
                    'text': '共分析200859个采样，未发现采样缺口，发现2个显著事件。',
                    'claim_ids': ['C1'], 'evidence_refs': ['E1'],
                }
            ],
            'possibilities': [
                {
                    'text': '其中一次事件符合回退后快速恢复的毛刺形态，但未证明物理原因。',
                    'claim_ids': ['C2'], 'evidence_refs': ['E1', 'E2'],
                }
            ],
            'next_steps': [
                {
                    'text': '围绕该事件检查更小时间窗的raw/filtered与reset上下文。',
                    'claim_ids': ['C3'], 'evidence_refs': [],
                }
            ],
            'limitations': [
                {
                    'text': '当前证据不能确定异常的物理原因。',
                    'claim_ids': ['C3'], 'evidence_refs': ['E1'],
                }
            ],
        }
        client = FakeClient(json.dumps(payload, ensure_ascii=False))
        result = ConstrainedReportComposer(client, model='local').run(
            user_request='检查12点编码器是否异常', claims=claims, catalog=catalog,
        )
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.report.conclusion.claim_ids, ('C1',))
        self.assertEqual(result.report.facts[0].evidence_refs, ('E1',))
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn('tools', client.calls[0])
        prompt = client.calls[0]['user_prompt']
        self.assertIn('2026-09-14 12:13:21:120', prompt)
        self.assertIn('pulse_delta', prompt)

    def test_unknown_claim_or_unowned_evidence_is_rejected(self):
        _, claims = fixture()
        payload = {
            'version': 1,
            'conclusion': {'text': '异常', 'claim_ids': ['C404'], 'evidence_refs': ['E99']},
            'facts': [], 'possibilities': [], 'next_steps': [], 'limitations': [],
        }
        report, errors = validate_report_payload(payload, claims)
        self.assertIsNone(report)
        self.assertTrue(any('unknown_claim' in error for error in errors))

    def test_inference_cannot_be_published_as_fact(self):
        _, claims = fixture()
        payload = {
            'version': 1,
            'conclusion': {'text': '存在异常', 'claim_ids': ['C1'], 'evidence_refs': ['E1']},
            'facts': [
                {'text': '毛刺原因已经确认', 'claim_ids': ['C2'], 'evidence_refs': ['E2']},
            ],
            'possibilities': [], 'next_steps': [], 'limitations': [],
        }
        report, errors = validate_report_payload(payload, claims)
        self.assertIsNone(report)
        self.assertIn('facts[0].fact_requires_observed', errors)

    def test_observed_fact_cannot_be_smuggled_into_possibilities(self):
        _, claims = fixture()
        payload = {
            'version': 1,
            'conclusion': {'text': '存在异常', 'claim_ids': ['C1'], 'evidence_refs': ['E1']},
            'facts': [],
            'possibilities': [
                {'text': '可能异常', 'claim_ids': ['C1'], 'evidence_refs': ['E1']},
            ],
            'next_steps': [], 'limitations': [],
        }
        report, errors = validate_report_payload(payload, claims)
        self.assertIsNone(report)
        self.assertIn('possibilities[0].requires_non_observed_claim', errors)

    def test_parse_failure_is_nonfatal_report_failure(self):
        catalog, claims = fixture()
        result = ConstrainedReportComposer(FakeClient('not-json'), model='local').run(
            user_request='检查', claims=claims, catalog=catalog,
        )
        self.assertFalse(result.valid)
        self.assertIsNone(result.report)
        self.assertTrue(result.parse_error)

    def test_runtime_audit_attaches_valid_report_without_removing_fallback(self):
        catalog, claims = fixture()
        payload = {
            'version': 1,
            'conclusion': {'text': '发现异常。', 'claim_ids': ['C1'], 'evidence_refs': ['E1']},
            'facts': [{'text': '发现2个显著事件。', 'claim_ids': ['C1'], 'evidence_refs': ['E1']}],
            'possibilities': [], 'next_steps': [], 'limitations': [],
        }
        composer = ConstrainedReportComposer(FakeClient(json.dumps(payload, ensure_ascii=False)), model='local')
        result = composer.run(user_request='检查', claims=claims, catalog=catalog)
        with tempfile.TemporaryDirectory() as td:
            store = AuditStore(Path(td))
            audit = RuntimeAudit(store, 'task-1')
            store.write_json('task-1', 'result.json', {'valid': True, 'answer': {'version': 3}})
            audit.persist_report_result(result)
            persisted = store.read_json('task-1', 'result.json')
            self.assertEqual(persisted['answer']['version'], 3)
            self.assertEqual(persisted['report']['conclusion']['text'], '发现异常。')
            self.assertTrue((Path(td) / 'task-1' / 'report.json').is_file())
            self.assertTrue((Path(td) / 'task-1' / 'report-meta.json').is_file())


if __name__ == '__main__':
    unittest.main()
