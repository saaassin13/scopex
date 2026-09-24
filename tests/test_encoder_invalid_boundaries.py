"""Raw extraction, representation and continuity must precede diagnosis."""
from test_encoder_motion_context import ANALYZER, analyze, cli, raw_text, series
from pathlib import Path
import tempfile
import unittest


class RawBoundaryTests(unittest.TestCase):
    def scan_text(self, text, **kwargs):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "raw.log"
            path.write_text(text)
            return ANALYZER["scan"]([path], **kwargs)

    def test_application_threshold_and_filtered_are_not_raw_samples(self):
        s = self.scan_text(
            "2026-09-15 00:00:00:000 EncoderVal [900], TurnTableSpeed [50 mm/s]\n"
            "2026-09-15 00:00:00:010 LastEncoderVal [10] is less than [1065101]\n"
            "2026-09-15 00:00:00:020 Get EncoderVal, raw[20], filtered[18446744073709551615]\n"
            "2026-09-15 00:00:00:040 Get EncoderVal, raw[21], filtered[bad]\n")
        self.assertEqual([r["count"] for r in s["samples"]], [20, 21])
        self.assertEqual(s["facts"]["invalid_samples"], 0)

    def test_no_raw_never_falls_back_to_application(self):
        s = self.scan_text("2026-09-15 00:00:00:000 EncoderVal [900], TurnTableSpeed [50 mm/s]\n")
        self.assertEqual(ANALYZER["report"](s)["status"], "no_raw_data")

    def test_large_unsigned_print_is_not_automatically_invalid(self):
        s = self.scan_text(raw_text(series([(20, -1)], initial=2**64 - 1)))
        self.assertEqual(s["facts"]["invalid_samples"], 0)
        self.assertEqual(s["samples"][0]["printed"], str(2**64 - 1))

    def test_signed_conversion_requires_profile_and_preserves_original(self):
        text = raw_text([("2026-09-15 00:00:00:000", 2**64 - 1),
                         ("2026-09-15 00:00:00:020", 0)])
        s = self.scan_text(text, profile={"source": "fixture: signed int64 printed as uint64",
                                         "unsigned_print_bits": 64, "count_min": -100, "count_max": 100})
        self.assertEqual([r["count"] for r in s["samples"]], [-1, 0])
        self.assertEqual(s["facts"]["delta_counts"]["max"], 1)

    def test_invalid_raw_breaks_both_sides_without_bridge(self):
        s = analyze([(20, 10), (20, 9990), (20, -10020), (20, -5)],
                    initial=100, profile={"source": "fixture range", "count_min": 0, "count_max": 1000})
        self.assertEqual(s["facts"]["invalid_samples"], 1)
        self.assertEqual(s["facts"]["delta_counts"]["min"], -5)
        self.assertEqual(s["facts"]["delta_counts"]["max"], 10)

    def test_gap_cannot_be_a_reverse_or_recovery(self):
        s = analyze([(100, 10)] * 30 + [(10000, -10000)] + [(100, 10)] * 30)
        self.assertEqual(s["facts"]["sampling_gap_count"], 1)
        self.assertEqual(s["facts"]["negative_steps_observed"], 0)
        self.assertEqual(s["candidates"], [])
        self.assertEqual(ANALYZER["report"](s)["status"], "inconclusive")

    def test_clock_rollback_is_preserved_before_sort(self):
        s = self.scan_text(raw_text([("2026-09-15 00:00:00:000", 100),
                                    ("2026-09-15 00:00:00:020", 110),
                                    ("2026-09-15 00:00:00:010", 120),
                                    ("2026-09-15 00:00:00:030", 130)]))
        self.assertTrue(any(i["kind"] == "non_increasing_source_time" for i in s["issues"]))
        self.assertEqual(s["facts"]["delta_counts"]["n"], 0)

    def test_duplicate_timestamp_is_not_silently_deduplicated(self):
        s = self.scan_text(raw_text([("2026-09-15 00:00:00:000", 100)] * 2))
        self.assertEqual(len(s["samples"]), 2)
        self.assertTrue(s["issues"])

    def test_malformed_raw_breaks_continuity(self):
        s = self.scan_text(
            "2026-09-15 00:00:00:000 Get EncoderVal, raw[100]\n"
            "2026-09-15 00:00:00:020 Get EncoderVal, raw[broken]\n"
            "2026-09-15 00:00:00:040 Get EncoderVal, raw[5000]\n")
        self.assertEqual(s["facts"]["delta_counts"]["n"], 0)
        self.assertEqual(s["issues"][0]["kind"], "unparsed_raw")

    def test_unparseable_time_is_not_no_data_success(self):
        s = self.scan_text("bad time Get EncoderVal, raw[100]\n")
        self.assertEqual(ANALYZER["report"](s)["status"], "inconclusive")

    def test_insufficient_local_reference_is_not_clean_completed_check(self):
        s = analyze([(100, 1000), (100, -980)])
        r = ANALYZER["report"](s)
        self.assertEqual(r["status"], "inconclusive")
        self.assertIn("insufficient_local_reference", r["inconclusive_reasons"])

    def test_reset_request_is_context_not_completed_reset_proof(self):
        s = self.scan_text(
            "2026-09-15 00:00:00:000 Get EncoderVal, raw[1000]\n"
            "2026-09-15 00:00:00:010 ResetEncoderValOnSerialPort\n"
            "2026-09-15 00:00:00:020 Get EncoderVal, raw[0]\n"
            "2026-09-15 00:00:00:040 Get EncoderVal, raw[10]\n")
        result = ANALYZER["inspect"](s, s["candidates"][0]["id"])
        self.assertEqual(result["status"], "unresolved")
        self.assertTrue(result["counter_log_context"])
        self.assertEqual(s["facts"]["negative_steps_observed"], 0)

    def test_explicit_window_cannot_invent_samples_in_gap(self):
        s = analyze([(100, 10)] * 3 + [(10000, 20)] + [(100, 10)] * 3)
        result = ANALYZER["inspect"](s, start=s["samples"][0]["t"] + 1000,
                                     end=s["samples"][0]["t"] + 2000)
        self.assertEqual(result["status"], "no_samples_in_region")
        self.assertEqual(s["reviews"], {})

    def test_rotation_tail_malformed_line_blocks_next_file(self):
        with tempfile.TemporaryDirectory() as td:
            first, second = Path(td) / "a.log", Path(td) / "b.log"
            first.write_text(
                "2026-09-15 00:00:00:000 Get EncoderVal, raw[100]\n"
                "2026-09-15 00:00:00:020 Get EncoderVal, raw[110]\n"
                "2026-09-15 00:00:00:040 Get EncoderVal, raw[broken]\n")
            second.write_text("2026-09-15 00:00:00:060 Get EncoderVal, raw[5000]\n")
            state = ANALYZER["scan"]([first, second])
            self.assertEqual(state["facts"]["delta_counts"]["n"], 1)
            self.assertEqual(state["pairs"][-1]["blocked"], True)

    def test_log_dir_selects_group_that_started_before_natural_hour(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "CowDisinfect-20260914-035500.log"
            log.write_text("2026-09-14 03:59:30:000 Get EncoderVal, raw[100]\n")
            (root / "CowDisinfect-20260914-041000.log").write_text(
                "2026-09-14 04:10:00:000 Get EncoderVal, raw[200]\n")
            start = ANALYZER["millis"]("2026-09-14 03:59:00:000")
            end = ANALYZER["millis"]("2026-09-14 04:00:00:000")
            state = ANALYZER["scan"](ANALYZER["discover_logs"](root, start, end), start, end)
            self.assertEqual(state["facts"]["samples_in_window"], 1)
            self.assertEqual(state["inputs"][0]["path"], str(log.absolute()))

    def test_short_gap_breaks_motion_comparison(self):
        state = analyze([(20, 10)] * 5 + [(400, 4000)] + [(20, 10)] * 5)
        self.assertEqual(state["facts"]["sampling_gap_count"], 1)
        self.assertEqual(state["facts"]["negative_steps_observed"], 0)

    def test_negative_only_profile_upper_bound_is_valid(self):
        state = self.scan_text(raw_text(series([(20, -1)], initial=-5)),
                               profile={"source": "fixture signed range", "count_max": -1})
        self.assertEqual(state["facts"]["invalid_samples"], 0)

    def test_capacity_error_does_not_save_partial_as_complete(self):
        with tempfile.TemporaryDirectory() as td:
            p, out = Path(td) / "raw.log", Path(td) / "state.json"
            p.write_text(raw_text(series([(100, 10)] * 5)))
            proc, result = cli("scan", p, "--out", out, "--max-samples", 2)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(result["status"], "error")
            self.assertFalse(out.exists())
