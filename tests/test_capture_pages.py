from copy import deepcopy
from uuid import uuid4

import pytest

from backend import records, storage
from capture_factory import Harness, record


@pytest.fixture
def setup(tmp_path):
    h = Harness(tmp_path / 'pages.sqlite3')
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    yield h, job, job['accounts'][0]['intervals'][0], auth
    h.close()


def test_out_of_order_pages_commit_records_before_ack_and_complete(setup):
    h, job, interval, auth = setup
    second = record(interval, 'second')
    assert h.page(job, interval, auth, number=2, records=[second], total_pages=2, total_records=2).status_code == 200
    incomplete = h.complete(job, interval, auth, pages=2, records=2)
    assert incomplete.status_code == 409 and incomplete.json()['error']['code'] == 'history_incomplete'
    assert h.page(job, interval, auth, records=[record(interval)], total_pages=2, total_records=2).status_code == 200
    assert h.complete(job, interval, auth, pages=2, records=2).json()['state'] == 'complete'
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM captured_records').fetchone()[0] == 2
        assert db.execute('SELECT amount FROM bank_transactions LIMIT 1').fetchone()[0] == '9007199254740993'


def test_exact_page_repeat_after_lease_expiry_or_complete_is_ack_only(setup):
    h, job, interval, auth = setup
    data = [record(interval)]
    assert h.page(job, interval, auth, records=data).json()['repeated'] is False
    h.now += 121
    repeat = h.page(job, interval, {'worker_id': str(uuid4()), 'lease_epoch': 90}, records=data)
    assert repeat.status_code == 200 and repeat.json()['repeated'] is True
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_versions').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM capture_pages').fetchone()[0] == 1


def test_changed_same_page_fails_interval_and_preserves_original(setup):
    h, job, interval, auth = setup
    original = [record(interval)]
    assert h.page(job, interval, auth, records=original).status_code == 200
    changed = h.page(job, interval, auth, records=[record(interval, amount='123')])
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'idempotency_conflict'
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT state FROM capture_intervals WHERE id=?', (interval['id'],)).fetchone()[0] == 'failed'
        assert db.execute('SELECT amount FROM bank_transactions').fetchone()[0] == original[0]['amount']
        assert db.execute('SELECT count(*) FROM coverage').fetchone()[0] == 0


def test_totals_changed_on_next_page_fails_snapshot(setup):
    h, job, interval, auth = setup
    assert h.page(job, interval, auth, records=[record(interval)], total_pages=2, total_records=2).status_code == 200
    changed = h.page(job, interval, auth, number=2, records=[record(interval, 'other')], total_pages=3, total_records=3)
    assert changed.status_code == 409
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0] == 1
        assert db.execute('SELECT state FROM capture_intervals WHERE id=?', (interval['id'],)).fetchone()[0] == 'failed'


def test_duplicate_identity_across_pages_rolls_back_whole_page(setup):
    h, job, interval, auth = setup
    assert h.page(job, interval, auth, records=[record(interval)], total_pages=2, total_records=3).status_code == 200
    failure = h.page(job, interval, auth, number=2, records=[record(interval, 'new-record'), record(interval, description='Changed')], total_pages=2, total_records=3)
    assert failure.status_code == 409 and failure.json()['error']['code'] == 'history_incomplete'
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM bank_versions').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM capture_pages').fetchone()[0] == 1


@pytest.mark.parametrize('changes,code', [
    ({'amount': '1.1'}, 'unsupported_currency'), ({'balance': '1.1'}, 'unsupported_currency'),
    ({'currency': 'USD'}, 'unsupported_currency'), ({'details_state': 'unavailable'}, 'validation_error'),
    ({'details': {'cookies': 'fixture-secret-must-not-leak'}}, 'validation_error'),
    ({'occurred_at': '2026-12-15T10:12:25+07:00'}, 'capture_context_changed'),
])
def test_server_validates_currency_scale_details_and_date(setup, changes, code):
    h, job, interval, auth = setup
    response = h.page(job, interval, auth, records=[record(interval, **changes)])
    assert response.status_code in (409, 422) and response.json()['error']['code'] == code
    assert 'fixture-secret-must-not-leak' not in response.text
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0] == 0


def test_cross_job_interval_cannot_be_acknowledged_or_completed(setup):
    h, job, interval, auth = setup
    assert h.page(job, interval, auth).status_code == 200
    other_connection = h.ready(profile='other-profile', names=('other-account',))
    other_job, other_auth = h.leased(other_connection['id'])
    assert h.page(other_job, interval, other_auth).status_code == 404
    assert h.complete(other_job, interval, other_auth).status_code == 404


def test_empty_page_advances_complete_frontier_not_delivery_state(setup):
    h, job, interval, auth = setup
    assert h.complete(job, interval, auth).status_code == 409
    assert h.page(job, interval, auth).status_code == 200
    assert h.complete(job, interval, auth).status_code == 200
    repeat = h.complete(job, interval, {'worker_id': str(uuid4()), 'lease_epoch': 42})
    assert repeat.status_code == 200
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT last_complete_end FROM bank_accounts').fetchone()[0] == interval['period']['end']
        assert db.execute('SELECT count(*) FROM coverage').fetchone()[0] == 1
        assert db.execute('SELECT delivery_state FROM capture_jobs').fetchone()[0] == 'waiting'


def test_record_revision_preserves_delivered_identity_on_reread(setup):
    h, job, interval, auth = setup
    assert h.page(job, interval, auth, records=[record(interval)]).status_code == 200
    assert h.complete(job, interval, auth, records=1).status_code == 200
    with storage.transaction(h.path) as db:
        identifier = db.execute('SELECT id FROM bank_transactions').fetchone()[0]
        db.execute("UPDATE bank_processing SET state='delivered'")
    h.client.post(f'/api/v1/jobs/{job["id"]}/stop', json={})
    new_job, new_auth = h.leased(job['connection_id'])
    new_interval = new_job['accounts'][0]['intervals'][0]
    # Explicitly reread an overlapping day of the previously completed interval.
    value = record(new_interval, description='New details')
    assert h.page(new_job, new_interval, new_auth, records=[value]).status_code == 200
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT id FROM bank_transactions').fetchone()[0] == identifier
        assert db.execute('SELECT revision FROM bank_transactions').fetchone()[0] == 2
        assert db.execute('SELECT state FROM bank_processing').fetchone()[0] == 'delivered'


def test_path_page_number_must_equal_body(setup):
    h, job, interval, auth = setup
    payload = {**auth, 'page_number': 1, 'total_pages': 1, 'total_records': 0, 'records': []}
    response = h.client.put(f'/api/v1/jobs/{job["id"]}/intervals/{interval["id"]}/pages/2', json=payload)
    assert response.status_code == 422


def test_more_nonempty_pages_than_total_records_is_rejected(setup):
    h, job, interval, auth = setup
    response = h.page(job, interval, auth, records=[record(interval)], total_pages=3, total_records=2)
    assert response.status_code == 409 and response.json()['error']['code'] == 'history_incomplete'
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM capture_pages').fetchone()[0] == 0
