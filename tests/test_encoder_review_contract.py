"""Review-contract counterexamples: decision context, audit rows and downstream rendering."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import runpy
import stat
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/encoder-health/scripts/encoder_health.py"
ANALYZER = runpy.run_path(str(SCRIPT))
PROFILE = {"source": "t", "max_rate_counts_s": 1000, "timestamp_uncertainty_ms": 5,
           "continuity_verified": True, "count_tolerance": 0}


def t(ms_offset):
    base = datetime(2026, 9, 15)
    return (base + timedelta(milliseconds=ms_offset)).strftime("%Y-%m-%d %H:%M:%S:%f")[:-3]


def raw_line(ms_offset, count):
    return f"{t(ms_offset)} [TRACE] Get EncoderVal, raw[{count}], filtered[0]\n"


def scan_text(text, start=None, end=None, profile=None):
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "raw.log"
        log.write_text(text, encoding="utf-8")
        return ANALYZER["scan"]([log], start, end, profile=profile)


class DecisionContextTests(unittest.TestCase):
    def test_reset_before_window_edge_blocks_false_anomaly(self):
        text = (f"{t(0)} [TRACE] ResetEncoderValOnSerialPort\n"
                + raw_line(200, 100000) + raw_line(400, 40)
                + "".join(raw_line(ms, v) for ms, v in
                          [(600, 45), (800, 50), (1000, 55), (1200, 60), (1400, 65)]))
        start = ANALYZER["millis"](t(100))
        s = scan_text(text, start, ANALYZER["millis"](t(2000)), PROFILE)
        # The pre-window reset is captured with margin and barriers the edge pair,
        # so the huge jump is explained instead of confirmed as a violation.
        self.assertTrue(any("ResetEncoderValOnSerialPort" in row["text"] for row in s["context"]))
        self.assertTrue(s["samples"][0]["barrier"])
        self.assertEqual([c for c in s["candidates"] if "profile_rate_candidate" in c["kinds"]], [])
        self.assertNotEqual(ANALYZER["report"](s)["status"], "confirmed_raw_anomaly")

    def test_control_event_after_last_sample_stays_unresolved(self):
        text = "".join(raw_line(i * 100, i * 10) for i in range(30))
        text += raw_line(3000, 3500) + f"{t(3050)} [TRACE] SetEncoderDevParam mode=2\n"
        # An explicit --end cut between the last raw sample and the control event
        # must still capture the event: the margin applies to both window edges.
        start = ANALYZER["millis"](t(0))
        end = ANALYZER["millis"](t(3010))
        s = scan_text(text, start, end, PROFILE)
        self.assertTrue(s["candidates"])
        self.assertTrue(any("SetEncoderDevParam" in row["text"] for row in s["context"]))
        result = ANALYZER["inspect"](s, s["candidates"][0]["id"])
        rate = next(c for c in result["checks"] if c["check"] == "verified_rate_limit")
        self.assertTrue(result["decision_counter_log_context"])
        self.assertEqual(rate["outcome"], "insufficient")
        self.assertEqual(result["status"], "unresolved")

    def test_rollback_catchup_pair_cannot_fabricate_a_rate_confirmation(self):
        # Real motion during a clock rollback must not produce a profile-backed
        # confirmation from the fabricated sorted dt of the catch-up pair.
        rows = [(ms, value) for ms, value in
                [(0, 0), (100, 10), (200, 20), (300, 30), (400, 40),
                 (350, 45), (360, 46), (370, 47), (380, 48), (390, 49),
                 (410, 500), (510, 510), (610, 520), (710, 530), (810, 540)]]
        text = "".join(raw_line(ms, value) for ms, value in rows)
        s = scan_text(text, profile=PROFILE)
        catch_up = next(r for r in s["samples"] if r["time"] == t(410))
        self.assertTrue(catch_up["barrier"])
        self.assertEqual([c for c in s["candidates"] if "profile_rate_candidate" in c["kinds"]], [])
        self.assertNotEqual(ANALYZER["report"](s)["status"], "confirmed_raw_anomaly")


class BatchSteeringTests(unittest.TestCase):
    @staticmethod
    def multi_candidate_state():
        # Two visible spikes produce multiple candidates.
        rows = [(i * 100, i * 10 + (3000 if i in (15, 25) else 0)) for i in range(40)]
        return scan_text("".join(raw_line(ms, value) for ms, value in rows))

    def test_report_next_action_carries_full_batched_command(self):
        s = self.multi_candidate_state()
        self.assertGreaterEqual(len(s["candidates"]), 2)
        report = ANALYZER["report"](s)
        ids = [c["id"] for c in s["candidates"]]
        for candidate_id in ids:
            self.assertIn(f"--candidate {candidate_id}", report["next_action"])
        self.assertIn("ONE batched", report["next_action"])

    def test_single_inspect_hint_lists_remaining_candidates(self):
        s = self.multi_candidate_state()
        first = s["candidates"][0]["id"]
        result = ANALYZER["inspect"](s, first)
        remaining = [c["id"] for c in s["candidates"] if c["id"] != first]
        self.assertIsNotNone(result["hint"])
        for candidate_id in remaining:
            self.assertIn(f"--candidate {candidate_id}", result["hint"])
        # After the last candidate is reviewed nothing remains to suggest.
        for candidate_id in remaining:
            ANALYZER["inspect"](s, candidate_id)
        self.assertIsNone(ANALYZER["batch_hint"](s))

    def test_batch_result_reports_remaining_after_partial_batch(self):
        s = self.multi_candidate_state()
        first_two = [c["id"] for c in s["candidates"][:2]]
        for candidate_id in first_two:
            ANALYZER["inspect"](s, candidate_id)
        hint = ANALYZER["batch_hint"](s)
        for candidate_id in [c["id"] for c in s["candidates"] if c["id"] not in first_two]:
            self.assertIn(f"--candidate {candidate_id}", hint)


class ReviewBucketTests(unittest.TestCase):
    def test_window_view_does_not_count_as_candidate_review(self):
        increments = [(20, 20)] * 30 + [(20, 500)] + [(20, 20)] * 10
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            now, count, lines = datetime(2026, 9, 15), 1000, []
            for dt, delta in [(0, 0)] + increments:
                now += timedelta(milliseconds=dt)
                count += delta
                lines.append(raw_line(0, count).replace(t(0), now.strftime("%Y-%m-%d %H:%M:%S:%f")[:-3], 1))
            log.write_text("".join(lines), encoding="utf-8")
            s = ANALYZER["scan"]([log])
        window = (s["samples"][30]["t"], s["samples"][31]["t"] + 1)
        ANALYZER["inspect"](s, None, *window)
        report = ANALYZER["report"](s)
        self.assertEqual(report["confirmed_total"], 0)
        self.assertEqual(report["unseen_total"], len(s["candidates"]))
        self.assertTrue(s["window_reviews"])

    def test_load_separates_window_reviews_from_old_snapshots(self):
        increments = [(20, 20)] * 30
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            now, count, lines = datetime(2026, 9, 15), 1000, []
            for dt, delta in [(0, 0)] + increments:
                now += timedelta(milliseconds=dt)
                count += delta
                lines.append(raw_line(0, count).replace(t(0), now.strftime("%Y-%m-%d %H:%M:%S:%f")[:-3], 1))
            log.write_text("".join(lines), encoding="utf-8")
            s = ANALYZER["scan"]([log])
            path = Path(td) / "snapshot.json"
            ANALYZER["save"](path, s)
            blob = json.loads(path.read_text())
            blob["reviews"]["2026-09-15 00:00:00:000/2026-09-15 00:00:01:000"] = {"status": "confirmed_raw_anomaly"}
            path.write_text(json.dumps(blob))
            loaded = ANALYZER["load"](path)
            self.assertEqual(loaded["reviews"], {})
            self.assertIn("2026-09-15 00:00:00:000/2026-09-15 00:00:01:000", loaded["window_reviews"])


class EvidenceKeepingTests(unittest.TestCase):
    def test_equal_timestamp_keeps_remaining_statistics(self):
        text = "".join(raw_line(ms, v) for ms, v in
                       [(0, 10), (150, 12), (300, 20), (300, 22), (450, 24), (600, 26), (750, 28), (900, 30)])
        s = scan_text(text)
        self.assertTrue(any(i["kind"] == "duplicate_timestamp" for i in s["issues"]))
        self.assertGreaterEqual(s["facts"]["delta_counts"]["n"], 4)
        self.assertGreaterEqual(s["facts"]["rate_counts_s"]["n"], 4)

    def test_multiple_readings_on_one_line_are_all_kept(self):
        text = (f"{t(0)} [TRACE] echo Get EncoderVal, raw[12] resp Get EncoderVal, raw[34]\n"
                + raw_line(200, 14) + raw_line(400, 16))
        s = scan_text(text)
        first_line = [row["count"] for row in s["samples"] if row["line_no"] == 1]
        self.assertEqual(first_line, [12, 34])
        self.assertTrue(any(i["kind"] == "multiple_raw_per_line" for i in s["issues"]))

    def test_alias_paths_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            log.write_text("".join(raw_line(i * 200, i * 10) for i in range(10)), encoding="utf-8")
            alias = Path(td) / ".." / Path(td).name / "raw.log"
            s = ANALYZER["scan"]([log, alias])
            self.assertEqual(len(s["inputs"]), 1)
            self.assertEqual(s["facts"]["samples_in_window"], 10)


class AuditRowTests(unittest.TestCase):
    def _run(self, args, access, cwd):
        env = {**os.environ, "SCOPEX_DATA_ACCESS_LOG": str(access)}
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                              text=True, env=env, cwd=cwd, timeout=60)

    def test_audit_rows_are_complete_private_and_record_failures(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "CowDisinfect-20260915-000000.log"
            empty.write_text("2026-09-15 00:00:00:000 nothing here\n", encoding="utf-8")
            access = Path(td) / "access.jsonl"
            out = json.loads(self._run(["scan", str(empty)], access, td).stdout)
            self.assertEqual(out["status"], "no_raw_data")
            self.assertFalse(access.exists())  # null bounds never reach the audit log

            log = Path(td) / "CowDisinfect-20260915-010000.log"
            log.write_text("".join(raw_line(i * 20, i * 2) for i in range(40)), encoding="utf-8")
            failed = self._run(["scan", str(log), "--start", t(0), "--end", t(60000),
                                "--max-samples", "5"], access, td)
            self.assertEqual(json.loads(failed.stdout)["status"], "error")
            row = json.loads(access.read_text().splitlines()[0])
            self.assertIs(row["completed"], False)

            snapshot = Path(td) / "s.json"
            done = self._run(["scan", str(log), "--start", t(0), "--end", t(60000),
                              "--out", str(snapshot)], access, td)
            self.assertEqual(json.loads(done.stdout)["status"], "inconclusive")
            row = json.loads(access.read_text().splitlines()[1])
            self.assertNotIn("completed", row)
            self.assertEqual(row["start"], t(0))
            self.assertEqual(stat.S_IMODE(access.stat().st_mode) & 0o077, 0)

    def test_failing_audit_append_does_not_discard_the_scan(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "CowDisinfect-20260915-000000.log"
            log.write_text("".join(raw_line(i * 20, i * 2) for i in range(20)), encoding="utf-8")
            snapshot = Path(td) / "s.json"
            # A directory as the access log must not lose a completed analysis.
            done = self._run(["scan", str(log), "--start", t(0), "--end", t(60000),
                              "--out", str(snapshot)], td, td)
            self.assertEqual(done.returncode, 0)
            self.assertTrue(snapshot.exists())


class PlotContractTests(unittest.TestCase):
    def test_decimation_keeps_points_after_a_break(self):
        xs = list(range(250000))
        ys = [1.0] * 250000
        ys[10000] = float("nan")
        ox, _ = ANALYZER["_decimate_series"](xs, ys, 20000)
        after = sum(1 for x in ox if x > 10000)
        self.assertGreaterEqual(after, 1000)
        self.assertLessEqual(len(ox), 20010)

    def test_plot_defaults_to_top_five_and_validates_before_writing(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            text = "".join(raw_line(i * 100, i * 10) for i in range(120))
            for k in range(8):
                marker = t((20 + k * 12) * 100).replace("[", "\\[")
                text = text.replace(f"{marker}", f"{marker}", 1)
            # Eight spikes keep this independent of exact marker text replacement.
            lines = text.splitlines(keepends=True)
            for k in range(8):
                index = 20 + k * 12
                lines[index] = raw_line(index * 100, index * 10 + 400 + k)
            log.write_text("".join(lines), encoding="utf-8")
            snapshot = Path(td) / "s.json"
            proc, out = ANALYZER and (None, None)
            proc = subprocess.run([sys.executable, str(SCRIPT), "scan", str(log),
                                   "--out", str(snapshot)], capture_output=True, text=True)
            available = json.loads(proc.stdout)["candidates_total"]
            self.assertGreaterEqual(available, 6)
            plots = Path(td) / "plots"
            proc = subprocess.run([sys.executable, str(SCRIPT), "plot", str(snapshot),
                                   "--out-dir", str(plots)], capture_output=True, text=True)
            out = json.loads(proc.stdout)
            self.assertEqual(len(out["candidates_plotted"]), 5)
            bad = Path(td) / "plots-bad"
            proc = subprocess.run([sys.executable, str(SCRIPT), "plot", str(snapshot),
                                   "--out-dir", str(bad), "--candidate", "R99"],
                                  capture_output=True, text=True)
            self.assertEqual(json.loads(proc.stdout)["status"], "error")
            self.assertFalse((bad / "overview.png").exists())


class CliContractTests(unittest.TestCase):
    def test_short_millisecond_timestamps_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            log.write_text("".join(raw_line(i * 20, i) for i in range(5)), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "scan", str(log),
                                   "--start", "2026-09-15 00:00:00:5",
                                   "--end", t(60000)], capture_output=True, text=True)
            self.assertEqual(json.loads(proc.stdout)["status"], "error")

    def test_batch_inspect_reviews_all_candidates_in_one_load(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "raw.log"
            lines = [raw_line(i * 100, i * 10).rstrip("\n") for i in range(60)]
            for k in range(3):
                index = 15 + k * 15
                lines[index] = raw_line(index * 100, index * 10 + 300).rstrip("\n")
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            snapshot = Path(td) / "s.json"
            subprocess.run([sys.executable, str(SCRIPT), "scan", str(log), "--out", str(snapshot)],
                           capture_output=True, text=True)
            proc = subprocess.run([sys.executable, str(SCRIPT), "inspect", str(snapshot),
                                   "--candidate", "R1", "--candidate", "R2", "--candidate", "R3"],
                                  capture_output=True, text=True)
            out = json.loads(proc.stdout)
            self.assertEqual(out["status"], "batch_inspected")
            self.assertEqual(len(out["inspections"]), 3)
            saved = json.loads(snapshot.read_text())
            self.assertTrue(all(key in saved["reviews"] for key in ("R1", "R2", "R3")))


class FinalizerRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT))
        from scopex.finalizer.answer import _humanize_business_facts
        from scopex.finalizer.report import _structured_business_content
        cls.humanize = staticmethod(_humanize_business_facts)
        cls.structured = staticmethod(_structured_business_content)

    PAYLOAD = {"scopex_role": "business_facts", "source": "encoder-health", "schema": 6,
               "status": "confirmed_raw_anomaly",
               "facts": {"samples_in_window": 39876, "invalid_samples": 0, "median_sample_dt_ms": 18.2},
               "confirmed_total": 1, "unresolved_total": 2, "unseen_total": 0, "candidates_total": 3,
               "candidates": [{"id": "R1", "start": "2026-09-20 18:41:23:456",
                               "end": "2026-09-20 18:41:23:812", "kinds": ["local_rate_deviation"],
                               "metrics": {"deviation": 12.0}, "peak_rate_counts_s": 5100.0,
                               "review_status": "confirmed_raw_anomaly"}],
               "inconclusive_reasons": ["unresolved_checks"],
               "coverage": {"uncovered_edges": [{"edge": "start"}], "internal_gap_count": 2}}

    def test_report_payload_renders_candidate_worklist_in_plain_speech(self):
        text = self.humanize(self.PAYLOAD)
        self.assertIn("18:41:23", text)
        self.assertIn("已确认", text)
        self.assertIn("偏离 12 倍", text)
        self.assertIn("未决", text)

    def test_structured_content_keeps_schema6_keys(self):
        item = SimpleNamespace(raw=json.dumps(self.PAYLOAD),
                               metadata={"evidence_type": "structured_business_facts"})
        kept = json.loads(self.structured(item))
        self.assertEqual(kept["status"], "confirmed_raw_anomaly")
        self.assertEqual(len(kept["candidates"]), 1)
        self.assertIn("inconclusive_reasons", kept)

    def test_inspect_payload_renders_without_leaking_raw_json(self):
        payload = {"scopex_role": "business_facts", "source": "encoder-health", "schema": 6,
                   "region": "R1", "start": "2026-09-20 18:41:23:456",
                   "end": "2026-09-20 18:41:23:812", "status": "unresolved",
                   "checks": [{"check": "motion_explanation", "outcome": "insufficient"}]}
        text = self.humanize(payload)
        self.assertIsNotNone(text)
        self.assertIn("R1", text)
        self.assertNotIn('{"scopex_role"', text)


if __name__ == "__main__":
    unittest.main()
