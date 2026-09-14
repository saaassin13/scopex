from __future__ import annotations

import json
import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.answer import compose_product_answer
from scopex.finalizer.claims import claim_set_from_dict


def one_fact_claim(ref='E1'):
    return claim_set_from_dict({
        'claims': [
            {'id': 'C1', 'kind': 'fact', 'topic': 'ignored', 'evidence_refs': [ref], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed'},
        ],
        'summary_claim_ids': ['C1'],
    })


class ProductAnswerReadabilityTests(unittest.TestCase):
    def test_nipple_business_scalars_are_rendered_for_people(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(source='exec:stats', raw='"nipple_recognition_rate": 0.978311,', metadata={'evidence_type': 'command_line'})
        catalog.add(source='exec:stats', raw='"total_cows": 438,', metadata={'evidence_type': 'command_line'})
        catalog.add(source='exec:stats', raw='"complete_four_nipple_cows": 400,', metadata={'evidence_type': 'command_line'})
        catalog.add(source='exec:stats', raw='"3": 38,', metadata={'evidence_type': 'command_line'})
        claims = claim_set_from_dict({
            'claims': [
                {'id': 'C1', 'kind': 'fact', 'topic': 'ignored', 'evidence_refs': ['E1'], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed'},
                {'id': 'C2', 'kind': 'fact', 'topic': 'ignored', 'evidence_refs': ['E2'], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed'},
                {'id': 'C3', 'kind': 'fact', 'topic': 'ignored', 'evidence_refs': ['E3'], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed'},
                {'id': 'C4', 'kind': 'fact', 'topic': 'ignored', 'evidence_refs': ['E4'], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed'},
            ],
            'summary_claim_ids': ['C1', 'C2', 'C3', 'C4'],
        })
        answer = compose_product_answer(claims, catalog).to_dict()
        texts = [answer['conclusion'][0]['text']] + [row['text'] for row in answer['explanation']]
        joined = '；'.join(texts)
        self.assertIn('乳头识别率为 97.83%', joined)
        self.assertIn('共统计 438 头牛', joined)
        self.assertIn('其中 400 头完整识别到 4 个乳头', joined)
        self.assertIn('38 头牛最终识别到 3 个乳头', joined)

    def test_structured_nipple_facts_are_rendered_as_business_summary(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='business_facts:nipple-recognition-analysis',
            raw=json.dumps({
                'scopex_role': 'business_facts',
                'source': 'nipple-recognition-analysis',
                'summary': {
                    'total_cows': 438,
                    'complete_four_nipple_cows': 400,
                    'complete_four_nipple_rate': 400 / 438,
                    'nipple_recognition_rate': 0.978311,
                    'distribution_by_final_2d_count': {'4': 400, '3': 38},
                },
                'quality': {'unfinished_cycles_in_window': 0, 'over_four_2d_detections': 0},
            }, ensure_ascii=False),
            metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'},
        )
        text = compose_product_answer(one_fact_claim(), catalog).to_dict()['conclusion'][0]['text']
        self.assertIn('共统计 438 头牛', text)
        self.assertIn('400 头完整识别到 4 个乳头', text)
        self.assertIn('总体乳头识别率为 97.83%', text)
        self.assertIn('3个乳头 38 头', text)

    def test_structured_encoder_facts_do_not_call_every_negative_delta_anomaly(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='business_facts:encoder-health',
            raw=json.dumps({
                'scopex_role': 'business_facts',
                'source': 'encoder-health',
                'facts': {
                    'samples_in_window': 35244,
                    'valid_samples': 35244,
                    'invalid_samples': 0,
                    'median_sample_dt_ms': 21.0,
                    'sampling_gap_count': 0,
                    'negative_jump_count': 231,
                    'negative_jump_abs_p95_pulses': 14.0,
                    'negative_jump_abs_max_pulses': 52.0,
                    'negative_jump_outlier_candidate_count': 2,
                    'large_negative_jump_candidate_count': 0,
                    'positive_delta_outlier_candidate_count': 1,
                    'flat_raw_candidate_count': 0,
                },
            }, ensure_ascii=False),
            metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'},
        )
        text = compose_product_answer(one_fact_claim(), catalog).to_dict()['conclusion'][0]['text']
        self.assertIn('时间窗内共 35244 个编码器采样', text)
        self.assertIn('采样缺口候选 0 个', text)
        self.assertIn('观察到 raw 数值下降 231 次', text)
        self.assertIn('显著 raw 下降候选 2', text)
        self.assertNotIn('231 次异常', text)

    def test_multi_evidence_claim_does_not_leak_structured_json_into_product_text(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='business_facts:encoder-health',
            raw=json.dumps({
                'scopex_role': 'business_facts',
                'source': 'encoder-health',
                'facts': {
                    'samples_in_window': 200859,
                    'valid_samples': 200859,
                    'invalid_samples': 0,
                    'median_sample_dt_ms': 21.0,
                    'sampling_gap_count': 0,
                    'negative_jump_count': 1191,
                    'negative_jump_abs_p95_pulses': 33.0,
                    'negative_jump_abs_max_pulses': 72.0,
                    'negative_jump_outlier_candidate_count': 4,
                    'large_negative_jump_candidate_count': 0,
                    'positive_delta_outlier_candidate_count': 0,
                    'flat_raw_candidate_count': 2,
                },
            }, ensure_ascii=False),
            metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'},
        )
        catalog.add(
            source='/agent-data/logs/CowDisinfect.log',
            raw='2026-09-14 12:09:30:023 [INFO] EncoderVal [2365209], TurnTableSpeed [0.000 mm/s]',
            metadata={'evidence_type': 'file_line'},
        )
        claims = claim_set_from_dict({
            'claims': [
                {'id': 'C1', 'kind': 'fact', 'topic': 'summary', 'evidence_refs': ['E1'], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed'},
                {'id': 'C2', 'kind': 'fact', 'topic': 'detail', 'evidence_refs': ['E1', 'E2'], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed'},
            ],
            'summary_claim_ids': ['C1', 'C2'],
        })
        answer = compose_product_answer(claims, catalog).to_dict()
        joined = '；'.join([answer['conclusion'][0]['text']] + [row['text'] for row in answer['explanation']])
        self.assertNotIn('"scopex_role"', joined)
        self.assertNotIn('"facts"', joined)
        self.assertIn('时间窗内共 200859 个编码器采样', joined)

    def test_missing_host_snapshot_is_not_presented_as_host_measurement(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='exec:host',
            raw="ls: cannot access '/scopex-host/': No such file or directory",
            metadata={'evidence_type': 'command_line'},
        )
        claims = one_fact_claim()
        text = compose_product_answer(claims, catalog).to_dict()['conclusion'][0]['text']
        self.assertIn('宿主机资源快照当前不可用', text)
        self.assertNotIn('host is healthy', text)


if __name__ == '__main__':
    unittest.main()
