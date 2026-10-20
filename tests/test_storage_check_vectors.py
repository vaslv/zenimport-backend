"""The browser predicates are compared with actual SQLite, not another port."""
import json
from pathlib import Path

from scripts.storage_check_vectors import generate


def test_frozen_sqlite_check_vectors_match_the_current_reference():
    expected=json.loads(Path('fixtures/business/v1/sqlite-check-vectors.json').read_text())
    assert expected==generate()
    assert len(expected['vectors'])>2000
