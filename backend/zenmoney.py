"""Application sync policy. Live HTTP and wire objects belong to zenmoney_api."""
import time
from copy import deepcopy
from zenmoney_api.protocol import FORCE_FETCH, decode_diff, encode_json

DEMO_ACCOUNT = '00000000-0000-4000-8000-000000000001'


class DemoClient:
    """Offline implementation of the SDK methods used by the demo application."""
    def __init__(self):
        self.operations = {}

    def read_diff(self, server_timestamp=0, *, force_fetch=None):
        return self.diff(server_timestamp, force_fetch=force_fetch)

    def upsert(self, kind, entities, server_timestamp=0):
        return self.diff(server_timestamp, changes={kind: entities})

    def diff(self, server_timestamp=0, *, changes=None, force_fetch=None):
        for item in (changes or {}).get('transaction', []):
            self.operations[item['id']] = deepcopy(item)
        data = {
            'serverTimestamp': max(int(time.time()), server_timestamp),
            'instrument': [{'id': 1, 'title': 'Vietnamese dong', 'shortTitle': 'VND'}],
            'user': [{'id': 1, 'currency': 1}],
            'account': [{'id': DEMO_ACCOUNT, 'user': 1, 'instrument': 1,
                         'title': 'Демо · VND', 'archive': False, 'type': 'cash'}],
            'tag': [{'id': '00000000-0000-4000-8000-000000000002', 'title': 'Еда', 'user': 1}],
            'transaction': list(self.operations.values()),
        }
        return decode_diff(encode_json(data), server_timestamp)

    def close(self):
        pass


def apply_diff(db, result, requested_cursor=None, *, full_collections=None):
    from .zenstore import store_diff
    store_diff(db, result, requested_cursor, full_collections=full_collections)


def request_diff(db, client, transactions=None):
    row = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
    full = db.execute("SELECT 1 FROM meta WHERE key='needs_full_sync'").fetchone()
    cursor = int(row['value']) if row and not full else 0
    force_fetch = list(FORCE_FETCH) if not row or full else None
    if transactions:
        result = client.upsert('transaction', transactions, cursor)
    else:
        result = client.read_diff(cursor, force_fetch=force_fetch)
    apply_diff(db, result, cursor, full_collections=force_fetch if not transactions else None)
    db.execute("DELETE FROM meta WHERE key='needs_full_sync'")
    return result
