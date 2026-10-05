import json
import time
from zenimport_zenmoney import Client, KINDS, FORCE_FETCH, validate_diff, entity_key

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
        if set(payload) - {'currentClientTimestamp', 'serverTimestamp', 'forceFetch'}:
            raise ValueError('Live client supports read-only synchronization')
        return Client(self.token).read_diff(payload['serverTimestamp'], force_fetch=payload.get('forceFetch'))


def apply_diff(db, result):
    validate_diff(result)
    for kind in KINDS:
        for item in result.get(kind, []):
            db.execute('INSERT OR REPLACE INTO zen_entities VALUES (?,?,?)',
                       (kind, entity_key(kind, item), json.dumps(item)))
    for item in result.get('deletion', []):
        db.execute('DELETE FROM zen_entities WHERE kind=? AND id=?',
                   (item['object'], str(item['id'])))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('cursor',?)", (str(result['serverTimestamp']),))


def request_diff(db, client, transactions=None):
    row = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
    payload = {'currentClientTimestamp': int(time.time()), 'serverTimestamp': int(row['value']) if row else 0}
    if not row:
        payload['forceFetch'] = list(FORCE_FETCH)
    if transactions:
        payload['transaction'] = transactions
    result = client.diff(payload)
    apply_diff(db, result)
    return result
