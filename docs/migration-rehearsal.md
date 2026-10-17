# M1 — rehearsal on a copy, October 16, 2026

Source `zenimport-live-data` was mounted **read-only** into an offline container. SQLite backup included WAL; user_version=12, application_id=0, integrity_check passed. The source server was not stopped, and original files/settings were unchanged. The copy has no active API connection: migration/validation ran through a separate CLI with `--network none`, without real token variables.

Source copy SHA256: `5bd31747c01b314f3b20c8c81287b8fbd11e359f4045f04f5989cbeed692d99e`. Private results are in ignored `data/rehearsal-20261016`, excluded from Git/Docker context. No actual cutover took place; this snapshot does not replace the final M4 backup.

## Results

| Check | Result |
|---|---:|
| Source table/row archive with exact contents and SQLite types | 36 / 58,651 |
| Accounts / bank transactions / legacy aliases | 1 / 561 / 561 |
| Groups / bank participants / ZenMoney participants | 539 / 543 / 541 |
| Mirror entities / tombstones | 32,672 / 1,844 |
| Bank exclusions / payee rules | 4 / 1 |
| Usable manual categories for new records | 4 |
| Creation jobs / attempts / confirmations | 71 / 140 / 545 |
| Delivery: delivered / prepared | 60 / 11 |
| Processing: delivered / pending / prepared / suppressed | 543 / 3 / 11 / 4 |

Checks went beyond counts: every bank record key/raw value; exact amounts/signs/currencies/dates/descriptions; original account bindings; group/participant/exclusion/rule contents; raw and financial mirror projections; soft/hard deletions and cursor; job UUIDs and immutable payloads of started attempts. All source responses/unused drafts are additionally retained in a lossless archive with a content fingerprint.

Repeated conversion: `repeated=true`, entire target database logical fingerprint unchanged. The SQLite snapshot was restored **into a new file**; its complete logical fingerprint matched the target. The source copy SHA256 remained unchanged after all checks. Integrity/FK checks passed.

Legacy accounts intentionally remain unconfigured; the new popup must confirm target/period. Coverage and delivery_authorizations were not invented from historical presence: both tables are empty. Eleven prepared records are not automatically sent merely on startup. Legacy existing-record edits and debt/transfer types are archived under the agreed policy; usable manual categories are stored separately. Deleted/delivered transactions are not considered new.

## Reproduction

`python -m backend.snapshot export-legacy --source /absolute/legacy.sqlite3 --destination /absolute/new-copy.sqlite3`: explicit schema 12 backup only, never overwrites the destination.

`python -m backend.rehearsal --source-copy /absolute/new-copy.sqlite3 --destination /absolute/converted.sqlite3 --restore-check /absolute/restored.sqlite3`: conversion, independent projection/archive verification, repeat, and restoration. Use only a private copy directory and an offline container without real tokens. This source database is nearly 2 GB; the converted copy with its full archive is about 4 GB, with additional space needed for restore validation and SQLite temporary sorting.

Synthetic checks for the new tool: WAL, foreign schema/overwrite prevention, complete repeat/restore, and detection of a changed amount despite an intact archive. Full ARM64 Docker run: **352 passed** and lifecycle checks passed. M2 validates AMD64 and restored-copy startup; M3/M4 remain separate acceptance stages.

After M2, created SQLite copies were compressed to .sqlite3.gz with complete uncompressed SHA256 verification; archives.json is private. Extract copies into a new private directory before repeating. M2 outcome: docs/m2-validation.md.
