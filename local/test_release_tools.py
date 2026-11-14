#!/usr/bin/env python3
"""Offline regression check: code drift cannot be hidden by a new snapshot."""
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import sys
import zipfile

here = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('postcheck', here / 'lampac-postupdate-check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
with tempfile.TemporaryDirectory() as folder:
    root = pathlib.Path(folder)
    app = root / 'app'
    release = root / 'release'
    app.mkdir(); release.mkdir()
    check.ROOT = str(app); check.RELEASE = str(release); check.BASE = str(root / 'baseline')
    target = app / 'module/Online/plugin.js'
    target.parent.mkdir(parents=True)
    target.write_text('verified code')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (release / 'overlay.sha256').write_text(f'{digest}  module/Online/plugin.js\n')
    (release / 'version.txt').write_text('custom-test')
    (app / 'version.txt').write_text('custom-test')
    (app / 'excludes.conf').write_text('private.json\noptional.json\n')
    (app / 'private.json').write_text('{"private":"fixture"}')
    (app / 'init.conf').write_text('{"LampaWeb":{"cssReplace":{"\\\\z":"fixture"}}}')
    check.snapshot()
    assert not any(level == 'FAIL' for level, _ in check.out), check.out
    baseline = (root / 'baseline/meta.json').read_bytes()
    target.write_text('overwritten by stock release')
    check.out.clear(); check.verify_release()
    assert any(level == 'FAIL' for level, _ in check.out)
    check.out.clear(); check.snapshot()
    assert any('snapshot refused' in msg for _, msg in check.out)
    assert (root / 'baseline/meta.json').read_bytes() == baseline
    target.write_text('verified code')
    (app / 'version.txt').write_text('stock-test')
    check.out.clear(); check.verify_release()
    assert any('version differs' in msg for _, msg in check.out)
    for path in ('../private.json', 'module/../private.json', '/etc/passwd', 'module//bad'):
        (release / 'overlay.sha256').write_text(f'{digest}  {path}\n')
        check.out.clear(); assert check.release_hashes() == {}
    (release / 'overlay.sha256').write_text(f'{digest}  module/Online/plugin.js\n' * 2)
    check.out.clear(); assert check.release_hashes() == {}
    assert 'LampaWeb.cssReplace' in check.conf_values()
    # Exercise the installer's actual preflight without executing its service/config writes.
    script = (here / 'deploy-host.sh').read_text()
    verifier = script.split('python3 - "$work" <<\'PY\'\n', 1)[1].split('\nPY', 1)[0]
    stage = root / 'download'; stage.mkdir()
    archive = stage / 'app.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('Core.dll', b'fixture core')
        z.writestr('module/Online/plugin.js', b'verified code')
    manifest = stage / 'overlay.sha256'
    manifest.write_text(f'{digest}  module/Online/plugin.js\n')
    info = {'zip_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'overlay_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(), 'overlay_files': '1'}
    (stage / 'build-info.txt').write_text('\n'.join(f'{k}={v}' for k, v in info.items()))
    def preflight():
        return subprocess.run(['python3', '-c', verifier, str(stage)], capture_output=True).returncode
    assert preflight() == 0
    manifest.write_text(manifest.read_text() + '\n')
    assert preflight() != 0
    manifest.write_text(f'{digest}  module/Online/plugin.js\n')
    archive.write_bytes(archive.read_bytes() + b'tampered')
    assert preflight() != 0
    # A local patch must be bound to the original release and its original file hash.
    (app / 'version.txt').write_text('custom-test')
    base_manifest = f'{digest}  module/Online/plugin.js\n'
    (release / 'overlay.sha256').write_text(base_manifest)
    target.write_text('reviewed local patch')
    replacement = hashlib.sha256(target.read_bytes()).hexdigest()
    ledger = {'release_manifest_sha256': hashlib.sha256(base_manifest.encode()).hexdigest(),
              'files': {'module/Online/plugin.js': {'base_sha256': digest, 'sha256': replacement}}}
    (release / 'overrides.json').write_text(json.dumps(ledger))
    check.out.clear(); check.verify_release()
    assert not any(level == 'FAIL' for level, _ in check.out), check.out
    assert (release / 'overlay.sha256').read_text() == base_manifest
    target.write_text('unrecorded drift')
    check.out.clear(); check.verify_release()
    assert any(level == 'FAIL' for level, _ in check.out)
    ledger['files']['module/Online/plugin.js']['base_sha256'] = '0' * 64
    (release / 'overrides.json').write_text(json.dumps(ledger))
    check.out.clear(); assert check.release_hashes() == {}
    ledger['files']['module/Online/plugin.js']['base_sha256'] = digest
    ledger['release_manifest_sha256'] = '0' * 64
    (release / 'overrides.json').write_text(json.dumps(ledger))
    check.out.clear(); assert check.release_hashes() == {}
    (release / 'overrides.json').write_text('[]')
    check.out.clear(); assert check.release_hashes() == {}
for name in ('deploy-host.sh', 'build-release.sh'):
    subprocess.run(['bash', '-n', str(here / name)], check=True)
print('PASS: manifest drift, snapshot refusal, version mismatch, path validation, CSS tracking, archive/manifest tampering and shell syntax')

subprocess.run([sys.executable, str(here / 'test_deploy.py')], check=True)
