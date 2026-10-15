"""Disposable subprocess crash points; remote result is a synthetic file."""
import os
from pathlib import Path

import httpx
from zenmoney_api import Client
from zenmoney_api.protocol import decode_json, encode_json

from backend import delivery, storage
from backend.mirror import Mirror
from capture_factory import TARGET, NOW
from mirror_factory import snapshot


def crash_writer(path, remote_file, when):
    def handle(request):
        body = decode_json(request.content)
        if 'transaction' in body:
            Path(remote_file).write_bytes(encode_json(body['transaction']))
            os._exit(23)  # API applied; process never received its response.
        return httpx.Response(200, content=encode_json(snapshot(cursor=1000,
            account=[{'id': TARGET, 'user': 1, 'instrument': 2, 'type': 'checking'}])))
    client = Client('fixture-sdk-token', transport=httpx.MockTransport(handle), clock=lambda: NOW)
    mirror = Mirror(path, client, 'fixture-sdk-token', lambda: NOW, sleep=lambda _: None)
    if when == 'before':
        mirror.refresh(force=True)
        with storage.transaction(path) as db:
            delivery.intent(db, mirror, NOW)
        os._exit(22)
    delivery.DeliveryWorker(path, mirror, lambda: NOW).run_once()
