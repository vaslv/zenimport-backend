# Public PyPI SDK dependency

The backend installs `zenmoney-python==0.7.0` (import `zenmoney_api`) from
https://pypi.org/project/zenmoney-python/0.7.0/. The MIT SDK remains a separate
project: https://github.com/vaslv/zenmoney-python.

`requirements.sdk.lock.txt` pins the exact version and the public wheel SHA256:
`529cd89ce941ddfabbe8a08c4ab5c587389680cda0e0b9b1f1abaf3e5f2734b7`.
This matches both PyPI metadata and the original GitHub Release artifact.

Docker installs runtime dependencies from their existing exact lock first, then
installs the SDK from `https://pypi.org/simple` with `--require-hashes`,
`--only-binary=:all:`, and `--no-deps`, and finally runs `pip check`.
The SDK download is public. Never add a PyPI upload token to `.env`, Docker build
arguments, an image, or CI secrets for dependency installation.
A fresh build needs internet access to PyPI and files.pythonhosted.org;
ordinary Docker layer caching can reuse an already verified dependency layer.

The old checked-in prerelease wheel/cache is removed. No SDK checkout or
neighboring project is used by builds. The public 0.7.0 wheel has the same runtime
module bytes as the previous prerelease cache; distribution metadata differs.
This change does not migrate SQLite, alter matching/delivery rules, or resubmit
transactions. The delivery journal and persistent volume must still be preserved.

## Upgrade the SDK deliberately

1. Choose an already published SDK release and inspect its PyPI metadata/README.
   Confirm the wheel is not yanked, supports Python 3.12, and has the expected
   `zenmoney-python` distribution name.
2. Update the exact dependency in `pyproject.toml` if the version changes.
3. Download its wheel to a private/temporary directory with pip (Python 3.12+):

```sh
python -m pip download --index-url https://pypi.org/simple --only-binary=:all: --no-deps --dest .local/sdk-download zenmoney-python==0.7.0
```

4. Compare the wheel SHA256 with the official PyPI version JSON and release
   manifest. Use the verified digest to update the lock; the command neither
   executes nor copies the wheel into the backend:

```sh
python3 scripts/update_sdk.py --wheel .local/sdk-download/zenmoney_python-0.7.0-py3-none-any.whl --sha256 529cd89ce941ddfabbe8a08c4ab5c587389680cda0e0b9b1f1abaf3e5f2734b7
```

5. Review SDK dependency changes against `requirements.runtime.lock.txt` and
   update the installed-SDK version expectation if adopting a new version.
   Run `scripts/test-platforms.sh` and the two image-only deployment checks.
   Commit the lock, dependency declaration, and any required integration changes.

`update_sdk.py` validates the digest, distribution metadata, filename, and declared
version before replacing the lock. Docker independently checks the downloaded
bytes. A new hash must never be used to rewrite an already published SDK version.

References: [pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/),
[deployment](server-deployment.md), [publication](publication.md).
