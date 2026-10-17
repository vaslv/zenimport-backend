-- The target actually used for a known bank/ZenMoney identity, not all past mappings.
CREATE TABLE processing_targets (
 bank_id TEXT NOT NULL REFERENCES bank_transactions(id), zen_id TEXT NOT NULL,
 target_id TEXT NOT NULL CHECK(length(target_id)>0), origin TEXT NOT NULL, at INTEGER NOT NULL,
 PRIMARY KEY(bank_id,zen_id));
CREATE TRIGGER processing_target_immutable BEFORE UPDATE ON processing_targets
 BEGIN SELECT RAISE(ABORT,'Matched account ownership is immutable'); END;
CREATE TRIGGER processing_target_preserved BEFORE DELETE ON processing_targets
 BEGIN SELECT RAISE(ABORT,'Matched account ownership is permanent'); END;
