# Portable migration protocol 2 (development)

This API is independent of ordinary capture API 1. The released Chrome extension
continues to use ordinary API 1. Standalone browser storage is not available until
its subsequent implementation and acceptance stages pass. Do not deploy this
unreleased development checkpoint over production data.

Use one backend process per SQLite database, as in the supplied Docker command.
Migration decisions and background work share the delivery writer file lock;
normal financial claims and mirror commits additionally check the persistent hold.
All endpoints require the existing backend bearer credential, enforce body limits
and return `Cache-Control: no-store`. Never put credentials into an archive,
command, receipt or peer URL. No endpoint executes uploaded SQL.

The shared `portable-http-v2.json` contract contains real Pydantic response schemas,
command fields, routes and null handling. Unknown fields fail. Protocol counters,
epochs, sizes and timestamps are canonical decimal strings in JSON; SQLite keeps
its bounded native integers. Chunk indexes in URLs are integers from 0 through 255.

## Discovery and status

Prefix: `/api/v1/migration/portable`.

- `GET /capabilities`: physical installation UUID, participant epoch/state,
  supported archive/financial schema, chunk/archive bounds and import eligibility.
  `legacy_hold` identifies an unfinished format-1 operation; complete that old
  operation before starting a live portable migration. Never infer the physical
  installation identity from a URL or portable financial lineage.
- `GET /switches/{switch}`: current physical state, optional permanent operation,
  immutable receipts and background task tickets. An unknown switch has no
  operation, receipts or tasks. It does not imply that an old source may resume.

The coordinator must verify source and destination status using each endpoint's
own authenticated connection. A receipt checksum is integrity, not a signature
or an authentication secret. The protocol protects against crashes, lost replies
and stale ordinary clients; a malicious administrator fabricating authority is
outside its threat model.

## Commands and live transfer

`POST /commands/{command}` returns HTTP 202 with a receipt or queued work ticket.
Every command has `request_id`, `switch_id`, `expected_epoch`. IDs are canonical
UUIDs; reuse the same request UUID and exact body after a lost reply. A changed
body with the same UUID is rejected. Epochs fence stale decisions. Receipt JSON
includes null fields: do not remove them before verifying its SHA256.

1. Discover the destination installation identity and eligibility. The source
   receives `source_start` with `peer_id`. Under the exclusive quiescence barrier
   it rejects unfinished capture, recovers any crashed attempted send as uncertain
   without an API retry, persists a global hold and queues a snapshot. A new
   source snapshot is never a new financial authorization.
2. Poll source status. Its background snapshot validates the complete financial
   graph, builds a bounded archive and stores immutable chunks. Only after all
   bytes are durable does it publish `source_seal`. Clients cannot seal arbitrary
   digests: source sealing is an internal server command.
3. Send `destination_start` with the verified `source_seal` in `peer_receipt`.
   This reserves an eligible target under `import_hold`. There is no database merge.
4. Download `GET /switches/{switch}/chunks/{number}` from the source. The response
   is raw bytes with `X-Chunk-SHA256`. Upload using `PUT` to the same target path;
   JSON fields are `expected_epoch`, canonical padded base64 `data` and `sha256`.
   Chunks are at most 262,144 bytes. A successful ACK is committed. Repeating the
   same index/data is safe; another value is rejected. Only the last chunk can be
   short; the complete archive must match its bound length and SHA256.
5. `POST /finalize` with the three common command fields returns a validation
   task. Validation runs in private staging and independently performs a fresh
   read-only full ZenMoney identity check. Wrong identity or malformed data stays
   held and blocked. No financial change is sent. One immutable task per kind is
   permitted for a switch; recover its ID through status instead of replacing it.
6. After validation completes, send `destination_arm` without additional fields.
   Arming grants no right to send. The coordinator verifies its receipt at the
   target, then sends source `source_handoff` with that receipt. Source handoff is
   irreversible and competes atomically with source cancellation.
7. Verify the source handoff at the source; send `destination_activate` with it
   to the target. Activation is queued and rechecks graph, fresh ZenMoney owner
   and empty-target eligibility. The financial rows, installation transition and
   permanent result receipt commit together. A crash rolls the transaction back,
   or recovery observes the already committed receipt; it never installs twice.
8. Verify target status. It is active but `awaiting_sync`; activation does not
   wake delivery. The next explicit ordinary Sync fetches a full current mirror
   before any possible send. The old source remains handed off and paused.

Background work is persisted as queued/running/blocked/complete. Restart recovers
running work. A blocked task is not retried in a loop; correct the cause and use
`POST /work/{task}/retry` with `{}`. This retains the original request/UUID. Lost
activation replies recover from permanent receipts without another API request.
Private scratch is not authority and is cleaned only within the installation's
own namespace under its exclusive writer lock.

## Cancellation and uncertain outcomes

Before handoff, send `source_cancel`. Its CAS must win at the source before
requesting `destination_discard` with the resulting cancellation receipt. The
latter also records a permanent discard when no upload ever started. This prevents
a delayed old start from acquiring activation rights. Only after verifying that
discard may `source_resume` consume its matching receipt and release the source.

If handoff won, cancellation/discard/resumption is forbidden. Recover forward or
perform a new reverse migration. An unreachable or unverifiable participant stays
paused. Do not clear SQL flags manually, silently fall back to an old browser
copy, or use legacy `/jobs` or `/migration/resume` to bypass the hold.

## Installation and format-1 compatibility

The target preserves its physical UUID, epoch, task journal and receipts. It loads
only the 49 logical financial tables from packaged definitions into the activation
transaction. Source schema audit goes to `portable_schema_audit`; target runtime
markers remain its own. An old metadata `migration_hold` is retained through the
explicit `portable:migration_hold` audit alias, never activated as a foreign lock.

Pre-sync setup/catalog data can be replaced under the eligibility rule. This is
not merging financial histories. The source journal, attempted payloads, UUIDs,
actual target sides and tombstones are retained. Mirror readiness/cursor/context
and derived predictions are invalidated; only never-attempted authorizations are
rebound to the destination's verified credential fingerprint. Attempted originals
stay immutable. Fresh full synchronization is mandatory before sending.

Format-1 archives with SQLite 6/7/8 remain available through the legacy routes on
eligible installations without portable participant history. They are verified
and upgraded only in a private copy and never clone the source's physical UUID.
The old transfer API cannot carry or replace permanent format-2 authority history
and is rejected after such history exists. Ordinary API-1 synchronization remains
supported. Never downgrade a schema-8 development database to a published older
binary; use a separately verified backup and reconcile remote side effects first.

Manual archive-only disaster recovery is distinct from this live handoff. Its
explicit former-writer retirement and snapshot-age flow belongs to L10; these
endpoints do not fabricate a source handoff for a manually uploaded file.
