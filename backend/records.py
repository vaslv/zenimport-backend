"""Storage-level record identity/versioning. Capture orchestration belongs to B3."""
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from .sync_contracts import BankRecord


def add_alias(db, account_id, bank_id, external_id, identity_version):
    existing = db.execute('SELECT bank_id FROM bank_aliases WHERE account_id=? AND external_id=?',
                          (account_id, external_id)).fetchone()
    if existing and existing[0] != bank_id:
        raise ValueError('Bank identity alias is already owned')
    db.execute('INSERT OR IGNORE INTO bank_aliases VALUES (?,?,?,?)',
               (account_id, external_id, identity_version, bank_id))


def save_record(db, account_id, record: BankRecord, *, parser_version: str, timezone: str):
    if record.identity_version == 'legacy-v12' or parser_version == 'legacy-v12':
        raise ValueError('Legacy markers are reserved for verified archive import')
    account = db.execute('SELECT currency,scale FROM bank_accounts WHERE id=?', (account_id,)).fetchone()
    if not account or account['currency'] != record.currency:
        raise ValueError('Record does not belong to the account currency')
    if len(record.amount.partition('.')[2]) > account['scale']:
        raise ValueError('Record exceeds the account currency scale')
    if record.occurred_at.astimezone(ZoneInfo(timezone)).date() != record.local_date:
        raise ValueError('Record date differs from bank timezone')
    raw = record.model_dump_json()
    known = db.execute('SELECT bank_id FROM bank_aliases WHERE account_id=? AND external_id=?',
                       (account_id, record.external_id)).fetchone()
    existing = db.execute('SELECT * FROM bank_transactions WHERE id=?', (known[0],)).fetchone() if known else None
    if existing is None:
        existing = db.execute('SELECT * FROM bank_transactions WHERE account_id=? AND external_id=?',
                              (account_id, record.external_id)).fetchone()
    if existing and existing['raw_json'] == raw:
        return existing['id']
    identifier = existing['id'] if existing else str(uuid4())
    revision = existing['revision'] + 1 if existing else 1
    observed = int(time.time())
    values = (record.identity_kind, record.identity_version, record.local_date.isoformat(),
              record.occurred_at.isoformat(), record.direction, record.amount, record.currency,
              record.description, record.beneficiary_account, record.beneficiary_name, raw, revision)
    if existing:
        db.execute('''UPDATE bank_transactions SET identity_kind=?,identity_version=?,local_date=?,occurred_at=?,
            direction=?,amount=?,currency=?,description=?,beneficiary_account=?,beneficiary_name=?,raw_json=?,revision=? WHERE id=?''',
                   (*values, identifier))
    else:
        db.execute('INSERT INTO bank_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (identifier, account_id, record.external_id, *values))
        db.execute('INSERT INTO bank_processing VALUES (?,?,?,?)', (identifier, 'pending', 'new_bank_record', observed))
    db.execute('INSERT INTO bank_versions VALUES (?,?,?,?,?,?)', (identifier, revision, raw, raw, observed, parser_version))
    add_alias(db, account_id, identifier, record.external_id, record.identity_version)
    return identifier
