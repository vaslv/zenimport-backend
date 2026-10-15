CREATE TABLE zen_mirror_state (
 id INTEGER PRIMARY KEY CHECK(id=1), source_hash TEXT,
 cursor INTEGER NOT NULL DEFAULT 0 CHECK(cursor>=0), generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0),
 needs_full INTEGER NOT NULL DEFAULT 1 CHECK(needs_full IN (0,1)), ready INTEGER NOT NULL DEFAULT 0 CHECK(ready IN (0,1)),
 refreshed_at INTEGER, error_code TEXT);
INSERT INTO zen_mirror_state(id) VALUES (1);
CREATE TABLE category_payments (
 bank_id TEXT PRIMARY KEY REFERENCES bank_transactions(id), category_id TEXT,
 reason TEXT NOT NULL, eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
 zen_ids_json TEXT NOT NULL CHECK(json_valid(zen_ids_json)));
CREATE TABLE category_predictions (
 bank_id TEXT PRIMARY KEY, bank_revision INTEGER NOT NULL, category_id TEXT,
 source TEXT NOT NULL CHECK(source IN ('manual','rule','history','unknown')), reason TEXT NOT NULL,
 candidates_json TEXT NOT NULL CHECK(json_valid(candidates_json)), mirror_generation INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 FOREIGN KEY(bank_id,bank_revision) REFERENCES bank_versions(bank_id,revision));
