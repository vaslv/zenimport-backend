# Backend image publication

The SDK is published independently as
[zenmoney-python 0.7.0](https://pypi.org/project/zenmoney-python/0.7.0/) and at
[GitHub Release v0.7.0](https://github.com/vaslv/zenmoney-python/releases/tag/v0.7.0).
The backend downloads this public PyPI package by exact version/SHA256;
see [SDK dependency](sdk-dependency.md). No SDK checkout or upload token is needed.

Backend source: https://github.com/vaslv/zenimport-backend, version 0.1.0, MIT.
Prepared registry target: `ghcr.io/vaslv/zenimport-backend`.
The Chrome project is independent and is not published by this workflow.

## Local verification before publication

```sh
./scripts/test-platforms.sh
./scripts/test-server-image.sh linux/arm64
./scripts/test-server-image.sh linux/amd64
```

These checks use unique disposable databases, synthetic tokens, and fake ZenMoney
responses. They do not read production `.env`, invoke a bank, or send real
financial transactions. ARM64 is native on the development Mac; AMD64 is emulated.
No claim is made that a remote server has been configured by these checks.

Review the complete source diff, clean Git state, lock/hash, workflow syntax,
secret scan, and deployment instructions. Commit locally. Publishing source
commits and creating Git tags each require separate explicit user permission.
A Git tag is not required by the image workflow.

## Manual GHCR workflow

`.github/workflows/ci.yml` runs checks on pushes/PRs or manual/reusable invocation;
it has `contents: read` only and never publishes anything.
`.github/workflows/publish-image.yml` publishes **only when manually dispatched**.
Pushing source commits alone does not publish an image.

After explicit source-push and image-publication authorization:

1. Push the reviewed backend commits without force and check the source revision.
2. In GitHub Actions, select **Publish backend image**, **Run workflow**, branch
   **main**. The source must already be committed to that branch.
3. The reusable verification job tests Linux ARM64/AMD64 and image-only deployment.
4. Only after both pass, the publication job uses the short-lived `GITHUB_TOKEN`
   with `contents: read` and `packages: write`, authenticating through stdin.
   No personal access token or PyPI token is required. Credentials are never
   passed to the Docker build. Registry login is removed in an always-run step.
5. The workflow refuses to replace an existing version image, builds both
   architectures, and pushes tags `0.1.0` and `sha-<full-source-commit>`. No `latest`
   tag or Git tag is created. The OCI source/revision labels identify the source.
6. It checks the registry manifest for both architectures, then pulls the published
   digest and reruns image-only HTTP/auth/persistence checks on both platforms.
   If a post-publication check fails, the image may already exist: do not deploy
   it just because the tag exists. Investigate the failed run; do not overwrite
   that version. The version guard also makes repeated dispatches fail safely.
7. The workflow summary prints `ghcr.io/vaslv/zenimport-backend@sha256:...`.
   Use that exact immutable reference on the server, not a moving tag.

The first GHCR package is private by default even when the repository is public.
In the package settings, change its visibility to **Public** to permit anonymous
server pulls. Then verify the digest/manifest without registry credentials.
The repository source label links the image package to its GitHub repository.
If organization policies restrict `GITHUB_TOKEN` package creation, resolve those
permissions before retrying; never put personal credentials into the Dockerfile.

## Server deployment and moving data

Follow [server-deployment.md](server-deployment.md) and [operations.md](operations.md):
choose the verified digest, preserve the named SQLite volume, generate a private
server `.env`, add the ZenMoney token only on the server, and configure HTTPS.
Do not run old and new delivery workers against the same ZenMoney account together.
When moving an existing installation, transfer a consistent database snapshot and
delivery journal. A fresh mirror alone cannot preserve deletions or arbitrary edits
that only the original journal knew about. See [migration-lab.md](migration-lab.md).

Preparation is not publication. A successful registry run, public anonymous pull,
and a server-specific HTTPS check must be reported separately with actual evidence.
