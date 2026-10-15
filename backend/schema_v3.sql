CREATE TABLE connection_discovery (
 connection_id TEXT NOT NULL, account_id TEXT NOT NULL, present INTEGER NOT NULL CHECK(present IN (0,1)),
 supported INTEGER NOT NULL CHECK(supported IN (0,1)), unsupported_reason TEXT, label TEXT NOT NULL, masked_number TEXT NOT NULL,
 PRIMARY KEY(connection_id,account_id),
 FOREIGN KEY(connection_id,account_id) REFERENCES connection_accounts(connection_id,account_id));
INSERT INTO connection_discovery SELECT c.connection_id,c.account_id,1,a.supported,a.unsupported_reason,a.label,a.masked_number
 FROM connection_accounts c JOIN bank_accounts a ON a.id=c.account_id;
CREATE UNIQUE INDEX one_active_capture ON capture_jobs(connection_id)
 WHERE capture_state IN ('planned','collecting','paused');
CREATE TABLE job_requests (
 request_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES capture_jobs(id),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)));
CREATE TABLE job_context (
 job_id TEXT PRIMARY KEY REFERENCES capture_jobs(id), connector_version TEXT NOT NULL,
 timezone TEXT NOT NULL, capabilities_json TEXT NOT NULL CHECK(json_valid(capabilities_json)));
CREATE TABLE job_accounts (
 job_id TEXT NOT NULL REFERENCES capture_jobs(id), account_id TEXT NOT NULL REFERENCES bank_accounts(id),
 settings_version INTEGER NOT NULL, target_id TEXT NOT NULL, import_start TEXT NOT NULL, authorized_start TEXT NOT NULL,
 PRIMARY KEY(job_id,account_id));
CREATE TABLE reimport_ranges (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES bank_accounts(id), settings_version INTEGER NOT NULL,
 source_job_id TEXT NOT NULL REFERENCES capture_jobs(id), remaining_json TEXT NOT NULL CHECK(json_valid(remaining_json)));
CREATE TABLE account_capture_locks (
 account_id TEXT PRIMARY KEY REFERENCES bank_accounts(id), job_id TEXT NOT NULL REFERENCES capture_jobs(id));
CREATE TABLE captured_records (
 interval_id TEXT NOT NULL, page_number INTEGER NOT NULL, bank_id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(interval_id,bank_id),
 FOREIGN KEY(interval_id,page_number) REFERENCES capture_pages(interval_id,page_number),
 FOREIGN KEY(bank_id,revision) REFERENCES bank_versions(bank_id,revision));
CREATE TABLE capture_errors (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES capture_jobs(id), account_id TEXT REFERENCES bank_accounts(id),
 code TEXT NOT NULL, retryable INTEGER NOT NULL CHECK(retryable IN (0,1)), resolved INTEGER NOT NULL DEFAULT 0 CHECK(resolved IN (0,1)),
 created_at INTEGER NOT NULL);
CREATE INDEX interval_job_account ON capture_intervals(job_id,account_id,state);
CREATE INDEX coverage_account_dates ON coverage(account_id,start_date,end_date);
CREATE INDEX attempts_bank ON delivery_attempts(bank_id);
CREATE TRIGGER interval_scope BEFORE INSERT ON capture_intervals
 WHEN NOT EXISTS (SELECT 1 FROM job_accounts WHERE job_id=NEW.job_id AND account_id=NEW.account_id AND settings_version=NEW.settings_version)
 BEGIN SELECT RAISE(ABORT,'Interval must belong to a job account snapshot'); END;
CREATE TRIGGER captured_record_scope BEFORE INSERT ON captured_records
 WHEN NOT EXISTS (SELECT 1 FROM capture_intervals i JOIN bank_transactions b ON b.id=NEW.bank_id
                  WHERE i.id=NEW.interval_id AND i.account_id=b.account_id)
 BEGIN SELECT RAISE(ABORT,'Captured record must belong to interval account'); END;
CREATE TRIGGER interval_identity BEFORE UPDATE ON capture_intervals
 WHEN NEW.id<>OLD.id OR NEW.job_id<>OLD.job_id OR NEW.account_id<>OLD.account_id
 OR NEW.settings_version<>OLD.settings_version OR NEW.start_date<>OLD.start_date OR NEW.end_date<>OLD.end_date
 OR (OLD.state='complete' AND (NEW.state<>'complete' OR NEW.total_pages IS NOT OLD.total_pages OR NEW.total_records IS NOT OLD.total_records))
 BEGIN SELECT RAISE(ABORT,'Capture interval identity and completion evidence are immutable'); END;
CREATE TRIGGER page_immutable BEFORE UPDATE ON capture_pages
 BEGIN SELECT RAISE(ABORT,'Acknowledged pages are append-only'); END;
CREATE TRIGGER page_preserved BEFORE DELETE ON capture_pages
 BEGIN SELECT RAISE(ABORT,'Acknowledged pages are append-only'); END;
CREATE TRIGGER captured_record_immutable BEFORE UPDATE ON captured_records
 BEGIN SELECT RAISE(ABORT,'Captured revisions are append-only'); END;
CREATE TRIGGER captured_record_preserved BEFORE DELETE ON captured_records
 BEGIN SELECT RAISE(ABORT,'Captured revisions are append-only'); END;
CREATE TRIGGER request_immutable BEFORE UPDATE ON job_requests
 BEGIN SELECT RAISE(ABORT,'Command identity is immutable'); END;
CREATE TRIGGER request_preserved BEFORE DELETE ON job_requests
 BEGIN SELECT RAISE(ABORT,'Command identity is permanent'); END;
CREATE TRIGGER job_account_snapshot BEFORE UPDATE ON job_accounts
 BEGIN SELECT RAISE(ABORT,'Job account settings snapshot is immutable'); END;
CREATE TRIGGER job_context_snapshot BEFORE UPDATE ON job_context
 BEGIN SELECT RAISE(ABORT,'Job connector context snapshot is immutable'); END;
