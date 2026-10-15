"""Container-only app factory. Real SDK, persistent synthetic remote, no network."""
from contextlib import asynccontextmanager, closing
from pathlib import Path
import os
import sqlite3
import time

import httpx
from zenmoney_api import Client
from zenmoney_api.protocol import decode_json, encode_json

from backend.app import create_app as backend_app
from backend.config import Settings
from capture_factory import TARGET
from mirror_factory import snapshot

REMOTE = Path('/data/lifecycle-remote.sqlite3')


def remote_db():
    db = sqlite3.connect(REMOTE, timeout=5)
    db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS writes(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute("INSERT OR IGNORE INTO meta VALUES ('cursor','100')")
    db.execute("INSERT OR IGNORE INTO meta VALUES ('mode','normal')")
    db.commit()
    return db


def respond(request):
    body = decode_json(request.content)
    assert request.url.path == '/v8/diff/'
    assert set(body) <= {'serverTimestamp', 'currentClientTimestamp', 'forceFetch', 'transaction'}
    with closing(remote_db()) as db:
        cursor = max(int(db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()[0]), body['serverTimestamp']) + 1
        db.execute("UPDATE meta SET value=? WHERE key='cursor'", (str(cursor),))
        if 'transaction' in body:
            db.execute('INSERT INTO writes(payload) VALUES (?)', (encode_json(body['transaction']).decode(),))
            for item in body['transaction']:
                db.execute('INSERT INTO operations VALUES (?,?)', (item['id'], encode_json(item).decode()))
        operations = [decode_json(row[0].encode()) for row in db.execute('SELECT payload FROM operations')]
        db.commit()
    if 'transaction' in body:
        # The fake remote committed first. SIGKILL here leaves a real durable
        # intent in the service and no response, like a lost network connection.
        while True:
            with closing(remote_db()) as db:
                mode = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()[0]
            if mode != 'hold':
                break
            time.sleep(0.05)
        return httpx.Response(200, content=encode_json({'serverTimestamp': cursor, 'transaction': body['transaction']}))
    return httpx.Response(200, content=encode_json(snapshot(cursor=cursor,
        account=[{'id': TARGET, 'user': 1, 'instrument': 2, 'type': 'checking', 'title': 'Fixture VND'}], transaction=operations)))


def create_app():
    settings = Settings.from_env()
    assert settings.api_token == 'fixture-backend-token-for-isolated-tests'
    assert settings.zenmoney_token == 'fixture-container-sdk-token'
    assert settings.database_path.parent == Path('/data')
    app = backend_app(settings, client_factory=lambda token: Client(token, transport=httpx.MockTransport(respond)))
    original = app.router.lifespan_context
    @asynccontextmanager
    async def lifecycle(app):
        async with original(app):
            try:
                yield
            finally:
                with closing(remote_db()) as db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES ('shutdown-entered','1')")
                    db.commit()
    app.router.lifespan_context = lifecycle
    return app
