from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from backend import records, storage
from backend.sync_contracts import BankRecord


def account(db, name='account', currency='VND', scale=0):
    db.execute('''INSERT INTO bank_accounts
        (id,bank,identity_scope,source_key,currency,scale,label,masked_number,supported)
        VALUES (?,'fixture','bank',?,?,?,?,?,1)''', (name, name, currency, scale, name, '…1234'))


def record(**changes):
    return BankRecord.model_validate({'external_id': 'fixture-reference', 'identity_kind': 'bank', 'identity_version': '1',
        'occurred_at': '2026-10-10T19:19:33+07:00', 'local_date': '2026-10-10', 'direction': 'expense',
        'amount': '9007199254740993', 'currency': 'VND', 'description': 'Fixture', 'details_state': 'complete', **changes})


@pytest.fixture
def path(tmp_path):
    result = tmp_path / 'financial.sqlite3'
    storage.initialize(result)
    with storage.transaction(result) as db:
        account(db)
    return result


def save(db, value=None):
    return records.save_record(db, 'account', value or record(), parser_version='fixture', timezone='Asia/Ho_Chi_Minh')


def test_exact_record_identity_alias_revision_and_terminal_state(path):
    with storage.transaction(path) as db:
        identifier = save(db)
        assert save(db) == identifier
        records.add_alias(db, 'account', identifier, 'another-reference', '2')
        db.execute("UPDATE bank_processing SET state='delivered' WHERE bank_id=?", (identifier,))
        assert save(db, record(external_id='another-reference', description='Updated bank details')) == identifier
        row = db.execute('SELECT * FROM bank_transactions').fetchone()
        assert row['revision'] == 2 and row['amount'] == '9007199254740993'
        assert row['external_id'] == 'fixture-reference'
        assert db.execute('SELECT count(*) FROM bank_versions').fetchone()[0] == 2
        assert db.execute('SELECT state FROM bank_processing').fetchone()[0] == 'delivered'
        assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        storage.check_integrity(db)


@pytest.mark.parametrize('changes', [{'currency': 'USD'}, {'amount': '1.1'}, {'occurred_at': '2026-10-11T19:19:33+07:00'}])
def test_record_currency_scale_and_timezone_checked(path, changes):
    with storage.transaction(path) as db, pytest.raises(ValueError):
        save(db, record(**changes))


@pytest.mark.parametrize('amount', ['0', '-1', '1e3', '01', '1.1234567890123', '1' * 25, 'NaN'])
def test_money_constraint_rejects_invalid_positive_magnitudes(path, amount):
    with storage.transaction(path) as db:
        save(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE bank_transactions SET amount=?', (amount,))


def test_unique_canonical_account_bank_record_alias_and_foreign_keys(path):
    with storage.transaction(path) as db:
        identifier = save(db)
        account(db, 'other')
        other = records.save_record(db, 'other', record(), parser_version='1', timezone='Asia/Ho_Chi_Minh')
        with pytest.raises(ValueError):
            records.add_alias(db, 'account', other, 'fixture-reference', '1')
        with pytest.raises(sqlite3.IntegrityError):
            records.add_alias(db, 'other', identifier, 'cross-account-alias', '1')
        for sql in ["INSERT INTO bank_accounts SELECT 'duplicate',bank,identity_scope,profile_key,source_key,currency,scale,label,masked_number,supported,unsupported_reason,selection,zenmoney_account_id,import_start,settings_version,last_complete_end FROM bank_accounts WHERE id='account'",
                    "INSERT INTO bank_transactions SELECT 'duplicate',account_id,external_id,identity_kind,identity_version,local_date,occurred_at,direction,amount,currency,description,beneficiary_account,beneficiary_name,raw_json,revision FROM bank_transactions WHERE account_id='account'",
                    "UPDATE bank_transactions SET account_id='other' WHERE account_id='account'",
                    "UPDATE bank_transactions SET external_id='replace-identity' WHERE account_id='account'"]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql)


@pytest.mark.parametrize('state', ['delivered', 'suppressed'])
@pytest.mark.parametrize('action', ['update', 'delete', 'replace'])
def test_processing_ledger_cannot_be_reset(path, state, action):
    with storage.transaction(path) as db:
        identifier = save(db)
        db.execute('UPDATE bank_processing SET state=?', (state,))
        sql, args = {
            'update': ("UPDATE bank_processing SET state='pending'", ()),
            'delete': ('DELETE FROM bank_processing', ()),
            'replace': ("INSERT OR REPLACE INTO bank_processing VALUES (?,'pending','reset',0)", (identifier,)),
        }[action]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, args)


@pytest.mark.parametrize('action', ['uuid', 'payload', 'flag', 'delete', 'replace'])
def test_attempted_delivery_identity_and_payload_are_permanent(path, action):
    with storage.transaction(path) as db:
        identifier = save(db)
        db.execute("INSERT INTO deliveries VALUES (?,'zen-uuid','{}','uncertain',1,'fixture',NULL,0,0)", (identifier,))
        sql, args = {
            'uuid': ("UPDATE deliveries SET zen_id='new-uuid'", ()),
            'payload': ("UPDATE deliveries SET payload='{\"change\":true}'", ()),
            'flag': ('UPDATE deliveries SET ever_attempted=0', ()),
            'delete': ('DELETE FROM deliveries', ()),
            'replace': ("INSERT OR REPLACE INTO deliveries VALUES (?,'new-uuid','{}','prepared',0,'fixture',NULL,0,0)", (identifier,)),
        }[action]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, args)


def test_mirror_deletion_cannot_remove_processing_or_link(path):
    with storage.transaction(path) as db:
        identifier = save(db)
        db.execute("INSERT INTO zen_entities VALUES ('transaction','zen-uuid','{}',NULL,0,NULL)")
        db.execute("INSERT INTO link_groups VALUES ('group','single','account',0,'fixture')")
        db.execute("INSERT INTO link_bank_members VALUES ('group',?)", (identifier,))
        db.execute("INSERT INTO link_zen_members VALUES ('group','zen-uuid')")
        db.execute("UPDATE bank_processing SET state='delivered'")
        db.execute('DELETE FROM zen_entities')
        db.execute("INSERT INTO zen_tombstones VALUES ('transaction','zen-uuid','mobile','{}')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM zen_tombstones')
        assert db.execute('SELECT zen_id FROM link_zen_members').fetchone()[0] == 'zen-uuid'
        assert db.execute('SELECT state FROM bank_processing').fetchone()[0] == 'delivered'


@pytest.mark.parametrize('table', ['bank_versions', 'delivery_attempts'])
@pytest.mark.parametrize('action', ['update', 'delete'])
def test_revision_and_attempt_history_are_append_only(path, table, action):
    with storage.transaction(path) as db:
        identifier = save(db)
        db.execute("INSERT INTO delivery_attempts VALUES ('attempt',?,'zen-uuid',0,'unknown','{}')", (identifier,))
        sql = ('DELETE FROM ' + table) if action == 'delete' else ('UPDATE ' + table + ' SET bank_id=bank_id')
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)


def make_v1(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE service_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO service_meta VALUES ('instance_id','fixture-instance')")
        db.execute(f'PRAGMA application_id={storage.APPLICATION_ID}')
        db.execute('PRAGMA user_version=1')
        db.commit()


def test_v1_upgrade_backup_is_private_complete_and_idempotent(tmp_path):
    path = tmp_path / 'v1.sqlite3'
    make_v1(path)
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("INSERT INTO service_meta VALUES ('wal-only','fixture')")
        writer.commit()
        backup = storage.initialize(path)
    assert backup.stat().st_mode & 0o777 == 0o600
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute("SELECT value FROM service_meta WHERE key='wal-only'").fetchone()[0] == 'fixture'
    with storage.transaction(path, read_only=True) as db:
        assert db.execute("SELECT value FROM service_meta WHERE key='instance_id'").fetchone()[0] == 'fixture-instance'
    assert storage.initialize(path) is None
    assert len(list(tmp_path.glob(f'*.pre-v{storage.SCHEMA_VERSION}-*'))) == 1


def test_v1_upgrade_failure_rolls_back_all_schema_changes(tmp_path, monkeypatch):
    path = tmp_path / 'v1.sqlite3'
    make_v1(path)
    original = sha256(path.read_bytes()).hexdigest()
    def fail(db):
        db.execute('CREATE TABLE incomplete (value TEXT)')
        raise RuntimeError('fixture-interrupted-migration')
    monkeypatch.setattr(storage, 'apply_schema', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        storage.initialize(path)
    assert sha256(path.read_bytes()).hexdigest() == original
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='incomplete'").fetchone()
