"""Progressive raw review, provenance, confirmation and existence stopping."""
from test_encoder_motion_context import ANALYZER, analyze, cli, raw_text, series
from pathlib import Path
import tempfile
import unittest


RATE_PROFILE = {"source": "test fixture: verified continuous counter and sample-time contract",
                "max_rate_counts_s": 1000, "timestamp_uncertainty_ms": 0,
                "continuity_verified": True}


class RawReviewTests(unittest.TestCase):
    def test_confirmed_block_does_not_stop_full_window_review(self):
        s = analyze([(100, 10)] * 30 + [(100, 500), (100, -480)]
                    + [(100, 10)] * 30 + [(100, 600)] + [(100, 10)] * 30,
                    profile=RATE_PROFILE)
        r = ANALYZER["inspect"](s, s["candidates"][0]["id"])
        self.assertFalse(r["stop_recommended"])
        summary = ANALYZER["report"](s)
        self.assertEqual(summary["status"], "confirmed_raw_anomaly")
        self.assertGreater(summary["unseen_total"], 0)
        self.assertEqual(summary["confirmed_total"], 1)
        self.assertFalse(summary["stop_recommended"])

    def test_decision_context_is_independent_of_display_context(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            rows = series([(100, 10)] * 30 + [(100, 500)] + [(100, 10)] * 30)
            text = raw_text(rows)
            insertion = "2026-09-15 00:00:02:950 ResetEncoderValOnSerialPort\n"
            text = text.replace("2026-09-15 00:00:03:000", insertion + "2026-09-15 00:00:03:000", 1)
            log.write_text(text)
            state = ANALYZER["scan"]([log], profile=RATE_PROFILE)
            candidate = state["candidates"][0]["id"]
            narrow = ANALYZER["inspect"](state, candidate, context_ms=0)
            narrow_outcome = narrow["checks"][0]["outcome"]
            wide = ANALYZER["inspect"](state, candidate, context_ms=5000)
            self.assertEqual(narrow_outcome, wide["checks"][0]["outcome"])
            self.assertFalse(narrow["stop_recommended"])
            self.assertFalse(wide["stop_recommended"])

    def test_raw_short_window_speed_preserves_signed_net_motion(self):
        state = analyze([(100, 10)] * 8)
        speeds = ANALYZER["_speed_values"](state, 200)
        self.assertAlmostEqual(speeds[2], 20 * (501.144 / 4096) * 1000 / 200)
        reverse = analyze([(100, -10)] * 8)
        self.assertAlmostEqual(ANALYZER["_speed_values"](reverse, 200)[2],
                               -20 * (501.144 / 4096) * 1000 / 200)

    def test_plot_is_generated_from_snapshot_with_raw_only_panels(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "state.json"
            out_dir = Path(td) / "plots"
            state = analyze([(100, 10)] * 20 + [(100, 500), (100, -480)] + [(100, 10)] * 20)
            ANALYZER["save"](state_path, state)
            candidate_id = state["candidates"][0]["id"]
            proc, result = cli("plot", state_path, "--out-dir", out_dir,
                               "--candidate", candidate_id)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len(result["artifacts"]), 2)
            self.assertEqual(result["raw_only"], True)
            for artifact in result["artifacts"]:
                image = Path(artifact["path"])
                self.assertTrue(image.exists())
                self.assertEqual(image.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(artifact["panels"],
                                 ["raw_count", "sample_dt_ms", "signed_delta_raw", "raw_speed_mm_s"])
            self.assertNotIn("filtered", str(result))
            self.assertNotIn("TurnTableSpeed", str(result))
            proc, summary = cli("report", state_path)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(summary["plots"]["status"], "plots_written")
            self.assertEqual(len(summary["plots"]["artifacts"]), 2)
            self.assertNotIn("sample_count", summary["plots"]["artifacts"][0])
            self.assertNotIn("panels", summary["plots"]["artifacts"][0])

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
        self.assertFalse(r["stop_recommended"])
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
