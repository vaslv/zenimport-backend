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
