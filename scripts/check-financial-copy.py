"""Read-only financial fingerprint for an offline runtime rehearsal.

Outputs hashes and row counts only. No API client or application is started.
HTTP response archives and mutable mirror/runtime freshness are checked by M1,
not included in this runtime invariant.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys

from backend import legacy_archive as archive, storage

TABLES = ('bank_accounts','account_bindings','bank_transactions','bank_aliases',
    'bank_versions','bank_processing','link_groups','link_bank_members','link_zen_members',
    'bank_exclusions','zen_exclusions','category_choices','recipient_rules','deliveries',
    'processing_receipts','delivery_attempts','zen_entities','zen_movements','zen_tags',
    'zen_tombstones','zen_deletions')

digest, counts = sha256(), {}
with storage.transaction(Path(sys.argv[1]), read_only=True) as db:
    for table in TABLES:
        digest.update(table.encode())
        counts[table] = 0
        for row in archive.rows(db, table):
            digest.update((archive.packed(row)+'\n').encode())
            counts[table] += 1
print(json.dumps({'sha256':digest.hexdigest(),'tables':counts},sort_keys=True))
