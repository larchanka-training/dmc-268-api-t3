"""Validate a model report against a schema and trusted VCS context.

This teaching utility does not run a model, execute a PR, or publish comments.
"""
import argparse
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/review.schema.json').read_text())
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f'non-JSON constant: {value}')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def validate(report, context):
    """Reject invalid output; context must come from the trusted orchestrator."""
    VALIDATOR.validate(report)
    for field in ('run_id', 'base_sha', 'head_sha'):
        if report[field] != context[field]:
            raise ValueError(f'{field} differs from trusted context')
    if type(context['input_complete']) is not bool:
        raise ValueError('input_complete must be boolean')
    coverage = report['coverage']
    if not context['input_complete'] and coverage['status'] == 'complete':
        raise ValueError('incomplete input cannot produce complete coverage')
    if report['hypotheses'] and coverage['status'] == 'complete':
        raise ValueError('missing context in hypotheses requires partial coverage')
    anchors = {(a['path'], a['side'], a['line']) for a in context['anchors']}
    for finding in report['findings']:
        a = finding['anchor']
        if (a['path'], a['side'], a['line']) not in anchors:
            raise ValueError('finding anchor is outside trusted diff')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--context', type=Path, required=True)
    args = parser.parse_args()
    try:
        validate(read_json(args.report), read_json(args.context))
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f'INVALID: {error}', file=sys.stderr)
        return 1
    except ValidationError as error:
        # Do not dump the model's raw data into logs.
        location = '/'.join(map(str, error.absolute_path)) or '<root>'
        print(f'INVALID: schema violation at {location}', file=sys.stderr)
        return 1
    print('VALID: schema, identity, coverage and diff anchors')
    return 0


if __name__ == '__main__':
    sys.exit(main())
