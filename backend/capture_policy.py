"""Trusted capture policy and machine-readable failures; no bank HTTP requests."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from .sync_contracts import Capabilities


MESSAGES = {
    'stale_context': 'The selected server storage context has changed',
    'payload_too_large': 'The archive exceeds the size limit',
    'migration_busy': 'Finish the current request or delivery and try again',
    'migration_paused': 'This server is paused after data export',
    'migration_not_empty': 'Import is only available before the first synchronization',
    'migration_invalid': 'Invalid or incompatible migration archive',
    'migration_wrong_account': 'The archive belongs to another ZenMoney account',
    'migration_incomplete': 'Finish or stop bank history capture before exporting',
    'not_found': 'Resource not found', 'validation_error': 'Invalid request',
    'unsupported_bank': 'Unsupported bank', 'unsupported_currency': 'Unsupported currency or scale',
    'account_unconfigured': 'Configure all discovered accounts', 'account_disabled': 'Account disabled',
    'settings_conflict': 'Account settings have changed', 'account_busy': 'Account is already part of another job',
    'capture_busy': 'Another browser is already capturing history', 'lease_expired': 'Capture lease expired',
    'idempotency_conflict': 'Repeated request content differs',
    'history_incomplete': 'History capture is incomplete', 'capture_context_changed': 'Capture context has changed',
    'zenmoney_unavailable': 'No current ZenMoney catalog is available',
    'zenmoney_account_unavailable': 'The selected ZenMoney account is unavailable',
    'delivery_uncertain': 'Verify the previous delivery result first',
    'existing_ownership_unknown': 'Could not identify the account used by the previous match',
    'bank_login_required': 'Sign in to online banking',
}


class SyncError(Exception):
    def __init__(self, code, status=409, *, retryable=False, persist=False):
        super().__init__(MESSAGES[code])
        self.code, self.status, self.retryable, self.persist = code, status, retryable, persist


def fail(code, status=409, **kwargs):
    raise SyncError(code, status, **kwargs)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def get(db, table, identifier):
    # Table names are module constants, never request input.
    row = db.execute('SELECT * FROM ' + table + ' WHERE id=?', (str(identifier),)).fetchone()
    if row is None:
        fail('not_found', 404)
    return row


def today(now, timezone):
    return datetime.fromtimestamp(now, ZoneInfo(timezone)).date()


def capabilities(connection):
    try:
        value = Capabilities.model_validate_json(connection['capabilities_json'])
    except ValueError:
        fail('capture_context_changed')
    if (connection['bank'] not in {'vietinbank', 'bidv'} or value.timezone != 'Asia/Ho_Chi_Minh'
            or value.details == 'unavailable' or connection['connector_version'] == 'legacy-v12'):
        fail('capture_context_changed')
    return value.model_dump(mode='json')


SCALES = {'VND': 0, 'USD': 2, 'EUR': 2}
DETAIL_FIELDS = frozenset('currency remark amount balance trxId processDate dorC refType refId tellerId corresponsiveAccount corresponsiveName channel serviceBranchId serviceBranchName pmtType sendingBankId sendingBranchId sendingBranchName receivingBankId receivingBranchId receivingBranchName'.split())
BIDV_DETAIL_FIELDS = frozenset('transactionDate referenceNumber transactionRemark transactionAmount balance'.split())


def validate_discovery(data):
    if data.bank not in {'vietinbank', 'bidv'}:
        fail('unsupported_bank', 422)
    if data.connector_version == 'legacy-v12':
        fail('validation_error', 422)
    if data.capabilities.timezone != 'Asia/Ho_Chi_Minh' or data.capabilities.details == 'unavailable':
        fail('validation_error', 422)
    if data.bank == 'bidv' and (data.capabilities.currencies != ['VND'] or data.capabilities.details != 'included'
                               or data.capabilities.max_window_days > 30):
        fail('validation_error', 422)
    for account in data.accounts:
        if data.bank == 'bidv' and account.supported and (account.currency != 'VND' or account.scale != 0):
            fail('unsupported_currency', 422)
        if account.supported and SCALES.get(account.currency) != account.scale:
            fail('unsupported_currency', 422)


def validate_record(record, account, context, start, end):
    if (record.identity_version == 'legacy-v12' or
            ('connector_version' in context.keys() and context['connector_version'] == 'legacy-v12')):
        fail('validation_error', 422)
    if record.currency != account['currency'] or len(record.amount.partition('.')[2]) > account['scale']:
        fail('unsupported_currency', 422)
    if not start <= record.local_date.isoformat() <= end or record.occurred_at.astimezone(ZoneInfo(context['timezone'])).date() != record.local_date:
        fail('capture_context_changed')
    # Each connector's detail allowlist stays independent; adding a bank must
    # not widen the fields accepted for an existing bank.
    fields = BIDV_DETAIL_FIELDS if account['bank'] == 'bidv' else DETAIL_FIELDS
    if record.details_state != 'complete' or set(record.details) - fields:
        fail('validation_error', 422)
    if record.balance is not None and len(record.balance.partition('.')[2]) > account['scale']:
        fail('unsupported_currency', 422)
