"""Real HTTP, SIGKILL/restart and snapshot restoration on a disposable volume."""
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend import storage
from zenmoney_api.protocol import decode_json
from capture_factory import TARGET
from runtime_server import REMOTE, remote_db

BASE = 'http://lifecycle:8000'
TOKEN = 'fixture-backend-token-for-isolated-tests'
STATE = Path('/data/lifecycle-expected.json')
SOURCE = Path('/data/lifecycle.sqlite3')
RESTORED = Path('/data/lifecycle-restored.sqlite3')


def api(method, path, data=None):
    request = Request(BASE + '/api/v1' + path, method=method,
        headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'},
        data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def until(check):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError('Disposable lifecycle scenario did not reach expected state')


def mode(value):
    with closing(remote_db()) as db:
        db.execute("UPDATE meta SET value=? WHERE key='mode'", (value,))
        db.commit()


def count_writes():
    with closing(remote_db()) as db:
        return db.execute('SELECT count(*) FROM writes').fetchone()[0]


def add_record(connection, day, external_id):
    job = api('POST', '/jobs', {'connection_id': connection, 'request_id': str(uuid4())})
    worker = str(uuid4())
    lease = api('POST', '/jobs/' + job['id'] + '/lease', {'worker_id': worker})
    auth = {'worker_id': worker, 'lease_epoch': lease['epoch']}
    interval = job['accounts'][0]['intervals'][0]
    prefix = '/jobs/' + job['id'] + '/intervals/' + interval['id']
    from capture_factory import record
    identifiers = ['fixture-first']
    if external_id != 'fixture-first':
        identifiers.append('fixture-second')
    if external_id not in identifiers:
        identifiers.append(external_id)
    rows = [record(interval, identifier, amount=str(100*(i+1)), local_date=day,
        occurred_at=day+f'T{10+i:02}:12:25+07:00') for i, identifier in enumerate(identifiers)]
    api('PUT', prefix + '/pages/1', {**auth, 'page_number': 1, 'total_pages': 1, 'total_records': len(rows), 'records': rows})
    api('POST', prefix + '/complete', {**auth, 'total_pages': 1, 'total_records': len(rows)})
    return job


phase = sys.argv[1]
if phase == 'seed':
    day = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
    connection = api('POST', '/connections/discover', {'bank': 'vietinbank', 'profile_key': 'fixture-lifecycle',
        'profile_identity': 'user_selected', 'connector_version': 'fixture-container', 'accounts_complete': True,
        'capabilities': {'timezone': 'Asia/Ho_Chi_Minh', 'currencies': ['VND'], 'max_window_days': 1, 'details': 'included'},
        'accounts': [{'source_key': 'fixture-lifecycle-account', 'identity_scope': 'bank', 'label': 'Fixture',
            'masked_number': '••1234', 'currency': 'VND', 'scale': 0, 'supported': True}]})
    api('PUT', '/accounts/' + connection['accounts'][0]['id'] + '/settings', {'expected_version': 0,
        'selection': 'enabled', 'zenmoney_account_id': TARGET, 'import_start': day})
    job = add_record(connection['id'], day, 'fixture-first')
    until(lambda: api('GET', '/jobs/' + job['id'])['delivery']['delivered'] == 1)
    mode('hold')
    job = add_record(connection['id'], day, 'fixture-second')
    until(lambda: count_writes() == 2)
    with storage.transaction(SOURCE, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM deliveries WHERE state='attempting'").fetchone()[0] == 1
        identity = db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        ids = dict(db.execute('SELECT bank_id,zen_id FROM deliveries'))
    STATE.write_text(json.dumps({'job': job['id'], 'ids': ids, 'identity': identity, 'connection': connection['id'], 'day': day, 'count': 2}))
    print('PASS: real HTTP and SDK write committed; container can be killed before response')
elif phase == 'graceful-seed':
    expected = json.loads(STATE.read_text())
    with closing(remote_db()) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('shutdown-entered','0')")
        db.commit()
    mode('hold')
    job = add_record(expected['connection'], expected['day'], 'fixture-third')
    until(lambda: count_writes() == 3)
    with storage.transaction(SOURCE, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM deliveries WHERE state='attempting'").fetchone()[0] == 1
        expected.update(job=job['id'], ids=dict(db.execute('SELECT bank_id,zen_id FROM deliveries')), count=3)
    STATE.write_text(json.dumps(expected))
    print('PASS: third write waiting for response; graceful shutdown can start')
elif phase == 'release':
    def shutdown_entered():
        with closing(remote_db()) as db:
            return db.execute("SELECT value FROM meta WHERE key='shutdown-entered'").fetchone()[0] == '1'
    until(shutdown_entered)
    mode('normal')
    print('PASS: shutdown entered with write in flight; fake response released')
elif phase in ('recovered', 'restored', 'graceful'):
    expected = json.loads(STATE.read_text())
    until(lambda: api('GET', '/jobs/' + expected['job'])['delivery_state'] == 'complete')
    result = api('GET', '/jobs/' + expected['job'])
    assert result['delivery'] == {'pending': 0, 'delivered': expected['count'], 'suppressed': 0, 'uncertain': 0}
    assert count_writes() == expected['count'], 'A confirmed/ambiguous operation was written again'
    with storage.transaction(RESTORED if phase == 'restored' else SOURCE, read_only=True) as db:
        assert dict(db.execute('SELECT bank_id,zen_id FROM deliveries')) == expected['ids']
        assert db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0] == expected['identity']
        storage.check_integrity(db)
    with closing(remote_db()) as db:
        assert all(decode_json(row[0].encode())['viewed'] is False for row in db.execute('SELECT payload FROM operations'))
    mode('normal')
    print('PASS: same UUIDs, no repeated writes, unread payload, integrity after ' + phase)
elif phase == 'snapshot':
    # Service is stopped. Export via SQLite backup, restore to a NEW file,
    # keeping the original and the snapshot intact. Never remove WAL blindly.
    from backend.snapshot import copy_snapshot
    saved = Path('/data/lifecycle-snapshot.sqlite3')
    copy_snapshot(SOURCE, saved)
    copy_snapshot(saved, RESTORED)
    storage.check(RESTORED)
    print('PASS: consistent snapshot restored to a new database without overwriting source')
else:
    raise AssertionError('Unknown lifecycle phase')
