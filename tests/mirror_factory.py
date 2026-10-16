"""Synthetic SDK transport and financial data; no real API, secrets or user rows."""
from copy import deepcopy
import httpx
from uuid import uuid4

from zenmoney_api import Client
from zenmoney_api.protocol import KINDS, decode_json, encode_json

from backend import records, storage
from backend.mirror import Mirror
from backend.sync_contracts import BankRecord
from capture_factory import NOW


def snapshot(cursor=100, **changes):
    result = {kind: [] for kind in KINDS}
    result.update(serverTimestamp=cursor,
        instrument=[{'id': 2, 'shortTitle': 'VND'}, {'id': 3, 'shortTitle': 'USD'}],
        user=[{'id': 1, 'login': 'fixture'}],
        account=[{'id': 'zen-vnd', 'user': 1, 'instrument': 2, 'type': 'checking', 'title': 'Fixture VND'}],
        tag=[{'id': 'category-a', 'title': 'A'}, {'id': 'category-b', 'title': 'B'}])
    result.update(changes)
    return result


def movement(identifier, category='category-a', *, amount=100, direction='expense', **changes):
    result = {'id': identifier, 'user': 1, 'date': '2026-10-01', 'incomeAccount': 'zen-vnd', 'outcomeAccount': 'zen-vnd',
        'incomeInstrument': 2, 'outcomeInstrument': 2, 'income': amount if direction == 'income' else 0,
        'outcome': amount if direction == 'expense' else 0, 'viewed': True, 'hold': False, 'tag': [category] if category else None}
    result.update(changes)
    return result


class Fixture:
    def __init__(self, path):
        self.path, self.now, self.responses, self.requests = path, NOW, [], []
        storage.initialize(path)
        def handler(request):
            body = decode_json(request.content)
            assert set(body) <= {'serverTimestamp', 'currentClientTimestamp', 'forceFetch'}, 'A mirror read attempted a write'
            self.requests.append(body)
            assert self.responses, 'Unexpected synthetic API request'
            response = self.responses.pop(0)
            if callable(response):
                return response(request)
            if isinstance(response, Exception):
                raise response
            return response if isinstance(response, httpx.Response) else httpx.Response(200, content=response if isinstance(response, bytes) else encode_json(response))
        self.client = Client('fixture-zenmoney-token', transport=httpx.MockTransport(handler), clock=lambda: self.now)
        self.mirror = Mirror(path, self.client, 'fixture-zenmoney-token', lambda: self.now, sleep=lambda _: None)
        self.data = snapshot()
        with storage.transaction(path) as db:
            self.account(db, 'bank-vnd')

    def close(self):
        self.client.close()

    def account(self, db, identifier):
        db.execute('''INSERT INTO bank_accounts (id,bank,identity_scope,source_key,currency,scale,label,masked_number,supported,selection,zenmoney_account_id,import_start)
            VALUES (?,'vietinbank','bank',?,'VND',0,'Fixture','••1234',1,'enabled','zen-vnd','2026-01-01')''', (identifier, identifier))
        db.execute('INSERT INTO account_bindings VALUES (?,?,?,?,?,?)', (str(uuid4()), identifier, 'zen-vnd', 1, 'fixture', self.now))

    def bank(self, *, account='bank-vnd', direction='expense', amount='100', beneficiary_account='merchant-1', beneficiary_name='Fixture shop', description='Fixture purchase', **changes):
        row = BankRecord.model_validate({'external_id': str(uuid4()), 'identity_kind': 'bank', 'identity_version': 'fixture',
            'occurred_at': '2026-10-01T12:00:00+07:00', 'local_date': '2026-10-01', 'direction': direction, 'amount': amount,
            'currency': 'VND', 'description': description, 'beneficiary_account': beneficiary_account,
            'beneficiary_name': beneficiary_name, 'details_state': 'complete', **changes})
        with storage.transaction(self.path) as db:
            return records.save_record(db, account, row, parser_version='fixture', timezone='Asia/Ho_Chi_Minh')

    def history(self, count=3, category='category-a', *, receipt=False, viewed=True, **bank_changes):
        banks, remote = [], []
        for _ in range(count):
            identifier = self.bank(**bank_changes)
            zen = 'zen-' + identifier
            remote.append(movement(zen, category, viewed=viewed, direction=bank_changes.get('direction', 'expense')))
            with storage.transaction(self.path) as db:
                db.execute("UPDATE bank_processing SET state='delivered' WHERE bank_id=?", (identifier,))
                if receipt:
                    db.execute('INSERT INTO processing_receipts VALUES (?,?,?,?)', (identifier, zen, 'mirror_exists', self.now))
                else:
                    group = str(uuid4())
                    db.execute('INSERT INTO link_groups VALUES (?,?,?,?,?)', (group, 'single', bank_changes.get('account', 'bank-vnd'), self.now, 'manual'))
                    db.execute('INSERT INTO link_bank_members VALUES (?,?)', (group, identifier))
                    db.execute('INSERT INTO link_zen_members VALUES (?,?)', (group, zen))
            banks.append(identifier)
        self.data['transaction'].extend(remote)
        return banks, remote

    def refresh(self, data=None):
        self.responses.append(deepcopy(self.data) if data is None else data)
        self.mirror.refresh(force=True)

    def predict(self, bank_id, revision=None):
        from backend.predictions import predict
        with storage.transaction(self.path, read_only=True) as db:
            return predict(db, bank_id, self.now, revision=revision)
