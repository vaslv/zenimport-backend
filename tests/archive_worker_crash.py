"""Disposable backup crash process. No SDK client or network is available."""
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time
from backend import portable_codec
from backend.archive_service import ArchiveService
from backend.migration import MigrationGate

path,marker=map(Path,sys.argv[1:])
original=portable_codec.create_archive

def create(*args,**kwargs):
    result=original(*args,**kwargs)
    marker.write_text('synthetic-backup-ready')
    while True:time.sleep(.05)
    return result

portable_codec.create_archive=create
service=ArchiveService(path,MigrationGate(path),SimpleNamespace(lock=threading.RLock()),lambda:1792540800)
assert service.run_once()
