from contextlib import closing
from hashlib import sha256
import sqlite3

import pytest

from backend import storage


def make_v4(path):
    storage.create_private(path)
    with closing(storage.configured(sqlite3.connect(path))) as db:
        db.execute('CREATE TABLE service_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO service_meta VALUES ('instance_id','fixture-v4')")
        storage.apply_schema(db)
        storage.execute_schema(db, storage.B3_SQL)
        storage.execute_schema(db, storage.B4_SQL)
        db.execute("UPDATE zen_mirror_state SET cursor=123,source_hash='fixture-source',generation=2,ready=1,needs_full=0")
        db.execute("INSERT INTO zen_entities VALUES ('account','fixture-kept','{\"amount\":0.123456789012345678901}',NULL,0,NULL)")
        db.executemany('INSERT INTO schema_migrations VALUES (?,0,NULL)', [(2,), (3,), (4,)])
        db.execute(f'PRAGMA application_id={storage.APPLICATION_ID}')
        db.execute('PRAGMA user_version=4')
        db.commit()


def test_upgrade_preserves_mirror_and_does_not_authorize_legacy_writes(tmp_path):
    path = tmp_path / 'v4.sqlite3'
    make_v4(path)
    backup = storage.initialize(path)
    assert backup.stat().st_mode & 0o777 == 0o600
    with storage.transaction(path, read_only=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 5
        assert db.execute('SELECT cursor,generation,ready FROM zen_mirror_state').fetchone()[:] == (123, 2, 1)
        assert db.execute('SELECT data FROM zen_entities').fetchone()[0] == '{"amount":0.123456789012345678901}'
        assert not db.execute('SELECT 1 FROM delivery_authorizations').fetchone()
        storage.check_integrity(db)
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
    assert storage.initialize(path) is None


def test_failed_v5_upgrade_leaves_source_unchanged(tmp_path, monkeypatch):
    path = tmp_path / 'failed-v4.sqlite3'
    make_v4(path)
    before = sha256(path.read_bytes()).hexdigest()
    original = storage.execute_schema
    def fail(db, sql):
        original(db, sql)
        if sql == storage.B5_SQL:
            raise RuntimeError('fixture-v5-fault')
    monkeypatch.setattr(storage, 'execute_schema', fail)
    with pytest.raises(RuntimeError):
        storage.initialize(path)
    assert sha256(path.read_bytes()).hexdigest() == before
