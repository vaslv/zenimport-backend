from contextlib import closing
import sqlite3

import pytest

from backend import storage
from backend.snapshot import copy_snapshot


def test_snapshot_keeps_wal_data_private_identity_and_source(tmp_path):
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'snapshot.sqlite3'
    storage.initialize(source)
    with closing(storage.configured(sqlite3.connect(source))) as writer:
        writer.execute("INSERT INTO service_meta VALUES ('fixture-snapshot','wal-committed')")
        writer.commit()
        identity = writer.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0]
        copy_snapshot(source, target)
        assert target.stat().st_mode & 0o777 == 0o600
        with storage.transaction(target, read_only=True) as restored:
            assert restored.execute("SELECT value FROM service_meta WHERE key='fixture-snapshot'").fetchone()[0] == 'wal-committed'
            assert restored.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0] == identity
        assert writer.execute("SELECT value FROM service_meta WHERE key='fixture-snapshot'").fetchone()[0] == 'wal-committed'


def test_snapshot_never_overwrites_source_or_destination(tmp_path):
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'existing'
    storage.initialize(source)
    target.write_bytes(b'fixture-do-not-replace')
    with pytest.raises(RuntimeError):
        copy_snapshot(source, target)
    with pytest.raises(RuntimeError):
        copy_snapshot(source, source)
    assert target.read_bytes() == b'fixture-do-not-replace'
    storage.check(source)


def test_snapshot_rejects_foreign_database_without_publishing(tmp_path):
    source, target = tmp_path / 'foreign.sqlite3', tmp_path / 'output.sqlite3'
    with closing(sqlite3.connect(source)) as db:
        db.execute('CREATE TABLE foreign_table(id INTEGER)')
    with pytest.raises(RuntimeError):
        copy_snapshot(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.zenimport-snapshot-*'))


def test_snapshot_failure_removes_temporary_output(tmp_path, monkeypatch):
    import backend.snapshot as snapshot
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'output.sqlite3'
    storage.initialize(source)
    def fail(*args):
        raise OSError('fixture-fsync-failure')
    monkeypatch.setattr(snapshot.os, 'fsync', fail)
    with pytest.raises(OSError):
        copy_snapshot(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.zenimport-snapshot-*'))


@pytest.mark.parametrize('action', ['export', 'restore'])
def test_snapshot_cli_uses_same_checked_new_file_contract(tmp_path, action):
    import subprocess
    import sys
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'copy.sqlite3'
    storage.initialize(source)
    result = subprocess.run([sys.executable, '-m', 'backend.snapshot', action,
        '--source', str(source), '--destination', str(target)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    storage.check(target)
    refused = subprocess.run([sys.executable, '-m', 'backend.snapshot', action,
        '--source', str(source), '--destination', str(target)], capture_output=True, text=True, timeout=10)
    assert refused.returncode == 1
    assert str(source) not in refused.stderr and str(target) not in refused.stderr


def test_snapshot_publication_race_preserves_other_winner(tmp_path, monkeypatch):
    import backend.snapshot as snapshot
    source, target = tmp_path / 'source.sqlite3', tmp_path / 'race.sqlite3'
    storage.initialize(source)
    original = snapshot.os.link
    def raced_link(old, new):
        target.write_bytes(b'fixture-other-exporter-won')
        original(old, new)
    monkeypatch.setattr(snapshot.os, 'link', raced_link)
    with pytest.raises(FileExistsError):
        copy_snapshot(source, target)
    assert target.read_bytes() == b'fixture-other-exporter-won'
    assert not list(tmp_path.glob('.zenimport-snapshot-*'))
