from contextlib import closing
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from zenmoney_api import Client

from backend.app import create_app
from backend.config import Settings, create_env
from backend.http import ApiBoundary
from backend import storage
from backend.sync_contracts import AccountSettings, ErrorResponse, ServerInfo, MAX_BODY_BYTES

TOKEN = 'fixture-backend-token-for-bootstrap'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / 'state/database.sqlite3', TOKEN)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as http:
        yield http


def test_health_info_and_freshness_are_separate(client):
    assert client.get('/health').json() == {'status': 'ok'}
    response = client.get('/api/v1/info', headers=HEADERS)
    assert response.status_code == 200
    info = ServerInfo.model_validate(response.json())
    assert info.api_version == 1 and info.service_version == '0.1.0'
    assert info.zenmoney_state == 'not_configured' and info.last_zenmoney_refresh is None
    assert response.headers['cache-control'] == 'no-store'
    UUID(response.headers['x-request-id'])
    assert TOKEN not in response.text and 'database' not in response.text


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'}, [('Authorization', 'Bearer ' + TOKEN)] * 2])
def test_api_requires_exactly_one_backend_token(client, headers):
    response = client.get('/api/v1/info', headers=headers)
    assert response.status_code == 401
    error = ErrorResponse.model_validate(response.json()).error
    assert error.code == 'unauthorized'
    assert str(error.request_id) == response.headers['x-request-id']
    assert TOKEN not in response.text


@pytest.mark.parametrize('path', ['/api/v2/info', '/api/state', '/api/zenmoney/sync'])
def test_old_and_unknown_api_versions_are_not_legacy_mutators(client, path):
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'unsupported_api_version'


@pytest.mark.parametrize('path', ['/', '/docs', '/redoc', '/openapi.json', '/demo-bank', '/app.html'])
def test_no_product_pages_or_old_bank_ui(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.headers['content-type'] == 'application/json'


def test_wrong_method_has_machine_code_and_allow_header(client):
    response = client.post('/api/v1/info', headers=HEADERS, json={})
    assert response.status_code == 405 and response.headers['allow'] == 'GET'
    assert response.json()['error']['code'] == 'method_not_allowed'


def test_configured_sdk_does_not_read_or_write_during_startup(settings, monkeypatch):
    def no_request(*args, **kwargs):
        pytest.fail('B1 startup must not call real ZenMoney')
    monkeypatch.setattr(Client, '_request', no_request)
    app = create_app(Settings(settings.database_path, TOKEN, 'fixture-zenmoney-token'))
    with TestClient(app) as client:
        sdk = app.state.zenmoney
        assert isinstance(sdk, Client) and not sdk.is_closed
        response = client.get('/api/v1/info', headers=HEADERS)
        assert response.json()['zenmoney_state'] == 'unavailable'
        assert response.json()['last_zenmoney_refresh'] is None
        assert client.get('/health').status_code == 200
    assert sdk.is_closed


def test_health_does_not_recreate_missing_database(client, settings):
    settings.database_path.unlink()
    assert client.get('/health').status_code == 503
    assert not settings.database_path.exists()


def test_validation_error_does_not_echo_input_or_body(settings):
    app = create_app(settings)

    @app.post('/api/v1/test-validation')
    def validate(data: AccountSettings):
        return data

    with TestClient(app) as client:
        marker = 'fixture-value-must-not-be-echoed'
        for payload in ({'expected_version': marker}, {'token': marker}):
            response = client.post('/api/v1/test-validation', headers=HEADERS, json=payload)
            assert response.status_code == 422
            assert marker not in response.text
            assert response.json()['error']['code'] == 'validation_error'
        response = client.post('/api/v1/test-validation', headers=HEADERS,
                               content=marker, extensions={})
        assert response.status_code == 422 and marker not in response.text


def test_unexpected_api_error_is_redacted_without_rethrow(settings, caplog):
    app = create_app(settings)
    marker = 'fixture-exception-input-must-not-be-logged'

    @app.get('/api/v1/test-failure')
    def fail():
        raise ValueError(marker)

    with TestClient(app) as client:
        response = client.get('/api/v1/test-failure', headers=HEADERS)
        assert response.status_code == 500
        assert response.json()['error']['code'] == 'internal_error'
        assert marker not in response.text and marker not in caplog.text


def test_declared_body_limit_enforced(client):
    response = client.post('/api/v1/info', headers=HEADERS, content=b'x' * (MAX_BODY_BYTES + 1))
    assert response.status_code == 413
    assert response.json()['error']['code'] == 'payload_too_large'


def test_chunked_body_limit_enforced_without_length_header():
    async def exercise():
        sent = []
        events = iter([
            {'type': 'http.request', 'body': b'x' * MAX_BODY_BYTES, 'more_body': True},
            {'type': 'http.request', 'body': b'x', 'more_body': False},
        ])

        async def downstream(*args):
            pytest.fail('Oversized body reached the application')

        async def receive():
            return next(events)

        async def send(event):
            sent.append(event)

        scope = {'type': 'http', 'path': '/api/v1/info',
                 'headers': [(b'authorization', ('Bearer ' + TOKEN).encode())]}
        await ApiBoundary(downstream, token=TOKEN)(scope, receive, send)
        assert sent[0]['status'] == 413
        assert json.loads(sent[1]['body'])['error']['code'] == 'payload_too_large'

    asyncio.run(exercise())


def test_storage_bootstrap_persists_and_has_owner(settings):
    path = settings.database_path
    storage.initialize(path)
    with closing(storage.connect_readonly(path)) as db:
        original_id = db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        assert db.execute('PRAGMA application_id').fetchone()[0] == storage.APPLICATION_ID
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    storage.initialize(path)
    with closing(storage.connect_readonly(path)) as db:
        assert db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0] == original_id
    assert path.stat().st_mode & 0o777 == 0o600
    storage.check(path)


@pytest.mark.parametrize('kind', ['legacy', 'future', 'foreign', 'corrupt'])
def test_existing_unrecognized_database_not_modified(tmp_path, kind):
    path = tmp_path / 'unrecognized.sqlite3'
    if kind == 'corrupt':
        path.write_bytes(b'fixture-not-an-sqlite-database')
    else:
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE existing_data (value TEXT)')
            db.execute("INSERT INTO existing_data VALUES ('fixture-financial-record')")
            if kind == 'legacy':
                db.execute('PRAGMA user_version=12')
            elif kind == 'future':
                db.execute(f'PRAGMA application_id={storage.APPLICATION_ID}')
                db.execute(f'PRAGMA user_version={storage.SCHEMA_VERSION + 1}')
            db.commit()
    before = sha256(path.read_bytes()).hexdigest()
    with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
        storage.initialize(path)
    assert sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('changes', [
    {'DATABASE_PATH': ''}, {'DATABASE_PATH': 'relative/database.sqlite3'},
    {'DATABASE_PATH': ':memory:'}, {'API_TOKEN': ''}, {'API_TOKEN': 'short'},
    {'API_TOKEN': 'fixture-token-with-a-newline\n'}, {'ZENMONEY_TOKEN': 'token with spaces'},
])
def test_invalid_configuration_fails_before_storage(tmp_path, changes):
    path = tmp_path / 'must-not-exist.sqlite3'
    values = {'DATABASE_PATH': str(path), 'API_TOKEN': TOKEN} | changes
    with pytest.raises(RuntimeError):
        Settings.from_env(values)
    assert not path.exists()


def test_settings_repr_contains_no_tokens(settings):
    configured = Settings(settings.database_path, TOKEN, 'fixture-zenmoney-token')
    assert TOKEN not in repr(configured) and 'fixture-zenmoney-token' not in repr(configured)


def test_database_path_cannot_be_directory(tmp_path):
    with pytest.raises(RuntimeError, match='directory'):
        Settings(tmp_path, TOKEN)


def test_generated_configuration_is_private_and_never_overwrites(tmp_path):
    output = tmp_path / '.env'
    create_env(output)
    original = output.read_bytes()
    env = dict(line.split('=', 1) for line in original.decode().splitlines() if line and not line.startswith('#'))
    assert len(env['API_TOKEN']) >= 24
    assert env['DATA_VOLUME'] == 'zenimport-backend-data'
    assert env['ZENMONEY_TOKEN'] == ''
    assert output.stat().st_mode & 0o777 == 0o600
    Settings.from_env(env)
    with pytest.raises(FileExistsError):
        create_env(output)
    assert output.read_bytes() == original
