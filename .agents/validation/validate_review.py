"""Validate or filter review/2.0.0 candidates against trusted snapshot context.

This local contract utility has no network or publication side effects.
"""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/review.schema.json').read_text())
CONTEXT_SCHEMA = json.loads((ROOT / 'schemas/review-context.schema.json').read_text())
ENVELOPE_SCHEMA = deepcopy(SCHEMA)
ENVELOPE_SCHEMA['properties']['findings']['items'] = {}
for schema in (SCHEMA, CONTEXT_SCHEMA, ENVELOPE_SCHEMA):
    Draft202012Validator.check_schema(schema)
VALIDATOR = Draft202012Validator(SCHEMA)
CONTEXT_VALIDATOR = Draft202012Validator(CONTEXT_SCHEMA)
ENVELOPE_VALIDATOR = Draft202012Validator(ENVELOPE_SCHEMA)
CANDIDATE_VALIDATOR = Draft202012Validator(SCHEMA['properties']['findings']['items'])


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-JSON constant')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def _position(location):
    return location['path'], location['side'], location['line']


def _prepare(report, context):
    CONTEXT_VALIDATOR.validate(context)
    ENVELOPE_VALIDATOR.validate(report)
    for field in ('run_id', 'base_sha', 'head_sha', 'merge_base_sha', 'rules_version'):
        if report[field] != context[field]:
            raise ValueError(f'{field} differs from trusted context')
    if not context['input_complete'] and report['coverage']['status'] == 'complete':
        raise ValueError('incomplete input cannot produce complete coverage')
    if report['hypotheses'] and report['coverage']['status'] == 'complete':
        raise ValueError('missing context in hypotheses requires partial coverage')
    rules = {rule['rule_id']: rule for rule in context['rules']}
    if len(rules) != len(context['rules']):
        raise ValueError('duplicate rule_id in trusted snapshot')
    locations = {_position(a) for a in context['locations']}
    anchors = {_position(a) for a in context['anchors']}
    if not anchors.issubset(locations):
        raise ValueError('trusted changed anchors must be supplied locations')
    return {'rules': rules, 'locations': locations, 'anchors': anchors}


def _candidate_reason(finding, trusted):
    if not CANDIDATE_VALIDATOR.is_valid(finding):
        return 'invalid_candidate_schema'
    if not math.isfinite(finding['confidence']):
        return 'invalid_confidence'
    rule = trusted['rules'].get(finding['rule_id'])
    if rule is None or not rule['enabled']:
        return 'rule_not_enabled'
    if finding['severity'] != rule['severity']:
        return 'rule_severity_mismatch'
    if _position(finding['anchor']) not in trusted['locations']:
        return 'unknown_location'
    if any(_position(a) not in trusted['anchors'] for a in finding['related_changed_lines']):
        return 'unrelated_changed_line'
    return None


def _fingerprint(finding):
    # Exact candidates only; paraphrases require the owning deduplication policy.
    return json.dumps(finding, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate(report, context):
    """Reject an invalid envelope or any invalid/duplicate candidate."""
    trusted = _prepare(report, context)
    VALIDATOR.validate(report)
    seen = set()
    for finding in report['findings']:
        reason = _candidate_reason(finding, trusted)
        if reason:
            raise ValueError(reason)
        key = _fingerprint(finding)
        if key in seen:
            raise ValueError('duplicate_candidate')
        seen.add(key)
    return report


def filter_candidates(report, context):
    """Preserve valid candidates and return safe rejection indices/reason codes.

    Invalid envelopes/context still raise. Inputs are never mutated.
    """
    trusted = _prepare(report, context)
    cleaned = deepcopy(report)
    cleaned['findings'] = []
    rejected = []
    seen = set()
    invalid = False
    for index, finding in enumerate(report['findings']):
        reason = _candidate_reason(finding, trusted)
        if reason is None:
            key = _fingerprint(finding)
            if key in seen:
                reason = 'duplicate_candidate'
            else:
                seen.add(key)
                cleaned['findings'].append(deepcopy(finding))
        if reason:
            rejected.append({'index': index, 'reason': reason})
            invalid = invalid or reason != 'duplicate_candidate'
    if invalid:
        cleaned['coverage']['status'] = 'partial'
        limitation = 'Invalid candidates were excluded; review coverage is incomplete.'
        if limitation not in cleaned['coverage']['limitations']:
            cleaned['coverage']['limitations'].append(limitation)
    validate(cleaned, context)
    return {'report': cleaned, 'rejections': rejected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--filter-candidates', action='store_true',
                        help='Print a validated report and safe rejection diagnostics as JSON.')
    args = parser.parse_args()
    try:
        report, context = read_json(args.report), read_json(args.context)
        if args.filter_candidates:
            result = filter_candidates(report, context)
            print(json.dumps(result, indent=2, allow_nan=False))
        else:
            validate(report, context)
            print('VALID: schema, snapshot, rules, coverage, locations and duplicates')
    except ValidationError as error:
        # Do not log the rejected value, property name or model-controlled path.
        print('INVALID: schema violation', file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, OSError):
        print('INVALID: report or trusted context failed validation', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
