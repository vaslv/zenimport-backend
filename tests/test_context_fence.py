"""Optional selected-context headers fence ordinary requests before any handler."""
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend import storage, migration_participant
from backend.app import create_app
from backend.config import Settings
from backend.migration import meta
from backend.sync_contracts import ErrorResponse

TOKEN='fixture-context-fence-token'


@pytest.fixture
def fixture(tmp_path):
    settings=Settings(tmp_path/'context.sqlite3',TOKEN)
    app=create_app(settings,start_worker=False);calls=[]
    @app.post('/api/v1/fence-fixture')
    def mutate():
        # Import/replace cannot acquire the exclusive gate during this request.
        assert app.state.migration_gate.active==1
        calls.append(True)
        return {'accepted':True}
    with TestClient(app) as client:
        with storage.transaction(settings.database_path,read_only=True) as db:
            identity=migration_participant.installation(db)['installation_id'];dataset=meta(db,'instance_id')
        yield client,settings,calls,{'Authorization':'Bearer '+TOKEN,
            'X-ZenImport-Installation':identity,'X-ZenImport-Dataset':dataset}


def test_matching_context_and_legacy_requests_keep_existing_behavior(fixture):
    client,_,calls,headers=fixture
    assert client.get('/api/v1/info',headers=headers).json()['context_fencing']==1
    assert client.post('/api/v1/fence-fixture',headers=headers).status_code==200
    assert client.post('/api/v1/fence-fixture',headers={'Authorization':headers['Authorization']}).status_code==200
    assert len(calls)==2


@pytest.mark.parametrize('field',['X-ZenImport-Installation','X-ZenImport-Dataset'])
def test_replaced_server_or_dataset_cannot_receive_an_old_context_mutation(fixture,field):
    client,_,calls,headers=fixture;headers=dict(headers,**{field:str(uuid4())})
    result=client.post('/api/v1/fence-fixture',headers=headers)
    assert result.status_code==409 and ErrorResponse.model_validate(result.json()).error.code=='stale_context'
    assert not calls


def test_same_address_dataset_change_is_rejected_under_the_request_gate(fixture):
    client,settings,calls,headers=fixture
    with storage.transaction(settings.database_path) as db:
        db.execute("UPDATE service_meta SET value=? WHERE key='instance_id'",(str(uuid4()),))
    result=client.post('/api/v1/fence-fixture',headers=headers)
    assert result.status_code==409 and result.json()['error']['code']=='stale_context' and not calls


@pytest.mark.parametrize('kind',['missing','duplicate','invalid'])
def test_partial_duplicate_and_malformed_contexts_do_not_reach_handler(fixture,kind):
    client,_,calls,headers=fixture;headers=list(headers.items())
    if kind=='missing':headers=[row for row in headers if row[0]!='X-ZenImport-Dataset']
    elif kind=='duplicate':headers.append(('X-ZenImport-Dataset',str(uuid4())))
    else:headers=[(key,'invalid' if key=='X-ZenImport-Dataset' else value) for key,value in headers]
    result=client.post('/api/v1/fence-fixture',headers=headers)
    assert result.status_code==422 and not calls
