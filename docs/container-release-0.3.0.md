# Backend 0.3.0

Release date: 2026-10-23.

Image: `ghcr.io/vaslv/zenimport-backend:0.3.0` for Linux AMD64 and ARM64.
Upgrade the backend before Chrome 0.3.0. The publication workflow verifies the
immutable image digest on both native architectures before extension release.

## Compatibility

- Ordinary synchronization remains API v1. Existing remote-only clients retain their configured server and jobs.
- Chrome 0.3.0 requires backend 0.3.0 even for ordinary remote work; upgrade the backend first. Standalone browser/server transitions also require the context/authority and portable transfer capabilities advertised by this release. Old servers cannot participate in the new transfer protocol.
- Portable ZIP format 2 / financial schema 1 carries financial facts, receipts, tombstones and lineage. Runtime credentials and physical server identity are excluded. Used databases are not merged.
- Legacy format-1 SQLite archives remain accepted through the legacy recovery route on an empty backend. Convert by restoring on an isolated backend and exporting format 2; Chrome cannot directly restore SQLite.
- The Python SDK remains hash-pinned to PyPI zenmoney-python 0.7.0. No sibling SDK source or private package token is needed.

## Deployment and upgrade

Use the image-only deployment procedure in [0.2.1 deployment](container-release-0.2.1.md), using the 0.3.0 image from the successful publication run. Before upgrade, stop writes and retain a verified private backup and persistent data volume. Never start an independent copy of the same financial database as a second writer.

For local verification, `scripts/build-image.sh linux/arm64` or `linux/amd64` builds the runtime image. `scripts/test-server-image.sh` uses its own disposable Compose project, fake API token and volume, without a ZenMoney token. It checks non-root/read-only execution, health/auth, persistence through recreation, bootstrap file permissions, architecture, SDK pin and OCI revision. Production Compose projects are not part of this test.

A migration handoff pauses the shared server for every client. Restore only after retiring former writers; an archive cannot prevent an independently copied writer from running. After explicit activation, the next user-requested synchronization releases the first-sync hold. Backup acknowledgement and restore activation are separate actions.

## Verification

The complete isolated backend suite passed 826 tests before the version-only metadata change. Candidate runtime image-only checks passed on linux/arm64 and linux/amd64. Both runs used disposable volumes and no financial network access. Browser/native SQLite roundtrips and authority scenarios are recorded in the Chrome repository's `docs/local-mode/release-acceptance.md`.

Independent review checked context gates, authority transitions, exact-request withdrawal, atomic activation and legacy API compatibility without remaining findings. This is bounded static and synthetic evidence. No new live ZenMoney writes or production migrations are part of release verification.

## Registry and deployment verification

The release uses the existing `publish-image.yml` workflow from main: source CI,
multi-architecture build and push, then anonymous image-only acceptance on AMD64
and ARM64. The version tag is never overwritten. Inspect the published reference
and record its immutable manifest digest before deploying:

```sh
docker buildx imagetools inspect ghcr.io/vaslv/zenimport-backend:0.3.0
docker pull ghcr.io/vaslv/zenimport-backend:0.3.0
```

Use that digest in `ZENIMPORT_IMAGE` and follow [server deployment](server-deployment.md).
The repository's Actions run records the exact source revision, manifest digest
and native acceptance results. A deployment must retain the persistent volume,
server credentials and delivery journal; publishing an image does not upgrade a
running service automatically.
