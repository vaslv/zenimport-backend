"""Disposable recovery process with read-only fake SDK and real SIGKILL points."""
from pathlib import Path
import sys
import time
import httpx
from zenmoney_api import Client
from zenmoney_api.protocol import decode_json
from backend import portable_graph
from backend.archive_service import ArchiveService
from backend.migration import MigrationGate
from backend.mirror import Mirror

path,response,marker=map(Path,sys.argv[1:4]);point,id,preview,stamp=sys.argv[4:]

def respond(request):
    assert set(decode_json(request.content))<={'serverTimestamp','currentClientTimestamp','forceFetch'}
    return httpx.Response(200,content=response.read_bytes())

def pause():
    marker.write_text('synthetic-recovery-ready')
    while True:time.sleep(.05)

with Client('fixture-sdk-token',transport=httpx.MockTransport(respond)) as client:
    clock=lambda:int(stamp);mirror=Mirror(path,client,'fixture-sdk-token',clock)
    service=ArchiveService(path,MigrationGate(path),mirror,clock)
    if point=='during-validation':
        original=portable_graph.validate_stage
        def validate(*args,**kwargs):pause();return original(*args,**kwargs)
        portable_graph.validate_stage=validate
        service.run_once()
    else:
        service.activate_recovery(id,preview,True);pause()
