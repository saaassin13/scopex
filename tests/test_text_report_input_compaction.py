"""Synthetic transport tests; no model or original business image is evaluated."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.media import EvidenceMediaLoader
from scopex.finalizer.client import FinalizerResponse
from scopex.finalizer.text_report import TextReportComposer


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jS1kAAAAASUVORK5CYII='
)


def response():
    return FinalizerResponse('仅为模拟报告，不判断图片质量。', 0.01, 0.02, 0.03,
                             ('stop',), True, {'completion_tokens': 10})


class TextReportInputCompactionTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(api_key='')
        self.client.complete.return_value = response()

    def add_output(self, catalog, call_id, command, raw, *, role=None):
        digest = hashlib.sha256(raw.encode()).hexdigest()
        for number, line in enumerate(raw.splitlines(), 1):
            if line.strip():
                metadata = {'evidence_type': 'command_line', 'line_number': number,
                            'command': command, 'exec_host': 'sandbox',
                            'result_sha256': digest}
                if role:
                    metadata['evidence_role'] = role
                catalog.add(source='exec:' + call_id, raw=line, tool_call_id=call_id,
                            metadata=metadata)

    def image_fixture(self, directory):
        catalog = EvidenceCatalog('fixture-task', 'fixture-session')
        self.add_output(catalog, 'help-call', 'python helper.py --help',
                        'usage: helper\nimages\noptions\n--help\ntext\nend')
        paths = [f'/readonly/sample-{index:02}.png' for index in range(7)]
        rows = [{'path': path, 'width': 1, 'height': 1,
                 'laplacian_variance': 12.0, 'gradient_energy': 11.0,
                 'brightness_mean': 10.0, 'contrast_stddev': 9.0,
                 'dark_clip_ratio': 0.01, 'bright_clip_ratio': 0.02} for path in paths]
        # Long command metadata is repeated on 81 lines in the audit, not needed
        # 81 times in the report prompt. Values and individual refs must survive.
        command = 'python helper.py ' + ' '.join(paths) + ' --label=' + 'fixture' * 60
        self.add_output(catalog, 'metrics-call', command, json.dumps({'images': rows}, indent=2))
        for path in paths[:4]:
            Path(directory, Path(path).name).write_bytes(PNG)
            catalog.add(source=path, raw='image:' + Path(path).name,
                        metadata={'evidence_type': 'image',
                                  'sha256': hashlib.sha256(PNG).hexdigest(),
                                  'media_type': 'image/png'})
        return catalog

    def test_81_line_output_and_four_images_fit_without_dropping_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            catalog = self.image_fixture(td)
            before = copy.deepcopy(catalog.snapshot())
            composer = TextReportComposer(
                self.client, model='fixture',
                media_loader=EvidenceMediaLoader((f'{td}:/readonly:ro',)))
            result = composer.run(user_request='check the selected sample', catalog=catalog,
                                  completion_reasons=('budget_reached', 'turn_timeout_budget'))
        self.assertTrue(result.valid, result.meta)
        self.assertEqual(len(catalog.items), 91)
        self.assertEqual(catalog.snapshot(), before)
        self.client.complete.assert_called_once()
        call = self.client.complete.call_args.kwargs
        prompt = call['user_prompt']
        self.assertEqual(prompt.count('[evidence_block type=command_line'), 2)
        self.assertEqual(prompt.count('command_preview='), 2)
        for item in catalog.items:
            self.assertIn(item.ref + ' |', prompt)
            if item.metadata['evidence_type'] != 'image':
                self.assertIn(item.raw, prompt)
        self.assertEqual(len(call['image_inputs']), 4)
        self.assertEqual(result.meta['image_evidence_refs'], ['E88', 'E89', 'E90', 'E91'])
        self.assertEqual(result.meta['input_chars'], len(prompt))
        self.assertEqual(result.meta['source_count'], 91)
        self.assertEqual(result.meta['max_input_chars'], 48000)
        self.assertLess(len(prompt), 10000)
        self.assertIn('turn_timeout_budget', prompt)
        self.assertNotIn('base64', json.dumps(result.meta))

    def test_working_data_filter_preserves_ref_gaps_and_groups_remaining_lines(self):
        catalog = EvidenceCatalog('t', 's')
        self.add_output(catalog, 'scratch', 'echo work', 'work', role='working_derived')
        self.add_output(catalog, 'real', 'read selected', 'value=12\nmissing=2')
        result = TextReportComposer(self.client, model='fixture').run(user_request='check', catalog=catalog)
        prompt = self.client.complete.call_args.kwargs['user_prompt']
        self.assertTrue(result.valid)
        self.assertNotIn('E1 |', prompt)
        self.assertEqual([row['ref'] for row in result.meta['sources']], ['E2', 'E3'])
        self.assertEqual(prompt.count('[evidence_block'), 1)
        self.assertIn('E2 | line=1 | value=12', prompt)
        self.assertIn('E3 | line=2 | missing=2', prompt)

    def test_grouping_never_combines_different_calls_with_the_same_source(self):
        catalog = EvidenceCatalog('t', 's')
        for call, value in [('a', 'first'), ('b', 'second'), ('a', 'third')]:
            catalog.add(source='same.log', raw=value, tool_call_id=call,
                        metadata={'evidence_type': 'file_line', 'line_number': 1})
        TextReportComposer(self.client, model='fixture').run(user_request='check', catalog=catalog)
        prompt = self.client.complete.call_args.kwargs['user_prompt']
        self.assertEqual(prompt.count('[evidence_block'), 3)
        self.assertLess(prompt.index('first'), prompt.index('second'))
        self.assertLess(prompt.index('second'), prompt.index('third'))

    def test_true_large_raw_data_still_fails_with_measured_capacity(self):
        catalog = EvidenceCatalog('t', 's')
        catalog.add(source='large.log', raw='raw-data-' * 6000)
        result = TextReportComposer(self.client, model='fixture').run(user_request='check', catalog=catalog)
        self.assertFalse(result.valid)
        self.assertIn('capacity_exceeded', result.meta['errors'][0])
        self.assertEqual(result.meta['attempt_count'], 0)
        self.assertGreater(result.meta['input_chars'], result.meta['max_input_chars'])
        self.client.complete.assert_not_called()
        self.assertEqual(catalog.items[0].raw, 'raw-data-' * 6000)

    def test_grouped_input_does_not_bypass_original_image_identity(self):
        with tempfile.TemporaryDirectory() as td:
            catalog = self.image_fixture(td)
            Path(td, 'sample-00.png').write_bytes(b'changed')
            result = TextReportComposer(self.client, model='fixture',
                media_loader=EvidenceMediaLoader((f'{td}:/readonly:ro',))).run(
                    user_request='check', catalog=catalog)
        self.assertFalse(result.valid)
        self.assertIn('image_evidence_changed', result.meta['errors'][0])
        self.client.complete.assert_not_called()

    def test_output_budget_and_no_tool_single_call_remain_unchanged(self):
        catalog = EvidenceCatalog('t', 's')
        self.add_output(catalog, 'a', 'echo test', 'value=12')
        result = TextReportComposer(self.client, model='fixture').run(user_request='check', catalog=catalog)
        self.assertTrue(result.valid)
        call = self.client.complete.call_args.kwargs
        self.assertNotIn('tools', call)
        self.assertEqual(call['max_tokens'], 2048)
        self.assertEqual(call['temperature'], 0)
        self.assertEqual(result.meta['attempt_count'], 1)


if __name__ == '__main__':
    unittest.main()
