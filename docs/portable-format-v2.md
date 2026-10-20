# Portable format-2 primitives (L3 in progress)

`backend/portable_codec.py` implements format serialization, not database restore
or write authority. It has no HTTP route and is not a replacement for the current
format-1 migration API. The participant protocol, complete financial graph and
owner validation, metadata/legacy secret screening, runtime normalization and
staged installation are required before exposing portable import/export.

The checked runtime definition is copied from the canonical Chrome contract into
`backend/portable_contract.json`; tests compare every part. Runtime builds do not
need the test checkout. Financial schema 1 has exactly 49 logical table files.
Each row uses declared column order and typed null/text/integer/blob scalars.
Current financial columns accept TEXT and signed 64-bit INTEGER; an unsupported
type fails rather than coercing a large number. Raw JSON fields remain text.

Table bytes are compact UTF-8 JSON arrays followed by LF. Primary keys must be
strictly increasing: integer keys compare numerically, text keys compare UTF-8
bytes, including astral characters. A repeated key or alternate serialization is
rejected. The manifest binds exact filenames, columns, counts, sizes and SHA256.
The dataset digest uses the frozen descriptor field order and excludes producer,
archive time and session IDs. It is not an authenticity proof or owner verification.

Input/output streams enforce compressed, emitted, manifest and row budgets. ZIP
checks reject duplicate/unknown paths, traversal, NUL-truncated names, encrypted
entries, symlinks/devices, unsupported compression and CRC/hash mismatches. A
failed read removes only its new private stage; existing paths are never replaced.
Export files are private, streamed with bounded writes and fsynced before success.
ZIP timestamps/order are deterministic for identical inputs. A process crash may
leave inactive temporary files; the durable controller must never activate those
files merely because they exist.

`portable-golden-v2.json` is a format-only synthetic fixture with large integer
tags, exact raw JSON and nulls. Both Node and Python independently verify its table
bytes and digest. It deliberately lacks financial provenance/foreign keys and
sets activation_permitted=false. Passing its format test must never authorize it
for import. Metadata classification and graph validity are separate gates.

Evidence so far: 598 backend Docker tests and 70 Chrome unit/contract tests, using
only synthetic files with execution networking disabled. The complete L3 stage
is not ready until the remaining gates in the approved plan pass.

## Financial staging validation (L3.2)

`portable_graph.validate_stage` rechecks the immutable staged files, loads them
into a new private proof database using packaged trusted SQL and bound values,
and checks foreign keys, uniqueness, CHECKs and historical insert predicates.
It validates raw bank revisions and aliases, capture page digests/coverage,
request context, immutable attempted UUID/payload/authorization evidence, terminal
processing proofs, mirror projections, declared owners and metadata disposition.
The proof database is destroyed; it is never installed as the active database.

An optional expected-owner set is a separate comparison, not authentication.
The future migration service must still obtain fresh token identity, enforce its
hold/epoch protocol and construct a properly initialized destination runtime.
Passing these graph checks does not confer activation rights.

Metadata keys and stock schema-12 legacy object definitions are explicitly pinned
in portable-validation-v2.json. Unknown keys/objects/columns fail closed pending
classification; archived SQL is compared as data and is never executed. Inert
legacy audit content and original journal uncertainty are retained. Legacy mirror
projections are checked against their original archived facts because old versions
accepted string monetary fields that the modern SDK rejects. Source host backup
paths and executable capture leases are normalized only as documented; unfinished
capture and attempting delivery cannot masquerade as quiescent history. Known
credential markers are rejected even inside nested JSON text. This screening is
not a general-purpose guarantee against arbitrary steganographic secrets in notes.

Docker validation: 626 backend tests passed, including current and legacy
synthetic graphs and hash-consistent corrupted archives. No live calls or active
storage changes were made. HTTP migration/activation remains unimplemented for
format 2; L3 is still in progress.

## Durable participant primitives (L3.3, internal only)

SQLite schema 8 adds a physical installation UUID independent of `instance_id`,
a monotonic participant epoch, explicit global holds, permanent switch bindings
and immutable command receipts. Received chunks are bounded to 256 KiB, keyed by
switch/index and committed before acknowledgment. Their complete contiguous byte
stream must match its declared length and SHA256 before sealing/validation.
These runtime authority tables never enter the portable financial cohort.

The source arbitrates `export_hold -> handed_off` versus
`export_hold -> cancel_pending` in one write transaction. Cancellation resumes
only after a matching destination discard receipt. A destination that never
started uploading still records an irreversible discard tombstone, preventing a
delayed old start command from activating that switch. Handoff cannot be undone;
a new reverse migration is required. Peer receipt hashes provide integrity, not
cross-server authentication: the coordinator must verify them through authenticated
status. The threat model excludes a malicious administrator fabricating authority.

Activation consumes a matched source handoff and installs data within the same
transaction. The internal primitive requires an installer; the unfinished HTTP
service cannot yet invoke it. Destination eligibility is rechecked immediately
before installation. An activated target stays awaiting explicit synchronization,
so its worker cannot send automatically. Repeated command UUIDs return the exact
persisted receipt, including after restart; a changed request is rejected.

Legacy requests cannot clear a format-2 hold. Startup token-context maintenance
is deferred while held; an old Mirror object cannot reclaim a replaced context.
Format-1 transfer is rejected after participant history exists. For eligible old
archives, formats with SQLite 6/7/8 remain accepted and a verified private copy is
upgraded before installation. Its installation controls are replaced with the
existing destination's physical identity, never cloned from the source. The
financial lineage is preserved. Original source schema audit has a separate
runtime table and remains exportable through its logical financial alias.

The public ordinary API remains version 1; portable HTTP capabilities/commands,
background staging/installation and full interop acceptance are still pending.
Do not advertise format-2 or standalone mode as available yet.
