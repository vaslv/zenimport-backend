from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version, PackageNotFoundError
from importlib.util import find_spec
from pathlib import Path

import httpx
import pytest
from zenmoney_api import Client, transactions as tx
from zenmoney_api.protocol import decode_json


def test_installed_vendored_sdk_supports_unreviewed_exact_creation():
    assert version('zenmoney-python') == '0.7.0'
    with pytest.raises(PackageNotFoundError):
        version('zenmoney-api-client')
    wheel = Path('vendor/zenmoney_python-0.7.0-py3-none-any.whl')
    assert sha256(wheel.read_bytes()).hexdigest() == Path('vendor/SHA256SUMS').read_text().split()[0]
    amount = Decimal('1234567890123456.123456')
    identifier = '00000000-0000-4000-8000-000000000001'

    def handler(request):
        operation = decode_json(request.content)['transaction'][0]
        assert operation['id'] == identifier and operation['viewed'] is False
        assert operation['outcome'] == amount and operation['tag'] is None
        return httpx.Response(200, json={'serverTimestamp': 101})

    item = tx.expense(id=identifier, user=1, now=100, date='2026-10-15',
                      account='fixture-account', instrument=1, amount=amount, viewed=False)
    with Client('fixture-token', transport=httpx.MockTransport(handler), clock=lambda: 100) as client:
        assert client.upsert('transaction', [item]).server_timestamp == 101


def test_runtime_has_no_legacy_editors_or_browser_checkout():
    for module in ('backend.creation', 'backend.operations', 'backend.reconciliation', 'backend.category_drafts'):
        assert find_spec(module) is None
    assert not Path('extension').exists()
    assert not Path('tools').exists()
    assert not Path('.env').exists()
