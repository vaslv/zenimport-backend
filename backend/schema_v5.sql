CREATE TABLE delivery_authorizations (
 bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), revision INTEGER NOT NULL,
 job_id TEXT NOT NULL REFERENCES capture_jobs(id), interval_id TEXT NOT NULL,
 settings_version INTEGER NOT NULL, target_id TEXT NOT NULL, source_hash TEXT,
 authorized_at INTEGER NOT NULL,
 FOREIGN KEY(bank_id,revision) REFERENCES bank_versions(bank_id,revision),
 FOREIGN KEY(interval_id,bank_id) REFERENCES captured_records(interval_id,bank_id));
CREATE TRIGGER delivery_authorization_proof BEFORE INSERT ON delivery_authorizations
 WHEN NOT EXISTS (SELECT 1 FROM captured_records r JOIN capture_intervals i ON i.id=r.interval_id
   JOIN bank_transactions b ON b.id=r.bank_id JOIN job_accounts a ON a.job_id=NEW.job_id AND a.account_id=b.account_id
   WHERE r.interval_id=NEW.interval_id AND r.bank_id=NEW.bank_id AND r.revision=NEW.revision AND i.state='complete'
     AND a.settings_version=NEW.settings_version AND a.target_id=NEW.target_id)
 BEGIN SELECT RAISE(ABORT,'Delivery needs completed capture and job authorization'); END;
CREATE TRIGGER delivery_authorization_frozen BEFORE UPDATE ON delivery_authorizations
 WHEN EXISTS(SELECT 1 FROM deliveries d WHERE d.bank_id=OLD.bank_id AND d.ever_attempted=1)
 BEGIN SELECT RAISE(ABORT,'Attempted delivery authorization is immutable'); END;
CREATE TABLE delivery_schedule (
 id INTEGER PRIMARY KEY CHECK(id=1), next_run_at INTEGER NOT NULL DEFAULT 0,
 failures INTEGER NOT NULL DEFAULT 0 CHECK(failures>=0));
INSERT INTO delivery_schedule(id) VALUES (1);
CREATE TRIGGER delivery_authorization_preserved BEFORE DELETE ON delivery_authorizations
 WHEN EXISTS(SELECT 1 FROM deliveries d WHERE d.bank_id=OLD.bank_id AND d.ever_attempted=1)
 BEGIN SELECT RAISE(ABORT,'Attempted delivery authorization cannot be deleted'); END;
CREATE TRIGGER delivery_authorization_update_proof BEFORE UPDATE ON delivery_authorizations
 WHEN NOT EXISTS (SELECT 1 FROM captured_records r JOIN capture_intervals i ON i.id=r.interval_id
   JOIN bank_transactions b ON b.id=r.bank_id JOIN job_accounts a ON a.job_id=NEW.job_id AND a.account_id=b.account_id
   WHERE r.interval_id=NEW.interval_id AND r.bank_id=NEW.bank_id AND r.revision=NEW.revision AND i.state='complete'
     AND a.settings_version=NEW.settings_version AND a.target_id=NEW.target_id)
 BEGIN SELECT RAISE(ABORT,'Delivery needs completed capture and job authorization'); END;
