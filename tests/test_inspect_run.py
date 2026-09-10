"""Offline tests for inspect_run; never call a model or write existing run files."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'inspect_run.py'
spec = importlib.util.spec_from_file_location('inspect_run', SCRIPT)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
EXPECTED = {'records': [{'time': '2026-09-10T10:00:00Z', 'code': 'E101'}]}
ANSWER = json.dumps(EXPECTED, separators=(',', ':'))


def fixture_result(**changes):
    result = {'case': 'tool-basic', 'status': 'wrong_answer_or_missing_evidence',
              'answer': '```json\n' + ANSWER + '\n```', 'correct': False,
              'within_sla': False, 'successful_reads': ['input.log'],
              'grade': {'required_listing': True}, 'wall_s': 30.0,
              'rounds': 4, 'tool_calls': 3}
    result.update(changes)
    return result


CASE = {'id': 'tool-basic', 'mode': 'tool', 'files': ['input.log'], 'expected': EXPECTED}


class AnswerTests(unittest.TestCase):
    def check_answer(self, value, strict, content, wrapper=False):
        self.assertEqual(review.classify_answer(value, EXPECTED), {
            'strict_match': strict, 'content_match': content, 'wrapper': wrapper})

    def test_plain(self):
        self.check_answer(ANSWER, True, True)

    def test_spaces_and_object_key_order(self):
        self.check_answer(' {"records": [{"code": "E101", "time": "2026-09-10T10:00:00Z"}]}\n', True, True)

    def test_code_fence(self):
        self.check_answer('```json\n' + ANSWER + '\n```', False, True, True)

    def test_bare_fence_and_crlf(self):
        self.check_answer('```\r\n' + ANSWER + '\r\n```', False, True, True)

    def test_uppercase_fence(self):
        self.check_answer('```JSON\n' + ANSWER + '\n```', False, True, True)

    def test_prose_not_salvaged(self):
        self.check_answer('The answer is:\n```json\n' + ANSWER + '\n```', False, None)

    def test_multiple_blocks_not_salvaged(self):
        self.check_answer('```json\n' + ANSWER + '\n```\n```json\n{}\n```', False, None, True)

    def test_truncated_json_not_repaired(self):
        self.check_answer(ANSWER[:-1], False, None)

    def test_incorrect_fenced_content(self):
        self.check_answer('```json\n{"records": []}\n```', False, False, True)

    def test_duplicate_keys_not_accepted(self):
        self.check_answer('{"records": [], "records": []}', False, None)

    def test_nan_not_accepted(self):
        self.check_answer('{"records": NaN}', False, None)

    def test_missing_answer(self):
        self.check_answer(None, False, None)

    def test_types_and_array_order(self):
        self.assertFalse(review.same_json(True, 1))
        self.assertFalse(review.same_json([1, 2], [2, 1]))
        self.assertFalse(review.same_json(1, 1.0))
        self.assertTrue(review.same_json({'a': 1, 'b': 2}, {'b': 2, 'a': 1}))


class DiagnosisTests(unittest.TestCase):
    def test_format_only_keeps_failure(self):
        result = fixture_result()
        before = json.dumps(result, sort_keys=True)
        detail = review.diagnose(result, CASE)
        self.assertEqual(detail['category'], 'format_only')
        self.assertTrue(detail['content_and_evidence'])
        self.assertTrue(detail['record_consistent'])
        self.assertEqual(json.dumps(result, sort_keys=True), before)
        self.assertFalse(result['correct'])

    def test_missing_read_not_format_only(self):
        detail = review.diagnose(fixture_result(successful_reads=[]), CASE)
        self.assertEqual(detail['category'], 'missing_evidence')
        self.assertFalse(detail['content_and_evidence'])

    def test_direct_requires_no_tools(self):
        detail = review.diagnose(fixture_result(answer=ANSWER, correct=True,
            status='correct', successful_reads=[]), {**CASE, 'mode': 'direct'})
        self.assertEqual(detail['category'], 'strict_match')

    def test_chain_requires_listing(self):
        detail = review.diagnose(fixture_result(grade={}), {**CASE, 'mode': 'chain'})
        self.assertEqual(detail['category'], 'missing_evidence')

    def test_value_mismatch(self):
        detail = review.diagnose(fixture_result(answer='{"records": []}'), CASE)
        self.assertEqual(detail['category'], 'content_mismatch')

    def test_unparseable_not_called_content_error(self):
        detail = review.diagnose(fixture_result(answer='not JSON'), CASE)
        self.assertEqual(detail['category'], 'unparseable')
        self.assertIsNone(detail['content_match'])

    def test_interrupted_with_residual_answer_not_success(self):
        detail = review.diagnose(fixture_result(status='interrupted'), CASE)
        self.assertEqual(detail['category'], 'not_completed')
        self.assertFalse(detail['content_and_evidence'])

    def test_inconsistent_saved_grade_warned(self):
        detail = review.diagnose(fixture_result(correct=True), CASE)
        self.assertFalse(detail['record_consistent'])


class FileTests(unittest.TestCase):
    def test_duplicate_tools_canonicalized(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.jsonl'
            events = [
                {'event': 'tool_started', 'name': 'read_file', 'arguments': '{"path":"input.log"}'},
                {'event': 'tool_finished', 'name': 'read_file', 'error': None},
                {'event': 'tool_started', 'name': 'read_file', 'arguments': '{ "path": "input.log" }'},
            ]
            path.write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
            self.assertEqual(review.duplicate_calls(path), 1)

    def test_missing_events_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(review.duplicate_calls(Path(td) / 'missing.jsonl'))

    def test_render_cli_and_no_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, data in {'cases.json': [CASE], 'results.json': [fixture_result()],
                               'plan.json': {'warmup': False, 'attempts': ['tool-basic']}}.items():
                (root / name).write_text(json.dumps(data), encoding='utf-8')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            output = review.render_run(root)
            self.assertIn('format_only', output)
            self.assertIn('| tool-basic | 1 | 0/1 | 0/1 | 1/1 | 1/1 |', output)
            self.assertNotIn('E101', output)
            child = subprocess.run([sys.executable, str(SCRIPT), str(root)],
                                   capture_output=True, text=True, timeout=5)
            self.assertEqual(child.returncode, 0, child.stderr)
            after = {p.name: p.read_bytes() for p in root.iterdir()}
            self.assertEqual(before, after)

    def test_cli_missing_path(self):
        with tempfile.TemporaryDirectory() as td:
            child = subprocess.run([sys.executable, str(SCRIPT), str(Path(td) / 'missing')],
                                   capture_output=True, text=True, timeout=5)
            self.assertEqual(child.returncode, 2)
            self.assertIn('ERROR:', child.stderr)


if __name__ == '__main__':
    unittest.main()
