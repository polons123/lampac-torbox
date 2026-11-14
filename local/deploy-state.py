#!/usr/bin/env python3
"""Prepare the legacy TorBox migration and keep a reversible host snapshot."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

LEGACY = ('mods/TorBox', 'wwwroot/plugins/torbox', 'torbox-private.json',
          'torbox-settings.json', 'database/torbox-library.json', 'database/torbox-series.json')
PLUGIN_PATHS = {'/torbox.js', '/plugins/torbox/torbox-cache-refresh.js'}
EXCLUDES = set(LEGACY) | {'mods/TorBox/', 'mods/TorBox/**', 'mods/TorBox/***',
                         'wwwroot/plugins/torbox/', 'wwwroot/plugins/torbox/**'}
MARKERS = ((b'var torboxVoiceMenu = null;', b'torbox_voice_options'),
           (b'window.lampac_torbox_loaded', b'window.lampac_torbox_headers'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def known_plugin(item):
    url = item.get('url', '') if isinstance(item, dict) else item
    if not isinstance(url, str):
        raise ValueError('customPlugins must contain URLs or objects with a URL')
    return urlsplit(url).path in PLUGIN_PATHS


def prepare(root, work, marker):
    plan = {'remove': [], 'writes': {}}
    legacy = [name for name in LEGACY if (root / name).exists() or (root / name).is_symlink()]
    if marker.exists() and not legacy:
        (work / 'migration.json').write_text(json.dumps(plan))
        return
    config = root / 'init.conf'
    if not config.is_file() or (root / 'init.yaml').exists():
        raise ValueError('migration requires init.conf in strict JSON format; review YAML manually')
    data = json.loads(config.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('LampaWeb', {}), dict):
        raise ValueError('invalid init.conf structure')
    web = data.get('LampaWeb', {})
    plugins = web.get('customPlugins', [])
    if not isinstance(plugins, list):
        raise ValueError('customPlugins must be a list')
    kept = [item for item in plugins if not known_plugin(item)]
    changed = 'TorBox' in data or kept != plugins
    data.pop('TorBox', None)
    if kept != plugins:
        web['customPlugins'] = kept
    replacements = {}
    if changed:
        replacements['init.conf'] = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()
    excludes = root / 'excludes.conf'
    if excludes.exists():
        old = excludes.read_bytes()
        new = b''.join(line for line in old.splitlines(keepends=True)
                       if line.decode().strip().removeprefix('/') not in EXCLUDES)
        if old != new:
            replacements['excludes.conf'] = new
    plan['remove'] = legacy
    # Only a detected old installation requires one scan, before downtime.
    if legacy or replacements:
        for path in (root / 'cache/static').rglob('*.js'):
            if (path.is_symlink() or not path.is_file() or path.stat().st_size > 3000000):
                continue
            content = path.read_bytes()
            if any(all(marker in content for marker in group) for group in MARKERS):
                plan['remove'].append(str(path.relative_to(root)))
    for index, (name, content) in enumerate(replacements.items()):
        target = work / f'config-{index}'
        target.write_bytes(content)
        plan['writes'][name] = {'before': sha(root / name), 'prepared': target.name}
    (work / 'migration.json').write_text(json.dumps(plan))


def atomic_write(path, content):
    stat = path.stat()
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, stat.st_mode & 0o777)
        os.chown(name, stat.st_uid, stat.st_gid)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def apply(root, work):
    plan = json.loads((work / 'migration.json').read_text())
    for name, edit in plan['writes'].items():
        if sha(root / name) != edit['before']:
            raise ValueError('configuration changed since preflight: ' + name)
    for name, edit in plan['writes'].items():
        atomic_write(root / name, (work / edit['prepared']).read_bytes())
    for name in plan['remove']:
        remove(root / name)


def snapshot(backup, paths):
    if any(backup.resolve().is_relative_to(path.resolve()) for path in paths):
        raise ValueError('backup must be outside all saved paths')
    records = []
    for index, path in enumerate(paths):
        saved = backup / str(index)
        exists = path.exists() or path.is_symlink()
        records.append({'path': str(path), 'exists': exists, 'saved': str(index)})
        if exists:
            if path.is_dir() and not path.is_symlink():
                saved.mkdir()
                subprocess.run(['rsync', '-a', str(path) + '/', str(saved) + '/'], check=True)
            else:
                shutil.copy2(path, saved, follow_symlinks=False)
                stat = path.lstat()
                os.chown(saved, stat.st_uid, stat.st_gid, follow_symlinks=False)
    (backup / 'snapshot.json').write_text(json.dumps(records))


def restore(backup):
    for item in json.loads((backup / 'snapshot.json').read_text()):
        path, saved = Path(item['path']), backup / item['saved']
        if not item['exists']:
            remove(path)
        elif saved.is_dir() and not saved.is_symlink():
            if path.is_symlink() or path.is_file():
                remove(path)
            path.mkdir(parents=True, exist_ok=True)
            subprocess.run(['rsync', '-a', '--delete', str(saved) + '/', str(path) + '/'], check=True)
        else:
            remove(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, path, follow_symlinks=False)
            stat = saved.lstat()
            os.chown(path, stat.st_uid, stat.st_gid, follow_symlinks=False)


if __name__ == '__main__':
    action, *args = sys.argv[1:]
    paths = list(map(Path, args))
    if action == 'prepare':
        prepare(*paths)
    elif action == 'apply':
        apply(*paths)
    elif action == 'snapshot':
        snapshot(paths[0], paths[1:])
    elif action == 'restore':
        restore(*paths)
    else:
        raise ValueError('unknown action')
