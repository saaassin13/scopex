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

    def test_structured_encoder_facts_lead_with_real_anomaly_events(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='business_facts:encoder-health',
            raw=json.dumps({
                'scopex_role': 'business_facts',
                'source': 'encoder-health',
                'facts': {
                    'samples_in_window': 35244,
                    'invalid_samples': 0,
                    'median_sample_dt_ms': 21.0,
                    'sampling_gap_count': 0,
                    'negative_steps_observed': 231,
                    'small_negative_groups_ignored': 228,
                    'anomaly_event_count': 3,
                    'reverse_glitch_candidate_count': 2,
                    'reverse_interval_candidate_count': 1,
                    'reverse_step_candidate_count': 0,
                    'positive_spike_candidate_count': 0,
                },
                'top_candidates': [
                    {'type': 'reverse_glitch_candidate', 'start': '2026-09-14 03:12:01:120', 'pulse_delta': -52},
                    {'type': 'reverse_interval_candidate', 'start': '2026-09-14 03:28:41:420', 'pulse_delta': -88},
                ],
            }, ensure_ascii=False),
            metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'},
        )
        text = compose_product_answer(one_fact_claim(), catalog).to_dict()['conclusion'][0]['text']
        self.assertIn('时间窗内共 35244 个编码器采样', text)
        self.assertIn('发现 3 个显著异常事件', text)
        self.assertIn('回退-恢复毛刺 2', text)
        self.assertIn('连续回退区间 1', text)
        self.assertIn('03:12:01:120', text)
        self.assertNotIn('231 次异常', text)

    def test_structured_encoder_zero_events_says_no_significant_glitch(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='business_facts:encoder-health',
            raw=json.dumps({
                'scopex_role': 'business_facts', 'source': 'encoder-health',
                'facts': {'samples_in_window': 1000, 'invalid_samples': 0, 'median_sample_dt_ms': 20.0, 'sampling_gap_count': 0, 'anomaly_event_count': 0},
            }),
            metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'},
        )
        text = compose_product_answer(one_fact_claim(), catalog).to_dict()['conclusion'][0]['text']
        self.assertIn('未发现显著毛刺、回退或异常跳变候选', text)

    def test_system_health_business_facts_are_human_readable(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='business_facts:system-health',
            raw=json.dumps({
                'scopex_role': 'business_facts', 'source': 'system-health',
                'facts': {
                    'captured_at': '2026-09-14T20:22:20+08:00',
                    'cpu_util_percent': 11.22,
                    'cpu_count': 20,
                    'memory_total_gb': 121.7,
                    'memory_used_gb': 57.735,
                    'memory_available_gb': 63.96,
                    'disk_root_used_percent': 49.7,
                    'disk_root_free_gb': 2030.7,
                    'gpu_count': 1,
                    'gpu_util_percent_max': 27.2,
                },
            }),
            metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'},
        )
        text = compose_product_answer(one_fact_claim(), catalog).to_dict()['conclusion'][0]['text']
        self.assertIn('当前 CPU 利用率 11.22%', text)
        self.assertIn('已用 57.74 GB', text)
        self.assertIn('可用 63.96 GB', text)
        self.assertIn('根磁盘已用 49.7%', text)
        self.assertNotIn('"cpu_util_percent"', text)

    def test_structured_fact_is_preferred_over_raw_lines_when_claim_has_both(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='business_facts:encoder-health',
            raw=json.dumps({
                'scopex_role': 'business_facts', 'source': 'encoder-health',
                'facts': {'samples_in_window': 100, 'invalid_samples': 0, 'median_sample_dt_ms': 20.0, 'sampling_gap_count': 0, 'anomaly_event_count': 0},
            }),
            metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'},
        )
        catalog.add(source='/agent-data/logs/app.log', raw='2026-09-14 raw line', metadata={'evidence_type': 'file_line'})
        claims = claim_set_from_dict({
            'claims': [{'id': 'C1', 'kind': 'fact', 'topic': 'ignored', 'evidence_refs': ['E1', 'E2'], 'confidence': 'high', 'scope': 'time_window', 'relation': 'observed'}],
            'summary_claim_ids': ['C1'],
        })
        text = compose_product_answer(claims, catalog).to_dict()['conclusion'][0]['text']
        self.assertIn('时间窗内共 100 个编码器采样', text)
        self.assertNotIn('scopex_role', text)
        self.assertNotIn('2026-09-14 raw line', text)

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
