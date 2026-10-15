"""Artificial accounts, mirror and capture requests shared by Docker tests."""
from datetime import datetime, timezone
import json
from uuid import uuid4
import httpx
from zenmoney_api import Client
from zenmoney_api.protocol import KINDS, decode_json, encode_json

from fastapi.testclient import TestClient

from backend import storage
from backend.app import create_app
from backend.config import Settings

TOKEN = 'fixture-backend-token-for-B3-capture'
TARGET = 'fixture-zenmoney-vnd'
NOW = int(datetime(2026, 10, 15, 5, tzinfo=timezone.utc).timestamp())


class Harness:
    def __init__(self, path):
        self.path, self.now = path, NOW
        self.sdk_available = True
        self.sdk_requests = []
        def respond(request):
            body = decode_json(request.content)
            assert set(body) <= {'serverTimestamp', 'currentClientTimestamp', 'forceFetch'}
            self.sdk_requests.append(body)
            if not self.sdk_available:
                raise httpx.ConnectError('fixture SDK unavailable')
            result = {'serverTimestamp': body['serverTimestamp'] + 1}
            if body['serverTimestamp'] == 0:
                result.update({kind: [] for kind in KINDS})
                with storage.transaction(path, read_only=True) as db:
                    for kind, data in db.execute('SELECT kind,data FROM zen_entities WHERE deleted=0'):
                        result[kind].append(decode_json(data.encode()))
            return httpx.Response(200, content=encode_json(result))
        self.app = create_app(Settings(path, TOKEN, 'fixture-read-only-sdk-token'), clock=lambda: self.now,
            client_factory=lambda token: Client(token, transport=httpx.MockTransport(respond), clock=lambda: self.now), start_worker=False)
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()
        self.client.headers['Authorization'] = 'Bearer ' + TOKEN

    def close(self):
        self.context.__exit__(None, None, None)

    def seed_mirror(self):
        with storage.transaction(self.path) as db:
            db.execute("INSERT OR REPLACE INTO service_meta VALUES ('zenmoney_refreshed_at',?)", (str(self.now),))
            for kind, identifier, data in [
                ('instrument', '2', {'id': 2, 'code': 'VND'}), ('instrument', '3', {'id': 3, 'code': 'USD'}),
                ('account', TARGET, {'id': TARGET, 'title': 'Fixture VND', 'type': 'checking', 'instrument': 2, 'user': 1}),
                ('account', 'fixture-usd', {'id': 'fixture-usd', 'title': 'Fixture USD', 'type': 'checking', 'instrument': 3, 'user': 1}),
                ('account', 'fixture-other-vnd', {'id': 'fixture-other-vnd', 'title': 'Other VND', 'type': 'cash', 'instrument': 2, 'user': 1}),
            ]:
                db.execute('INSERT OR REPLACE INTO zen_entities VALUES (?,?,?,NULL,0,NULL)', (kind, identifier, json.dumps(data)))

    def discover(self, *, profile='fixture-profile', scope='bank', names=('account-1',), **changes):
        payload = {'bank': 'vietinbank', 'profile_key': profile, 'profile_identity': 'user_selected', 'connector_version': '1.0.0',
                   'capabilities': {'timezone': 'Asia/Ho_Chi_Minh', 'currencies': ['VND'], 'max_window_days': 30,
                                    'earliest_date': None, 'details': 'included'}, 'accounts_complete': True,
                   'accounts': [{'source_key': name, 'identity_scope': scope, 'label': 'Fixture ' + name,
                                 'masked_number': '••1234', 'currency': 'VND', 'scale': 0, 'supported': True} for name in names]}
        payload.update(changes)
        response = self.client.post('/api/v1/connections/discover', json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    def configure(self, account_id, *, version=0, selection='enabled', start='2026-09-15', target=TARGET):
        data = {'expected_version': version, 'selection': selection}
        if selection == 'enabled':
            data.update(import_start=start, zenmoney_account_id=target)
        return self.client.put(f'/api/v1/accounts/{account_id}/settings', json=data)

    def ready(self, **kwargs):
        self.seed_mirror()
        connection = self.discover(**kwargs)
        for account in connection['accounts']:
            response = self.configure(account['id'])
            assert response.status_code == 200, response.text
        return connection

    def start(self, connection_id, *, request_id=None, **kwargs):
        return self.client.post('/api/v1/jobs', json={'connection_id': connection_id, 'request_id': str(request_id or uuid4()), **kwargs})

    def leased(self, connection_id):
        response = self.start(connection_id)
        assert response.status_code == 202, response.text
        job = response.json()
        worker = str(uuid4())
        lease = self.client.post(f'/api/v1/jobs/{job["id"]}/lease', json={'worker_id': worker})
        assert lease.status_code == 200, lease.text
        auth = {'worker_id': worker, 'lease_epoch': lease.json()['epoch']}
        return job, auth

    def page(self, job, interval, auth, *, number=1, records=None, total_pages=1, total_records=None):
        records = [] if records is None else records
        data = {**auth, 'page_number': number, 'total_pages': total_pages,
                'total_records': len(records) if total_records is None else total_records, 'records': records}
        return self.client.put(f'/api/v1/jobs/{job["id"]}/intervals/{interval["id"]}/pages/{number}', json=data)

    def complete(self, job, interval, auth, *, pages=1, records=0):
        return self.client.post(f'/api/v1/jobs/{job["id"]}/intervals/{interval["id"]}/complete',
                                json={**auth, 'total_pages': pages, 'total_records': records})

    def finish_empty(self, job, auth):
        for account in job['accounts']:
            for interval in account['intervals']:
                assert self.page(job, interval, auth).status_code == 200
                result = self.complete(job, interval, auth)
                assert result.status_code == 200, result.text


def record(interval, identifier='fixture-record', **changes):
    local_date = interval['period']['start']
    return {'external_id': identifier, 'identity_kind': 'bank', 'identity_version': 'fixture-1',
            'occurred_at': local_date + 'T10:12:25+07:00', 'local_date': local_date,
            'direction': 'expense', 'amount': '9007199254740993', 'currency': 'VND',
            'description': 'Fixture purchase', 'beneficiary_account': 'fixture-beneficiary',
            'beneficiary_name': 'Fixture Shop', 'details_state': 'complete', 'details': {'channel': '60'}, **changes}
