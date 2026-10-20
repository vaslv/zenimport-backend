"""Keep shared browser screening/Decimal cases tied to actual Python behavior."""
import json
from pathlib import Path
from scripts.graph_value_vectors import generate


def test_archive_value_vectors_match_the_actual_reference():
    expected=json.loads(Path('fixtures/business/v1/archive-value-vectors.json').read_text())
    assert expected==generate()
    assert len(expected['decimals'])==538
    assert len(expected['metadata'])>400
