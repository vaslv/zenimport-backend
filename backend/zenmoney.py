import json
import time
import httpx

KINDS = ('account', 'tag', 'transaction', 'instrument', 'user')
DEMO_ACCOUNT = '00000000-0000-4000-8000-000000000001'


class ZenMoney:
    def __init__(self, mode, token):
        self.mode, self.token = mode, token

    def diff(self, payload):
        if self.mode == 'demo':
            return {
                'serverTimestamp': int(time.time()),
                'instrument': [{'id': 1, 'title': 'Vietnamese dong', 'shortTitle': 'VND'}],
                'user': [{'id': 1}],
                'account': [{'id': DEMO_ACCOUNT, 'user': 1, 'instrument': 1,
                             'title': 'Демо · VND', 'archive': False}],
                'tag': [{'id': '00000000-0000-4000-8000-000000000002',
                         'title': 'Еда', 'user': 1}],
                'transaction': payload.get('transaction', []),
            }
        with httpx.Client(timeout=25) as client:
            response = client.post('https://api.zenmoney.ru/v8/diff/',
                                   headers={'Authorization': f'Bearer {self.token}'}, json=payload)
            response.raise_for_status()
            result = response.json()
        validate_diff(result)
        return result


def validate_diff(result):
    if not isinstance(result, dict) or type(result.get('serverTimestamp')) is not int:
        raise ValueError('Invalid ZenMoney response')
    if result.get('error'):
        raise ValueError('ZenMoney rejected diff')
    for kind in KINDS:
        if not isinstance(result.get(kind, []), list):
            raise ValueError('Invalid entity collection')
        for item in result.get(kind, []):
            if not isinstance(item, dict) or 'id' not in item:
                raise ValueError('Invalid entity')
    if not isinstance(result.get('deletion', []), list):
        raise ValueError('Invalid deletions')
    for item in result.get('deletion', []):
        if not isinstance(item, dict) or 'id' not in item or 'object' not in item:
            raise ValueError('Invalid deletion')


def apply_diff(db, result):
    validate_diff(result)
    for kind in KINDS:
        for item in result.get(kind, []):
            db.execute('INSERT OR REPLACE INTO zen_entities VALUES (?,?,?)',
                       (kind, str(item['id']), json.dumps(item)))
    for item in result.get('deletion', []):
        db.execute('DELETE FROM zen_entities WHERE kind=? AND id=?',
                   (item['object'], str(item['id'])))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('cursor',?)", (str(result['serverTimestamp']),))


def request_diff(db, client, transactions=None):
    row = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
    payload = {'currentClientTimestamp': int(time.time()), 'serverTimestamp': int(row['value']) if row else 0}
    if not row:
        payload['forceFetch'] = list(KINDS)
    if transactions:
        payload['transaction'] = transactions
    result = client.diff(payload)
    apply_diff(db, result)
    return result
