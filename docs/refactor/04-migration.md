# Data migration and overall acceptance

These stages require completed B6/E5. The user authorized plan implementation on October 15; apply the legacy draft policy below when preparing copy-based migration. Detailed rationale: Current data migration in the overall design.

## M1. Rehearse on a copy

Create a consistent SQLite backup including WAL; never copy only the main file of an actively changing database. Record schema/application version, checksums, and available space. The copy must not connect to production ZenMoney. Do not clean the original database/archives.

Verify bank records/raw data, accounts/mappings, 1:N/N:M groups, exclusions, rules, mirror/cursor, deletions, UUIDs, and attempts. Supplement counts with key/content/money comparisons. Run migration again with identical results. Known deliveries remain processed even when current targets are absent from the mirror.

Proposed policy: archive edits to existing ZenMoney records only; retain eligible manual categories for new records; archive unsaved legacy transfer/debt types, with new records becoming expense/income. Unstarted deliveries may get new formatting with their original UUID and viewed=false; started/uncertain deliveries retain payloads and are checked first. Never change viewed on previously delivered records.

Migrate history coverage only with proven completeness. Otherwise reread the chosen period with original identities/delivery history.

Output: report per data group, no loss, uncheckable legacy states listed, and validated recovery path. Never declare migration successful without evidence.

## M2. Complete validation without the real API

Run built projects against a test database/fake ZenMoney. Confirm every overall acceptance criterion, especially: deletions never return, edits are not overwritten, ambiguous records are created as new, unknown-send never retries blindly, empty intervals advance coverage, and stopping workers loses no data.

Check persistent-volume startup and container recreation. Images work without neighboring sources. Record exact artifact versions and both claimed platform results.

## M3. Checks fixtures cannot replace

In an authenticated VietinBank session, verify account lists/switching, history limits, details, and key stability. Never request a bank password for server transmission.

Using an agreed transaction/test account, verify actual viewed=false creation/display in current mobile ZenMoney, category/type edits, then separate deletion and repeat synchronization. Agree live writes in advance; do not use valuable historical records for destructive checks. Record observed behavior instead of assuming it from another user's Android/iOS.

## M4. Cutover

After acceptance, stop the old sender/active jobs. Take the final backup and migrate; explicitly attach the correct volume, avoiding an empty database caused by a new Compose name. Old/new writers must never run simultaneously.

First startup reads/verifies old uncertain deliveries. New writes require the user button. Check one selected account/result first, then others.

Rollback after new writes is not simply restoring an old backup: it loses new UUIDs and risks duplicates. Before cutover, select/test preservation of the current journal during version rollback or agreed restoration. Never claim automatic schema downgrade without a test.

Complete: production data preserved, repeats safe, mobile acceptance confirmed, reproducible startup/backup/recovery instructions. Pushing, tags, publication, and deleting the old project are not separately authorized.
