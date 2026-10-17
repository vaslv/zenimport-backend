# Synthetic legacy v12 schema

`schema.sql` was produced by initializing an **empty temporary database** with
legacy `backend.db.initialize`, ZenImport source `f3b2838`. It contains no user
financial data, accounts, or tokens. The internal migration timestamp was replaced
with a constant. `tests/legacy_factory.py` adds synthetic test rows only.
The converter never executes SQL from the source database: the schema is retained
as archived data.
