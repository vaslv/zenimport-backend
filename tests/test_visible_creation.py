"""Visible-page scope restricts preparation and remote writes on the server."""
from test_operation_types import setup
from backend.db import transaction
import copy
from test_flow import IMPORT


def test_visible_scope_does_not_prepare_hidden_history(tmp_path):
    api,path,remote,bank_id=setup(tmp_path)
    extra=copy.deepcopy(IMPORT)
    extra['transactions'][0]['external_id']='hidden'
    extra['transactions'][0]['amount_minor']=-12345
    assert api.post('/api/imports',json=extra).status_code==200
    scope={'bank':'demo','account':api.get('/api/state').json()['bank_accounts'][0]['id'],
           'bank_ids':[bank_id],'include_uncategorized':True,'creations_only':True}
    response=api.post('/api/batch/preview',json=scope)
    assert response.status_code==200,response.text
    items=response.json()['items'];assert [i['id'] for i in items]==[bank_id]
    assert not items[0]['error']
    with transaction(path,read_only=True) as db:
        assert [r[0] for r in db.execute('SELECT bank_id FROM creation_jobs')]==[bank_id]
    sent=api.post('/api/batch/send',json={**scope,'items':[{'kind':'creation','id':bank_id,'snapshot':items[0]['snapshot']}]})
    assert sent.status_code==200,sent.text
    assert sent.json()['results'][0]['state']=='sent'
    assert len(remote.writes)==1 and len(remote.writes[0])==1
    assert remote.writes[0][0]['tag'] is None or remote.writes[0][0]['tag']==[]
    assert api.post('/api/batch/preview',json=scope).json()['items']==[]
    assert len(remote.writes)==1


def test_visible_scope_cannot_send_another_row(tmp_path):
    api,path,remote,bank_id=setup(tmp_path)
    account=api.get('/api/state').json()['bank_accounts'][0]['id']
    all_scope={'bank':'demo','account':account,'include_uncategorized':True,'creations_only':True}
    item=api.post('/api/batch/preview',json=all_scope).json()['items'][0]
    result=api.post('/api/batch/send',json={**all_scope,'bank_ids':['different-row'],
        'items':[{'kind':'creation','id':bank_id,'snapshot':item['snapshot']}]})
    assert result.status_code==409
    assert not remote.writes
