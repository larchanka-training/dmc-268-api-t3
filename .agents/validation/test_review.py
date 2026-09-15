"""Behavioral contract checks with synthetic data; no production integration."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from jsonschema.exceptions import ValidationError
from validate_review import filter_candidates, read_json, validate

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
                           ('head_sha', 'd' * 40), ('merge_base_sha', 'e' * 40),
                           ('rules_version', 'another-snapshot')]:
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


    def test_rule_id_is_required(self):
        del self.report['findings'][0]['rule_id']
        self.reject()

    def test_unknown_disabled_or_wrong_severity_rule(self):
        for mutation in ('unknown', 'disabled', 'severity'):
            with self.subTest(mutation=mutation):
                report, context = deepcopy(self.report), deepcopy(self.context)
                if mutation == 'unknown':
                    report['findings'][0]['rule_id'] = 'other-project-rule'
                elif mutation == 'disabled':
                    context['rules'][0]['enabled'] = False
                else:
                    report['findings'][0]['severity'] = 'critical'
                with self.assertRaises(ValueError):
                    validate(report, context)

    def test_contextual_primary_location_with_changed_relation(self):
        self.report['findings'][0]['anchor'] = {'path': 'example.py', 'side': 'RIGHT', 'line': 1}
        validate(self.report, self.context)
        self.report['findings'][0]['related_changed_lines'] = [self.report['findings'][0]['anchor']]
        self.reject()

    def test_changed_relation_required_and_verified(self):
        for related in ([], [{'path': 'other.py', 'side': 'RIGHT', 'line': 3}],
                        [self.context['anchors'][0], self.context['anchors'][0]]):
            with self.subTest(related=related):
                self.report['findings'][0]['related_changed_lines'] = related
                self.reject()

    def test_invalid_trusted_context_is_fatal_to_filtering(self):
        for mutation in ('duplicate_rule', 'missing_location', 'boolean_string'):
            context = deepcopy(self.context)
            if mutation == 'duplicate_rule':
                context['rules'].append(deepcopy(context['rules'][0]))
            elif mutation == 'missing_location':
                context['locations'] = []
            else:
                context['input_complete'] = 'false'
            with self.subTest(mutation=mutation):
                with self.assertRaises((ValueError, ValidationError)):
                    filter_candidates(self.report, context)

    def test_confidence_bounds_and_non_finite_values(self):
        for confidence in (-0.1, 1.1, True, 'high', float('nan'), float('inf')):
            with self.subTest(confidence=confidence):
                self.report['findings'][0]['confidence'] = confidence
                self.reject()
        for confidence in (0, 1):
            self.report['findings'][0]['confidence'] = confidence
            validate(self.report, self.context)

    def test_legacy_version_is_rejected(self):
        self.report['schema_version'] = '1.0.0'
        self.reject()

    def test_duplicate_candidates_strict_and_filtering(self):
        self.report['findings'].append(deepcopy(self.report['findings'][0]))
        self.reject()
        result = filter_candidates(self.report, self.context)
        self.assertEqual(len(result['report']['findings']), 1)
        self.assertEqual(result['report']['coverage']['status'], 'complete')
        self.assertEqual(result['rejections'], [{'index': 1, 'reason': 'duplicate_candidate'}])

    def test_mixed_candidates_preserve_valid_and_mark_partial(self):
        bad = deepcopy(self.report['findings'][0])
        bad['rule_id'] = 'unknown'
        bad['evidence'] = 'UNTRUSTED_SECRET_SENTINEL'
        self.report['findings'].insert(0, bad)
        original = deepcopy(self.report)
        result = filter_candidates(self.report, self.context)
        self.assertEqual(self.report, original)
        self.assertEqual(result['report']['findings'], [original['findings'][1]])
        self.assertEqual(result['report']['coverage']['status'], 'partial')
        self.assertEqual(result['rejections'], [{'index': 0, 'reason': 'rule_not_enabled'}])
        self.assertNotIn('UNTRUSTED_SECRET_SENTINEL', str(result))
        validate(result['report'], self.context)

    def test_all_rejected_is_partial_not_clean(self):
        self.report['findings'] = [None, {'evidence': 'UNTRUSTED'}]
        result = filter_candidates(self.report, self.context)
        self.assertEqual(result['report']['findings'], [])
        self.assertEqual(result['report']['coverage']['status'], 'partial')
        self.assertTrue(result['report']['coverage']['limitations'])
        self.assertEqual(len(result['rejections']), 2)

    def test_filter_rejects_bad_envelope_instead_of_salvaging(self):
        for mutation in ('identity', 'extra', 'failed_findings', 'incomplete'):
            report, context = deepcopy(self.report), deepcopy(self.context)
            if mutation == 'identity':
                report['head_sha'] = 'f' * 40
            elif mutation == 'extra':
                report['execute'] = 'untrusted'
            elif mutation == 'failed_findings':
                report['coverage'] = {'status': 'failed', 'limitations': ['Unavailable']}
            else:
                context['input_complete'] = False
            with self.subTest(mutation=mutation):
                with self.assertRaises((ValueError, ValidationError)):
                    filter_candidates(report, context)

    def test_no_enabled_rules_permits_only_empty_findings(self):
        self.context['rules'] = []
        self.reject()
        self.report['findings'] = []
        validate(self.report, self.context)

    def test_distinct_findings_at_same_location_are_not_merged(self):
        other = deepcopy(self.report['findings'][0])
        other['scenario'] = 'Another independently evidenced input path.'
        self.report['findings'].append(other)
        result = filter_candidates(self.report, self.context)
        self.assertEqual(result['rejections'], [])
        self.assertEqual(len(result['report']['findings']), 2)

    def test_existing_partial_limitations_survive_filtering(self):
        self.report['coverage'] = {'status': 'partial', 'limitations': ['Missing caller']}
        self.report['findings'][0]['severity'] = 'critical'
        result = filter_candidates(self.report, self.context)
        self.assertIn('Missing caller', result['report']['coverage']['limitations'])
        self.assertEqual(len(result['report']['coverage']['limitations']), 2)


if __name__ == '__main__':
    unittest.main()
