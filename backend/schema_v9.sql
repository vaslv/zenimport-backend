-- Archive snapshots/recovery are private runtime controls, not peer receipts.
CREATE TABLE archive_records (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('backup','recovery')),
 installation_id TEXT NOT NULL, initial_epoch INTEGER NOT NULL CHECK(initial_epoch BETWEEN 1 AND 9223372036854775805),
 phase TEXT NOT NULL CHECK(phase IN ('building','ready','uploading','validating','preview','activated','blocked','cancelled','cleaned')),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 dataset_id TEXT NOT NULL, created_at INTEGER NOT NULL CHECK(created_at>=0),
 archive_sha256 TEXT CHECK(archive_sha256 IS NULL OR length(archive_sha256)=64),
 archive_size INTEGER CHECK(archive_size IS NULL OR archive_size BETWEEN 1 AND 67108864),
 manifest_json TEXT CHECK(manifest_json IS NULL OR json_valid(manifest_json)),
 preview_json TEXT CHECK(preview_json IS NULL OR json_valid(preview_json)),
 acknowledged_at INTEGER, error_code TEXT);
CREATE TRIGGER archive_binding_immutable BEFORE UPDATE ON archive_records
 WHEN NEW.id<>OLD.id OR NEW.kind<>OLD.kind OR NEW.installation_id<>OLD.installation_id OR NEW.initial_epoch<>OLD.initial_epoch
 OR NEW.request_json<>OLD.request_json OR NEW.dataset_id<>OLD.dataset_id OR NEW.created_at<>OLD.created_at
 OR (OLD.archive_sha256 IS NOT NULL AND (NEW.archive_sha256 IS NOT OLD.archive_sha256 OR NEW.archive_size IS NOT OLD.archive_size))
 OR (OLD.manifest_json IS NOT NULL AND NEW.manifest_json IS NOT OLD.manifest_json)
 OR (OLD.acknowledged_at IS NOT NULL AND NEW.acknowledged_at IS NOT OLD.acknowledged_at)
 OR (OLD.phase IN ('activated','cleaned') AND NEW.phase<>OLD.phase)
 BEGIN SELECT RAISE(ABORT,'Archive identity and terminal outcome are permanent'); END;
CREATE TRIGGER archive_record_preserved BEFORE DELETE ON archive_records
 BEGIN SELECT RAISE(ABORT,'Archive outcome is permanent'); END;
CREATE TABLE archive_chunks (
 id TEXT NOT NULL REFERENCES archive_records(id), number INTEGER NOT NULL CHECK(number BETWEEN 0 AND 255),
 data BLOB NOT NULL CHECK(length(data) BETWEEN 1 AND 262144),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64), PRIMARY KEY(id,number));
CREATE TRIGGER archive_chunk_immutable BEFORE UPDATE ON archive_chunks
 BEGIN SELECT RAISE(ABORT,'Archive chunk is immutable'); END;
