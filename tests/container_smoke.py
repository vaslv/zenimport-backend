"""HTTP + persisted bootstrap identity across container replacement; test volume only."""
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.storage import connect_readonly, transaction
from backend.sync_contracts import ErrorResponse, ServerInfo


def api(method, path, data=None):
    request = Request(base + '/api/v1' + path, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def capture_smoke(stage):
    progress_file = Path('/data/test-capture-progress.json')
    if stage == 'seed':
        stamp = int(time.time())
        end = datetime.fromtimestamp(stamp, ZoneInfo('Asia/Ho_Chi_Minh')).date()
        with transaction(Path('/data/zenimport.sqlite3')) as db:
            db.execute("INSERT INTO service_meta VALUES ('zenmoney_refreshed_at',?)", (str(stamp),))
            for kind, identifier, data in [('instrument', '2', {'id': 2, 'code': 'VND'}),
                ('account', 'fixture-http-target', {'id': 'fixture-http-target', 'type': 'checking', 'title': 'Fixture', 'instrument': 2, 'user': 1})]:
                db.execute('INSERT INTO zen_entities VALUES (?,?,?,NULL,0,NULL)', (kind, identifier, json.dumps(data)))
        connection = api('POST', '/connections/discover', {'bank': 'vietinbank', 'connector_version': 'fixture-smoke',
            'profile_key': 'fixture-smoke', 'profile_identity': 'user_selected', 'accounts_complete': True,
            'capabilities': {'timezone': 'Asia/Ho_Chi_Minh', 'currencies': ['VND'], 'max_window_days': 1, 'details': 'included'},
            'accounts': [{'source_key': 'fixture-http-source', 'identity_scope': 'bank', 'label': 'Fixture', 'masked_number': '••1234',
                          'currency': 'VND', 'scale': 0, 'supported': True}]})
        api('PUT', '/accounts/' + connection['accounts'][0]['id'] + '/settings', {'expected_version': 0, 'selection': 'enabled',
            'zenmoney_account_id': 'fixture-http-target', 'import_start': (end - timedelta(days=2)).isoformat()})
        command = {'connection_id': connection['id'], 'request_id': str(uuid4())}
        job = api('POST', '/jobs', command)
        worker = str(uuid4())
        lease = api('POST', '/jobs/' + job['id'] + '/lease', {'worker_id': worker})
        auth = {'worker_id': worker, 'lease_epoch': lease['epoch']}
        intervals = job['accounts'][0]['intervals']
        from capture_factory import record
        payload = {**auth, 'page_number': 1, 'total_pages': 1, 'total_records': 1, 'records': [record(intervals[0])]}
        prefix = '/jobs/' + job['id'] + '/intervals/'
        api('PUT', prefix + intervals[0]['id'] + '/pages/1', payload)
        api('POST', prefix + intervals[0]['id'] + '/complete', {**auth, 'total_pages': 1, 'total_records': 1})
        api('PUT', prefix + intervals[1]['id'] + '/pages/1', {**auth, 'page_number': 1, 'total_pages': 1, 'total_records': 0, 'records': []})
        progress_file.write_text(json.dumps({'job': job, 'command': command, 'auth': auth, 'payload': payload}))
    else:
        saved = json.loads(progress_file.read_text())
        job, auth = saved['job'], saved['auth']
        replay = api('POST', '/jobs', saved['command'])
        assert replay['id'] == job['id']
        assert replay['accounts'][0]['intervals'][0]['state'] == 'complete'
        assert replay['accounts'][0]['intervals'][1]['received_pages'] == 1
        prefix = '/jobs/' + job['id'] + '/intervals/'
        first = job['accounts'][0]['intervals'][0]
        assert api('PUT', prefix + first['id'] + '/pages/1', saved['payload'])['repeated'] is True
        lease = api('POST', '/jobs/' + job['id'] + '/lease', {'worker_id': auth['worker_id']})
        auth['lease_epoch'] = lease['epoch']
        for interval in job['accounts'][0]['intervals'][1:]:
            api('PUT', prefix + interval['id'] + '/pages/1', {**auth, 'page_number': 1, 'total_pages': 1, 'total_records': 0, 'records': []})
            api('POST', prefix + interval['id'] + '/complete', {**auth, 'total_pages': 1, 'total_records': 0})
        finished = api('GET', '/jobs/' + job['id'])
        assert finished['capture_state'] == 'complete' and finished['delivery_state'] == 'waiting'
        assert finished['delivery']['pending'] == 1

base = os.environ['BASE_URL']
token = os.environ['API_TOKEN']
with urlopen(base + '/health', timeout=5) as response:
    assert json.load(response) == {'status': 'ok'}
try:
    urlopen(base + '/api/v1/info', timeout=5)
except HTTPError as exc:
    assert exc.code == 401
    assert ErrorResponse.model_validate(json.load(exc)).error.code == 'unauthorized'
else:
    raise AssertionError('Protected info accepted without credentials')
request = Request(base + '/api/v1/info', headers={'Authorization': 'Bearer ' + token})
with urlopen(request, timeout=5) as response:
    info = ServerInfo.model_validate(json.load(response))
    assert info.api_version == 1 and info.zenmoney_state == 'not_configured'
with closing(connect_readonly(Path('/data/zenimport.sqlite3'))) as db:
    identity = db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
expected = Path('/data/test-expected-instance-id')
if sys.argv[1] == 'seed':
    expected.write_text(identity)
    from legacy_factory import build
    from backend.migrate_legacy import convert
    from backend import storage
    source = build(Path('/tmp/legacy-fixture.sqlite3'))
    destination = Path('/data/migrated-fixture.sqlite3')
    convert(source, destination)
    with storage.transaction(destination, read_only=True) as db:
        financial = [tuple(row) for row in db.execute('SELECT id,amount FROM bank_transactions ORDER BY id')]
    Path('/data/test-expected-financial.json').write_text(json.dumps(financial))
    # Also exercise financial persistence in the running service's own database.
    with transaction(Path('/data/zenimport.sqlite3')) as db:
        db.execute("INSERT INTO zen_entities VALUES ('account','fixture-account','{\"balance\":9007199254740993.123456789012}',NULL,0,NULL)")
elif sys.argv[1] == 'verify':
    assert expected.read_text() == identity, 'Database identity changed after replacement'
    with transaction(Path('/data/zenimport.sqlite3'), read_only=True) as db:
        assert db.execute("SELECT data FROM zen_entities WHERE kind='account' AND id='fixture-account'").fetchone()[0] == '{"balance":9007199254740993.123456789012}'
    with transaction(Path('/data/migrated-fixture.sqlite3'), read_only=True) as db:
        financial = [list(row) for row in db.execute('SELECT id,amount FROM bank_transactions ORDER BY id')]
    assert financial == json.loads(Path('/data/test-expected-financial.json').read_text())
else:
    raise AssertionError('Unknown test stage')
capture_smoke(sys.argv[1])
print('PASS: HTTP/auth, conversion, capture resume and financial persistence')
