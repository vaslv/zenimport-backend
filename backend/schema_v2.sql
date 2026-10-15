CREATE TABLE schema_migrations (
 version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL, backup_path TEXT);
CREATE TABLE connections (
 id TEXT PRIMARY KEY, bank TEXT NOT NULL, profile_key TEXT NOT NULL,
 profile_identity TEXT NOT NULL CHECK(profile_identity IN ('bank','user_selected')),
 connector_version TEXT NOT NULL, timezone TEXT,
 capabilities_json TEXT NOT NULL CHECK(json_valid(capabilities_json)),
 accounts_complete INTEGER NOT NULL DEFAULT 0 CHECK(accounts_complete IN (0,1)),
 UNIQUE(bank,profile_key));
CREATE TABLE bank_accounts (
 id TEXT PRIMARY KEY, bank TEXT NOT NULL, identity_scope TEXT NOT NULL CHECK(identity_scope IN ('bank','profile')),
 profile_key TEXT NOT NULL DEFAULT '', source_key TEXT NOT NULL, currency TEXT NOT NULL,
 scale INTEGER NOT NULL CHECK(scale BETWEEN 0 AND 12), label TEXT NOT NULL, masked_number TEXT NOT NULL,
 supported INTEGER NOT NULL CHECK(supported IN (0,1)), unsupported_reason TEXT,
 selection TEXT NOT NULL DEFAULT 'unconfigured' CHECK(selection IN ('unconfigured','enabled','ignored')),
 zenmoney_account_id TEXT, import_start TEXT, settings_version INTEGER NOT NULL DEFAULT 0 CHECK(settings_version>=0),
 last_complete_end TEXT,
 CHECK((identity_scope='bank' AND profile_key='') OR (identity_scope='profile' AND profile_key<>'')),
 CHECK((selection='enabled' AND zenmoney_account_id IS NOT NULL AND import_start IS NOT NULL)
    OR (selection<>'enabled' AND zenmoney_account_id IS NULL)),
 UNIQUE(bank,identity_scope,profile_key,source_key,currency));
CREATE TABLE connection_accounts (
 connection_id TEXT NOT NULL REFERENCES connections(id), account_id TEXT NOT NULL REFERENCES bank_accounts(id),
 PRIMARY KEY(connection_id,account_id));
CREATE TABLE account_bindings (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES bank_accounts(id),
 zenmoney_account_id TEXT NOT NULL, settings_version INTEGER, origin TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE bank_transactions (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES bank_accounts(id), external_id TEXT NOT NULL,
 identity_kind TEXT NOT NULL CHECK(identity_kind IN ('bank','fingerprint')), identity_version TEXT NOT NULL,
 local_date TEXT NOT NULL, occurred_at TEXT, direction TEXT NOT NULL CHECK(direction IN ('expense','income')),
 amount TEXT NOT NULL CHECK(money_valid(amount)), currency TEXT NOT NULL,
 description TEXT NOT NULL, beneficiary_account TEXT, beneficiary_name TEXT,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
 UNIQUE(account_id,external_id), UNIQUE(id,account_id));
CREATE TRIGGER bank_identity_immutable BEFORE UPDATE ON bank_transactions
 WHEN NEW.id<>OLD.id OR NEW.account_id<>OLD.account_id OR NEW.external_id<>OLD.external_id
 BEGIN SELECT RAISE(ABORT,'Canonical bank identity is immutable'); END;
CREATE TABLE bank_aliases (
 account_id TEXT NOT NULL REFERENCES bank_accounts(id), external_id TEXT NOT NULL, identity_version TEXT NOT NULL,
 bank_id TEXT NOT NULL, PRIMARY KEY(account_id,external_id),
 FOREIGN KEY(bank_id,account_id) REFERENCES bank_transactions(id,account_id));
CREATE TABLE bank_versions (
 bank_id TEXT NOT NULL REFERENCES bank_transactions(id), revision INTEGER NOT NULL CHECK(revision>=1),
 data_json TEXT NOT NULL CHECK(json_valid(data_json)), raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
 observed_at INTEGER NOT NULL, parser_version TEXT NOT NULL, PRIMARY KEY(bank_id,revision));
CREATE TRIGGER bank_version_immutable BEFORE UPDATE ON bank_versions
 BEGIN SELECT RAISE(ABORT,'Bank revision history is append-only'); END;
CREATE TRIGGER bank_version_preserved BEFORE DELETE ON bank_versions
 BEGIN SELECT RAISE(ABORT,'Bank revision history is append-only'); END;
CREATE TABLE bank_processing (
 bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id),
 state TEXT NOT NULL CHECK(state IN ('pending','prepared','uncertain','delivered','suppressed')),
 reason TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TRIGGER processing_terminal_update BEFORE UPDATE OF state ON bank_processing
 WHEN (OLD.state='suppressed' AND NEW.state<>'suppressed')
   OR (OLD.state='delivered' AND NEW.state NOT IN ('delivered','suppressed'))
 BEGIN SELECT RAISE(ABORT,'Processed bank operation cannot be reset'); END;
CREATE TRIGGER processing_terminal_delete BEFORE DELETE ON bank_processing
 WHEN OLD.state IN ('delivered','suppressed')
 BEGIN SELECT RAISE(ABORT,'Processed bank operation cannot be deleted'); END;
CREATE TABLE processing_receipts (
 bank_id TEXT NOT NULL REFERENCES bank_transactions(id), zen_id TEXT NOT NULL,
 origin TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY(bank_id,zen_id));
CREATE TRIGGER receipt_immutable BEFORE UPDATE ON processing_receipts
 BEGIN SELECT RAISE(ABORT,'Confirmed processing history is append-only'); END;
CREATE TRIGGER receipt_preserved BEFORE DELETE ON processing_receipts
 BEGIN SELECT RAISE(ABORT,'Confirmed processing history is append-only'); END;
CREATE TABLE capture_jobs (
 id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES connections(id),
 request_id TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 end_date TEXT NOT NULL, capture_state TEXT NOT NULL, delivery_state TEXT NOT NULL,
 worker_id TEXT, lease_epoch INTEGER NOT NULL DEFAULT 0, lease_expires_at INTEGER,
 created_at INTEGER NOT NULL);
CREATE TABLE capture_intervals (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES capture_jobs(id),
 account_id TEXT NOT NULL REFERENCES bank_accounts(id), settings_version INTEGER NOT NULL,
 start_date TEXT NOT NULL, end_date TEXT NOT NULL, state TEXT NOT NULL,
 total_pages INTEGER, total_records INTEGER, CHECK(start_date<=end_date));
CREATE TABLE capture_pages (
 interval_id TEXT NOT NULL REFERENCES capture_intervals(id), page_number INTEGER NOT NULL CHECK(page_number>=1),
 digest TEXT NOT NULL, records_count INTEGER NOT NULL CHECK(records_count>=0),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(interval_id,page_number));
CREATE TABLE coverage (
 account_id TEXT NOT NULL REFERENCES bank_accounts(id), start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 interval_id TEXT NOT NULL UNIQUE REFERENCES capture_intervals(id), CHECK(start_date<=end_date),
 PRIMARY KEY(account_id,start_date,end_date,interval_id));
CREATE TABLE zen_sync_runs (
 id TEXT PRIMARY KEY, received_at INTEGER NOT NULL, requested_cursor INTEGER, response_cursor INTEGER,
 origin TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE zen_entities (
 kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)), changed INTEGER,
 deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)), run_id TEXT REFERENCES zen_sync_runs(id),
 PRIMARY KEY(kind,id));
CREATE TABLE zen_movements (
 kind TEXT NOT NULL DEFAULT 'transaction' CHECK(kind IN ('transaction','reminder','reminderMarker')),
 id TEXT NOT NULL, date TEXT, income_account TEXT, outcome_account TEXT, income_instrument TEXT,
 outcome_instrument TEXT, income TEXT, outcome TEXT, viewed INTEGER, hold INTEGER,
 PRIMARY KEY(kind,id), FOREIGN KEY(kind,id) REFERENCES zen_entities(kind,id) ON DELETE CASCADE);
CREATE INDEX movement_accounts ON zen_movements(income_account,outcome_account,date);
CREATE TABLE zen_tags (
 kind TEXT NOT NULL, entity_id TEXT NOT NULL, tag_id TEXT NOT NULL, position INTEGER NOT NULL,
 PRIMARY KEY(kind,entity_id,tag_id), FOREIGN KEY(kind,entity_id) REFERENCES zen_entities(kind,id) ON DELETE CASCADE);
CREATE TABLE zen_deletions (
 id TEXT PRIMARY KEY, run_id TEXT REFERENCES zen_sync_runs(id), kind TEXT NOT NULL, entity_id TEXT NOT NULL,
 data TEXT NOT NULL CHECK(json_valid(data)));
CREATE TABLE zen_tombstones (
 kind TEXT NOT NULL, entity_id TEXT NOT NULL, reason TEXT NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)),
 PRIMARY KEY(kind,entity_id));
CREATE TRIGGER tombstone_preserved BEFORE DELETE ON zen_tombstones
 BEGIN SELECT RAISE(ABORT,'Remote deletion history is permanent'); END;
CREATE TRIGGER tombstone_identity BEFORE UPDATE ON zen_tombstones
 WHEN NEW.kind<>OLD.kind OR NEW.entity_id<>OLD.entity_id
 BEGIN SELECT RAISE(ABORT,'Remote deletion identity is immutable'); END;
CREATE TABLE link_groups (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('single','split','settlement')),
 account_id TEXT NOT NULL REFERENCES bank_accounts(id), created_at INTEGER NOT NULL, origin TEXT NOT NULL);
CREATE TABLE link_bank_members (
 group_id TEXT NOT NULL REFERENCES link_groups(id), bank_id TEXT NOT NULL UNIQUE REFERENCES bank_transactions(id),
 PRIMARY KEY(group_id,bank_id));
CREATE TABLE link_zen_members (
 group_id TEXT NOT NULL REFERENCES link_groups(id), zen_id TEXT NOT NULL UNIQUE, PRIMARY KEY(group_id,zen_id));
CREATE TABLE deliveries (
 bank_id TEXT NOT NULL UNIQUE REFERENCES bank_transactions(id), zen_id TEXT PRIMARY KEY,
 payload TEXT NOT NULL CHECK(json_valid(payload)), state TEXT NOT NULL CHECK(state IN ('prepared','attempting','uncertain','delivered','suppressed')),
 ever_attempted INTEGER NOT NULL CHECK(ever_attempted IN (0,1)), origin TEXT NOT NULL,
 authorized_job_id TEXT REFERENCES capture_jobs(id), updated_at INTEGER NOT NULL,
 needs_rebuild INTEGER NOT NULL DEFAULT 0 CHECK(needs_rebuild IN (0,1)),
 CHECK(state<>'attempting' OR (ever_attempted=1 AND needs_rebuild=0)));
CREATE TRIGGER delivery_immutable BEFORE UPDATE ON deliveries
 WHEN NEW.zen_id<>OLD.zen_id OR NEW.bank_id<>OLD.bank_id OR NEW.ever_attempted<OLD.ever_attempted
   OR (OLD.ever_attempted=1 AND NEW.payload<>OLD.payload)
   OR (OLD.state='suppressed' AND NEW.state<>'suppressed')
   OR (OLD.state='delivered' AND NEW.state NOT IN ('delivered','suppressed'))
 BEGIN SELECT RAISE(ABORT,'Delivery identity or attempted payload is immutable'); END;
CREATE TRIGGER delivery_preserved BEFORE DELETE ON deliveries
 WHEN OLD.ever_attempted=1 OR OLD.state IN ('delivered','suppressed')
 BEGIN SELECT RAISE(ABORT,'Attempted delivery cannot be deleted'); END;
CREATE TABLE delivery_attempts (
 id TEXT PRIMARY KEY, bank_id TEXT NOT NULL REFERENCES bank_transactions(id), zen_id TEXT NOT NULL,
 at INTEGER NOT NULL, outcome TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE INDEX attempted_zen_id ON delivery_attempts(zen_id);
CREATE TRIGGER attempt_immutable BEFORE UPDATE ON delivery_attempts
 BEGIN SELECT RAISE(ABORT,'Delivery attempts are append-only'); END;
CREATE TRIGGER attempt_preserved BEFORE DELETE ON delivery_attempts
 BEGIN SELECT RAISE(ABORT,'Delivery attempts are append-only'); END;
CREATE TABLE bank_exclusions (
 bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), reason TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE zen_exclusions (zen_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL);
CREATE TABLE recipient_rules (
 bank TEXT NOT NULL, name TEXT NOT NULL, direction TEXT NOT NULL, category_id TEXT NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(bank,name,direction));
CREATE TABLE category_choices (
 bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), category_id TEXT, source TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE category_evidence (
 bank_id TEXT NOT NULL REFERENCES bank_transactions(id), zen_id TEXT NOT NULL, category_id TEXT NOT NULL,
 direction TEXT NOT NULL, origin TEXT NOT NULL, eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
 PRIMARY KEY(bank_id,zen_id,category_id));
CREATE TABLE legacy_imports (
 digest TEXT PRIMARY KEY, imported_at INTEGER NOT NULL, source_version INTEGER NOT NULL,
 manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)), report_json TEXT NOT NULL CHECK(json_valid(report_json)));
CREATE TABLE legacy_objects (
 source_digest TEXT NOT NULL REFERENCES legacy_imports(digest), type TEXT NOT NULL, name TEXT NOT NULL,
 table_name TEXT NOT NULL, sql TEXT, columns_json TEXT, PRIMARY KEY(source_digest,type,name));
CREATE TABLE legacy_rows (
 source_digest TEXT NOT NULL REFERENCES legacy_imports(digest), table_name TEXT NOT NULL,
 ordinal INTEGER NOT NULL, values_json TEXT NOT NULL CHECK(json_valid(values_json)),
 PRIMARY KEY(source_digest,table_name,ordinal));
