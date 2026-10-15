"""Stateful fake ZenMoney behind the real pinned SDK; no outside network."""
from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from zenmoney_api import Client
from zenmoney_api.protocol import decode_json, encode_json

from backend import storage
from backend.app import create_app
from backend.config import Settings
from capture_factory import Harness, NOW, TARGET, TOKEN, record
from mirror_factory import snapshot


class FakeZenMoney:
    def __init__(self, path):
        self.path, self.cursor = path, 100
        self.transactions, self.deletions, self.writes, self.reads = {}, [], [], []
        self.mode, self.read_failure = 'success', None
        self.before_write, self.before_read = None, None
        self.data = snapshot(account=[{'id': TARGET, 'user': 1, 'instrument': 2, 'type': 'checking', 'title': 'Fixture VND'},
            {'id': 'fixture-other-vnd', 'user': 1, 'instrument': 2, 'type': 'cash', 'title': 'Other VND'}])

    def handle(self, request):
        assert request.url.path == '/v8/diff/'
        body = decode_json(request.content)
        self.cursor = max(self.cursor, body['serverTimestamp']) + 1
        if 'transaction' not in body:
            assert set(body) <= {'serverTimestamp', 'currentClientTimestamp', 'forceFetch'}
            self.reads.append(body)
            if self.before_read:
                self.before_read()
            if self.read_failure:
                return httpx.Response(self.read_failure, text='fixture-private-response')
            result = deepcopy(self.data) | {'serverTimestamp': self.cursor, 'transaction': list(self.transactions.values()), 'deletion': self.deletions}
            return httpx.Response(200, content=encode_json(result))
        assert set(body) <= {'serverTimestamp', 'currentClientTimestamp', 'transaction'}
        self.writes.append(deepcopy(body['transaction']))
        # Intent must have committed and released SQLite before network.
        with storage.transaction(self.path) as db:
            for tx in body['transaction']:
                row = db.execute('SELECT state,ever_attempted,payload FROM deliveries WHERE zen_id=?', (tx['id'],)).fetchone()
                assert row[:2] == ('attempting', 1)
                assert decode_json(row['payload'].encode()) == tx
                assert db.execute("SELECT 1 FROM delivery_attempts WHERE zen_id=? AND outcome='intent'", (tx['id'],)).fetchone()
            db.execute("INSERT OR REPLACE INTO service_meta VALUES ('fixture-network-write-lock-free','yes')")
        if self.before_write:
            self.before_write()
        if self.mode in ('reject', 'timeout_before'):
            if self.mode == 'reject':
                return httpx.Response(400, text='fixture-private-body')
            raise httpx.ReadTimeout('fixture-private-timeout')
        accepted = body['transaction'][:1] if self.mode == 'partial' else body['transaction']
        for tx in accepted:
            self.transactions[tx['id']] = deepcopy(tx)
        if self.mode == 'edited':
            for tx in accepted:
                self.transactions[tx['id']].update(viewed=True, tag=['category-b'], comment='Changed in mobile', outcome=999)
        if self.mode == 'deleted':
            for tx in accepted:
                del self.transactions[tx['id']]
                self.deletions.append({'object': 'transaction', 'id': tx['id']})
        if self.mode in ('timeout_after', 'edited', 'deleted'):
            raise httpx.ReadTimeout('fixture-response-lost')
        if self.mode == 'crash_after':
            raise SystemExit('fixture-process-crash-after-server-commit')
        if self.mode == 'malformed':
            return httpx.Response(200, content=b'not-json')
        if self.mode == 'no_echo':
            return httpx.Response(200, content=encode_json({'serverTimestamp': self.cursor}))
        return httpx.Response(200, content=encode_json({'serverTimestamp': self.cursor, 'transaction': accepted}))


class DeliveryHarness(Harness):
    def __init__(self, path, *, start_worker=False):
        self.path, self.now = path, NOW
        self.remote = FakeZenMoney(path)
        self.sdk = Client('fixture-sdk-token', transport=httpx.MockTransport(self.remote.handle), clock=lambda: self.now)
        self.app = create_app(Settings(path, TOKEN, 'fixture-sdk-token'), clock=lambda: self.now,
            client_factory=lambda _: self.sdk, start_worker=start_worker)
        self.context = TestClient(self.app)
        self.client = self.context.__enter__()
        self.client.headers['Authorization'] = 'Bearer ' + TOKEN
        self.worker = self.app.state.delivery
        self.connection = self.ready()
        self.account_id = self.connection['accounts'][0]['id']

    def capture(self, count=1, *, complete=True, changes=None):
        job, auth = self.leased(self.connection['id'])
        interval = job['accounts'][0]['intervals'][0]
        rows = [record(interval, 'fixture-' + str(i), **({'amount': '100'} | (changes or {}))) for i in range(count)]
        result = self.page(job, interval, auth, records=rows)
        assert result.status_code == 200, result.text
        if complete:
            result = self.complete(job, interval, auth, records=count)
            assert result.status_code == 200, result.text
        return job, interval, auth, rows

    def states(self):
        with storage.transaction(self.path, read_only=True) as db:
            return [r[0] for r in db.execute('SELECT state FROM bank_processing ORDER BY bank_id')]

    def due(self):
        self.now += 301
        self.worker.run_once()

    def bank_ids(self):
        with storage.transaction(self.path, read_only=True) as db:
            return dict(db.execute('SELECT external_id,id FROM bank_transactions'))

    def job(self, job):
        return self.client.get('/api/v1/jobs/' + job['id']).json()
