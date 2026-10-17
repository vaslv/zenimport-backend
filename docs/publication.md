# Publication sequence for the three projects

1. https://github.com/vaslv/zenmoney-python: independent **zenmoney-python 0.7.0** package, import `zenmoney_api`, MIT. Prepared wheel/sdist/SHA256SUMS and CI belong to the SDK. Publish its commits and first verified GitHub Release first.
2. https://github.com/vaslv/zenimport-backend: backend 0.1.0, MIT. Depends on the separate SDK package pinned by exact version/digest; includes Compose deployment from a prebuilt image without an SDK checkout. A local wheel is used before SDK release.
3. https://github.com/vaslv/zenimport-chrome: MV3 0.1.9, MIT, with PRIVACY.md, reproducible ZIP, and CI. Users enter connection settings in the popup.

## Already prepared locally

- Origin remotes point to the created repositories; initial MIT licenses and GitHub history are preserved by ordinary local merges, without force/rewrite.
- SDK/Chrome/backend CI runs checks; workflows do not push, create tags, automatically publish to PyPI/GHCR/Chrome Web Store, or run live financial tests.
- The backend SDK pin agrees across pyproject, wheel, SHA256, and requirements-sdk.lock.txt. pip verifies the digest and never searches an unpublished index.
- Docker runtime contains only the service/SDK/minimal configuration helper; .env, financial history, SQLite, tests, browser, and live runner are excluded.
- Image-only compose.server.yaml keeps SQLite in an explicitly named persistent volume and exposes HTTP only on loopback. HTTPS setup is documented separately.

## After separate publication permission

Follow `ZenMoneyAPI/docs/publication.md` for the SDK. At preparation time, GitHub contains only the initial license; local preparation itself sends nothing there. After SDK release, download its wheel/manifest and update the cache with `scripts/update_sdk.py --wheel ... --sha256 ...`, then rerun backend checks. The pyproject version and digest must match the published package; ordinary backend builds never access a neighboring SDK project.

Check that origin/main is unchanged, then separately obtain permission to push backend commits without force and wait for successful CI. Git tag creation/pushing require separate approval. The image version must correspond to the chosen service tag/commit.

GHCR publication requires GitHub Packages access, repository `ghcr.io/vaslv/zenimport-backend`, and separate permission for registry push. Prefer short-lived CI credentials/protected environments; never store registry tokens in code/Dockerfile/build args. After authorization, a multiplatform image can be built from the verified commit:

```sh
# Only after separate registry push permission and GHCR login setup:
docker buildx build --platform linux/amd64,linux/arm64 --target final   --build-arg VCS_REF=VERIFIED_COMMIT   --build-arg SOURCE_URL=https://github.com/vaslv/zenimport-backend   --tag ghcr.io/vaslv/zenimport-backend:0.1.0 --push .
```

This example has not been executed. Publishing creates the multiplatform registry manifest; both executable images are validated locally and separately. After pushing, verify manifest/digest and install **by digest**, not latest. Configure public GHCR package visibility for open downloads. A public GitHub repository does not itself make its container package public. On the server, verify HTTPS, restoration of a consistent database, and one repeated synchronization.

For Chrome, after separate push/tag/Release approval, attach only the verified ZIP, its SHA256, and privacy description. Store publication is not separately planned.

## Limitations

CI files were checked locally but had not yet run on GitHub: push was not authorized. Local build readiness does not mean the SDK/image is published or an external server configured. This stage uses no live production transactions. Secrets, .local, .env, server databases, and private evidence are excluded from Git, Docker context, Python archives, and the extension ZIP.
