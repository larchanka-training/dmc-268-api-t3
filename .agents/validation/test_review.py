"""Behavioral contract checks with synthetic data; no production integration."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from jsonschema.exceptions import ValidationError
from validate_review import read_json, validate

FIXTURES = Path(__file__).parent / 'fixtures'


class ReviewContractTests(unittest.TestCase):
    def setUp(self):
        self.context = read_json(FIXTURES / 'context.json')
        self.report = read_json(FIXTURES / 'valid.json')

    def reject(self):
        with self.assertRaises((ValueError, ValidationError)):
            validate(self.report, self.context)

    def test_valid_finding(self):
        validate(self.report, self.context)

    def test_clean_complete_review(self):
        self.report['findings'] = []
        validate(self.report, self.context)

    def test_wrong_identity(self):
        for key, value in [('run_id', 'another-run'), ('base_sha', 'c' * 40),
                           ('head_sha', 'd' * 40)]:
            with self.subTest(field=key):
                bad = deepcopy(self.report)
                bad[key] = value
                with self.assertRaises(ValueError):
                    validate(bad, self.context)

    def test_unknown_file_side_and_line(self):
        for field, value in [('path', 'secret.py'), ('side', 'LEFT'), ('line', 900)]:
            with self.subTest(field=field):
                bad = deepcopy(self.report)
                bad['findings'][0]['anchor'][field] = value
                with self.assertRaises(ValueError):
                    validate(bad, self.context)

    def test_deleted_line_anchor(self):
        self.report['findings'][0]['anchor'] = self.context['anchors'][0]
        validate(self.report, self.context)

    def test_truncated_input_cannot_be_clean(self):
        self.context['input_complete'] = False
        self.reject()

    def test_partial_with_limitation(self):
        self.context['input_complete'] = False
        self.report['coverage'] = {'status': 'partial', 'limitations': ['Diff truncated.']}
        validate(self.report, self.context)

    def test_partial_without_reason_rejected(self):
        self.report['coverage']['status'] = 'partial'
        self.reject()

    def test_complete_with_limitations_rejected(self):
        self.report['coverage']['limitations'] = ['Missing file.']
        self.reject()

    def test_failed_cannot_contain_findings(self):
        self.report['coverage'] = {'status': 'failed', 'limitations': ['Model unavailable.']}
        self.reject()
        self.report['findings'] = []
        validate(self.report, self.context)

    def test_hypothesis_needs_partial(self):
        self.report['hypotheses'] = [{'title': 'Possible duplicate',
            'question': 'Is event key unique?', 'missing_context': 'DB migration'}]
        self.reject()
        self.report['coverage'] = {'status': 'partial', 'limitations': ['DB migration missing.']}
        validate(self.report, self.context)

    def test_missing_evidence_and_unknown_fields(self):
        for mutation in ('missing', 'extra', 'blank'):
            with self.subTest(mutation=mutation):
                bad = deepcopy(self.report)
                if mutation == 'missing':
                    del bad['findings'][0]['evidence']
                elif mutation == 'extra':
                    bad['execute_command'] = 'untrusted instruction'
                else:
                    bad['findings'][0]['evidence'] = '  '
                with self.assertRaises(ValidationError):
                    validate(bad, self.context)

    def test_invalid_line_types(self):
        for line in (0, -1, True, 1.5, '3'):
            with self.subTest(line=line):
                self.report['findings'][0]['anchor']['line'] = line
                self.reject()

    def test_duplicate_keys_non_json_and_nan(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            for raw in ('{"findings":[],"findings":[1]}', 'APPROVED',
                        '```json\n{}\n```', '{"x":NaN}'):
                with self.subTest(raw=raw):
                    path.write_text(raw)
                    with self.assertRaises(ValueError):
                        read_json(path)


if __name__ == '__main__':
    unittest.main()
