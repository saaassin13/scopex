from __future__ import annotations

import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.answer import compose_product_answer
from scopex.finalizer.claims import claim_set_from_dict


class ProductAnswerReadabilityTests(unittest.TestCase):
    def test_nipple_business_scalars_are_rendered_for_people(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='exec:stats',
            raw='"nipple_recognition_rate": 0.978311,',
            metadata={'evidence_type': 'command_line'},
        )
        catalog.add(
            source='exec:stats',
            raw='"total_cows": 438,',
            metadata={'evidence_type': 'command_line'},
        )
        catalog.add(
            source='exec:stats',
            raw='"complete_four_nipple_cows": 400,',
            metadata={'evidence_type': 'command_line'},
        )
        catalog.add(
            source='exec:stats',
            raw='"3": 38,',
            metadata={'evidence_type': 'command_line'},
        )
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

    def test_missing_host_snapshot_is_not_presented_as_host_measurement(self):
        catalog = EvidenceCatalog('task-1', 'session-1')
        catalog.add(
            source='exec:host',
            raw="ls: cannot access '/scopex-host/': No such file or directory",
            metadata={'evidence_type': 'command_line'},
        )
        claims = claim_set_from_dict({
            'claims': [
                {'id': 'C1', 'kind': 'fact', 'topic': 'host is healthy', 'evidence_refs': ['E1'], 'confidence': 'high', 'scope': 'component', 'relation': 'observed'},
            ],
            'summary_claim_ids': ['C1'],
        })
        text = compose_product_answer(claims, catalog).to_dict()['conclusion'][0]['text']
        self.assertIn('宿主机资源快照当前不可用', text)
        self.assertNotIn('host is healthy', text)


if __name__ == '__main__':
    unittest.main()
