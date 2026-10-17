# B1 acceptance — October 15, 2026

- Repository copied from ZenImport f3b2838 without hardlinks; remote removed, history preserved.
- Docker Engine 29.4.0, Compose 5.1.2, linux/arm64 (Engine reports aarch64), CPython 3.12.15.
- `scripts/test-docker.sh`: successful. 93 tests: 56 B0 models, 35 bootstrap/config/auth/storage, two SDK/runtime composition checks.
- A separate config image creates .env with synthetic configuration in tmpfs as UID 10001 without networking; the file is neither placed in the project/image nor printed.
- Unit/contract containers have no network. Smoke containers use an internal network with no published port.
- Real HTTP: /health 200, /api/v1/info without a token 401, with a synthetic token 200.
- Backend forcibly recreated; health and API work, and the persistent volume's instance_id is unchanged.
- The script removed the test volume/containers; .env settings were not read. The production container and volume were not used.
- Installed vendored SDK 0.6.0, SHA256 b28722f6ee09ea79726cbab64fa769171904b6c9a2e6b825bc2d2c88437e1e58; fake HTTP confirmed the specified UUID, viewed=false, exact Decimal, and absent category.
- Verified rejection without modification of legacy/foreign/future/corrupt databases, preservation on repeated bootstrap, HTTP limits with Content-Length and chunked bodies without it, and no input echo in 422/500 responses.
- Runtime contains no extension/tools, HTML editor, or legacy edit delivery modules. Docker context is allowlisted.

One warning: current Starlette TestClient deprecates httpx usage and recommends httpx2. This is a test adapter warning, not an SDK/runtime error. Dependencies were preserved during extraction; test client migration is a separate task if needed.

AMD64, a live bank, real ZenMoney, and mobile viewed=false display were not tested in B1. Financial schema/migration, history capture, and delivery were not yet implemented. B1 is not acceptance of the entire synchronization flow.
