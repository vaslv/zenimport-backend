"""Reference dispatcher over current Python behavior and exact Decimal arithmetic."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import re
import sys
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from zenmoney_api.protocol import decode_json, encode_json
from backend import capture_policy, existing, predictions
from backend.sync_contracts import BankRecord, Money, WireDate, WireTime


class ContractError(Exception):
    pass


def fail(code):
    raise ContractError(code)


class FixtureContext:
    def __init__(self, clock, ids):
        if type(clock) is not str or len(clock) > 32 or not re.fullmatch(r'0|[1-9][0-9]*', clock):
            fail('unsupported_scenario')
        try:
            valid = all(type(value) is str and str(UUID(value)) == value for value in ids)
        except ValueError:
            valid = False
        if not valid or len(set(ids)) != len(ids):
            fail('unsupported_scenario')
        self.now, self.ids, self.index = int(clock), list(ids), 0

    @property
    def remaining(self):
        return len(self.ids) - self.index

    def next_uuid(self):
        if not self.remaining:
            fail('unsupported_scenario')
        result = self.ids[self.index]
        self.index += 1
        return result


def text(value):
    if type(value) is not str:
        fail('invalid_dto')
    return value


def money(value):
    if type(value) is not str or len(value) > 256 or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value):
        fail('invalid_amount')
    integer, _, fraction = value.removeprefix('-').partition('.')
    if len(integer) > 96 or len(fraction) > 96:
        fail('amount_overflow')
    return Decimal(value)


def plain(value):
    if not value:
        return '0'
    result = format(value, 'f')
    if '.' in result:
        result = result.rstrip('0').rstrip('.')
    integer, _, fraction = result.removeprefix('-').partition('.')
    if len(integer) > 96 or len(fraction) > 96:
        fail('amount_overflow')
    return result


def identity(value):
    value = text(value)
    if len(value) > 16384:
        fail('invalid_dto')
    if any(unicodedata.category(char) in {'Cn', 'Cs'} for char in value):
        fail('unsupported_unicode')
    return predictions.normalized(value)


def day(value):
    try:
        return TypeAdapter(WireDate).validate_python(text(value)).isoformat()
    except ValidationError:
        fail('invalid_date')


def compact_wire(raw):
    result = []
    quoted = escaped = False
    for char in raw:
        if quoted:
            result.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            result.append(char)
        elif char not in ' \r\n\t':
            result.append(char)
    return ''.join(result)


def canonical_dto(value, depth=0, budget=None):
    budget = [100000] if budget is None else budget
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        fail('invalid_dto')
    if type(value) is int and abs(value) > 9007199254740991:
        fail('invalid_dto')
    if type(value) is str and any(0xd800 <= ord(c) <= 0xdfff for c in value):
        fail('invalid_dto')
    if type(value) is list:
        for item in value:
            canonical_dto(item, depth + 1, budget)
    elif type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or any(0xd800 <= ord(c) <= 0xdfff for c in key):
                fail('invalid_dto')
            canonical_dto(item, depth + 1, budget)
    elif type(value) not in {str, bool, int, type(None)}:
        fail('invalid_dto')
    result = capture_policy.canonical(value)
    if len(result.encode()) > 2_000_000:
        fail('invalid_dto')
    return result


def execute(operation, data):
    with localcontext() as context:
        context.prec = 256
        if operation == 'money.sum':
            values = data.get('values')
            if type(values) is not list or len(values) > 100000:
                fail('invalid_dto')
            result = Decimal(0)
            for value in values:
                result += money(value)
                plain(result)  # Same explicit intermediate bound; never round.
            return plain(result)
        if operation in {'money.compare', 'money.subtract'}:
            left, right = money(data.get('left')), money(data.get('right'))
            return (left > right) - (left < right) if operation.endswith('compare') else plain(left - right)
        if operation == 'money.sign':
            value = money(data.get('value'))
            return (value > 0) - (value < 0)
        if operation == 'money.bank':
            value = data.get('value')
            if type(value) is not str or not re.fullmatch(r'(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?', value):
                fail('invalid_bank_amount')
            scale = data.get('scale', 12)
            if type(scale) is not int or not 0 <= scale <= 12 or len(value.partition('.')[2]) > scale:
                fail('unsupported_scale')
            try:
                checked = TypeAdapter(Money).validate_python(value)
                BankRecord.nonzero_amount(checked)
            except (ValidationError, ValueError):
                fail('invalid_bank_amount')
            return plain(Decimal(checked))
        if operation == 'money.wire':
            token = text(data.get('value'))
            if len(token) > 256:
                fail('amount_overflow')
            if not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', token):
                fail('invalid_amount')
            try:
                value = Decimal(token)
                if value and (value.adjusted() >= 96 or value.normalize().as_tuple().exponent < -96):
                    fail('amount_overflow')
                return plain(value)
            except InvalidOperation:
                fail('amount_overflow')
        if operation == 'movement.signed':
            tx = dict(data['transaction'])
            for side in ['income', 'outcome']:
                if side in tx:
                    tx[side] = money(tx[side])
            value = existing.movement(tx, data['target'], data['instrument'])
            return None if value is None else plain(value)
        if operation == 'identity.normalize':
            return identity(data.get('value'))
        if operation == 'identity.recipient':
            row = data['row']
            for key in ['beneficiary_account', 'beneficiary_name', 'description']:
                identity(row.get(key) or '')
            return predictions.recipient(row)
        if operation == 'dates.calendar':
            return day(data.get('value'))
        if operation == 'dates.localDay':
            try:
                value = TypeAdapter(WireTime).validate_python(text(data.get('value')))
            except ValidationError:
                fail('invalid_date')
            try:
                return value.astimezone(ZoneInfo(text(data.get('timezone')))).date().isoformat()
            except ZoneInfoNotFoundError:
                fail('invalid_timezone')
        if operation == 'dates.months':
            value = date.fromisoformat(day(data.get('value')))
            delta = data.get('delta')
            if type(delta) is not int or abs(delta) > 1200:
                fail('invalid_date')
            ordinal = value.year * 12 + value.month - 1 + delta
            year, month = divmod(ordinal, 12)
            if not 1 <= year <= 9999:
                fail('invalid_date')
            # Calendar arithmetic only; monetary arithmetic never uses integers
            # converted from a binary floating-point value.
            from calendar import monthrange
            return date(year, month + 1, min(value.day, monthrange(year, month + 1)[1])).isoformat()
        if operation in {'wire.canonical', 'wire.digest'}:
            value = canonical_dto(data['value'])
            return sha256(value.encode()).hexdigest() if operation.endswith('digest') else value
        if operation == 'wire.roundtrip':
            return compact_wire(encode_json(decode_json(text(data.get('value')).encode())).decode())
        fail('unsupported_command')


def run_scenario(scenario):
    context = FixtureContext(scenario['clock'], scenario['uuids'])
    if (scenario['contract_version'] != 1 or scenario['initial_state'] or context.remaining
            or scenario['expected']['state_assertions']):
        fail('unsupported_scenario')
    results = []
    for command in scenario['commands']:
        if command.get('fault', 'none') != 'none':
            fail('unsupported_scenario')
        try:
            results.append(execute(command['operation'], command['input']))
        except ContractError as error:
            if str(error) in {'unsupported_command', 'unsupported_scenario'}:
                raise
            results.append({'error': str(error)})
    return {'results': results, 'external_writes': [], 'state_assertions': []}


if __name__ == '__main__':
    scenario = json.load(sys.stdin)
    print(json.dumps(run_scenario(scenario), ensure_ascii=True, separators=(',', ':')))
