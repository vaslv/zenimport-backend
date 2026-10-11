"""Fixed-endpoint transport with separate reads and category updates."""
import json
import time
import math
import uuid
from datetime import date
from collections.abc import Callable, Sequence
import httpx

ENDPOINT = 'https://api.zenmoney.ru/v8/diff/'
KINDS = ('instrument', 'company', 'user', 'account', 'tag', 'merchant',
         'budget', 'reminder', 'reminderMarker', 'transaction', 'country')
# Country is observed in responses but not documented as a forceFetch key.
FORCE_FETCH = tuple(kind for kind in KINDS if kind != 'country')


class ZenMoneyError(Exception):
    """Sanitized error: never includes credentials or remote body."""


class TransportError(ZenMoneyError):
    pass


class ResponseError(ZenMoneyError):
    pass


class HTTPError(ZenMoneyError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f'ZenMoney HTTP {status_code}')


class Diff(dict):
    def __init__(self, data: dict, raw_json: str):
        super().__init__(data)
        self.raw_json = raw_json


def entity_key(kind: str, item: dict) -> str:
    if kind == 'budget':
        if not all(k in item for k in ('user', 'date', 'tag')):
            raise ResponseError('Invalid budget key')
        if type(item['user']) not in (str, int) or not isinstance(item['date'], str):
            raise ResponseError('Invalid budget key')
        if item['tag'] is not None and not isinstance(item['tag'], str):
            raise ResponseError('Invalid budget key')
        return json.dumps([item['user'], item['date'], item['tag']], separators=(',', ':'))
    identifier = item.get('id')
    if type(identifier) not in (str, int) or identifier == '':
        raise ResponseError('Invalid entity identifier')
    return str(identifier)


def validate_diff(data: dict) -> None:
    if not isinstance(data, dict) or type(data.get('serverTimestamp')) is not int or data['serverTimestamp'] < 0:
        raise ResponseError('Invalid server timestamp')
    if data.get('error'):
        raise ResponseError('ZenMoney rejected diff')
    for kind in KINDS:
        rows = data.get(kind, [])
        if not isinstance(rows, list):
            raise ResponseError('Invalid entity collection')
        for row in rows:
            if not isinstance(row, dict):
                raise ResponseError('Invalid entity')
            entity_key(kind, row)
    if not isinstance(data.get('deletion', []), list):
        raise ResponseError('Invalid deletions')
    for row in data.get('deletion', []):
        if not isinstance(row, dict) or not isinstance(row.get('object'), str) or type(row.get('id')) not in (str, int):
            raise ResponseError('Invalid deletion')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError('Non-finite JSON number')


class Client:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 60, max_response_bytes: int = 64 * 1024 * 1024,
                 clock: Callable[[], float] = time.time):
        if not token or any(c.isspace() for c in token):
            raise ValueError('A nonempty bearer token without whitespace is required')
        if max_response_bytes <= 0 or timeout <= 0:
            raise ValueError('Response limit and timeout must be positive')
        self._token, self._transport = token, transport
        self._timeout, self._limit, self._clock = timeout, max_response_bytes, clock

    def read_diff(self, server_timestamp: int = 0, *, force_fetch: Sequence[str] | None = None) -> Diff:
        if type(server_timestamp) is not int or server_timestamp < 0:
            raise ValueError('Invalid synchronization cursor')
        payload = {'currentClientTimestamp': int(self._clock()), 'serverTimestamp': server_timestamp}
        if force_fetch is not None:
            if isinstance(force_fetch, str) or any(k not in FORCE_FETCH for k in force_fetch):
                raise ValueError('Unsupported forceFetch collection')
            payload['forceFetch'] = list(dict.fromkeys(force_fetch))
        return self._exchange(payload)

    @staticmethod
    def _category_payloads(changes, server_timestamp=0):
        if type(server_timestamp) is not int or server_timestamp < 0 or not changes or len(changes) > 20:
            raise ValueError('Invalid category update request')
        transactions = []
        for before, payload in changes:
            if not before.get('id') or before.get('deleted') or before.get('hold') or before.get('incomeAccount') != before.get('outcomeAccount'):
                raise ValueError('Cannot categorize this transaction')
            if not isinstance(payload.get('tag'), list) or len(payload['tag']) != 1 or not isinstance(payload['tag'][0], str) or not payload['tag'][0]:
                raise ValueError('Expected one category')
            if type(payload.get('changed')) is not int:
                raise ValueError('Expected changed timestamp')
            clean = lambda item: {k:v for k,v in item.items() if k not in ('tag', 'changed')}
            if clean(before) != clean(payload):
                raise ValueError('Only category updates are allowed')
            transactions.append(payload)
        if len({t['id'] for t in transactions}) != len(transactions):
            raise ValueError('Duplicate transaction')
        return transactions

    def update_categories(self, changes, server_timestamp=0):
        return self.write_transactions([], changes, server_timestamp)

    @staticmethod
    def _creation_payloads(transactions, server_timestamp=0):
        if type(server_timestamp) is not int or server_timestamp < 0 or not 1 <= len(transactions) <= 20:
            raise ValueError('Invalid creation request')
        allowed = {'id','user','created','changed','deleted','hold','date','incomeAccount','outcomeAccount',
                   'incomeInstrument','outcomeInstrument','income','outcome','tag','payee','comment'}
        for tx in transactions:
            if set(tx) != allowed or uuid.UUID(tx['id']).version != 4:
                raise ValueError('Expected a new UUID and supported creation fields')
            date.fromisoformat(tx['date'])
            if any(type(tx[k]) is not int or tx[k] <= 0 for k in ('user','created','changed','incomeInstrument','outcomeInstrument')):
                raise ValueError('Invalid identity or timestamps')
            if tx['deleted'] is not False or tx['hold'] is not False:
                raise ValueError('Only posted transactions may be created')
            if not tx['incomeAccount'] or tx['incomeAccount'] != tx['outcomeAccount'] or tx['incomeInstrument'] != tx['outcomeInstrument']:
                raise ValueError('Only single-account income or expense is supported')
            if any(type(tx[k]) not in (int,float) or not math.isfinite(tx[k]) or tx[k]<0 for k in ('income','outcome')) or (tx['income']>0)==(tx['outcome']>0):
                raise ValueError('Expected one positive amount')
            if tx['tag'] is not None and (not isinstance(tx['tag'],list) or len(tx['tag'])!=1 or not isinstance(tx['tag'][0],str) or not tx['tag'][0]):
                raise ValueError('Expected one category or null')
            if not isinstance(tx['comment'],str) or (tx['payee'] is not None and not isinstance(tx['payee'],str)):
                raise ValueError('Invalid description')
        if len({t['id'] for t in transactions}) != len(transactions): raise ValueError('Duplicate UUID')
        return transactions

    def create_transactions(self, transactions, server_timestamp=0):
        return self.write_transactions(transactions, [], server_timestamp)

    def write_transactions(self, creations, changes, server_timestamp=0):
        if not 1 <= len(creations)+len(changes) <= 20:
            raise ValueError('Expected between 1 and 20 changes')
        transactions = (self._creation_payloads(creations, server_timestamp) if creations else []) + (self._category_payloads(changes, server_timestamp) if changes else [])
        if len({t['id'] for t in transactions}) != len(transactions):
            raise ValueError('Duplicate transaction across batch')
        return self._exchange({'currentClientTimestamp':int(self._clock()),'serverTimestamp':server_timestamp,'transaction':transactions})

    def _exchange(self, payload):
        server_timestamp = payload['serverTimestamp']
        try:
            # No redirects, proxy environment or retries; bearer stays on the fixed endpoint.
            with httpx.Client(timeout=self._timeout, transport=self._transport,
                              follow_redirects=False, trust_env=False) as client:
                with client.stream('POST', ENDPOINT, headers={'Authorization': f'Bearer {self._token}'}, json=payload) as response:
                    if response.status_code != 200:
                        raise HTTPError(response.status_code)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self._limit:
                            raise ResponseError('ZenMoney response exceeds configured limit')
        except httpx.HTTPError:
            raise TransportError('ZenMoney connection failed') from None
        try:
            raw = bytes(body).decode('utf-8')
            data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_number)
        except (ValueError, UnicodeError):
            raise ResponseError('Invalid ZenMoney JSON') from None
        validate_diff(data)
        if data['serverTimestamp'] < server_timestamp:
            raise ResponseError('ZenMoney cursor moved backwards')
        return Diff(data, raw)
