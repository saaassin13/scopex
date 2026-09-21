"""Progressive raw review, provenance, confirmation and existence stopping."""
from test_encoder_motion_context import ANALYZER, analyze, cli, raw_text, series
from pathlib import Path
import tempfile
import unittest


RATE_PROFILE = {"source": "test fixture: verified continuous counter and sample-time contract",
                "max_rate_counts_s": 1000, "timestamp_uncertainty_ms": 0,
                "continuity_verified": True}


class RawReviewTests(unittest.TestCase):
    def test_first_confirmed_block_stops_with_later_blocks_unseen(self):
        s = analyze([(100, 10)] * 30 + [(100, 500), (100, -480)]
                    + [(100, 10)] * 30 + [(100, 600)] + [(100, 10)] * 30,
                    profile=RATE_PROFILE)
        r = ANALYZER["inspect"](s, s["candidates"][0]["id"])
        self.assertTrue(r["stop_recommended"])
        summary = ANALYZER["report"](s)
        self.assertEqual(summary["status"], "confirmed_raw_anomaly")
        self.assertGreater(summary["unseen_total"], 0)
        self.assertEqual(summary["confirmed_total"], 1)

    def test_suspected_then_confirmed_does_not_require_resolving_first(self):
        s = analyze([(100, 10)] * 30 + [(100, 30)] * 30 + [(100, 500)] + [(100, 10)] * 30,
                    profile=RATE_PROFILE)
        first = ANALYZER["inspect"](s, s["candidates"][0]["id"])
        self.assertFalse(first["stop_recommended"])
        for c in s["candidates"][1:]:
            if ANALYZER["inspect"](s, c["id"])["stop_recommended"]:
                break
        self.assertEqual(ANALYZER["report"](s)["status"], "confirmed_raw_anomaly")

    def test_range_violation_has_raw_line_and_profile_source(self):
        s = analyze([(100, 10)] * 4, initial=100,
                    profile={"source": "fixture known register range", "count_max": 120})
        r = ANALYZER["inspect"](s, s["candidates"][0]["id"])
        self.assertTrue(r["stop_recommended"])
        self.assertIn("Get EncoderVal, raw", r["evidence"][0]["text"])
        self.assertEqual(r["checks"][0]["profile_source"], "fixture known register range")

    def test_clock_uncertainty_prevents_false_rate_confirmation(self):
        p = dict(RATE_PROFILE, timestamp_uncertainty_ms=500)
        s = analyze([(100, 10)] * 30 + [(100, 500)] + [(100, 10)] * 30, profile=p)
        r = ANALYZER["inspect"](s, s["candidates"][0]["id"])
        self.assertFalse(r["stop_recommended"])

    def test_profile_cannot_silently_assume_time_or_continuity(self):
        for p in ({"count_max": 10},
                  {"source": "fixture", "max_rate_counts_s": 10},
                  {"source": "fixture", "max_rate_counts_s": 10, "timestamp_uncertainty_ms": 0}):
            with self.assertRaises(ValueError):
                analyze([(100, 10)], profile=p)

    def test_looking_at_candidate_does_not_mean_resolved(self):
        s = analyze([(100, 10)] * 30 + [(100, 500)] + [(100, 10)] * 30)
        ANALYZER["inspect"](s, s["candidates"][0]["id"])
        r = ANALYZER["report"](s)
        self.assertEqual(r["unresolved_total"], 1)
        self.assertEqual(r["unseen_total"], 0)
        self.assertEqual(r["status"], "inconclusive")

    def test_normal_rate_transition_can_be_reviewed_without_false_confirmation(self):
        s = analyze([(100, 10)] * 30 + [(100, 50)] * 30)
        self.assertTrue(s["candidates"])
        for c in s["candidates"]:
            r = ANALYZER["inspect"](s, c["id"])
            self.assertEqual(r["status"], "compatible_motion")
            self.assertFalse(r["stop_recommended"])
        self.assertEqual(ANALYZER["report"](s)["status"], "no_anomaly_evidence_in_completed_checks")

    def test_counter_context_prevents_premature_rate_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            rows = series([(100, 10)] * 30 + [(100, 500)] + [(100, 10)] * 30)
            text = raw_text(rows)
            # A reset request shortly before the jump is present but is not a completion log.
            insertion = "2026-09-15 00:00:02:950 ResetEncoderValOnSerialPort\n"
            text = text.replace("2026-09-15 00:00:03:000", insertion + "2026-09-15 00:00:03:000", 1)
            log.write_text(text)
            s = ANALYZER["scan"]([log], profile=RATE_PROFILE)
            for c in s["candidates"]:
                self.assertFalse(ANALYZER["inspect"](s, c["id"])["stop_recommended"])

    def test_comparison_shows_measured_departure_and_endpoint_without_fault_claim(self):
        s = analyze([(100, 10)] * 30 + [(100, 1000), (100, -980)] + [(100, 10)] * 30)
        r = ANALYZER["inspect"](s, s["candidates"][0]["id"])
        self.assertGreater(r["comparison"]["max_abs_trend_residual_counts"], 900)
        self.assertTrue(r["comparison"]["endpoint_matches_local_trend"])
        self.assertFalse(r["stop_recommended"])

    def test_arbitrary_region_does_not_require_detected_id(self):
        s = analyze([(100, 10)] * 50)
        r = ANALYZER["inspect"](s, start=s["samples"][10]["t"], end=s["samples"][20]["t"])
        self.assertEqual(r["core"]["samples"], 10)
        self.assertEqual(r["status"], "no_anomaly_evidence")
        self.assertEqual(ANALYZER["report"](s)["status"], "no_anomaly_evidence_in_completed_checks")

    def test_pagination_reaches_all_candidates(self):
        steps = [(100, 10)] * 30
        for _ in range(12):
            steps += [(100, 500), (100, -480)] + [(100, 10)] * 30
        s = analyze(steps)
        ids, offset = [], 0
        while offset is not None:
            page = ANALYZER["report"](s, offset=offset, limit=3)
            ids.extend(c["id"] for c in page["candidates"])
            offset = page["next_offset"]
        self.assertEqual(len(ids), len(s["candidates"]))
        self.assertEqual(len(ids), len(set(ids)))

    def test_trace_pagination_preserves_exact_counts_and_peak_evidence(self):
        s = analyze([(100, 10)] * 30 + [(100, 1000), (100, -980)] + [(100, 10)] * 30)
        c = s["candidates"][0]
        seen, offset = [], 0
        while offset is not None:
            r = ANALYZER["inspect"](s, c["id"], offset=offset, limit=5)
            seen.extend(x["raw_count"] for x in r["trace"])
            offset = r["next_offset"]
        self.assertEqual(len(seen), r["context"]["samples"])
        self.assertGreaterEqual(max(map(int, seen)), 2300)
        self.assertEqual(len(s["reviews"]), 1)

    def test_snapshot_survives_source_deletion_and_cli_restart(self):
        with tempfile.TemporaryDirectory() as td:
            log, state = Path(td) / "raw.log", Path(td) / "state.json"
            log.write_text(raw_text(series([(100, 10)] * 30 + [(100, 500)] + [(100, 10)] * 30)))
            proc, result = cli("scan", log, "--out", state)
            self.assertEqual(proc.returncode, 0)
            cid = result["candidates"][0]["id"]
            log.unlink()
            proc, detail = cli("inspect", state, "--candidate", cid)
            self.assertEqual(proc.returncode, 0)
            self.assertTrue(detail["evidence"])
            proc, summary = cli("report", state)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(summary["unseen_total"], 0)
            self.assertEqual(summary["unresolved_total"], 1)

    def test_repeated_normal_reversals_do_not_confirm_by_count(self):
        s = analyze(([(100, 10)] * 30 + [(100, -10)] * 30 + [(100, 0)] * 30) * 5)
        for c in s["candidates"]:
            self.assertFalse(ANALYZER["inspect"](s, c["id"])["stop_recommended"])
        self.assertFalse(ANALYZER["report"](s)["stop_recommended"])

    def test_missing_window_edges_do_not_certify_whole_request(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            log.write_text(raw_text(series([(100, 10)] * 10)))
            start = ANALYZER["millis"]("2026-09-14 23:59:00:000")
            end = ANALYZER["millis"]("2026-09-15 00:01:00:000")
            s = ANALYZER["scan"]([log], start, end)
            self.assertEqual(ANALYZER["report"](s)["status"], "inconclusive")

    def test_snapshot_must_not_overwrite_input(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            text = raw_text(series([(100, 10)]))
            log.write_text(text)
            proc, result = cli("scan", log, "--out", log)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(log.read_text(), text)
