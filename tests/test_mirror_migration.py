from contextlib import closing
from hashlib import sha256
import sqlite3

import pytest

from backend import storage


def make_v3(path):
    storage.create_private(path)
    with closing(storage.configured(sqlite3.connect(path))) as db:
        db.execute('CREATE TABLE service_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO service_meta VALUES ('instance_id','fixture-v3-instance')")
        db.execute("INSERT INTO service_meta VALUES ('zenmoney_refreshed_at','1')")
        storage.apply_schema(db)
        storage.execute_schema(db, storage.B3_SQL)
        db.execute("INSERT INTO zen_entities VALUES ('account','fixture-kept','{\"amount\":0.123456789012345678901}',NULL,0,NULL)")
        db.executemany('INSERT INTO schema_migrations VALUES (?,0,NULL)', [(2,), (3,)])
        db.execute(f'PRAGMA application_id={storage.APPLICATION_ID}')
        db.execute('PRAGMA user_version=3')
        db.commit()


def test_b3_upgrade_keeps_data_private_backup_and_requires_real_full_refresh(tmp_path):
    path = tmp_path / 'v3.sqlite3'
    make_v3(path)
    backup = storage.initialize(path)
    assert backup.stat().st_mode & 0o777 == 0o600
    with storage.transaction(path, read_only=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
        assert db.execute('SELECT data FROM zen_entities').fetchone()[0] == '{"amount":0.123456789012345678901}'
        assert db.execute('SELECT cursor,ready,needs_full FROM zen_mirror_state').fetchone()[:] == (0, 0, 1)
        storage.check_integrity(db)
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0] == 'fixture-v3-instance'
    assert storage.initialize(path) is None


def test_b4_upgrade_failure_rolls_back_entire_migration(tmp_path, monkeypatch):
    path = tmp_path / 'failed.sqlite3'
    make_v3(path)
    before = sha256(path.read_bytes()).hexdigest()
    original = storage.execute_schema
    def fail(db, sql):
        original(db, sql)
        if sql == storage.B4_SQL:
            raise RuntimeError('fixture-v4-migration-failure')
    monkeypatch.setattr(storage, 'execute_schema', fail)
    with pytest.raises(RuntimeError):
        storage.initialize(path)
    assert sha256(path.read_bytes()).hexdigest() == before
