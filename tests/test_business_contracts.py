from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata

import pytest

from contracts.reference import ContractError, FixtureContext, execute, run_scenario
from contracts.suite import ROOT, run_suite


def test_pinned_contract_copy_has_no_drift():
    pin = json.loads((ROOT.parent / 'revision.json').read_text())
    assert re.fullmatch('[0-9a-f]{40}', pin['source_commit'])
    assert sha256((ROOT / 'manifest.json').read_bytes()).hexdigest() == pin['manifest_sha256']
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    files = sorted(str(path.relative_to(ROOT)) for path in ROOT.rglob('*') if path.is_file() and path.name != 'manifest.json')
    assert files == sorted(manifest['files'])
    for name, digest in manifest['files'].items():
        assert sha256((ROOT / name).read_bytes()).hexdigest() == digest, name


def test_real_reference_behavior_matches_shared_expected_results():
    assert unicodedata.unidata_version == '15.0.0'
    result = run_suite()
    assert len(result['scenarios']) == 8
    assert result['arithmetic_count'] == 500 and result['unicode_count'] > 6000
    assert len(result['future_rejected']) == 4 and result['external_writes'] == 0


@pytest.mark.parametrize('operation,data', [
    ('money.sum', {'values': [0.1]}),
    ('wire.canonical', {'value': {'amount': 0.1}}),
    ('wire.canonical', {'value': {'id': 9007199254740993}}),
])
def test_unsafe_binary_float_or_integer_dtos_are_rejected(operation, data):
    with pytest.raises(ContractError):
        execute(operation, data)


def test_unimplemented_commands_and_state_assertions_do_not_fake_a_pass():
    example = json.loads((ROOT / 'scenarios/money-large.json').read_text())
    with pytest.raises(ContractError, match='unsupported_command'):
        run_scenario({**example, 'commands': [{'operation': 'delivery.advance', 'input': {}}]})
    with pytest.raises(ContractError, match='unsupported_scenario'):
        run_scenario({**example, 'expected': {**example['expected'], 'state_assertions': [{'unknown': True}]}})


def test_fixture_dependencies_are_deterministic_and_bounded():
    identifier = '00000000-0000-4000-8000-000000000001'
    ids = [identifier]
    context = FixtureContext('9007199254740993', ids)
    ids[0] = 'changed'
    assert context.now == 9007199254740993 and context.next_uuid() == identifier
    assert context.remaining == 0
    with pytest.raises(ContractError, match='unsupported_scenario'):
        context.next_uuid()
