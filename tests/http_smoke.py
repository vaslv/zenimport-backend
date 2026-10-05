import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx

root = Path(__file__).resolve().parent.parent
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
with tempfile.TemporaryDirectory(prefix='zenbridge-smoke-') as temp:
    env = dict(os.environ, PORT=str(port), API_TOKEN=secrets.token_urlsafe(32), ZENMONEY_MODE='demo', DATABASE_PATH=str(Path(temp) / 'db.sqlite3'))
    process = subprocess.Popen([sys.executable, '-m', 'backend'], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'Authorization': 'Bearer '+env['API_TOKEN']}) as client:
            for attempt in range(50):
                try:
                    response = client.get('/health')
                    response.raise_for_status()
                    break
                except httpx.ConnectError:
                    if process.poll() is not None:
                        raise RuntimeError('Server exited before startup')
                    time.sleep(.1)
            assert response.json()['mode'] == 'demo'
            assert 'data-zenbridge-demo' in client.get('/demo-bank').text
            assert client.post('/api/imports', json=json.loads((root / 'fixtures/import.json').read_text())).json()['added'] == 1
            client.post('/api/zenmoney/sync', json={}).raise_for_status()
            state = client.get('/api/state').json()
            account = state['bank_accounts'][0]['id']
            tx = state['review'][0]['id']
            client.put(f'/api/accounts/{account}/mapping', json={'zenmoney_account': state['zenmoney_accounts'][0]['id']}).raise_for_status()
            client.post(f'/api/review/{tx}/confirm', json={'action':'create'}).raise_for_status()
            assert client.post('/api/outbox/send', json={}).json()['results'][0]['state'] == 'sent'
            print('PASS: real HTTP process → import → sync → mapping → confirm → send (demo)')
    finally:
        process.terminate()
        process.wait(timeout=10)
