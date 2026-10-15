"""Cross-project boundaries: precision, identity, completeness and safe setup."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend import sync_contracts as c

EXAMPLES = json.loads((Path(__file__).parents[1] / 'fixtures/sync-v1/examples.json').read_text())


@pytest.mark.parametrize('name', sorted(EXAMPLES))
def test_public_examples_round_trip(name):
    model = getattr(c, name)
    data = EXAMPLES[name]
    parsed = model.model_validate_json(json.dumps(data))
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


def record(**changes):
    data = deepcopy(EXAMPLES['CapturePage']['records'][0])
    data.update(changes)
    return data


@pytest.mark.parametrize('amount', [0, 100.1, True, '0', '-10', '+10', '01', '1e3', '1,000', 'NaN', '1.0000000000001'])
def test_amount_cannot_coerce_sign_precision_or_json_number(amount):
    with pytest.raises(ValidationError):
        c.BankRecord.model_validate(record(amount=amount))


def test_large_fraction_keeps_all_digits_and_direction_is_separate():
    amount = '123456789012345678901234.123456789012'
    data = c.BankRecord.model_validate(record(amount=amount, currency='USD', direction='income'))
    assert json.loads(data.model_dump_json())['amount'] == amount
    assert Decimal(data.amount) == Decimal(amount)
    assert data.direction == 'income'


@pytest.mark.parametrize('occurred_at', ['2026-10-15T10:12:25', '2026-10-15 10:12:25+07:00', 1792020000, True])
def test_timestamp_must_have_explicit_offset(occurred_at):
    with pytest.raises(ValidationError):
        c.BankRecord.model_validate(record(occurred_at=occurred_at))


@pytest.mark.parametrize('value', [1792020000, '2026-10-15T00:00:00Z', '20261015', True])
def test_calendar_date_cannot_be_timestamp(value):
    with pytest.raises(ValidationError):
        c.BankRecord.model_validate(record(local_date=value))


@pytest.mark.parametrize('changes', [
    {'zenmoney_account_id': None}, {'import_start': None}, {'expected_version': True},
    {'selection': 'ignored'}, {'selection': 'unconfigured'},
])
def test_invalid_or_ambiguous_account_settings_rejected(changes):
    data = EXAMPLES['AccountSettings'] | changes
    with pytest.raises(ValidationError):
        c.AccountSettings.model_validate(data)


def test_ignored_does_not_mean_unconfigured():
    ignored = c.AccountSettings(expected_version=1, selection='ignored')
    unconfigured = c.AccountSettings(expected_version=1, selection='unconfigured')
    assert ignored.selection != unconfigured.selection


def test_empty_period_has_valid_page_and_completion():
    data = EXAMPLES['CapturePage'] | {'total_records': 0, 'records': []}
    page = c.CapturePage.model_validate(data)
    complete = c.CaptureComplete.model_validate(EXAMPLES['CaptureComplete'] | {'total_records': 0})
    assert page.total_records == complete.total_records == 0


@pytest.mark.parametrize('changes', [
    {'page_number': 2}, {'total_records': 0}, {'records': []},
    {'total_pages': True}, {'records': EXAMPLES['CapturePage']['records'] * 2, 'total_records': 2},
])
def test_page_cannot_claim_inconsistent_counts_or_duplicate_ids(changes):
    with pytest.raises(ValidationError):
        c.CapturePage.model_validate(EXAMPLES['CapturePage'] | changes)


def test_discovery_cannot_silently_merge_accounts():
    data = deepcopy(EXAMPLES['DiscoverConnection'])
    data['accounts'].append(data['accounts'][0])
    with pytest.raises(ValidationError):
        c.DiscoverConnection.model_validate(data)


def test_discovery_requires_explanation_of_unsupported_account():
    data = deepcopy(EXAMPLES['DiscoverConnection'])
    data['accounts'][1]['unsupported_reason'] = None
    with pytest.raises(ValidationError):
        c.DiscoverConnection.model_validate(data)


def test_unknown_timezone_cannot_define_import_day():
    data = deepcopy(EXAMPLES['DiscoverConnection'])
    data['capabilities']['timezone'] = 'unknown/timezone'
    with pytest.raises(ValidationError):
        c.DiscoverConnection.model_validate(data)


@pytest.mark.parametrize('name', ['StartSync', 'CapturePage', 'AccountSettings'])
def test_extra_authorization_or_legacy_review_fields_rejected(name):
    for key in ('token', 'cookies', 'reviewed_candidates'):
        with pytest.raises(ValidationError):
            getattr(c, name).model_validate(EXAMPLES[name] | {key: 'not-a-real-secret'})


def test_invalid_range_rejected():
    with pytest.raises(ValidationError):
        c.DateRange(start='2026-10-16', end='2026-10-15')


def test_schemas_are_json_serializable_and_references_resolve():
    schemas = json.loads(json.dumps(c.json_schemas()))
    assert schemas['CapturePage']['properties']['records']['maxItems'] == 500
    assert schemas['CapturePage']['additionalProperties'] is False

    def check(value, root):
        if isinstance(value, dict):
            if '$ref' in value:
                target = root
                for part in value['$ref'].removeprefix('#/').split('/'):
                    target = target[part]
            for child in value.values():
                check(child, root)
        elif isinstance(value, list):
            for child in value:
                check(child, root)

    for schema in schemas.values():
        check(schema, schema)
