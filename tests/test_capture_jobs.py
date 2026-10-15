from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend import storage
from capture_factory import Harness, record


@pytest.fixture
def h(tmp_path):
    value = Harness(tmp_path / 'jobs.sqlite3')
    yield value
    value.close()


def test_request_repeat_attach_and_different_body_conflict(h):
    connection = h.ready()
    request_id = str(uuid4())
    first = h.start(connection['id'], request_id=request_id)
    repeat = h.start(connection['id'], request_id=request_id)
    attached_id = str(uuid4())
    attached = h.start(connection['id'], request_id=attached_id)
    assert first.status_code == repeat.status_code == attached.status_code == 202
    assert first.json()['id'] == repeat.json()['id'] == attached.json()['id']
    changed = h.start(connection['id'], request_id=request_id, reimport_from='2026-09-01')
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'idempotency_conflict'
    different_plan = h.start(connection['id'], reimport_from='2026-09-01')
    assert different_plan.status_code == 409 and different_plan.json()['error']['code'] == 'account_busy'
    h.client.post(f'/api/v1/jobs/{first.json()["id"]}/stop', json={})
    assert h.start(connection['id'], request_id=attached_id).json()['id'] == first.json()['id']


def test_concurrent_start_commands_and_shared_account_are_serialized(h):
    connection = h.ready()
    second = h.discover(profile='another-profile')
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda c: h.start(c), [connection['id'], second['id']]))
    assert sorted(r.status_code for r in responses) == [202, 409]
    failure = next(r for r in responses if r.status_code == 409)
    assert failure.json()['error']['code'] == 'account_busy'
    success = next(r.json() for r in responses if r.status_code == 202)
    owner_connection = success['connection_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        attached = list(pool.map(lambda _: h.start(owner_connection), range(2)))
    assert {r.json()['id'] for r in attached} == {success['id']}


def test_lease_owner_renewal_expiry_epoch_and_stale_writer(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    other = str(uuid4())
    busy = h.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': other})
    assert busy.status_code == 409 and busy.json()['error']['code'] == 'capture_busy'
    renewal = h.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': auth['worker_id']})
    assert renewal.json()['epoch'] == auth['lease_epoch']
    h.now += 120
    expired = h.page(job, interval, auth)
    assert expired.status_code == 409 and expired.json()['error']['code'] == 'lease_expired'
    progress = h.client.get(f'/api/v1/jobs/{job["id"]}').json()
    assert progress['capture_state'] == 'paused'
    lease = h.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': other})
    assert lease.json()['epoch'] == auth['lease_epoch'] + 1
    assert h.page(job, interval, auth).status_code == 409
    fresh_auth = {'worker_id': other, 'lease_epoch': lease.json()['epoch']}
    assert h.page(job, interval, fresh_auth).status_code == 200


def test_bank_login_pause_requires_next_click_and_preserves_pages(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    assert h.page(job, interval, auth).status_code == 200
    paused = h.client.post(f'/api/v1/jobs/{job["id"]}/capture-status', json={**auth, 'state': 'paused', 'code': 'bank_login_required'})
    assert paused.json()['capture_state'] == 'paused'
    blocked = h.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': auth['worker_id']})
    assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'bank_login_required'
    resumed = h.start(connection['id']).json()
    assert resumed['id'] == job['id'] and resumed['accounts'][0]['intervals'][0]['received_pages'] == 1
    new_lease = h.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': auth['worker_id']}).json()
    assert new_lease['epoch'] == auth['lease_epoch'] + 1


def test_stop_only_capture_preserves_complete_coverage_and_partial_pages(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    first, second = job['accounts'][0]['intervals']
    assert h.page(job, first, auth).status_code == 200
    assert h.complete(job, first, auth).status_code == 200
    assert h.page(job, second, auth, records=[record(second)]).status_code == 200
    stopped = h.client.post(f'/api/v1/jobs/{job["id"]}/stop', json={})
    assert stopped.status_code == 200 and stopped.json()['capture_state'] == 'cancelled'
    assert stopped.json()['delivery_state'] == 'waiting'
    assert h.client.post(f'/api/v1/jobs/{job["id"]}/stop', json={}).json() == stopped.json()
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT count(*) FROM coverage').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM capture_pages').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM account_capture_locks').fetchone()[0] == 0


def test_disabled_account_stops_it_but_other_account_can_finish(h):
    connection = h.ready(names=('one', 'two'))
    job, auth = h.leased(connection['id'])
    account_a, account_b = job['accounts']
    interval = account_a['intervals'][0]
    assert h.page(job, interval, auth).status_code == 200
    disabled = h.configure(account_a['account_id'], version=1, selection='ignored')
    assert disabled.status_code == 200
    assert h.complete(job, interval, auth).json()['error']['code'] == 'account_disabled'
    # The already committed identical page still has an ACK after disabling.
    assert h.page(job, interval, auth).json()['repeated'] is True
    for i in account_b['intervals']:
        assert h.page(job, i, auth).status_code == 200
        assert h.complete(job, i, auth).status_code == 200
    result = h.client.get(f'/api/v1/jobs/{job["id"]}').json()
    assert result['accounts'][1]['capture_state'] == 'complete'
    assert result['capture_state'] == 'cancelled'


def test_discovery_capability_change_invalidates_old_snapshot(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    interval = job['accounts'][0]['intervals'][0]
    h.discover(connector_version='2.0.0')
    old = h.client.get(f'/api/v1/jobs/{job["id"]}').json()
    assert old['capture_state'] == 'failed'
    assert old['errors'][0]['code'] == 'capture_context_changed'
    assert h.page(job, interval, auth).status_code == 409
    assert h.start(connection['id']).json()['id'] != job['id']


def test_first_import_and_rolling_overlap_are_calendar_based(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    intervals = job['accounts'][0]['intervals']
    assert [i['period'] for i in intervals] == [{'start': '2026-09-15', 'end': '2026-10-14'}, {'start': '2026-10-15', 'end': '2026-10-15'}]
    h.finish_empty(job, auth)
    h.now += 86400 * 3
    again = h.start(connection['id']).json()
    assert again['end_date'] == '2026-10-18'
    assert [i['period'] for i in again['accounts'][0]['intervals']] == [{'start': '2026-10-01', 'end': '2026-10-18'}]


def test_completed_later_island_does_not_skip_initial_gap(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    first, later = job['accounts'][0]['intervals']
    assert h.page(job, later, auth).status_code == 200
    assert h.complete(job, later, auth).status_code == 200
    h.client.post(f'/api/v1/jobs/{job["id"]}/stop', json={})
    view = h.client.get(f'/api/v1/connections/{connection["id"]}').json()
    assert view['accounts'][0]['last_complete_end'] is None
    next_job = h.start(connection['id']).json()
    assert [i['period'] for i in next_job['accounts'][0]['intervals']] == [first['period']]


def test_forced_reimport_ignores_coverage_and_failure_retains_gap(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    h.finish_empty(job, auth)
    forced = h.start(connection['id'], reimport_from='2026-10-10').json()
    assert forced['accounts'][0]['intervals'][0]['period'] == {'start': '2026-10-10', 'end': '2026-10-15'}
    h.client.post(f'/api/v1/jobs/{forced["id"]}/stop', json={})
    # A normal next click must retain the forced obligation even though older
    # coverage already proves those dates. It cannot silently fall back to overlap.
    repeated = h.start(connection['id']).json()
    assert repeated['accounts'][0]['intervals'][0]['period']['start'] <= '2026-10-10'
    with storage.transaction(h.path, read_only=True) as db:
        assert db.execute('SELECT remaining_json FROM reimport_ranges').fetchone()[0] == '[["2026-10-10","2026-10-15"]]'


@pytest.mark.parametrize('stamp,start,end', [
    ('2028-03-01T00:30:00+00:00', '2028-02-01', '2028-03-01'),
    ('2026-12-31T18:00:00+00:00', '2026-12-15', '2027-01-01')])
def test_bank_timezone_leap_year_and_new_year(h, stamp, start, end):
    h.now = int(datetime.fromisoformat(stamp).timestamp())
    h.seed_mirror()
    connection = h.discover()
    assert h.configure(connection['accounts'][0]['id'], start=start).status_code == 200
    job = h.start(connection['id']).json()
    assert job['end_date'] == end
    assert job['accounts'][0]['intervals'][0]['period']['start'] == start


def test_long_pause_keeps_windows_and_fixed_job_end(h):
    connection = h.ready()
    job, auth = h.leased(connection['id'])
    end = job['end_date']
    h.now += 86400 * 200
    resumed = h.start(connection['id']).json()
    assert resumed['id'] == job['id'] and resumed['end_date'] == end
