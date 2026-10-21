"""Archive-only backup controls. Never create peer receipts or SDK mutations."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from uuid import UUID, uuid5

from . import __version__, storage, migration_participant as participant
from . import portable_codec as codec, portable_graph as graph
from .capture_policy import SyncError
from .migration import owners,meta
from .archive_recovery import ArchiveRecovery
from zenmoney_api import HTTPError,ResponseError,TransportError

CHUNK=262144


class ArchiveService(ArchiveRecovery):
    def __init__(self,path,gate,mirror,clock):
        self.path,self.gate,self.mirror,self.clock=path,gate,mirror,clock
        self.stop_event,self.signal=threading.Event(),threading.Event()
        self.thread=None

    def start(self):
        self.thread=threading.Thread(target=self.loop,name='archive-backups',daemon=True);self.thread.start()

    def stop(self):
        self.stop_event.set();self.signal.set()
        if self.thread:self.thread.join()

    def loop(self):
        while not self.stop_event.is_set():
            self.signal.clear()
            try:self.run_once()
            except Exception:logging.getLogger(__name__).error('Archive work interrupted; durable state retained')
            self.signal.wait(2)

    @staticmethod
    def _record(db,id):
        participant.uuid(id)
        row=db.execute('SELECT * FROM archive_records WHERE id=?',(id,)).fetchone()
        participant.require(row is not None,'not_found')
        participant.require(row['installation_id']==participant.installation(db)['installation_id'])
        return row

    @staticmethod
    def _held(db,row):
        current=participant.installation(db)
        participant.require(current['installation_id']==row['installation_id'] and current['operation_id']==row['id'] and
            current['participant_epoch']==row['initial_epoch']+1 and
            ((row['kind']=='backup' and current['state']=='export_hold' and row['phase'] in ('building','blocked')) or
             (row['kind']=='recovery' and current['state']=='import_hold' and row['phase'] in ('uploading','validating','preview','blocked'))),'migration_paused')
        if row['kind']=='backup':participant.require(meta(db,'instance_id')==row['dataset_id'],'migration_invalid')
        return current

    @staticmethod
    def _cache_pending(db):
        return db.execute("""SELECT 1 FROM archive_records a WHERE phase<>'cleaned' AND
            (phase<>'activated' OR acknowledged_at IS NULL OR EXISTS(SELECT 1 FROM archive_chunks c WHERE c.id=a.id)) LIMIT 1""").fetchone()

    @staticmethod
    def _view(row):
        manifest=json.loads(row['manifest_json']) if row['manifest_json'] else None
        return dict(id=row['id'],kind=row['kind'],phase=row['phase'],installation_id=row['installation_id'],
            initial_epoch=str(row['initial_epoch']),dataset_id=row['dataset_id'],created_at=str(row['created_at']),
            archive_sha256=row['archive_sha256'],archive_size=str(row['archive_size']) if row['archive_size'] is not None else None,
            chunks=str((row['archive_size']+CHUNK-1)//CHUNK) if row['archive_size'] is not None else None,
            dataset_digest=manifest['dataset_digest'] if manifest else None,
            acknowledged_at=str(row['acknowledged_at']) if row['acknowledged_at'] is not None else None,error_code=row['error_code'])

    def status(self,id):
        with storage.transaction(self.path,read_only=True) as db:return self._view(self._record(db,id))

    def begin_backup(self,request):
        participant.require(type(request) is dict and set(request)=={'id','installation_id','expected_epoch'})
        id=participant.uuid(request['id']);installation=participant.uuid(request['installation_id']);epoch=participant.integer(request['expected_epoch'],maximum=2**63-3)
        raw=participant.canonical(request).decode()
        with self.gate.exclusive(),self.mirror.lock,storage.transaction(self.path) as db:
            previous=db.execute('SELECT * FROM archive_records WHERE id=?',(id,)).fetchone()
            if previous:
                participant.require(previous['kind']=='backup' and previous['request_json']==raw)
                return self._view(self._record(db,id))
            current=participant.installation(db)
            participant.require(current['installation_id']==installation and current['participant_epoch']==epoch,'migration_busy')
            participant.require(current['state']=='active' and not current['awaiting_sync'] and not meta(db,'migration_hold'),'migration_paused')
            participant.require(not self._cache_pending(db),'migration_busy')
            participant.require(not db.execute('SELECT 1 FROM migration_operations WHERE switch_id=?',(id,)).fetchone())
            participant.require(not db.execute("SELECT 1 FROM capture_jobs WHERE capture_state IN ('planned','collecting','paused') LIMIT 1").fetchone(),'migration_incomplete')
            participant.require(owners(db),'zenmoney_unavailable')
            from .delivery import recover
            recover(db,self.clock())
            dataset=participant.uuid(meta(db,'instance_id'));now=self.clock()
            participant.require(type(now) is int and 0<=now<=253402300799)
            db.execute("INSERT INTO archive_records(id,kind,installation_id,initial_epoch,phase,request_json,dataset_id,created_at) VALUES (?,'backup',?,?,'building',?,?,?)",(id,installation,epoch,raw,dataset,now))
            db.execute("UPDATE migration_installation SET state='export_hold',operation_id=?,participant_epoch=participant_epoch+1 WHERE id=1",(id,))
            result=self._view(self._record(db,id))
        self.signal.set();return result

    def _backup(self,id):
        with storage.transaction(self.path,read_only=True) as db:
            row=dict(self._record(db,id));self._held(db,row)
        with TemporaryDirectory(prefix='.archive-work-'+row['installation_id']+'-',dir=self.path.parent) as directory:
            root=Path(directory);archive=root/'backup.zip'
            with storage.transaction(self.path,read_only=True) as db:
                self._held(db,row);values,_=graph.source_tables(db,row['dataset_id'])
                manifest=codec.create_archive(archive,values,dataset_id=row['dataset_id'],owners=owners(db),
                    session_id=id,archive_id=str(uuid5(UUID(id),'backup-archive-v2')),
                    created_at=datetime.fromtimestamp(row['created_at'],timezone.utc).isoformat().replace('+00:00','Z'),version=__version__)
            stage=codec.read_archive(archive,root/'checked');graph.validate_stage(stage)
            # Partial unsealed chunks were never downloadable. Rebuild only those
            # belonging to this still-held backup after a process interruption.
            with storage.transaction(self.path) as db:
                self._held(db,self._record(db,id));db.execute('DELETE FROM archive_chunks WHERE id=?',(id,))
            checksum=sha256();size=0;number=0
            with archive.open('rb') as stream:
                while data:=stream.read(CHUNK):
                    participant.require(number<256)
                    checksum.update(data);size+=len(data)
                    with storage.transaction(self.path) as db:
                        self._held(db,self._record(db,id))
                        db.execute('INSERT INTO archive_chunks VALUES (?,?,?,?)',(id,number,data,sha256(data).hexdigest()))
                    number+=1
            with storage.transaction(self.path) as db:
                current=self._record(db,id);self._held(db,current)
                # Check durable bytes again before publication/hold release.
                actual=sha256();actual_size=0
                for i,part in enumerate(db.execute('SELECT * FROM archive_chunks WHERE id=? ORDER BY number',(id,))):
                    participant.require(i==part['number'] and sha256(part['data']).hexdigest()==part['sha256'])
                    actual.update(part['data']);actual_size+=len(part['data'])
                participant.require(actual_size==size and actual.hexdigest()==checksum.hexdigest())
                db.execute("UPDATE archive_records SET phase='ready',archive_sha256=?,archive_size=?,manifest_json=?,error_code=NULL WHERE id=?",
                    (checksum.hexdigest(),size,codec.canonical(manifest).decode(),id))
                db.execute("UPDATE migration_installation SET state='active',operation_id=NULL,participant_epoch=participant_epoch+1 WHERE id=1")

    def run_once(self):
        with storage.transaction(self.path,read_only=True) as db:
            installation=participant.uuid(participant.installation(db)['installation_id'])
            row=db.execute("SELECT id FROM archive_records WHERE (kind='backup' AND phase='building') OR (kind='recovery' AND phase='validating') ORDER BY created_at,id LIMIT 1").fetchone()
        prefix='.archive-work-'+installation+'-'
        if row is None and not any(self.path.parent.glob(prefix+'*')):return False
        try:
            with self.gate.exclusive(),self.mirror.lock:
                # The cross-process writer lock proves no live archive builder
                # owns these scratch paths. Never follow a stale symlink.
                for orphan in self.path.parent.glob(prefix+'*'):
                    if orphan.is_symlink():orphan.unlink()
                    elif orphan.is_dir():shutil.rmtree(orphan)
                    else:orphan.unlink()
                if row is None:return False
                # A different process may have completed/cancelled before this lock.
                with storage.transaction(self.path,read_only=True) as db:
                    value=self._record(db,row['id'])
                    if value['phase'] not in ('building','validating'):return False
                    kind=value['kind']
                try:
                    if kind=='backup':self._backup(row['id'])
                    else:self._preview(row['id'])
                except Exception as exc:
                    code=exc.code if isinstance(exc,SyncError) else 'zenmoney_unavailable' if isinstance(exc,(HTTPError,ResponseError,TransportError)) else 'migration_invalid'
                    with storage.transaction(self.path) as db:
                        value=self._record(db,row['id']);self._held(db,value)
                        db.execute("UPDATE archive_records SET phase='blocked',error_code=? WHERE id=?",(code,row['id']))
                return True
        except SyncError as exc:
            if exc.code!='migration_busy':raise
            return False

    def retry(self,id):
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            row=self._record(db,id)
            if row['phase']=='blocked':
                self._held(db,row);db.execute("UPDATE archive_records SET phase=?,error_code=NULL WHERE id=?",('building' if row['kind']=='backup' else 'validating',id))
            result=self._view(self._record(db,id))
        self.signal.set();return result

    def cancel(self,id):
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            row=self._record(db,id)
            if row['phase'] in ('cancelled','cleaned'):return self._view(row)
            self._held(db,row)
            db.execute("UPDATE archive_records SET phase='cancelled',error_code=NULL WHERE id=?",(id,))
            db.execute("UPDATE migration_installation SET state='active',operation_id=NULL,participant_epoch=participant_epoch+1 WHERE id=1")
            return self._view(self._record(db,id))

    def download(self,id,number):
        participant.require(type(number) is int and 0<=number<256)
        with storage.transaction(self.path,read_only=True) as db:
            row=self._record(db,id);participant.require(row['phase']=='ready' and number<(row['archive_size']+CHUNK-1)//CHUNK,'migration_incomplete')
            part=db.execute('SELECT data,sha256 FROM archive_chunks WHERE id=? AND number=?',(id,number)).fetchone()
            participant.require(part is not None and sha256(part['data']).hexdigest()==part['sha256'],'migration_incomplete')
            return part['data'],part['sha256']

    def acknowledge(self,id,checksum):
        participant.digest(checksum)
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            row=self._record(db,id);participant.require(row['phase'] in ('ready','activated') and row['archive_sha256']==checksum,'migration_incomplete')
            now=self.clock();participant.require(type(now) is int and row['created_at']<=now<=253402300799)
            if row['acknowledged_at'] is None:db.execute('UPDATE archive_records SET acknowledged_at=? WHERE id=?',(now,id))
            return self._view(self._record(db,id))

    def clean(self,id):
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            row=self._record(db,id)
            participant.require(row['phase'] in ('cancelled','cleaned') or row['phase'] in ('ready','activated') and row['acknowledged_at'] is not None,'migration_paused')
            part=db.execute('SELECT number FROM archive_chunks WHERE id=? ORDER BY number LIMIT 1',(id,)).fetchone()
            if part:
                db.execute('DELETE FROM archive_chunks WHERE id=? AND number=?',(id,part[0]));return dict(complete=False)
            # Activation is permanent authority evidence, not a cache phase.
            if row['phase']!='activated':db.execute("UPDATE archive_records SET phase='cleaned' WHERE id=?",(id,))
            return dict(complete=True)
