# Independent zenmoney-python package

Pinned **zenmoney-python==0.7.0**, import `zenmoney_api`.
Project: https://github.com/vaslv/zenmoney-python, MIT.
The wheel contains no backend code; SDK sources are not copied here.

Before the first GitHub release, the wheel was built from a verified local SDK
commit. `SHA256SUMS` identifies its bytes; `requirements-sdk.lock.txt` pins name,
version, and SHA256. Docker validates the manifest first, installs the package
without an index/dependencies using `--require-hashes`, and runs pip check.
Its dependencies are pinned separately in requirements.runtime.lock.txt; ordinary
builds need no neighboring checkout. `pyproject.toml` and a test check the same
canonical name/version; the image contains no old zenmoney-api-client.
This is a package dependency; vendor is its cache.

After the first SDK release, download the wheel and SHA256SUMS from GitHub Release,
verify the digest, and run from the backend root:

```sh
python3 scripts/update_sdk.py --wheel /path/zenmoney_python-0.7.0-py3-none-any.whl --sha256 VERIFIED_DIGEST
./scripts/test-platforms.sh
```

Updating makes no network calls. The script refuses to change the cache if SHA256,
name, or the dependency version in pyproject.toml differs. To adopt another version,
explicitly change the pyproject.toml pin first, then install the verified wheel.
After updating, run Docker checks and commit wheel/manifest/lock together.
Do not install the SDK from a working branch or a neighboring PYTHONPATH.

A GitHub repository is not PyPI. A future index-based installation requires a
separate PyPI release and retained pin/digest. Current builds do not depend on
availability of an unpublished package on the internet. Never replace an already
published artifact of the same version with a differently hashed rebuild.

The current prerelease 0.7.0 wheel was obtained after validating SDK commit `ba17ce3`
(preserving original history and the initial GitHub license). SDK Docker checks:
260 tests, strict mypy, 99% coverage; renaming did not change the runtime API.
