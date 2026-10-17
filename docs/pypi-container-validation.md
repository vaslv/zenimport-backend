# PyPI dependency and container publication readiness — 2026-10-17

This file records the initial preparation checkpoint. Completed publication and
actual native GitHub acceptance are recorded in
[container-release-0.1.0.md](container-release-0.1.0.md).

## Dependency evidence

- Backend version: 0.1.0; SDK import: `zenmoney_api`.
- Public release: https://pypi.org/project/zenmoney-python/0.7.0/.
- Wheel: `zenmoney_python-0.7.0-py3-none-any.whl`.
- Verified SHA256:
  `529cd89ce941ddfabbe8a08c4ab5c587389680cda0e0b9b1f1abaf3e5f2734b7`.
- The official version JSON, wheel bytes, and original GitHub Release hash agree.
  SDK runtime module bytes match the previously vendored prerelease. Only the
  package acquisition mechanism/distribution metadata changes; no SQLite schema,
  match, prediction, delivery, or synchronization policy changes are introduced.
- Dependency commit: `edb11bf`. The Dockerfile downloads from public PyPI with an
  exact version/hash, binary-only/no-deps installation, then runs `pip check`.
  Vendor binaries/cache are removed. No PyPI token is used or needed by builds.

## Executed local checks

| Check | Result |
|---|---|
| Native Linux ARM64, CPython 3.12.15 | 492 tests passed |
| Emulated Linux AMD64, CPython 3.12.15 | 492 tests passed |
| Both platforms: HTTP/auth and persistence/recreation | Passed |
| Both platforms: SIGKILL after fake API commit, recovery without duplicate writes | Passed |
| Both platforms: in-flight graceful shutdown and snapshot restoration | Passed |
| Both platforms: image-only server deployment | Passed |
| Server bootstrap | Mode 600; existing configuration not overwritten |
| Runtime isolation | UID/GID 10001, read-only root, loopback HTTP, no tests/browser/vendor/.env |
| Image metadata and database preservation | OCI revision verified; same instance after recreation |
| New/manual reusable GitHub workflows | actionlint passed |
| Repository history secret scan | 126 prior commits; no leaks found |
| Prepared changes | Redacted gitleaks scan and git diff --check passed |

The existing Starlette test-client deprecation warning remains. Tests execute on
Docker/OrbStack; AMD64 is emulated, not a physical server benchmark. Logs are kept
privately in `.local/publication-pypi/` and excluded from Git/build context.

An initial ARM64 run passed all unit tests but could not allocate a Docker network:
the predefined IPAM pools were exhausted. Two unused historical `zenimport-check-*`
networks with zero attached containers were removed, then the complete two-platform
suite passed. No broad Docker prune was used. All test projects/volumes were
self-contained and removed by their own cleanup traps.

No production backend restart, real database access, bank session, ZenMoney API
write, or PyPI upload occurred. No SDK/Chrome source changes or publication occur
as part of this backend stage. Review was focused on dependency pinning, build
context isolation, secret boundaries, workflow dispatch/permissions, immutable
version checks, and image-only deployment.

## Publication boundary

Prepared `.github/workflows/publish-image.yml` is manual-only; it reuses the full
platform CI before granting the publication job `packages: write`. It uses the
short-lived GitHub Actions token, creates no Git tags, does not publish `latest`,
refuses to overwrite a published version, and fails closed when an existing-version
probe fails for a reason other than a missing manifest. It publishes AMD64/ARM64,
records the immutable digest, and reruns image-only checks on the pulled image.

Workflow syntax has been checked locally. The actual GHCR workflow/manifest,
anonymous public download, and remote server HTTPS have not been validated at this
checkpoint: preparation does not publish. Source push and registry publication
still require user authorization; see [publication.md](publication.md).

The installed generic `ship` workflow cannot run in full because its required
`review/checklist.md` is absent. A focused manual source review and the executable
checks above were performed instead; no claim is made that the full gstack ship
pipeline passed.
