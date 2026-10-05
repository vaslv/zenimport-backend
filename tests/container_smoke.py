"""Black-box checks against a running DEMO Compose service, across recreation."""
import json
import os
import sys
from pathlib import Path
import httpx

phase = sys.argv[1]
if phase not in ('seed', 'verify'):
    raise SystemExit('Expected seed or verify')
base_url = os.environ['SMOKE_BASE_URL']
token = os.environ['SMOKE_TOKEN']
fixture = json.loads((Path(__file__).resolve().parent.parent / 'fixtures/import.json').read_text())
fixture['account'] = 'container-persistence-test'
fixture['transactions'][0]['external_id'] = 'container-test-001'

with httpx.Client(base_url=base_url, headers={'Authorization': f'Bearer {token}'}, timeout=15) as client:
    def call(method, url, body=None):
        response = client.request(method, url, **({'json': body} if body is not None else {}))
        response.raise_for_status()
        return response.json()

    assert call('GET', '/health') == {'status': 'ok', 'mode': 'demo'}, 'Never run smoke on LIVE'
    assert client.get('/api/state', headers={'Authorization': 'invalid'}).status_code == 401
    assert 'data-zenbridge-demo' in client.get('/demo-bank').text
    state = call('GET', '/api/state')
    if phase == 'seed':
        assert state['review'] == [], 'Requires a fresh disposable test volume'
        assert call('POST', '/api/imports', fixture)['added'] == 1
        call('POST', '/api/zenmoney/sync', {})
        state = call('GET', '/api/state')
        account, tx = state['bank_accounts'][0], state['review'][0]
        call('PUT', f"/api/accounts/{account['id']}/mapping", {'zenmoney_account': state['zenmoney_accounts'][0]['id']})
        assert call('POST', f"/api/review/{tx['id']}/confirm", {'action': 'create'})['state'] == 'queued'
        print('PASS: authenticated HTTP import → mapping → durable queued operation')
    else:
        assert len(state['review']) == 1
        assert state['review'][0]['state'] == 'queued', 'Queue was lost on recreation'
        assert len(state['outbox']) == 1
        queued_uuid = state['outbox'][0]['zen_id']
        assert state['bank_accounts'][0]['zenmoney_account']
        assert call('POST', '/api/imports', fixture)['duplicates'] == 1
        result = call('POST', '/api/outbox/send', {})
        assert result['results'][0]['state'] == 'sent'
        assert call('POST', '/api/outbox/send', {})['results'] == []
        state = call('GET', '/api/state')
        assert len(state['zenmoney_transactions']) == 1
        assert state['zenmoney_transactions'][0]['id'] == queued_uuid
        assert state['zenmoney_transactions'][0]['outcome'] == 65000
        print('PASS: recreated container preserved data, mapping and UUID; DEMO send is idempotent')
