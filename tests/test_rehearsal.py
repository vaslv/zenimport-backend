from contextlib import closing
import sqlite3

import pytest

from backend import migrate_legacy, rehearsal, storage
from backend.snapshot import copy_snapshot
from legacy_factory import build


def test_explicit_legacy_snapshot_includes_committed_wal(tmp_path):
    source=build(tmp_path/'legacy.sqlite3')
    destination=tmp_path/'snapshot.sqlite3'
    with closing(sqlite3.connect(source)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("INSERT INTO meta VALUES ('fixture-wal','committed')")
        writer.commit()
        copy_snapshot(source,destination,legacy=True)
        with closing(sqlite3.connect(destination)) as copy:
            assert copy.execute("SELECT value FROM meta WHERE key='fixture-wal'").fetchone()[0]=='committed'
            assert copy.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert writer.execute('PRAGMA user_version').fetchone()[0]==12
    assert destination.stat().st_mode & 0o777 == 0o600


def test_legacy_mode_rejects_owned_or_existing_destination(tmp_path):
    owned=tmp_path/'owned.sqlite3';storage.initialize(owned)
    new=tmp_path/'new.sqlite3'
    with pytest.raises(RuntimeError):copy_snapshot(owned,new,legacy=True)
    assert not new.exists()
    source=build(tmp_path/'legacy.sqlite3')
    new.write_bytes(b'keep')
    with pytest.raises(RuntimeError):copy_snapshot(source,new,legacy=True)
    assert new.read_bytes()==b'keep'


def test_offline_rehearsal_repeats_and_restores_identically(tmp_path):
    source=build(tmp_path/'legacy-copy.sqlite3')
    report=rehearsal.rehearse(source,tmp_path/'converted.sqlite3',tmp_path/'restored.sqlite3')
    assert report['repeat_identical'] and report['restored_identical'] and report['source_copy_unchanged']
    assert report['network_used'] is False
    assert report['checks']['bank_operations']>0 and report['checks']['creation_jobs']>0


def test_verifier_checks_money_in_addition_to_lossless_archive(tmp_path):
    source=build(tmp_path/'legacy-copy.sqlite3');converted=tmp_path/'converted.sqlite3'
    migrate_legacy.convert(source,converted)
    with storage.transaction(converted) as db:
        db.execute("UPDATE bank_transactions SET amount='999' WHERE id=(SELECT id FROM bank_transactions LIMIT 1)")
    with pytest.raises(RuntimeError):rehearsal.verify(source,converted)
