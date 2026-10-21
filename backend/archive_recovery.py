"""Explicit archive recovery, independent of live peer/handoff authority."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from zenmoney_api.protocol import FORCE_FETCH
from zenmoney_api import HTTPError,ResponseError,TransportError
from . import storage,mirror as mirror_module,portable_codec as codec,portable_graph as graph,portable_install
from . import migration_participant as participant,discrepancies
from .migration import owners,meta
from .capture_policy import SyncError

CHUNK=262144


class ArchiveRecovery:
    def _live(self,root):
        participant.require(self.mirror.client is not None,'zenmoney_unavailable')
        try:
            diff=self.mirror.client.read_diff(0,force_fetch=FORCE_FETCH)
            checked_at=self.clock()
            path=root/'live.sqlite3';storage.initialize(path)
            with storage.transaction(path) as db:
                db.execute('UPDATE zen_mirror_state SET source_hash=? WHERE id=1',(self.mirror.source_hash,))
                mirror_module.apply_diff(db,diff,expected_generation=0,source_hash=self.mirror.source_hash,
                    requested_cursor=0,full=True,now=self.clock(),origin='archive-recovery-preview')
                identity=owners(db)
                if not identity:raise SyncError('zenmoney_unavailable',503,retryable=True)
            return identity,diff,sha256(diff.raw_json.encode('utf-8')).hexdigest(),checked_at
        except (HTTPError,ResponseError,TransportError):
            raise SyncError('zenmoney_unavailable',503,retryable=True) from None

    def begin_recovery(self,request):
        fields={'id','installation_id','expected_epoch','dataset_id','archive_sha256','archive_size','owner_ids',
            'former_writers_retired','accept_post_snapshot_evidence_loss'}
        participant.require(type(request) is dict and set(request)==fields)
        id=participant.uuid(request['id']);installation=participant.uuid(request['installation_id']);dataset=participant.uuid(request['dataset_id'])
        epoch=participant.integer(request['expected_epoch'],maximum=2**63-3);participant.digest(request['archive_sha256'])
        size=participant.integer(request['archive_size'],maximum=67108864)
        participant.require(request['former_writers_retired'] is True and request['accept_post_snapshot_evidence_loss'] is True,'migration_paused')
        declared=request['owner_ids'];participant.require(type(declared) is list and 0<len(declared)<=1000 and
            all(type(v) is str and 0<len(v)<=240 for v in declared) and declared==sorted(set(declared)))
        raw=participant.canonical(request).decode()
        with self.gate.exclusive(),self.mirror.lock:
            with storage.transaction(self.path,read_only=True) as db:
                previous=db.execute('SELECT * FROM archive_records WHERE id=?',(id,)).fetchone()
                if previous:
                    participant.require(previous['kind']=='recovery' and previous['request_json']==raw)
                    return self._view(self._record(db,id))
                current=participant.installation(db)
                participant.require(current['installation_id']==installation and current['participant_epoch']==epoch,'migration_busy')
                participant._eligible(db,current)
                participant.require(not self._cache_pending(db),'migration_busy')
                participant.require(not db.execute('SELECT 1 FROM migration_operations WHERE switch_id=?',(id,)).fetchone())
            with TemporaryDirectory(prefix='.archive-work-'+installation+'-',dir=self.path.parent) as directory:
                actual,_,_,_=self._live(Path(directory));participant.require(actual==declared,'migration_wrong_account')
            with storage.transaction(self.path) as db:
                current=participant.installation(db)
                participant.require(current['installation_id']==installation and current['participant_epoch']==epoch,'migration_busy');participant._eligible(db,current)
                now=self.clock();participant.require(type(now) is int and 0<=now<=253402300799)
                db.execute("INSERT INTO archive_records(id,kind,installation_id,initial_epoch,phase,request_json,dataset_id,created_at,archive_sha256,archive_size) VALUES (?,'recovery',?,?,'uploading',?,?,?,?,?)",
                    (id,installation,epoch,raw,dataset,now,request['archive_sha256'],size))
                db.execute("UPDATE migration_installation SET state='import_hold',operation_id=?,participant_epoch=participant_epoch+1 WHERE id=1",(id,))
                return self._view(self._record(db,id))

    def upload(self,id,number,data,checksum):
        participant.digest(checksum);participant.require(type(number) is int and 0<=number<256 and type(data) is bytes and 0<len(data)<=CHUNK)
        participant.require(sha256(data).hexdigest()==checksum)
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            row=self._record(db,id);self._held(db,row)
            participant.require(row['kind']=='recovery' and row['phase']=='uploading','migration_paused')
            participant.require(number<(row['archive_size']+CHUNK-1)//CHUNK and len(data)==min(CHUNK,row['archive_size']-number*CHUNK))
            previous=db.execute('SELECT data,sha256 FROM archive_chunks WHERE id=? AND number=?',(id,number)).fetchone()
            if previous:participant.require(previous['sha256']==checksum and previous['data']==data)
            else:db.execute('INSERT INTO archive_chunks VALUES (?,?,?,?)',(id,number,data,checksum))
            return dict(number=str(number),bytes=str(len(data)),sha256=checksum)

    def _incoming(self,db,row,path):
        checksum=sha256();size=0;storage.create_private(path)
        with path.open('wb') as output:
            for i,part in enumerate(db.execute('SELECT * FROM archive_chunks WHERE id=? ORDER BY number',(row['id'],))):
                participant.require(i==part['number'] and len(part['data'])==min(CHUNK,row['archive_size']-size) and
                    sha256(part['data']).hexdigest()==part['sha256'],'migration_incomplete')
                size+=len(part['data']);checksum.update(part['data']);output.write(part['data'])
        participant.require(size==row['archive_size'] and checksum.hexdigest()==row['archive_sha256'],'migration_incomplete')

    def finalize_recovery(self,id):
        with self.gate.exclusive(),storage.transaction(self.path) as db:
            row=self._record(db,id);self._held(db,row);participant.require(row['kind']=='recovery','migration_paused')
            # Check completeness before queuing. A new preview is explicitly
            # requested again after expiry or a changed remote account.
            checksum=sha256();size=0
            for i,part in enumerate(db.execute('SELECT * FROM archive_chunks WHERE id=? ORDER BY number',(id,))):
                participant.require(i==part['number'] and len(part['data'])==min(CHUNK,row['archive_size']-size) and sha256(part['data']).hexdigest()==part['sha256'],'migration_incomplete')
                checksum.update(part['data']);size+=len(part['data'])
            participant.require(size==row['archive_size'] and checksum.hexdigest()==row['archive_sha256'],'migration_incomplete')
            db.execute("UPDATE archive_records SET phase='validating',error_code=NULL WHERE id=?",(id,))
            result=self._view(self._record(db,id))
        self.signal.set();return result

    def _stage(self,row,root):
        incoming=root/'incoming.zip'
        with storage.transaction(self.path,read_only=True) as db:
            self._held(db,self._record(db,row['id']));self._incoming(db,row,incoming)
        stage=codec.read_archive(incoming,root/'stage');manifest=stage.manifest
        participant.require(manifest['dataset_id']==row['dataset_id'] and manifest['zenmoney_identity']['owner_ids']==json.loads(row['request_json'])['owner_ids'],'migration_wrong_account')
        if row['manifest_json'] is not None:participant.require(row['manifest_json']==codec.canonical(manifest).decode())
        summary=graph.validate_stage(stage,expected_owners=tuple(manifest['zenmoney_identity']['owner_ids']))
        return stage,summary

    def _preview(self,id):
        with storage.transaction(self.path,read_only=True) as db:row=dict(self._record(db,id));self._held(db,row)
        with TemporaryDirectory(prefix='.archive-work-'+row['installation_id']+'-',dir=self.path.parent) as directory:
            root=Path(directory);stage,summary=self._stage(row,root)
            actual,diff,sdk_hash,checked_at=self._live(root);participant.require(tuple(actual)==summary.declared_owners,'migration_wrong_account')
            preview_path=root/'preview.sqlite3';storage.initialize(preview_path)
            missing,extra,unchecked=set(),set(),set();counts=dict(summary.counts)
            with storage.transaction(preview_path) as db:
                portable_install._copy_facts(db,stage,source_hash=self.mirror.source_hash,archive_sha256=row['archive_sha256'],accepted_at=row['created_at'])
                generation=db.execute('SELECT generation FROM zen_mirror_state WHERE id=1').fetchone()[0]
                mirror_module.apply_diff(db,diff,expected_generation=generation,source_hash=self.mirror.source_hash,
                    requested_cursor=0,full=True,now=self.clock(),origin='archive-recovery-preview')
                for connection in list(db.execute('SELECT id FROM connections')):
                    try:report=discrepancies.build(db,connection[0],self.clock())
                    except SyncError:
                        unchecked.update(a[0] for a in db.execute('SELECT account_id FROM connection_accounts WHERE connection_id=?',(connection[0],)))
                        continue
                    targets={a['id']:a['zenmoney_account_id'] for a in db.execute('SELECT id,zenmoney_account_id FROM bank_accounts')}
                    for account in report['accounts']:
                        if account['status']!='ready' or account['unchecked_days']:unchecked.add(account['id'])
                    for day in report['days']:
                        missing.update(item['id'] for item in day['bank_operations'])
                        extra.update((targets[day['account_id']],item['id']) for item in day['operations'])
                zen_count=db.execute("""SELECT count(*) FROM zen_entities e WHERE kind='transaction' AND deleted=0
                    AND NOT EXISTS(SELECT 1 FROM zen_tombstones t WHERE t.kind=e.kind AND t.entity_id=e.id)""").fetchone()[0]
            preview=dict(id=str(uuid4()),archive_sha256=row['archive_sha256'],sdk_sha256=sdk_hash,
                source_hash=self.mirror.source_hash,checked_at=str(checked_at),cursor=str(diff.server_timestamp),
                bank_transactions=str(counts['bank_transactions']),zen_transactions=str(zen_count),
                missing_bank=len(missing),extra_zen=len(extra),unchecked_accounts=len(unchecked))
            with storage.transaction(self.path) as db:
                self._held(db,self._record(db,id))
                db.execute("UPDATE archive_records SET phase='preview',manifest_json=?,preview_json=?,error_code=NULL WHERE id=?",
                    (codec.canonical(stage.manifest).decode(),json.dumps(preview,separators=(',',':')),id))

    def preview(self,id):
        with storage.transaction(self.path,read_only=True) as db:
            row=self._record(db,id);participant.require(row['kind']=='recovery' and row['phase'] in ('preview','activated') and row['preview_json'] is not None,'migration_incomplete')
            return json.loads(row['preview_json'])

    def _fresh_preview(self,preview):
        now=self.clock();participant.require(type(now) is int and int(preview['checked_at'])<=now<=int(preview['checked_at'])+300 and
            preview['source_hash']==self.mirror.source_hash,'migration_wrong_account')

    def activate_recovery(self,id,preview_id,accept_summary):
        participant.uuid(preview_id);participant.require(accept_summary is True,'migration_paused')
        with self.gate.exclusive(),self.mirror.lock:
            with storage.transaction(self.path,read_only=True) as db:
                row=dict(self._record(db,id));participant.require(row['kind']=='recovery')
                preview=json.loads(row['preview_json']) if row['preview_json'] else None
                participant.require(preview is not None and preview['id']==preview_id,'migration_incomplete')
                if row['phase']=='activated':return self._view(row)
                self._held(db,row);participant.require(row['phase']=='preview','migration_incomplete')
            self._fresh_preview(preview)
            with TemporaryDirectory(prefix='.archive-work-'+row['installation_id']+'-',dir=self.path.parent) as directory:
                root=Path(directory);stage,summary=self._stage(row,root)
                actual,_,_,_=self._live(root);participant.require(tuple(actual)==summary.declared_owners,'migration_wrong_account')
                with storage.transaction(self.path) as db:
                    current=self._record(db,id);self._held(db,current)
                    participant.require(current['preview_json']==row['preview_json'],'migration_busy')
                    self._fresh_preview(preview)
                    portable_install.install_recovery(db,current,stage,source_hash=self.mirror.source_hash,preview_id=preview_id)
                    # A large copy can itself cross the deadline. Roll back all
                    # imported facts and triggers before changing writer authority.
                    self._fresh_preview(preview)
                    db.execute("UPDATE migration_installation SET state='active',operation_id=NULL,participant_epoch=participant_epoch+1,awaiting_sync=1,ever_activated=1 WHERE id=1")
                    db.execute("UPDATE archive_records SET phase='activated',error_code=NULL WHERE id=?",(id,))
                    return self._view(self._record(db,id))
