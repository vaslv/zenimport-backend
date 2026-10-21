"""Resumable private migration work; no financial API write is available here."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import logging
import shutil
from tempfile import TemporaryDirectory
import threading
from uuid import UUID, uuid5

from zenmoney_api import HTTPError, ResponseError, TransportError
from zenmoney_api.protocol import FORCE_FETCH

from . import __version__, mirror as mirror_module, storage
from .capture_policy import SyncError
from .migration import owners
from . import migration_participant as participant, portable_codec as codec, portable_graph as graph, portable_install


class PortableMigration:
    def __init__(self,path,gate,mirror,clock):
        self.path,self.gate,self.mirror,self.clock=path,gate,mirror,clock
        self.stopping,self.signal=threading.Event(),threading.Event()
        self.thread=None

    def start(self):
        self.thread=threading.Thread(target=self.loop,name='portable-migration',daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set();self.signal.set()
        if self.thread:self.thread.join()

    def loop(self):
        while not self.stopping.is_set():
            self.signal.clear()
            try:self.run_once()
            except Exception:
                # Unexpected failure leaves the persisted running task recoverable.
                # Never include SDK responses, tokens or financial rows in logs.
                logging.getLogger(__name__).error('Migration work interrupted; durable state retained')
            self.signal.wait(2)

    def capabilities(self):
        with storage.transaction(self.path,read_only=True) as db:
            current=participant.status(db)
            from .migration import started,meta
            legacy=meta(db,'migration_hold')
            eligible=not started(db) and not meta(db,'migration_import_sha256') and not legacy and current['state']=='active'
            eligible=eligible and not participant.installation(db)['ever_activated'] and not current['awaiting_sync']
            dataset=participant.uuid(meta(db,'instance_id'))
            return dict(dataset_id=dataset,protocol_version='2',archive_version='2',financial_schema_version='1',
                chunk_bytes='262144',max_archive_bytes=str(participant.LIMIT),can_import=bool(eligible),legacy_hold=legacy,**current)

    def identity(self):
        # A fresh read verifies the configured credential, without publishing a
        # cursor or unpausing an imported/shared financial dataset.
        with self.gate.exclusive(), self.mirror.lock:
            try:
                with TemporaryDirectory(prefix='.portable-identity-',dir=self.path.parent) as directory:
                    current=self._read_identity(Path(directory))
            except (TransportError, HTTPError, ResponseError, ValueError, sqlite3.Error):
                raise SyncError('zenmoney_unavailable',503,retryable=True) from None
            return dict(self.capabilities(),owner_ids=list(current),checked_at=str(self.clock()))

    def quiesce(self,request):
        # Explicit mode-switch preparation stops one active bank capture only.
        # The final source_start gate still rechecks absence of all active jobs.
        from . import capture
        from .migration import meta
        participant.require(type(request) is dict and set(request)=={'installation_id','expected_epoch'})
        expected=participant.uuid(request['installation_id']);epoch=participant.integer(request['expected_epoch'])
        with self.gate.exclusive(),self.mirror.lock,storage.transaction(self.path) as db:
            current=participant.installation(db)
            participant.require(current['installation_id']==expected and current['participant_epoch']==epoch,'migration_busy')
            participant.require(current['state']=='active' and current['operation_id'] is None and not meta(db,'migration_hold'),'migration_paused')
            job=db.execute("SELECT id FROM capture_jobs WHERE capture_state IN ('planned','collecting','paused') LIMIT 1").fetchone()
            if job:capture.stop(db,job['id'],self.clock())
            return dict(installation_id=expected,participant_epoch=str(epoch),ready=job is None)

    def status(self,switch):
        with storage.transaction(self.path,read_only=True) as db:
            result=participant.status(db,switch)
            from .migration import meta
            result['legacy_hold']=meta(db,'migration_hold')
            result['work']=[self._ticket(row) for row in db.execute('SELECT * FROM migration_work WHERE switch_id=? ORDER BY created_at,id',(switch,))]
            return result

    @staticmethod
    def _ticket(row):
        return dict(id=row['id'],switch_id=row['switch_id'],kind=row['kind'],state=row['state'],error_code=row['error_code'])

    def _enqueue(self,db,kind,request):
        request_id=participant.uuid(request['request_id'])
        switch=participant.uuid(request['switch_id'])
        raw=participant.canonical(request).decode();checksum=sha256(raw.encode()).hexdigest()
        previous=db.execute('SELECT * FROM migration_work WHERE id=?',(request_id,)).fetchone()
        if previous:
            participant.require(previous['request_digest']==checksum and previous['kind']==kind)
            return self._ticket(previous)
        participant.require(not db.execute('SELECT 1 FROM migration_receipts WHERE request_id=?',(request_id,)).fetchone())
        participant.require(not db.execute('SELECT 1 FROM migration_work WHERE switch_id=? AND kind=?',(switch,kind)).fetchone(),'migration_busy')
        now=self.clock()
        db.execute("INSERT INTO migration_work VALUES (?,?,?,?,?,'queued',NULL,?,?)",(request_id,switch,kind,raw,checksum,now,now))
        return self._ticket(db.execute('SELECT * FROM migration_work WHERE id=?',(request_id,)).fetchone())

    def command(self,command,request):
        participant.require(command in participant.FIELDS and command!='source_seal')
        participant.require(type(request) is dict and set(request)==participant.COMMON|participant.FIELDS[command])
        participant.uuid(request['request_id']);participant.uuid(request['switch_id'])
        with self.gate.exclusive(),self.mirror.lock,storage.transaction(self.path) as db:
            if command=='destination_activate':
                participant.require(type(request) is dict and set(request)==participant.COMMON|{'peer_receipt'})
                previous=db.execute('SELECT receipt_json FROM migration_receipts WHERE request_id=?',(participant.uuid(request['request_id']),)).fetchone()
                if previous:
                    # Reuse the participant's exact permanent-request comparison.
                    result=participant.transition(db,command,request,self.clock())
                    return dict(receipt=result)
                current=participant.installation(db)
                operation=db.execute('SELECT * FROM migration_operations WHERE switch_id=?',(participant.uuid(request['switch_id']),)).fetchone()
                participant.require(operation is not None and current['operation_id']==request['switch_id'] and current['state']=='armed','migration_paused')
                participant.require(current['participant_epoch']==participant.integer(request['expected_epoch']),'migration_busy')
                peer=participant.verify_receipt(request['peer_receipt'],command='source_handoff',current=current,operation=operation)
                participant.require(peer['peer_epoch']==str(current['participant_epoch']),'migration_paused')
                participant._eligible(db,current,state='armed')
                result=dict(work=self._enqueue(db,'activate',request))
            else:
                if command=='source_start' and not db.execute('SELECT 1 FROM migration_receipts WHERE request_id=?',(request['request_id'],)).fetchone():
                    from .delivery import recover
                    participant.require(owners(db),'zenmoney_unavailable')
                    participant.require(not db.execute("SELECT 1 FROM capture_jobs WHERE capture_state IN ('planned','collecting','paused')").fetchone(),'migration_incomplete')
                    # The exclusive writer file lock proves no old send is still
                    # running. Preserve a crashed send as uncertain, never as new.
                    recover(db,self.clock())
                receipt=participant.transition(db,command,request,self.clock())
                result=dict(receipt=receipt)
                if command=='source_start':
                    task_request=dict(request_id=str(uuid5(UUID(request['switch_id']),'snapshot-work-v2')),switch_id=request['switch_id'])
                    result['work']=self._enqueue(db,'snapshot',task_request)
        self.signal.set()
        return result

    def chunk(self,switch,expected_epoch,number,data,checksum):
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            return participant.put_chunk(db,switch,expected_epoch=expected_epoch,number=number,data=data,sha256_digest=checksum)

    def download(self,switch,number):
        participant.uuid(switch)
        participant.require(type(number) is int and 0<=number<=255)
        with storage.transaction(self.path,read_only=True) as db:
            operation=db.execute('SELECT * FROM migration_operations WHERE switch_id=?',(switch,)).fetchone()
            participant.require(operation is not None and operation['role']=='source' and operation['dataset_digest'] is not None,'migration_incomplete')
            row=db.execute('SELECT data,sha256 FROM migration_chunks WHERE switch_id=? AND number=?',(switch,number)).fetchone()
            participant.require(row is not None,'not_found')
            participant.require(sha256(row['data']).hexdigest()==row['sha256'])
            return row['data'],row['sha256']

    def finalize(self,request):
        participant.require(type(request) is dict and set(request)==participant.COMMON)
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            prior=db.execute('SELECT * FROM migration_work WHERE id=?',(participant.uuid(request['request_id']),)).fetchone()
            if prior:return self._enqueue(db,'validate',request)
            current=participant.installation(db)
            operation=db.execute('SELECT * FROM migration_operations WHERE switch_id=?',(participant.uuid(request['switch_id']),)).fetchone()
            participant.require(current['participant_epoch']==participant.integer(request['expected_epoch']),'migration_busy')
            participant.require(operation is not None and current['operation_id']==request['switch_id'] and operation['state']=='import_hold','migration_paused')
            participant.require(participant.archive_details(db,request['switch_id'])==(operation['archive_sha256'],operation['archive_size']),'migration_incomplete')
            result=self._enqueue(db,'validate',request)
        self.signal.set()
        return result

    def retry(self,task_id):
        participant.uuid(task_id)
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            task=db.execute('SELECT * FROM migration_work WHERE id=?',(task_id,)).fetchone()
            participant.require(task is not None,'not_found')
            if task['state']=='blocked':
                current=participant.installation(db)
                participant.require(current['operation_id']==task['switch_id'],'migration_paused')
                db.execute("UPDATE migration_work SET state='queued',error_code=NULL,updated_at=max(updated_at,?) WHERE id=?",(self.clock(),task_id))
            result=self._ticket(db.execute('SELECT * FROM migration_work WHERE id=?',(task_id,)).fetchone())
        self.signal.set()
        return result

    def run_once(self):
        with storage.transaction(self.path,read_only=True) as db:
            pending=db.execute("SELECT id FROM migration_work WHERE state IN ('queued','running') ORDER BY created_at,id LIMIT 1").fetchone()
        if not pending:return False
        try:
            with self.gate.exclusive(),self.mirror.lock:
                with storage.transaction(self.path,read_only=True) as db:
                    identity=participant.uuid(participant.installation(db)['installation_id'])
                # Only private scratch belonging to this installation. Chunks,
                # receipts and task authority live in SQLite, never in these paths.
                for orphan in self.path.parent.glob('.portable-work-'+identity+'-*'):
                    if orphan.is_symlink():orphan.unlink()
                    elif orphan.is_dir():shutil.rmtree(orphan)
                with storage.transaction(self.path) as db:
                    task=dict(db.execute('SELECT * FROM migration_work WHERE id=?',(pending[0],)).fetchone())
                    if task['state'] not in ('queued','running'):return False
                    db.execute("UPDATE migration_work SET state='running',updated_at=max(updated_at,?) WHERE id=?",(self.clock(),task['id']))
                try:
                    self._perform(task)
                except Exception as exc:
                    code = exc.code if isinstance(exc,SyncError) else 'zenmoney_unavailable' if isinstance(exc,(HTTPError,ResponseError,TransportError)) else 'migration_invalid'
                    with storage.transaction(self.path) as db:
                        committed=task['kind']=='activate' and db.execute('SELECT 1 FROM migration_receipts WHERE request_id=?',(task['id'],)).fetchone()
                        db.execute('UPDATE migration_work SET state=?,error_code=?,updated_at=max(updated_at,?) WHERE id=?',
                            ('complete' if committed else 'blocked',None if committed else code,self.clock(),task['id']))
                else:
                    with storage.transaction(self.path) as db:
                        db.execute("UPDATE migration_work SET state='complete',error_code=NULL,updated_at=max(updated_at,?) WHERE id=?",(self.clock(),task['id']))
                return True
        except SyncError as exc:
            if exc.code!='migration_busy':raise
            return False

    def _operation(self,switch):
        with storage.transaction(self.path,read_only=True) as db:
            control=participant.installation(db)
            row=db.execute('SELECT * FROM migration_operations WHERE switch_id=?',(switch,)).fetchone()
            participant.require(row is not None and control['operation_id']==switch,'migration_paused')
            return dict(control),dict(row)

    def _perform(self,task):
        switch=task['switch_id'];request=json.loads(task['request_json'])
        with storage.transaction(self.path,read_only=True) as db:
            operation=db.execute('SELECT * FROM migration_operations WHERE switch_id=?',(switch,)).fetchone()
            if task['kind']=='snapshot' and operation is not None and operation['dataset_digest'] is not None:
                participant.require(db.execute("SELECT 1 FROM migration_receipts WHERE switch_id=? AND command='source_seal'",(switch,)).fetchone())
                return
            if task['kind']=='validate' and operation is not None and operation['validated']:
                return
        if task['kind']=='activate':
            with storage.transaction(self.path,read_only=True) as db:
                if db.execute('SELECT 1 FROM migration_receipts WHERE request_id=?',(task['id'],)).fetchone():
                    participant.transition(db,'destination_activate',request,self.clock())
                    return
        control,operation=self._operation(switch)
        with TemporaryDirectory(prefix='.portable-work-'+control['installation_id']+'-',dir=self.path.parent) as directory:
            root=Path(directory)
            if task['kind']=='snapshot':
                participant.require(operation['state']=='export_hold','migration_paused')
                if operation['dataset_digest'] is not None:return
                archive=root/'snapshot.zip'
                with storage.transaction(self.path,read_only=True) as db:
                    values,_=graph.source_tables(db,operation['dataset_id'])
                    manifest=codec.create_archive(archive,values,dataset_id=operation['dataset_id'],owners=owners(db),
                        session_id=switch,archive_id=str(uuid5(UUID(switch),'archive-v2')),
                        created_at=datetime.fromtimestamp(operation['created_at'],timezone.utc).isoformat().replace('+00:00','Z'),version=__version__)
                stage=codec.read_archive(archive,root/'checked')
                graph.validate_stage(stage)
                # Unsealed partial source chunks were never downloadable or
                # externally acknowledged. Rebuild them after an interrupted task.
                with storage.transaction(self.path) as db:db.execute('DELETE FROM migration_chunks WHERE switch_id=?',(switch,))
                checksum=sha256();size=0
                with archive.open('rb') as stream:
                    number=0
                    while data:=stream.read(262144):
                        checksum.update(data);size+=len(data)
                        with storage.transaction(self.path) as db:
                            participant.put_chunk(db,switch,expected_epoch=str(control['participant_epoch']),number=number,data=data,sha256_digest=sha256(data).hexdigest())
                        number+=1
                seal=dict(request_id=str(uuid5(UUID(switch),'source-seal-v2')),switch_id=switch,expected_epoch=str(control['participant_epoch']),
                    dataset_digest=manifest['dataset_digest'],archive_sha256=checksum.hexdigest(),archive_size=str(size))
                with storage.transaction(self.path) as db:participant.transition(db,'source_seal',seal,self.clock())
                return
            participant.require(operation['state']==('armed' if task['kind']=='activate' else 'import_hold'),'migration_paused')
            archive=root/'incoming.zip';storage.create_private(archive)
            with storage.transaction(self.path,read_only=True) as db,archive.open('wb') as stream:
                participant.require(participant.archive_details(db,switch)==(operation['archive_sha256'],operation['archive_size']))
                for row in db.execute('SELECT data FROM migration_chunks WHERE switch_id=? ORDER BY number',(switch,)):stream.write(row[0])
            stage=codec.read_archive(archive,root/'checked')
            participant.require(stage.manifest['export_session_id']==switch)
            participant.require(stage.manifest['dataset_digest']==operation['dataset_digest'] and stage.manifest['dataset_id']==operation['dataset_id'])
            summary=graph.validate_stage(stage)
            self._fresh_owner(root,summary.declared_owners)
            if task['kind']=='validate':
                with storage.transaction(self.path) as db:
                    participant.require(participant.installation(db)['participant_epoch']==participant.integer(request['expected_epoch']),'migration_busy')
                    participant.set_validated(db,switch,dataset_digest=summary.dataset_digest)
            else:
                with storage.transaction(self.path) as db:
                    participant.transition(db,'destination_activate',request,self.clock(),
                        install=lambda db,op:portable_install.install(db,op,stage,source_hash=self.mirror.source_hash))

    def _fresh_owner(self,root,expected):
        participant.require(tuple(self._read_identity(root))==tuple(expected),'migration_wrong_account')

    def _read_identity(self,root):
        participant.require(self.mirror.client is not None,'zenmoney_unavailable')
        diff=self.mirror.client.read_diff(0,force_fetch=FORCE_FETCH)
        probe=root/'identity.sqlite3';storage.initialize(probe)
        with storage.transaction(probe) as db:
            db.execute('UPDATE zen_mirror_state SET source_hash=? WHERE id=1',(self.mirror.source_hash,))
            mirror_module.apply_diff(db,diff,expected_generation=0,source_hash=self.mirror.source_hash,
                requested_cursor=0,full=True,now=self.clock(),origin='migration-identity-check')
            result=tuple(owners(db))
            participant.require(bool(result),'zenmoney_unavailable')
            return result
