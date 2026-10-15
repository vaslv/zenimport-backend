from uuid import uuid4

from fastapi.testclient import TestClient
import httpx

from backend import storage
from backend.app import create_app
from backend.config import Settings
from capture_factory import Harness, TOKEN, record
from mirror_factory import Fixture, snapshot


def test_catalog_reads_sdk_and_info_only_reports_proved_freshness(tmp_path):
    f = Fixture(tmp_path / 'api.sqlite3')
    try:
        app = create_app(Settings(f.path, TOKEN, 'fixture-zenmoney-token'), clock=lambda: f.now, client_factory=lambda _: f.client, start_worker=False)
        with TestClient(app) as client:
            headers = {'Authorization': 'Bearer ' + TOKEN}
            assert client.get('/api/v1/info', headers=headers).json()['zenmoney_state'] == 'unavailable'
            assert f.requests == []
            f.responses.append(snapshot())
            assert client.get('/api/v1/zenmoney/accounts', headers=headers).status_code == 200
            info = client.get('/api/v1/info', headers=headers).json()
            assert info['zenmoney_state'] == 'ready' and info['last_zenmoney_refresh'] is not None
            assert client.get('/api/v1/zenmoney/accounts', headers=headers).status_code == 200
            assert len(f.requests) == 1
            f.now += 301
            f.responses.append(httpx.Response(401, text='fixture-sensitive-response'))
            unavailable = client.get('/api/v1/zenmoney/accounts', headers=headers)
            assert unavailable.status_code == 503 and 'fixture-sensitive' not in unavailable.text
            assert client.get('/api/v1/info', headers=headers).json()['zenmoney_state'] == 'unavailable'
    finally:
        f.close()


def test_capture_continues_when_sdk_is_down_and_delivery_waits_for_refresh(tmp_path):
    h = Harness(tmp_path / 'offline.sqlite3')
    try:
        connection = h.ready()
        before = len(h.sdk_requests)
        h.sdk_available = False
        command = str(uuid4())
        job = h.start(connection['id'], request_id=command).json()
        assert job['capture_state'] == 'planned' and job['delivery_state'] == 'blocked'
        assert any(e['code'] == 'zenmoney_unavailable' for e in job['errors'])
        after = len(h.sdk_requests)
        assert after == before + 2
        assert h.start(connection['id'], request_id=command).json()['id'] == job['id']
        assert len(h.sdk_requests) == after
        worker = str(uuid4())
        lease = h.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': worker}).json()
        auth = {'worker_id': worker, 'lease_epoch': lease['epoch']}
        interval = job['accounts'][0]['intervals'][0]
        assert h.page(job, interval, auth, records=[record(interval)]).status_code == 200
        assert h.complete(job, interval, auth, records=1).status_code == 200
        with storage.transaction(h.path, read_only=True) as db:
            assert db.execute('SELECT category_id,reason FROM category_predictions').fetchone()[:] == (None, 'zenmoney_unavailable')
            assert db.execute('SELECT count(*) FROM bank_transactions').fetchone()[0] == 1
        h.sdk_available = True
        assert h.client.get('/api/v1/zenmoney/accounts').status_code == 200
        resumed = h.client.get(f'/api/v1/jobs/{job["id"]}').json()
        assert resumed['delivery_state'] == 'waiting'
        assert not any(e['code'] == 'zenmoney_unavailable' for e in resumed['errors'])
    finally:
        h.close()


def test_identical_settings_retry_and_disabling_do_not_need_live_sdk(tmp_path):
    h = Harness(tmp_path / 'settings.sqlite3')
    try:
        connection = h.ready()
        account = connection['accounts'][0]['id']
        h.sdk_available = False
        h.now += 301
        count = len(h.sdk_requests)
        assert h.configure(account).status_code == 200
        assert len(h.sdk_requests) == count
        assert h.configure(account, version=1, selection='ignored').status_code == 200
        assert len(h.sdk_requests) == count
    finally:
        h.close()
