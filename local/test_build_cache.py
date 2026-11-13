#!/usr/bin/env python3
"""Run the real builder on a tiny fixture; curl/git are offline test doubles."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

source = Path(__file__).resolve().parent / 'build-release.sh'
with tempfile.TemporaryDirectory(prefix='lampac-build-cache-') as folder:
    root = Path(folder)
    repo = root / 'repo'
    (repo / 'local').mkdir(parents=True)
    shutil.copy2(source, repo / 'local/build-release.sh')
    (repo / 'local/base').write_text('fixture-base\n')
    archive = root / 'fixture.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('Core.dll', b'fixture, not a real binary')
    binaries = root / 'bin'; binaries.mkdir()
    (binaries / 'git').write_text('''#!/bin/sh
case "$*" in
  *"--verify"*|*"diff --name-only"*) exit 0 ;;
  *"rev-parse"*) printf '%040d\\n' 1 ;;
  *) exit 99 ;;
esac
''')
    (binaries / 'curl').write_text('''#!/usr/bin/env python3
import os, pathlib, shutil, sys, time
if os.environ.get('FIXTURE_OFFLINE'): sys.exit(22)
with open(os.environ['FIXTURE_CALLS'], 'a') as log: log.write(sys.argv[-1]+'\\n')
time.sleep(.1)
target=pathlib.Path(sys.argv[sys.argv.index('-o')+1])
if os.environ.get('FIXTURE_FAIL'):
 target.write_bytes(b'partial download'); sys.exit(22)
if os.environ.get('FIXTURE_BAD_ZIP'): target.write_bytes(b'not a zip')
else: shutil.copyfile(os.environ['FIXTURE_ZIP'], target)
''')
    for path in binaries.iterdir(): path.chmod(0o755)
    calls = root / 'calls'
    cache = root / 'cache with spaces'
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
               LAMPAC_BUILD_CACHE=str(cache), FIXTURE_CALLS=str(calls), FIXTURE_ZIP=str(archive))
    env['LAMPAC_UPSTREAM_REPO'] = 'fixture/upstream'
    def run(tag='v1', **overrides):
        return subprocess.run(['bash', str(repo / 'local/build-release.sh'), tag],
                              env=dict(env, **overrides), capture_output=True, text=True)
    def count(): return len(calls.read_text().splitlines())
    first = run(); assert first.returncode == 0, first.stderr
    assert count() == 1
    original = next(cache.glob('*.zip'))
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    second = run(FIXTURE_OFFLINE='1'); assert second.returncode == 0, second.stderr
    assert count() == 1 and 'SHA-256' in second.stdout
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
    original.write_bytes(b'corrupt')
    offline = run(FIXTURE_OFFLINE='1'); assert offline.returncode != 0
    repaired = run(); assert repaired.returncode == 0, repaired.stderr
    assert count() == 2 and hashlib.sha256(original.read_bytes()).hexdigest() == digest
    # Missing checksum is not silently trusted.
    next(cache.glob('*.sha256')).unlink()
    assert run().returncode == 0 and count() == 3
    assert run('v2').returncode == 0 and count() == 4
    assert run(LAMPAC_UPSTREAM_REPO='fixture/other').returncode == 0 and count() == 5
    entries = len(list(cache.glob('*.zip')))
    assert run('failed', FIXTURE_FAIL='1').returncode != 0
    assert len(list(cache.glob('*.zip'))) == entries
    assert run('invalid', FIXTURE_BAD_ZIP='1').returncode != 0
    assert len(list(cache.glob('*.zip'))) == entries
    # Concurrent builds of the same URL download only once.
    before = count()
    other = root / 'other-repo'
    shutil.copytree(repo / 'local', other / 'local')
    processes = [subprocess.Popen(['bash', str(build / 'local/build-release.sh'), 'parallel'],
                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for build in (repo, other)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
    assert count() == before + 1
print('PASS: download once, offline reuse, checksum validation/repair, URL isolation, failed/invalid downloads and concurrent cache access')
