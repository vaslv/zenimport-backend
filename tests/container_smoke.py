"""HTTP + persisted bootstrap identity across container replacement; test volume only."""
from contextlib import closing
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.storage import connect_readonly, transaction
from backend.sync_contracts import ErrorResponse, ServerInfo

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
        assert db.execute('SELECT data FROM zen_entities').fetchone()[0] == '{"balance":9007199254740993.123456789012}'
    with transaction(Path('/data/migrated-fixture.sqlite3'), read_only=True) as db:
        financial = [list(row) for row in db.execute('SELECT id,amount FROM bank_transactions ORDER BY id')]
    assert financial == json.loads(Path('/data/test-expected-financial.json').read_text())
else:
    raise AssertionError('Unknown test stage')
print('PASS: HTTP/auth, converted database and financial persistence')
