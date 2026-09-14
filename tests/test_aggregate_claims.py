from __future__ import annotations

import copy
import json
import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.validator import validate_claim_payload


def fact(cid: str, topic: str, refs=None):
    return {
        "id": cid, "kind": "fact", "topic": topic,
        "evidence_refs": ["E1"] if refs is None else refs,
        "confidence": "high", "scope": "time_window", "relation": "observed",
    }


class AggregateClaimTests(unittest.TestCase):
    def catalog(self, aggregate=True):
        c = EvidenceCatalog("task-regression", "session-regression")
        c.add(
            source="test:stats", raw=json.dumps({"scopex_role": "business_facts", "facts": {"events": 5, "flat_intervals": 3}}),
            metadata={"evidence_type": "structured_business_facts" if aggregate else "file_line"},
        )
        return c

    def payload(self):
        return {
            "claims": [fact("C1", "检测到5个候选事件"), fact("C2", "存在3段计数恒定区间")],
            "summary_claim_ids": ["C1", "C2"],
        }

    def test_different_facts_can_share_one_structured_result(self):
        self.assertEqual(validate_claim_payload(self.payload(), self.catalog()), [])

    def test_exact_aggregate_duplicate_is_still_rejected(self):
        p = self.payload()
        p["claims"][1]["topic"] = p["claims"][0]["topic"]
        self.assertIn("claims[1].duplicate_claim", validate_claim_payload(p, self.catalog()))

    def test_whitespace_does_not_hide_exact_duplicate(self):
        p = self.payload()
        p["claims"][1]["topic"] = "  " + p["claims"][0]["topic"] + "  "
        self.assertIn("claims[1].duplicate_claim", validate_claim_payload(p, self.catalog()))

    def test_legacy_line_deduplication_unchanged(self):
        self.assertIn("claims[1].duplicate_claim", validate_claim_payload(self.payload(), self.catalog(False)))

    def test_unknown_reference_is_not_permitted(self):
        p = self.payload()
        p["claims"][1]["evidence_refs"] = ["E99"]
        errors = validate_claim_payload(p, self.catalog())
        self.assertIn("claims[1].evidence_refs", errors)
        self.assertIn("claims[1].fact_requires_evidence", errors)

    def test_reference_limit_is_not_relaxed(self):
        c = self.catalog()
        for index in range(2, 6):
            c.add(source=f"test:{index}", raw=str(index))
        p = self.payload()
        p["claims"][1]["evidence_refs"] = ["E1", "E2", "E3", "E4", "E5"]
        self.assertIn("claims[1].evidence_refs_limit", validate_claim_payload(p, c))

    def test_hypothesis_cannot_be_an_observed_fact(self):
        p = self.payload()
        p["claims"][1]["relation"] = "causal_hypothesis"
        self.assertIn("claims[1].fact_relation", validate_claim_payload(p, self.catalog()))

    def test_validation_does_not_rewrite_input(self):
        p = self.payload()
        before = copy.deepcopy(p)
        validate_claim_payload(p, self.catalog())
        self.assertEqual(p, before)


if __name__ == "__main__":
    unittest.main()
