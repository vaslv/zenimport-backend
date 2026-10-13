"""Application boundaries around the independently installed API client."""
import pytest
from backend.zenmoney import ZenMoney
from backend.creation import matches
from zenimport_zenmoney import CREATION_DEFAULTS


def test_read_wrapper_rejects_writes():
    with pytest.raises(ValueError):
        ZenMoney('live', 'secret').diff({'serverTimestamp': 0, 'transaction': []})


def test_creation_verification_accepts_wire_defaults():
    payload = {'id': 'local-id', 'created': 100, 'changed': 100, 'tag': None,
               'income': 0, 'outcome': 42}
    assert matches(payload, {**CREATION_DEFAULTS, **payload})
    assert not matches(payload, {**CREATION_DEFAULTS, **payload, 'outcome': 43})
