"""Trusted capture policy and machine-readable failures; no bank HTTP requests."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from .sync_contracts import Capabilities


MESSAGES = {
    'not_found': 'Ресурс не найден', 'validation_error': 'Некорректный запрос',
    'unsupported_bank': 'Банк не поддерживается', 'unsupported_currency': 'Валюта или масштаб не поддерживается',
    'account_unconfigured': 'Настройте все найденные счета', 'account_disabled': 'Счёт отключён',
    'settings_conflict': 'Настройки счёта изменились', 'account_busy': 'Счёт уже участвует в другом задании',
    'capture_busy': 'Сбор уже выполняется другим браузером', 'lease_expired': 'Разрешение сборщика истекло',
    'idempotency_conflict': 'Содержимое повторного запроса отличается',
    'history_incomplete': 'История получена не полностью', 'capture_context_changed': 'Контекст получения изменился',
    'zenmoney_unavailable': 'Нет актуального каталога ZenMoney',
    'zenmoney_account_unavailable': 'Выбранный счёт ZenMoney недоступен',
    'delivery_uncertain': 'Сначала требуется проверить результат прежней отправки',
    'bank_login_required': 'Войдите в личный кабинет банка',
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
    if connection['bank'] != 'vietinbank' or value.timezone != 'Asia/Ho_Chi_Minh' or value.details == 'unavailable':
        fail('capture_context_changed')
    return value.model_dump(mode='json')


SCALES = {'VND': 0, 'USD': 2, 'EUR': 2}
DETAIL_FIELDS = frozenset('currency remark amount balance trxId processDate dorC refType refId tellerId corresponsiveAccount corresponsiveName channel serviceBranchId serviceBranchName pmtType sendingBankId sendingBranchId sendingBranchName receivingBankId receivingBranchId receivingBranchName'.split())


def validate_discovery(data):
    if data.bank != 'vietinbank':
        fail('unsupported_bank', 422)
    if data.capabilities.timezone != 'Asia/Ho_Chi_Minh' or data.capabilities.details == 'unavailable':
        fail('validation_error', 422)
    for account in data.accounts:
        if account.supported and SCALES.get(account.currency) != account.scale:
            fail('unsupported_currency', 422)


def validate_record(record, account, context, start, end):
    if record.currency != account['currency'] or len(record.amount.partition('.')[2]) > account['scale']:
        fail('unsupported_currency', 422)
    if not start <= record.local_date.isoformat() <= end or record.occurred_at.astimezone(ZoneInfo(context['timezone'])).date() != record.local_date:
        fail('capture_context_changed')
    # VietinBank has transaction details; client cannot claim them unavailable.
    if record.details_state != 'complete' or set(record.details) - DETAIL_FIELDS:
        fail('validation_error', 422)
    if record.balance is not None and len(record.balance.partition('.')[2]) > account['scale']:
        fail('unsupported_currency', 422)
