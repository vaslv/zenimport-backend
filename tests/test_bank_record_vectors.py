import json
from pathlib import Path
from scripts.bank_record_vectors import generate


def test_shared_bank_record_observations_match_actual_python():
    expected=json.loads(Path('fixtures/business/v1/bank-record-vectors.json').read_text())
    assert generate()==expected
    assert len(expected['records'])>100
