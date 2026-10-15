BEGIN TRANSACTION;
CREATE TABLE audit (
 id INTEGER PRIMARY KEY, at INTEGER NOT NULL, operation TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE bank_accounts (
 id TEXT PRIMARY KEY, bank TEXT NOT NULL, account TEXT NOT NULL,
 currency TEXT NOT NULL, zenmoney_account TEXT,
 UNIQUE(bank, account));
CREATE TABLE bank_operation_drafts (bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), definition TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE bank_transactions (
 id TEXT PRIMARY KEY, bank_account TEXT NOT NULL REFERENCES bank_accounts(id),
 external_id TEXT NOT NULL, date TEXT NOT NULL, amount_minor INTEGER NOT NULL,
 description TEXT NOT NULL, raw TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', linked_id TEXT,
 UNIQUE(bank_account, external_id));
CREATE TABLE category_drafts (bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), category_id TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE creation_drafts (bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), category_id TEXT, source TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE creation_jobs (bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), zen_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, state TEXT NOT NULL, error TEXT);
CREATE TABLE imports (
 id TEXT PRIMARY KEY, received_at INTEGER NOT NULL, bank_account TEXT NOT NULL,
 adapter_version TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "meta" VALUES('mode','live');
CREATE TABLE outbox (
 bank_transaction TEXT PRIMARY KEY REFERENCES bank_transactions(id),
 zen_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', error TEXT);
CREATE TABLE recipient_rules (bank TEXT NOT NULL, name TEXT NOT NULL, direction TEXT NOT NULL, category_id TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(bank,name,direction));
CREATE TABLE reconciliation_bank_members (
        group_id TEXT NOT NULL REFERENCES reconciliation_groups(id) ON DELETE CASCADE,
        bank_id TEXT NOT NULL UNIQUE REFERENCES bank_transactions(id), PRIMARY KEY(group_id,bank_id));
CREATE TABLE reconciliation_exclusions (zen_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL);
CREATE TABLE reconciliation_groups (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('single','split','settlement')),
        bank_account TEXT NOT NULL REFERENCES bank_accounts(id), created_at INTEGER NOT NULL,
        origin TEXT NOT NULL);
CREATE TABLE reconciliation_zen_members (
        group_id TEXT NOT NULL REFERENCES reconciliation_groups(id) ON DELETE CASCADE,
        zen_id TEXT NOT NULL UNIQUE, PRIMARY KEY(group_id,zen_id));
CREATE TABLE zen_account (
            id TEXT PRIMARY KEY, user_id TEXT,instrument_id TEXT,company_id TEXT,title TEXT,type TEXT,archive INTEGER,balance TEXT,role_id TEXT,sync_ids TEXT,start_balance TEXT,credit_limit TEXT,in_balance INTEGER,savings INTEGER,enable_correction INTEGER,enable_sms INTEGER,private INTEGER,balance_correction_type TEXT,capitalization INTEGER,percent TEXT,start_date TEXT,end_date_offset INTEGER,end_date_offset_interval TEXT,payoff_step INTEGER,payoff_interval TEXT, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_budget (
            id TEXT PRIMARY KEY, user_id TEXT,date TEXT,tag_id TEXT,income TEXT,outcome TEXT,income_lock INTEGER,outcome_lock INTEGER,is_income_forecast INTEGER,is_outcome_forecast INTEGER, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_category_drafts (zen_id TEXT PRIMARY KEY, category_id TEXT NOT NULL, original_tags TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE zen_category_sends (zen_id TEXT PRIMARY KEY, category_id TEXT NOT NULL, draft_updated_at INTEGER NOT NULL, before_payload TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, error TEXT);
CREATE TABLE zen_company (
            id TEXT PRIMARY KEY, title TEXT,full_title TEXT,www TEXT,country_id TEXT, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_country (
            id TEXT PRIMARY KEY, title TEXT, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_deletions (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES zen_sync_runs(id),
        kind TEXT NOT NULL, entity_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE zen_instrument (
            id TEXT PRIMARY KEY, title TEXT,code TEXT,symbol TEXT,rate TEXT, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_merchant (
            id TEXT PRIMARY KEY, user_id TEXT,title TEXT,mcc INTEGER, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_migrations (
        version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL, report TEXT NOT NULL);
INSERT INTO "zen_migrations" VALUES(11,1700000000,'{"restored_from_archives": 0, "without_archive": 0}');
CREATE TABLE zen_operation_drafts (zen_id TEXT PRIMARY KEY, definition TEXT NOT NULL, before_payload TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE zen_operation_sends (zen_id TEXT PRIMARY KEY, definition TEXT NOT NULL, draft_snapshot TEXT NOT NULL, before_payload TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, error TEXT);
CREATE TABLE zen_reminder (
            id TEXT PRIMARY KEY, income_account TEXT,outcome_account TEXT,income_instrument TEXT,outcome_instrument TEXT,income TEXT,outcome TEXT,merchant_id TEXT,payee TEXT,comment TEXT,user_id TEXT,start_date TEXT,end_date TEXT,interval TEXT,step INTEGER,points TEXT,notify INTEGER, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_reminder_marker (
            id TEXT PRIMARY KEY, income_account TEXT,outcome_account TEXT,income_instrument TEXT,outcome_instrument TEXT,income TEXT,outcome TEXT,merchant_id TEXT,payee TEXT,comment TEXT,user_id TEXT,reminder_id TEXT,date TEXT,state TEXT,notify INTEGER,is_forecast INTEGER, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_reminder_marker_tags (
            owner_id TEXT NOT NULL REFERENCES zen_reminder_marker(id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(owner_id,tag_id));
CREATE TABLE zen_reminder_tags (
            owner_id TEXT NOT NULL REFERENCES zen_reminder(id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(owner_id,tag_id));
CREATE TABLE zen_sync_runs (
        id INTEGER PRIMARY KEY, received_at INTEGER NOT NULL, requested_cursor INTEGER,
        response_cursor INTEGER, origin TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE zen_tag (
            id TEXT PRIMARY KEY, user_id TEXT,parent_id TEXT,title TEXT,icon TEXT,picture TEXT,color INTEGER,show_income INTEGER,show_outcome INTEGER,budget_income INTEGER,budget_outcome INTEGER,required INTEGER,archive INTEGER,static_id TEXT, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_transaction (
            id TEXT PRIMARY KEY, income_account TEXT,outcome_account TEXT,income_instrument TEXT,outcome_instrument TEXT,income TEXT,outcome TEXT,merchant_id TEXT,payee TEXT,comment TEXT,user_id TEXT,date TEXT,created INTEGER,op_income TEXT,op_outcome TEXT,op_income_instrument TEXT,op_outcome_instrument TEXT,original_payee TEXT,hold INTEGER,mcc INTEGER,reminder_marker_id TEXT,latitude TEXT,longitude TEXT,viewed INTEGER,qr_code TEXT,source TEXT,income_bank_id TEXT,outcome_bank_id TEXT, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE TABLE zen_transaction_tags (
            transaction_id TEXT NOT NULL REFERENCES zen_transaction(id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(transaction_id,tag_id));
CREATE TABLE zen_user (
            id TEXT PRIMARY KEY, login TEXT,parent_id TEXT,currency_id TEXT, changed INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL, run_id INTEGER REFERENCES zen_sync_runs(id));
CREATE INDEX zen_transaction_tags_tag ON zen_transaction_tags(tag_id);
CREATE INDEX zen_reminder_tags_tag ON zen_reminder_tags(tag_id);
CREATE INDEX zen_reminder_marker_tags_tag ON zen_reminder_marker_tags(tag_id);
CREATE INDEX zen_transaction_date ON zen_transaction(date);
CREATE INDEX zen_transaction_income_account ON zen_transaction(income_account);
CREATE INDEX zen_transaction_outcome_account ON zen_transaction(outcome_account);
CREATE INDEX zen_transaction_payee ON zen_transaction(payee);
CREATE INDEX zen_transaction_merchant_id ON zen_transaction(merchant_id);
CREATE INDEX zen_transaction_reminder_marker_id ON zen_transaction(reminder_marker_id);
CREATE INDEX zen_account_user_id ON zen_account(user_id);
CREATE INDEX zen_account_instrument_id ON zen_account(instrument_id);
CREATE INDEX zen_account_company_id ON zen_account(company_id);
CREATE INDEX zen_tag_user_id ON zen_tag(user_id);
CREATE INDEX zen_tag_parent_id ON zen_tag(parent_id);
CREATE INDEX zen_budget_user_id ON zen_budget(user_id);
CREATE INDEX zen_budget_date ON zen_budget(date);
CREATE INDEX zen_budget_tag_id ON zen_budget(tag_id);
CREATE INDEX zen_reminder_marker_reminder_id ON zen_reminder_marker(reminder_id);
CREATE INDEX zen_reminder_marker_date ON zen_reminder_marker(date);
CREATE VIEW split_links AS SELECT b.bank_id,z.zen_id
            FROM reconciliation_bank_members b JOIN reconciliation_groups g ON g.id=b.group_id
            JOIN reconciliation_zen_members z ON z.group_id=g.id WHERE g.kind='split';
COMMIT;
PRAGMA user_version=12;
