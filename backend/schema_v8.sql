-- Installation authority is local runtime state, never portable financial data.
CREATE TABLE migration_installation (
 id INTEGER PRIMARY KEY CHECK(id=1), installation_id TEXT NOT NULL UNIQUE,
 participant_epoch INTEGER NOT NULL CHECK(participant_epoch>=1),
 state TEXT NOT NULL CHECK(state IN ('active','export_hold','import_hold','armed','cancel_pending','handed_off')),
 operation_id TEXT, awaiting_sync INTEGER NOT NULL DEFAULT 0 CHECK(awaiting_sync IN (0,1)),
 ever_activated INTEGER NOT NULL DEFAULT 0 CHECK(ever_activated IN (0,1)),
 CHECK((state='active' AND operation_id IS NULL) OR (state<>'active' AND operation_id IS NOT NULL)));
CREATE TRIGGER installation_identity_immutable BEFORE UPDATE ON migration_installation
 WHEN NEW.installation_id<>OLD.installation_id OR NEW.participant_epoch<OLD.participant_epoch OR NEW.ever_activated<OLD.ever_activated
 BEGIN SELECT RAISE(ABORT,'Installation authority cannot move backwards'); END;
CREATE TRIGGER installation_preserved BEFORE DELETE ON migration_installation
 BEGIN SELECT RAISE(ABORT,'Installation identity is permanent'); END;
CREATE TRIGGER installation_fenced BEFORE UPDATE ON migration_installation
 WHEN (NEW.state<>OLD.state OR NEW.operation_id IS NOT OLD.operation_id OR NEW.ever_activated<>OLD.ever_activated)
   AND NEW.participant_epoch<=OLD.participant_epoch
 BEGIN SELECT RAISE(ABORT,'Installation transition must advance its fence'); END;
CREATE TABLE migration_operations (
 switch_id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('source','destination')),
 peer_id TEXT NOT NULL, dataset_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('export_hold','import_hold','armed','cancel_pending','handed_off','activated','discarded','resumed')),
 dataset_digest TEXT, archive_sha256 TEXT, archive_size INTEGER CHECK(archive_size BETWEEN 1 AND 67108864),
 peer_epoch INTEGER, validated INTEGER NOT NULL DEFAULT 0 CHECK(validated IN (0,1)),
 created_at INTEGER NOT NULL CHECK(created_at>=0), updated_at INTEGER NOT NULL CHECK(updated_at>=created_at),
 CHECK((dataset_digest IS NULL AND archive_sha256 IS NULL AND archive_size IS NULL) OR
       (dataset_digest IS NOT NULL AND archive_sha256 IS NOT NULL AND length(dataset_digest)=64 AND length(archive_sha256)=64 AND archive_size IS NOT NULL)));
CREATE TRIGGER migration_binding_immutable BEFORE UPDATE ON migration_operations
 WHEN NEW.switch_id<>OLD.switch_id OR NEW.role<>OLD.role OR NEW.peer_id<>OLD.peer_id OR NEW.dataset_id<>OLD.dataset_id
   OR (OLD.dataset_digest IS NOT NULL AND (NEW.dataset_digest IS NOT OLD.dataset_digest OR NEW.archive_sha256 IS NOT OLD.archive_sha256 OR NEW.archive_size IS NOT OLD.archive_size))
   OR NEW.created_at<>OLD.created_at
 BEGIN SELECT RAISE(ABORT,'Migration binding is immutable'); END;
CREATE TRIGGER migration_operation_preserved BEFORE DELETE ON migration_operations
 BEGIN SELECT RAISE(ABORT,'Migration outcome is permanent'); END;
CREATE TRIGGER migration_state_machine BEFORE UPDATE ON migration_operations
 WHEN NEW.state<>OLD.state AND NOT (
   (OLD.role='source' AND OLD.state='export_hold' AND NEW.state IN ('cancel_pending','handed_off')) OR
   (OLD.role='source' AND OLD.state='cancel_pending' AND NEW.state='resumed') OR
   (OLD.role='destination' AND OLD.state='import_hold' AND NEW.state IN ('armed','discarded')) OR
   (OLD.role='destination' AND OLD.state='armed' AND NEW.state IN ('activated','discarded')))
 BEGIN SELECT RAISE(ABORT,'Migration decision cannot move backwards'); END;
CREATE TABLE migration_receipts (
 request_id TEXT PRIMARY KEY, switch_id TEXT NOT NULL REFERENCES migration_operations(switch_id),
 command TEXT NOT NULL, request_digest TEXT NOT NULL CHECK(length(request_digest)=64),
 receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)), receipt_sha256 TEXT NOT NULL CHECK(length(receipt_sha256)=64));
CREATE TRIGGER migration_receipt_immutable BEFORE UPDATE ON migration_receipts
 BEGIN SELECT RAISE(ABORT,'Migration receipt is immutable'); END;
CREATE TRIGGER migration_receipt_preserved BEFORE DELETE ON migration_receipts
 BEGIN SELECT RAISE(ABORT,'Migration receipt is permanent'); END;
CREATE TABLE migration_chunks (
 switch_id TEXT NOT NULL REFERENCES migration_operations(switch_id),
 number INTEGER NOT NULL CHECK(number BETWEEN 0 AND 255),
 data BLOB NOT NULL CHECK(length(data) BETWEEN 1 AND 262144),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64), PRIMARY KEY(switch_id,number));
CREATE TRIGGER migration_chunk_immutable BEFORE UPDATE ON migration_chunks
 BEGIN SELECT RAISE(ABORT,'Acknowledged migration chunk is immutable'); END;
-- Original runtime migration audit, distinct from the destination's own schema.
CREATE TABLE portable_schema_audit (
 version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL, backup_path TEXT CHECK(backup_path IS NULL));
CREATE TABLE migration_work (
 id TEXT PRIMARY KEY, switch_id TEXT NOT NULL REFERENCES migration_operations(switch_id),
 kind TEXT NOT NULL CHECK(kind IN ('snapshot','validate','activate')),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)), request_digest TEXT NOT NULL CHECK(length(request_digest)=64),
 state TEXT NOT NULL CHECK(state IN ('queued','running','blocked','complete')), error_code TEXT,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL CHECK(updated_at>=created_at), UNIQUE(switch_id,kind));
CREATE TRIGGER migration_work_binding BEFORE UPDATE ON migration_work
 WHEN NEW.id<>OLD.id OR NEW.switch_id<>OLD.switch_id OR NEW.kind<>OLD.kind OR NEW.request_json<>OLD.request_json
   OR NEW.request_digest<>OLD.request_digest OR NEW.created_at<>OLD.created_at OR (OLD.state='complete' AND NEW.state<>'complete')
 BEGIN SELECT RAISE(ABORT,'Migration work identity and completed outcome are permanent'); END;
CREATE TRIGGER migration_work_preserved BEFORE DELETE ON migration_work
 BEGIN SELECT RAISE(ABORT,'Migration work outcome is permanent'); END;
