"""BIDV wire policy and durable capture, using a disposable synthetic database."""
import pytest

from backend import storage
from capture_factory import Harness, record


@pytest.fixture
def h(tmp_path):
    value = Harness(tmp_path / 'bidv.sqlite3')
    yield value
    value.close()


def test_bidv_account_identity_does_not_alias_vietinbank_and_reuses_mapping(h):
    viet = h.ready(names=('12345678',))
    bidv = h.ready(bank='bidv', names=('12345678',))
    assert bidv['accounts'][0]['id'] != viet['accounts'][0]['id']
    again = h.discover(bank='bidv', names=('12345678',), profile='second-browser')
    assert again['accounts'][0]['id'] == bidv['accounts'][0]['id']
    assert again['accounts'][0]['selection'] == 'enabled'


def test_bidv_postings_with_shared_reference_survive_repeated_capture(h):
    connection = h.ready(bank='bidv')
    for _ in range(2):
        job, auth = h.leased(connection['id'])
        for account in job['accounts']:
            for interval in account['intervals']:
                rows = [record(interval, 'bidv:f1:fixture-fee', amount='9900', details={'referenceNumber': 'shared-reference'}),
                        record(interval, 'bidv:f1:fixture-principal', amount='100000', details={'referenceNumber': 'shared-reference'})]
                # Stable source records belong to one day, not every interval.
                for row in rows:
                    row.update(local_date='2026-10-10', occurred_at='2026-10-10T10:12:25+07:00')
                if not interval['period']['start'] <= '2026-10-10' <= interval['period']['end']:
                    rows = []
                assert h.page(job, interval, auth, records=rows).status_code == 200
                assert h.complete(job, interval, auth, records=len(rows)).status_code == 200
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM bank_versions').fetchone()[0] == 2


@pytest.mark.parametrize('bank,details', [('bidv', {'trxId': 'not-bidv'}), ('vietinbank', {'referenceNumber': 'not-vietinbank'}),
                                        ('bidv', {'Authorization': 'fixture-secret'})])
def test_bank_specific_detail_allowlist_does_not_leak_between_connectors(h, bank, details):
    connection = h.ready(bank=bank)
    job, auth = h.leased(connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    result = h.page(job, interval, auth, records=[record(interval, details=details)])
    assert result.status_code == 422
    assert result.json()['error']['code'] == 'validation_error'
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [{'currencies': ['USD']}, {'max_window_days': 31}, {'details': 'separate'}])
def test_bidv_rejects_unverified_capabilities(h, changes):
    caps = {'timezone': 'Asia/Ho_Chi_Minh', 'currencies': ['VND'], 'max_window_days': 30, 'details': 'included', **changes}
    with pytest.raises(AssertionError):
        h.discover(bank='bidv', capabilities=caps)
