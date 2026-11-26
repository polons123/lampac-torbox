"""Exercise the protected updater with the pinned upstream zip and optional unpatched theme."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

repo = Path(__file__).resolve().parents[2]
tree = '6fcdfab624f98ac0f7a91244e3d3b637f104a8c2'
parser = argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
parser.add_argument('--theme', type=Path)
args = parser.parse_args()
archive = args.archive.resolve()

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    package = root / 'module/LampaWeb/frontend'
    shutil.copytree(repo / 'Modules/LampaWeb/frontend', package)
    for patch in (repo / 'local/frontend-patches').glob('*.patch'):
        shutil.copy2(patch, package)
    live = root / 'wwwroot/lampa-main'
    live.mkdir(parents=True)
    (live / 'tree').write_text('previous')
    (live / 'user-file').write_text('keep')

    def call(action, *files, revision=tree, expect=0):
        process = subprocess.run([sys.executable, '-B', package / 'install.py', action, 'yumata/lampa',
                                  revision, *files], cwd=root, capture_output=True)
        assert process.returncode == expect, process.stderr.decode()
        return process.stderr

    if args.theme:
        theme = root / 'wwwroot/plugins/new_view/new_view.js'
        theme.parent.mkdir(parents=True)
        shutil.copy2(args.theme, theme)
    call('check', expect=10)
    if args.theme:
        assert hashlib.sha256(theme.read_bytes()).hexdigest() == 'dd2be138924687bd471b82da4f3f4d2d50561133071e1185ac7bff79bfa3ff0e'
    bad = root / 'bad.zip'
    with zipfile.ZipFile(bad, 'w') as output:
        for name in ('app.min.js', 'index.html'):
            output.writestr(f'lampa-{tree}/{name}', 'incompatible template')
    call('install', bad, expect=1)
    assert (live / 'tree').read_text() == 'previous' and not (live / 'app.min.js').exists()
    call('install', archive)
    assert (live / 'tree').read_text() == tree and (live / 'user-file').read_text() == 'keep'
    assert hashlib.sha256((live / 'app.min.js').read_bytes()).hexdigest() == 'c5eeaeeeb2ea9ba4135571682eed78b483a1a599d56a076965ae04caebac2ac3'
    assert hashlib.sha256((live / 'index.html').read_bytes()).hexdigest() == 'ba4bb5580bbd9bf6d90b5a13847286a40ad3457e11c4c8eff8a593be37da4b9f'
    inode = live.stat().st_ino
    call('check')
    call('check', revision='unknown', expect=1)
    call('install', bad, expect=1)
    assert live.stat().st_ino == inode
    with zipfile.ZipFile(archive) as source:
        for name in ('app.min.js', 'index.html'):
            (live / name).write_bytes(source.read(f'lampa-{tree}/{name}'))
    call('check')
    call('check')
    if args.theme:
        theme.write_text('unknown New View')
        assert b'Unsupported new_view.js' in call('check')
        assert theme.read_text() == 'unknown New View'
    print('PASS exact outputs, independent New View, old and unknown trees, preserved assets, failed staging, in-place recovery and idempotence')
