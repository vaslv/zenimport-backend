"""Executable architecture evidence, not merely a successful image build."""
import os
import platform
import sys

expected = {'linux/arm64': 'aarch64', 'linux/amd64': 'x86_64'}[os.environ['TEST_PLATFORM']]
assert platform.machine() == expected, (platform.machine(), expected)
assert os.getuid() == 10001 and os.getgid() == 10001
assert os.geteuid() != 0
assert not os.path.exists('/app/.env')
try:
    open('/app/test-readonly-marker', 'x')
except OSError:
    pass
else:
    raise AssertionError('Runtime root filesystem is writable')
print(f'PASS: executed {platform.system()}/{platform.machine()}, CPython {platform.python_version()}, UID/GID 10001, read-only root')
