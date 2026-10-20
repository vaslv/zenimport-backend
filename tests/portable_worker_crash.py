"""Child process for disposable migration crash tests. Fake read-only SDK only."""
from pathlib import Path
import sys
import time

import httpx
from zenmoney_api import Client
from zenmoney_api.protocol import decode_json

from backend import portable_graph, storage
from backend.migration import MigrationGate
from backend.mirror import Mirror
from backend.portable_service import PortableMigration

path,response,marker,point=map(Path,sys.argv[1:])
storage.initialize(path)

def respond(request):
    assert request.method=='POST' and request.url.path=='/v8/diff/'
    assert set(decode_json(request.content)) <= {'serverTimestamp','currentClientTimestamp','forceFetch'}
    return httpx.Response(200,content=response.read_bytes())

def pause():
    marker.write_text('synthetic-worker-ready')
    while True:time.sleep(0.05)

with Client('fixture-sdk-token',transport=httpx.MockTransport(respond)) as client:
    clock=lambda:1792540800
    mirror=Mirror(path,client,'fixture-sdk-token',clock)
    service=PortableMigration(path,MigrationGate(path),mirror,clock)
    if point.name=='during-staging':
        original=portable_graph.validate_stage
        def stage(*args,**kwargs):pause();return original(*args,**kwargs)
        portable_graph.validate_stage=stage
    else:
        original=service._perform
        def perform(task):original(task);pause()
        service._perform=perform
    assert service.run_once()
