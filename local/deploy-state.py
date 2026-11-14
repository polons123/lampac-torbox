#!/usr/bin/env python3
"""Validate deployments and keep a reversible host snapshot."""
import json
import os
import pwd
import stat
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def prepare(root, work, user):
    """Validate configuration; preserve the existing TorBox integration and data."""
    config = root / 'init.conf'
    if not config.is_file() or (root / 'init.yaml').exists():
        raise ValueError('deployment requires init.conf in strict JSON format')
    data = json.loads(config.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('LampaWeb', {}), dict):
        raise ValueError('invalid init.conf structure')
    if not isinstance(data.get('LampaWeb', {}).get('customPlugins', []), list):
        raise ValueError('customPlugins must be a list')
    code_directories(root, work)
    if not accessible([root], user)[0]:
        raise ValueError('service user cannot traverse installation root: ' + str(root))


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def accessible(paths, user):
    account = pwd.getpwnam(str(user))
    identity = {}
    if account.pw_uid != os.geteuid():
        identity = dict(user=account.pw_uid, group=account.pw_gid,
                        extra_groups=os.getgrouplist(account.pw_name, account.pw_gid))
    probe = 'import json,os,sys;print(json.dumps([os.access(p,os.R_OK|os.X_OK) for p in json.load(sys.stdin)]))'
    result = subprocess.run([sys.executable, '-c', probe], input=json.dumps(list(map(str, paths))),
                            text=True, capture_output=True, check=True, **identity)
    return json.loads(result.stdout)


def code_directories(root, work):
    directories = set()
    for line in (work / 'overlay.sha256').read_text().splitlines():
        name = line.split(None, 1)[1]
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('mods', 'module', 'wwwroot', 'release-tools'):
            raise ValueError('invalid overlay path: ' + name)
        directory = root / relative.parent
        while directory != root:
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise ValueError('overlay directory must be a real directory: ' + str(directory))
            directories.add(directory)
            directory = directory.parent
    return sorted(directories, key=lambda path: (len(path.parts), str(path)))


def directories(root, work, user):
    paths = code_directories(root, work)
    account = pwd.getpwnam(str(user))
    for path in paths:
        if not path.exists():
            path.mkdir(mode=0o755)
            os.chmod(path, 0o755)
            os.chown(path, account.pw_uid, account.pw_gid)
    for path, ok in zip(paths, accessible(paths, user)):
        if not ok:
            # Repair only managed code directories; keep private modes and the root intact.
            os.chown(path, account.pw_uid, account.pw_gid)
            os.chmod(path, stat.S_IMODE(path.stat().st_mode) | 0o500)
    if not all(accessible(paths, user)):
        raise ValueError('service user cannot traverse overlay directories')


def disk_space(backup, work, paths):
    """Budget by filesystem, using bytes available to unprivileged SQLite writers."""
    required = {}
    reserve = 512 * 1024 * 1024
    def add(destination, size):
        while not destination.exists():
            destination = destination.parent
        device = destination.stat().st_dev
        entry = required.setdefault(device, [destination, reserve])
        entry[1] += size
    def size(path):
        if not path.exists() and not path.is_symlink():
            return 0
        st = path.lstat()
        total = max(st.st_size, st.st_blocks * 512, 4096)
        if path.is_dir() and not path.is_symlink():
            total += sum(size(child) for child in path.iterdir())
        return total
    add(backup, sum(size(path) for path in paths))
    add(work, 0)
    archive = work / 'app.zip'
    if archive.exists():
        with zipfile.ZipFile(archive) as z:
            expanded = sum(max(4096, ((i.file_size + 4095) // 4096) * 4096) for i in z.infolist())
        # Installer downloads and extracts a second copy under /tmp; it also grows ROOT.
        add(work, expanded if not (work / 'app').exists() else 0)
        add(Path('/tmp'), expanded + archive.stat().st_size)
        add(paths[0], expanded)
    for destination, needed in required.values():
        free = shutil.disk_usage(destination).free
        if free <= needed:
            raise ValueError(f'insufficient space on {destination}: need {needed} bytes, available {free}; no backups deleted')
    destination, needed = required[work.stat().st_dev]
    return shutil.disk_usage(destination).free - needed


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
        prepare(Path(args[0]), Path(args[1]), args[2])
    elif action == 'directories':
        directories(Path(args[0]), Path(args[1]), args[2])
    elif action == 'space':
        print(disk_space(paths[0], paths[1], paths[2:]))
    elif action == 'snapshot':
        snapshot(paths[0], paths[1:])
    elif action == 'restore':
        restore(*paths)
    else:
        raise ValueError('unknown action')
