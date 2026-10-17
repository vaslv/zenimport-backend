-- Human-readable names belong to bank connections, not physical bank accounts.
CREATE TABLE connection_labels (
 connection_id TEXT PRIMARY KEY REFERENCES connections(id),
 label TEXT NOT NULL CHECK(length(trim(label)) BETWEEN 1 AND 120));
