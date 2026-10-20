"""Deterministic synthetic reference output consumed by the cross-language gate."""
from hashlib import sha256
import json
from pathlib import Path

from .reference import ContractError, canonical_dto, execute, identity, run_scenario

ROOT = Path(__file__).resolve().parents[2] / 'fixtures/business/v1'


def run_suite():
    scenarios, future = {}, []
    for path in sorted((ROOT / 'scenarios').glob('*.json')):
        scenario = json.loads(path.read_text())
        if scenario['implemented_by_stage'] != 'L2':
            try:
                run_scenario(scenario)
            except ContractError as error:
                assert str(error) in {'unsupported_command', 'unsupported_scenario'}
                future.append(scenario['id'])
            else:
                raise AssertionError('Unimplemented future scenario falsely passed')
            continue
        result = run_scenario(scenario)
        assert result == scenario['expected'], scenario['id']
        scenarios[scenario['id']] = result
    arithmetic = []
    for row in json.loads((ROOT / 'arithmetic-vectors.json').read_text())['vectors']:
        value = {operation: execute('money.' + operation, {'left': row['left'], 'right': row['right'],
                 'values': [row['left'], row['right']], 'value': row['left']})
                 for operation in ['sum', 'subtract', 'compare', 'sign']}
        assert value == {key: row[key] for key in value}
        arithmetic.append(value)
    unicode = []
    for value, expected in json.loads((ROOT / 'unicode15.json').read_text())['vectors']:
        observed = identity(value)
        assert observed == expected, repr(value)
        unicode.append(observed)
    digest = lambda value: sha256(canonical_dto(value).encode()).hexdigest()
    return dict(scenarios=scenarios, future_rejected=future,
                arithmetic_count=len(arithmetic), arithmetic_digest=digest(arithmetic),
                unicode_count=len(unicode), unicode_digest=digest(unicode), external_writes=0)


if __name__ == '__main__':
    print(json.dumps(run_suite(), sort_keys=True, ensure_ascii=True, separators=(',', ':')))
