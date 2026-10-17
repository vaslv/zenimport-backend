# Backend 0.1.0 — published container acceptance

Date: 2026-10-17. Registry: GitHub Container Registry. License: MIT.

- [Source repository](https://github.com/vaslv/zenimport-backend).
- [Public image package](https://github.com/vaslv/zenimport-backend/pkgs/container/zenimport-backend).
- Tag: `ghcr.io/vaslv/zenimport-backend:0.1.0`.
- Immutable image reference: `ghcr.io/vaslv/zenimport-backend@sha256:77747ac1ddf90ea12ebcfd7d4202f67660bc3375a5ffafa508bfa82392fdfd0b`.
- Runtime source commit: `60e5d24c6d4398c76f0ee9a0535e4bcd4a5f0d99`.
- Verification harness commit: `b18af65fbd3f07c3e9db869cb05bdc1c8cbbb968`.
- Supported runtime architectures: Linux AMD64 and ARM64.
- SDK: public PyPI `zenmoney-python==0.7.0`, wheel SHA256
  `529cd89ce941ddfabbe8a08c4ab5c587389680cda0e0b9b1f1abaf3e5f2734b7`.

## Executed acceptance

[Native backend CI](https://github.com/vaslv/zenimport-backend/actions/runs/39029767213)
completed successfully on `ubuntu-24.04` and `ubuntu-24.04-arm`: 492 tests on each
architecture, followed by HTTP/auth, persistence/recreation, capture/delivery
recovery, SIGKILL after a fake API commit, graceful shutdown, and snapshot restore.

[Published-image verification](https://github.com/vaslv/zenimport-backend/actions/runs/39029794475)
completed successfully on both native architectures. Each fresh runner pulled the
same immutable digest anonymously and checked private configuration creation/no
overwrite, service UID 10001/read-only root, architecture, SDK, loopback HTTP,
OCI source revision, and named-volume database preservation after recreation.
This workflow never publishes, retags, or rebuilds the artifact.

Local checks independently downloaded the public registry manifest without account
credentials, verified its SHA256 and both platform descriptors, pulled both images
with an empty Docker credential configuration, and executed each installed SDK.
Both full image-only deployment checks also passed locally using the published
digest. ARM64 was native and AMD64 emulated on the development Mac.

All financial checks used synthetic data and fake SDK responses. Production
containers, the production SQLite volume, real bank history, and real ZenMoney
transactions were not changed. No Git tags were created, and SDK/Chrome publication
was not part of this backend task.

## Initial CI issues and retained evidence

The initial image-only CI check exposed a Linux host/runtime UID mismatch while
reading a private mode-600 bind-mounted configuration file. Commit `60e5d24` fixed
the test harness to read it as the host file owner without weakening permissions.

[The first image publication](https://github.com/vaslv/zenimport-backend/actions/runs/39028734965)
uploaded this artifact successfully after both pre-publication matrices passed,
but its post-publication ARM check timed out under QEMU. A parallel emulated ARM
run also exceeded subprocess-exit deadlines. These failed runs remain failed;
no assertions were disabled and the published version was not overwritten.

Commit `b18af65` moved execution checks to native GitHub ARM64/AMD64 runners and
added a separately repeatable immutable-image verification workflow. The successful
runs above validate the original artifact from `60e5d24` with that corrected
verification harness. QEMU is retained only for multiplatform release builds.
Existing Starlette and checkout-action runtime deprecation warnings are non-fatal.

## Install on a new server

The server needs Docker Engine and Compose. No host Python, SDK checkout, PyPI
upload token, or GHCR login is required. In a new deployment directory:

```sh
git clone https://github.com/vaslv/zenimport-backend.git
cd zenimport-backend
git checkout 60e5d24c6d4398c76f0ee9a0535e4bcd4a5f0d99
export ZENIMPORT_IMAGE=ghcr.io/vaslv/zenimport-backend@sha256:77747ac1ddf90ea12ebcfd7d4202f67660bc3375a5ffafa508bfa82392fdfd0b
docker pull "$ZENIMPORT_IMAGE"
./scripts/init-server.sh
```

The helper creates a mode-600 `.env` containing a generated backend `API_TOKEN`,
image reference, and explicit persistent-volume name. Edit `.env` on the server
and set `ZENMONEY_TOKEN`; copy only `API_TOKEN` into the Chrome extension. Never
send either token to GitHub, PyPI, Docker build arguments, or public logs.
Then start:

```sh
docker compose -f compose.server.yaml up -d --wait backend
curl --fail http://127.0.0.1:18792/health
```

Configure HTTPS using the [server guide](server-deployment.md); port 18792 stays
bound to loopback. Actual remote DNS/TLS/server deployment has not been performed
as part of image publication.

For a move from an existing backend, stop its worker and transfer a consistent
SQLite snapshot **including the delivery journal** into the selected server volume.
Do not run old and new workers simultaneously or discard the original journal
just because the mirror can be downloaded again. See [operations.md](operations.md)
and [migration-lab.md](migration-lab.md). Ordinary image updates keep the same named
volume and do not recreate the financial history.
