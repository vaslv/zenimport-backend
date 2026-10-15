from contextlib import closing
from hashlib import sha256
import json
import os
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from backend import legacy_archive, migrate_legacy, storage
from backend.app import create_app
from backend.config import Settings
from legacy_factory import bank, build, ident, insert, job


@pytest.fixture
def legacy(tmp_path):
    return build(tmp_path / 'legacy.sqlite3')


@pytest.fixture
def migrated(legacy, tmp_path):
    destination = tmp_path / 'converted.sqlite3'
    migrate_legacy.convert(legacy, destination)
    return destination


def test_full_conversion_preserves_source_keys_links_and_exact_money(legacy, migrated):
    with storage.transaction(migrated, read_only=True) as db:
        amounts = dict(db.execute('SELECT id,amount FROM bank_transactions'))
        assert amounts[ident('huge')] == '9007199254740993'
        assert amounts[ident('cents')] == '123.45'
        assert db.execute('SELECT occurred_at FROM bank_transactions WHERE id=?', (ident('manual'),)).fetchone()[0] == '2026-10-10T19:19:33+07:00'
        assert db.execute('SELECT count(*) FROM bank_accounts').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM bank_accounts WHERE selection=\'unconfigured\' AND last_complete_end IS NULL').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM account_bindings').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM coverage').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM category_evidence').fetchone()[0] == 0
        assert db.execute('SELECT category_id FROM category_choices WHERE bank_id=?', (ident('manual'),)).fetchone()[0] == ident('groceries')
        assert not db.execute('SELECT 1 FROM category_choices WHERE bank_id=?', (ident('huge'),)).fetchone()
        assert db.execute('SELECT count(*) FROM link_bank_members WHERE group_id=?', (ident('transfer'),)).fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM link_zen_members WHERE group_id=?', (ident('split'),)).fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM link_bank_members WHERE group_id=?', (ident('settlement'),)).fetchone()[0] == 2
        assert db.execute('SELECT income FROM zen_movements WHERE id=?', (ident('one-zen'),)).fetchone()[0] == '0'
        assert db.execute('SELECT outcome FROM zen_movements WHERE id=?', (ident('one-zen'),)).fetchone()[0] == '123.450000000001'
        assert '0.1234567890123456789012345' in db.execute('SELECT payload FROM zen_sync_runs').fetchone()[0]
        assert db.execute("SELECT value FROM service_meta WHERE key='legacy:zenmoney_cursor'").fetchone()[0] == '100'
        storage.check_integrity(db)
    with closing(sqlite3.connect(legacy)) as source, storage.transaction(migrated, read_only=True) as target:
        assert dict(source.execute('SELECT id,raw FROM bank_transactions')) == dict(target.execute('SELECT id,raw_json FROM bank_transactions'))


@pytest.mark.parametrize('name,state', [
    ('ignored', 'suppressed'), ('deleted', 'suppressed'), ('soft', 'suppressed'),
    ('absent', 'delivered'), ('edited', 'delivered'), ('split-bank', 'delivered'),
    ('settlement-b1', 'delivered'), ('transfer-b2', 'delivered'),
    ('prepared', 'prepared'), ('out-queued', 'prepared'), ('out-sent', 'delivered'),
    ('unknown', 'uncertain'), ('retry', 'uncertain'), ('conflict', 'uncertain'),
    ('unproven', 'uncertain'), ('orphan', 'uncertain'), ('remote-edited', 'delivered')])
def test_processed_deleted_and_unknown_are_not_new_again(migrated, name, state):
    with storage.transaction(migrated, read_only=True) as db:
        assert db.execute('SELECT state FROM bank_processing WHERE bank_id=?', (ident(name),)).fetchone()[0] == state
        if name in ('absent', 'deleted'):
            assert not db.execute("SELECT 1 FROM zen_entities WHERE kind='transaction' AND id=?", (ident('zen-' + name),)).fetchone()


def test_attempted_payload_is_byte_exact_and_prepared_rebuilt_unread(legacy, migrated):
    with closing(sqlite3.connect(legacy)) as source, storage.transaction(migrated, read_only=True) as db:
        for name in ('unknown', 'retry', 'conflict', 'unproven', 'remote-edited'):
            original = source.execute('SELECT zen_id,payload FROM creation_jobs WHERE bank_id=?', (ident(name),)).fetchone()
            row = db.execute('SELECT * FROM deliveries WHERE bank_id=?', (ident(name),)).fetchone()
            assert (row['zen_id'], row['payload']) == original
            assert row['ever_attempted'] == 1
        for name in ('prepared', 'out-queued'):
            row = db.execute('SELECT * FROM deliveries WHERE bank_id=?', (ident(name),)).fetchone()
            payload = json.loads(row['payload'])
            assert row['zen_id'] == ident('zen-' + name) and row['ever_attempted'] == 0
            assert payload['viewed'] is False
            assert payload['outcome'] == 12345 and payload['income'] == 0
        assert db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0] == 5


def test_archive_preserves_unknown_schema_blobs_types_and_obsolete_drafts(migrated):
    with storage.transaction(migrated, read_only=True) as db:
        digest, manifest = db.execute('SELECT digest,manifest_json FROM legacy_imports').fetchone()
        legacy_archive.verify_archive(db, digest, json.loads(manifest))
        packed = db.execute("SELECT values_json FROM legacy_rows WHERE table_name='extra_unknown'").fetchone()[0]
        assert json.loads(packed) == [['integer', '9007199254740993'], ['real', '0x1.4000000000000p+0'],
                                       ['text', 'unknown\x00text'], ['blob', 'AP8='], ['null', None]]
        assert db.execute("SELECT count(*) FROM legacy_rows WHERE table_name='zen_operation_drafts'").fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM bank_exclusions').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM zen_exclusions').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM recipient_rules').fetchone()[0] == 1


def test_all_entity_kinds_and_reminder_tags_keep_exact_raw_data(legacy, tmp_path):
    from zenmoney_api.protocol import KINDS
    originals = {}
    with closing(sqlite3.connect(legacy)) as db:
        for kind in KINDS:
            table = 'zen_' + ('reminder_marker' if kind == 'reminderMarker' else kind)
            identifier = ident('all-kind-' + kind)
            raw = '{"id":"' + identifier + '","income":0.123456789012345678901,"outcome":0,"extension":{"keep":true}}'
            fields = {}
            if kind in ('transaction', 'reminder', 'reminderMarker'):
                fields = {'income': '0.123456789012345678901', 'outcome': '0'}
                tag_table = table + '_tags'
                owner = 'transaction_id' if kind == 'transaction' else 'owner_id'
            insert(db, table, id=identifier, data=raw, **fields)
            if fields:
                insert(db, tag_table, **{owner: identifier}, tag_id=ident('groceries'), position=0)
            originals[kind] = (identifier, raw)
        db.commit()
    destination = tmp_path / 'all-kinds.sqlite3'
    migrate_legacy.convert(legacy, destination)
    with storage.transaction(destination, read_only=True) as db:
        for kind, (identifier, raw) in originals.items():
            assert db.execute('SELECT data FROM zen_entities WHERE kind=? AND id=?', (kind, identifier)).fetchone()[0] == raw
            if kind in ('transaction', 'reminder', 'reminderMarker'):
                assert db.execute('SELECT income FROM zen_movements WHERE kind=? AND id=?', (kind, identifier)).fetchone()[0] == '0.123456789012345678901'
                assert db.execute('SELECT tag_id FROM zen_tags WHERE kind=? AND entity_id=?', (kind, identifier)).fetchone()[0] == ident('groceries')


def test_reopened_job_does_not_forget_previous_different_uuid_attempt(legacy, tmp_path):
    with closing(sqlite3.connect(legacy)) as db:
        insert(db, 'audit', at=1700000000, operation='creation_intent', data=json.dumps({
            'bank_id': ident('prepared'), 'payload': {'id': ident('previous-uuid'), 'outcome': 12345}}))
        db.commit()
    destination = tmp_path / 'reopened.sqlite3'
    migrate_legacy.convert(legacy, destination)
    with storage.transaction(destination, read_only=True) as db:
        row = db.execute('SELECT * FROM deliveries WHERE bank_id=?', (ident('prepared'),)).fetchone()
        assert row['state'] == 'uncertain' and row['ever_attempted'] == 1
        assert row['zen_id'] == ident('zen-prepared')
        assert db.execute('SELECT 1 FROM delivery_attempts WHERE zen_id=?', (ident('previous-uuid'),)).fetchone()


@pytest.mark.parametrize('operation', ['creation_verified', 'sent', 'confirm'])
def test_historical_confirmation_survives_removed_job_and_link(legacy, tmp_path, operation):
    with closing(sqlite3.connect(legacy)) as db:
        data = {'zen_id': ident('previous-remote'), 'bank_id': ident('manual')} if operation == 'creation_verified' else {
            'id': ident('manual'), 'zen_id': ident('previous-remote'), 'action': 'link', 'match_id': ident('previous-remote')}
        insert(db, 'audit', at=1700000000, operation=operation, data=json.dumps(data))
        db.commit()
    destination = tmp_path / 'historical.sqlite3'
    migrate_legacy.convert(legacy, destination)
    with storage.transaction(destination, read_only=True) as db:
        assert db.execute('SELECT state FROM bank_processing WHERE bank_id=?', (ident('manual'),)).fetchone()[0] == 'delivered'
        assert db.execute('SELECT zen_id FROM processing_receipts WHERE bank_id=?', (ident('manual'),)).fetchone()[0] == ident('previous-remote')
        assert not db.execute('SELECT 1 FROM category_choices WHERE bank_id=?', (ident('manual'),)).fetchone()


def test_unattempted_without_target_preserves_payload_and_requires_rebuild(legacy, tmp_path):
    with closing(sqlite3.connect(legacy)) as db:
        db.execute('UPDATE bank_accounts SET zenmoney_account=NULL')
        db.commit()
    destination = tmp_path / 'unmapped.sqlite3'
    migrate_legacy.convert(legacy, destination)
    with storage.transaction(destination, read_only=True) as db, closing(sqlite3.connect(legacy)) as source:
        row = db.execute('SELECT * FROM deliveries WHERE bank_id=?', (ident('prepared'),)).fetchone()
        assert row['origin'].endswith(':rebuild_required')
        assert row['needs_rebuild'] == 1
        assert row['payload'] == source.execute('SELECT payload FROM creation_jobs WHERE bank_id=?', (ident('prepared'),)).fetchone()[0]


def test_unknown_source_triggers_are_archived_not_executed(legacy, tmp_path):
    with closing(sqlite3.connect(legacy)) as db:
        db.execute("CREATE TRIGGER dangerous AFTER INSERT ON extra_unknown BEGIN DELETE FROM bank_transactions; END")
        db.commit()
    destination = tmp_path / 'triggers.sqlite3'
    migrate_legacy.convert(legacy, destination)
    with storage.transaction(destination, read_only=True) as db:
        assert db.execute("SELECT 1 FROM legacy_objects WHERE type='trigger' AND name='dangerous'").fetchone()
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='dangerous'").fetchone()


def test_concurrent_publication_does_not_overwrite_destination(legacy, tmp_path, monkeypatch):
    destination = tmp_path / 'racing.sqlite3'
    original_link = os.link
    def race(source, target):
        destination.write_bytes(b'fixture-racing-writer')
        original_link(source, target)
    monkeypatch.setattr(migrate_legacy.os, 'link', race)
    with pytest.raises(FileExistsError):
        migrate_legacy.convert(legacy, destination)
    assert destination.read_bytes() == b'fixture-racing-writer'
    assert not list(tmp_path.glob('.legacy-conversion-*'))


def test_repeat_is_idempotent_and_source_is_unchanged(legacy, migrated):
    before_source = sha256(legacy.read_bytes()).hexdigest()
    before_target = sha256(migrated.read_bytes()).hexdigest()
    report = migrate_legacy.convert(legacy, migrated)
    assert report['repeated'] is True
    assert sha256(legacy.read_bytes()).hexdigest() == before_source
    assert sha256(migrated.read_bytes()).hexdigest() == before_target
    assert migrated.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('change', ['version', 'foreign_key', 'zero', 'unknown_state', 'broken_group', 'uuid_conflict', 'money_mismatch', 'missing_table', 'missing_identity'])
def test_bad_source_leaves_no_destination_and_preserves_source(legacy, tmp_path, change):
    with closing(sqlite3.connect(legacy)) as db:
        if change == 'version':
            db.execute('PRAGMA user_version=11')
        elif change == 'foreign_key':
            db.execute("UPDATE bank_transactions SET bank_account='unknown' WHERE id=?", (ident('manual'),))
        elif change == 'zero':
            db.execute('UPDATE bank_transactions SET amount_minor=0 WHERE id=?', (ident('manual'),))
        elif change == 'unknown_state':
            db.execute("UPDATE bank_transactions SET state='mystery' WHERE id=?", (ident('manual'),))
        elif change == 'broken_group':
            db.execute('DELETE FROM reconciliation_zen_members WHERE group_id=?', (ident('split'),))
        elif change == 'uuid_conflict':
            row = db.execute('SELECT bank_id,payload FROM creation_jobs WHERE bank_id=?', (ident('prepared'),)).fetchone()
            payload = json.loads(row[1]) | {'id': ident('conflicting-uuid')}
            insert(db, 'outbox', bank_transaction=row[0], zen_id=payload['id'], payload=json.dumps(payload))
        elif change == 'money_mismatch':
            db.execute("UPDATE zen_transaction SET outcome='999' WHERE id=?", (ident('one-zen'),))
        elif change == 'missing_table':
            db.execute('DROP TABLE creation_jobs')
        else:
            db.execute('UPDATE bank_transactions SET linked_id=NULL WHERE id=?', (ident('absent'),))
        db.commit()
    before = sha256(legacy.read_bytes()).hexdigest()
    destination = tmp_path / 'not-published.sqlite3'
    with pytest.raises((RuntimeError, ValueError, sqlite3.Error)):
        migrate_legacy.convert(legacy, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob('.legacy-conversion-*'))
    assert sha256(legacy.read_bytes()).hexdigest() == before


def test_existing_destination_and_modified_source_are_never_overwritten(legacy, migrated):
    with closing(sqlite3.connect(legacy)) as db:
        db.execute("UPDATE bank_transactions SET description='Changed fixture'")
        db.commit()
    before = sha256(migrated.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match='already exists'):
        migrate_legacy.convert(legacy, migrated)
    assert sha256(migrated.read_bytes()).hexdigest() == before


def test_repeat_detects_archive_tampering(legacy, migrated):
    with storage.transaction(migrated) as db:
        db.execute("UPDATE legacy_rows SET values_json='[]' WHERE table_name='extra_unknown'")
    with pytest.raises(RuntimeError, match='content mismatch'):
        migrate_legacy.convert(legacy, migrated)


def test_live_wal_rows_included_and_source_not_checkpointed(legacy, tmp_path):
    destination = tmp_path / 'wal-converted.sqlite3'
    with closing(sqlite3.connect(legacy)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        bank(writer, 'wal-only')
        writer.commit()
        wal = legacy.with_name(legacy.name + '-wal')
        before = sha256(wal.read_bytes()).hexdigest()
        migrate_legacy.convert(legacy, destination)
        assert sha256(wal.read_bytes()).hexdigest() == before
        with storage.transaction(destination, read_only=True) as db:
            assert db.execute('SELECT 1 FROM bank_transactions WHERE id=?', (ident('wal-only'),)).fetchone()


@pytest.mark.parametrize('alias', ['same', 'hardlink', 'symlink'])
def test_in_place_conversion_rejected(legacy, tmp_path, alias):
    destination = legacy if alias == 'same' else tmp_path / 'alias.sqlite3'
    if alias == 'hardlink':
        os.link(legacy, destination)
    elif alias == 'symlink':
        destination.symlink_to(legacy)
    with pytest.raises(RuntimeError, match='separate destination'):
        migrate_legacy.convert(legacy, destination)


def test_cli_and_runtime_accept_converted_database_without_network(legacy, tmp_path):
    destination = tmp_path / 'cli.sqlite3'
    result = subprocess.run([sys.executable, '-m', 'backend.migrate_legacy', '--source', str(legacy),
                             '--destination', str(destination)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['source_version'] == 12
    assert 'fixture-account' not in result.stdout
    with TestClient(create_app(Settings(destination, 'fixture-backend-token-for-conversion'))) as http:
        assert http.get('/health').status_code == 200
    assert migrate_legacy.convert(legacy, destination)['repeated'] is True


def test_cli_error_does_not_echo_source_content(tmp_path):
    path = tmp_path / 'corrupt.sqlite3'
    path.write_text('fixture-financial-secret')
    result = subprocess.run([sys.executable, '-m', 'backend.migrate_legacy', '--source', str(path),
                             '--destination', str(tmp_path / 'absent.sqlite3')], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'fixture-financial-secret' not in result.stderr and 'Traceback' not in result.stderr
