"""Shared wire fixtures remain actual observations of the Python participant."""
import json
from pathlib import Path
from scripts.participant_wire_vectors import vectors


def test_shared_participant_wire_vectors_are_independently_recomputed():
    assert vectors()==json.loads(Path('fixtures/business/v1/participant-wire-vectors.json').read_text())
