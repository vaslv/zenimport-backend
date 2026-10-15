"""HTTP + persisted bootstrap identity across container replacement; test volume only."""
from contextlib import closing
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.storage import connect_readonly
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
elif sys.argv[1] == 'verify':
    assert expected.read_text() == identity, 'Database identity changed after replacement'
else:
    raise AssertionError('Unknown test stage')
print('PASS: HTTP/auth and persisted backend identity')
