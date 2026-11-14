#!/usr/bin/env python3
"""Post-update check for a manifest-managed custom Lampac release.

  lampac_postupdate_check.py [check]      state of every protected file, init.conf keys, modules, endpoints
  lampac_postupdate_check.py snapshot     save the current (known good) state as the baseline
  lampac_postupdate_check.py drift        3-way compare with upstream: old release / new release / our files
  lampac_postupdate_check.py apply        put the clean merges made by `drift` into /opt/lampac (with backup)
  add --local to run only here (optional fleet roster: /root/lampac-check-hosts.json)

Data protection comes from excludes.conf; release code hashes come from the installed
overlay manifest. Snapshot refuses code drift instead of blessing it as a new baseline.
Check is read-only. Snapshot and apply write only when explicitly invoked.
"""
import base64, difflib, glob, hashlib, json, os, re, shutil, subprocess, sys, time, urllib.request, zipfile

ROOT = '/opt/lampac'
BASE = '/root/lampac_patch_baseline'
CACHE = '/root/lampac_upstream_cache'
REPO = 'lampac-nextgen/lampac'
PORT = 19092
RELEASE = '/root/lampac-release-state'
# Optional operator-owned fleet roster; never publish infrastructure addresses here.
HOSTS = json.load(open('/root/lampac-check-hosts.json')) if os.path.isfile('/root/lampac-check-hosts.json') else [('local', None)]

# patched file -> substrings that must be present (proves the patch survived)
MARKERS = {
    'mods/TorBox/Controller.cs': ['torbox/metadata', 'RememberFilm'],
    'mods/TorBox/Delete.cs': ['torbox/delete/check', 'operation="delete",all=false', 'deletion_confirmed'],
    'mods/TorBox/Inventory.cs': ['offset=', 'entry.Generation'],
    'mods/TorBox/Online.cs': ['Разрешение: исходное', 'quality=new JObject()'],
    'mods/TorBox/plugin.js': ['Удалить из TorBox', 'confirmed: true'],
    'module/PidTor/Controller.cs': ['local patch: tracker names'],
    'module/PidTor/Model.cs': ['Peers'],
    'module/Online/plugin.js': ['pfApply'],
    'module/Tracks/Controller.cs': ['tracks/stat', 'SemaphoreSlim'],
    'module/Tracks/plugin.js': ['Раздача'],
    'module/Tracks/manifest.json': ['"enable": true'],
    'module/AdminPanel/manifest.json': ['"enable": true'],
    'module/Telemetry/manifest.json': ['"enable": true'],
    'module/AdminPanel/AdminPanelController.cs': ['UnixCreateMode', 'UnixFileMode.UserRead|UnixFileMode.UserWrite'],
}
# init.conf values that carry a fix (compared with the baseline): dotted paths
CONF_KEYS = ['LampaWeb.tree', 'LampaWeb.cssReplace', 'Phantom.m4s', 'Redheadsound.enable', 'Aladin.enable', 'PidTor.enable',
             'PidTor.redapi', 'Tracks.enable']
# endpoint -> accepted codes
ENDPOINTS = {'tracks.js': (200,), 'online.js': (200,), 'adminpanel': (200, 301, 302, 401), 'lite/pidtor': (200, 401, 403)}

out = []


def say(level, msg):
    out.append((level, msg))


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def protected():
    p = f'{ROOT}/excludes.conf'
    if not os.path.exists(p):
        return []
    patterns = [l.strip() for l in open(p, encoding='utf-8') if l.strip() and not l.lstrip().startswith('#')]
    files = []
    for pattern in patterns:
        matches = [path for path in glob.glob(os.path.join(ROOT, pattern), recursive=True) if os.path.isfile(path)]
        files.extend(os.path.relpath(path, ROOT) for path in matches) if matches else files.append(pattern)
    return sorted(set(files))


def version():
    try:
        return open(f'{ROOT}/version.txt').read().strip().lstrip('v')
    except OSError:
        return '?'


def conf_values():
    try:
        d = json.load(open(f'{ROOT}/init.conf', encoding='utf-8-sig'))
    except Exception as e:
        say('FAIL', f'init.conf is not valid JSON: {e}')
        return {}
    res = {}
    for k in CONF_KEYS:
        v = d
        for part in k.split('.'):
            v = v.get(part) if isinstance(v, dict) else None
        res[k] = v
    res['customPlugins'] = sorted(str(x.get('url', x) if isinstance(x, dict) else x) for x in d.get('LampaWeb', {}).get('customPlugins', []))
    return res


def load_base():
    try:
        return json.load(open(f'{BASE}/meta.json'))
    except Exception:
        return None


def release_hashes():
    try:
        rows = open(f'{RELEASE}/overlay.sha256', encoding='utf-8').read().splitlines()
        result = {}
        for row in rows:
            digest, path = row.split(None, 1)
            if not re.fullmatch('[a-f0-9]{64}', digest) or not path.startswith(('mods/', 'module/', 'wwwroot/', 'release-tools/')) or any(p in ('', '.', '..') for p in path.split('/')) or path in result:
                raise ValueError('invalid or duplicate manifest entry')
            result[path] = digest
        if not result:
            raise ValueError('empty manifest')
        overrides_path = f'{RELEASE}/overrides.json'
        if os.path.isfile(overrides_path):
            overrides = json.load(open(overrides_path, encoding='utf-8'))
            if not isinstance(overrides, dict) or overrides.get('release_manifest_sha256') != sha(f'{RELEASE}/overlay.sha256'):
                raise ValueError('local patches belong to a different release')
            patches = overrides.get('files')
            if not isinstance(patches, dict) or not patches:
                raise ValueError('empty local patch ledger')
            for path, patch in patches.items():
                if not isinstance(patch, dict) or path not in result or patch.get('base_sha256') != result[path] or not re.fullmatch('[a-f0-9]{64}', str(patch.get('sha256', ''))):
                    raise ValueError('invalid local patch entry')
                result[path] = patch['sha256']
            say('INFO', f'{len(patches)} recorded local patches over the installed release')
        return result
    except (OSError, ValueError):
        say('FAIL', 'installed release manifest or local patch ledger is missing or invalid')
        return {}


def verify_release():
    hashes = release_hashes()
    for path, digest in hashes.items():
        filename = f'{ROOT}/{path}'
        if not os.path.isfile(filename) or sha(filename) != digest:
            say('FAIL', f'{path}: does not match installed release manifest')
    try:
        expected = open(f'{RELEASE}/version.txt').read().strip()
        if expected != version():
            say('FAIL', 'installed version differs from release manifest version')
    except OSError:
        say('FAIL', 'release manifest version is missing')
    if hashes and not any(level == 'FAIL' for level, _ in out):
        say('OK', f'all {len(hashes)} release overlay hashes match')
    return hashes


# ---------------------------------------------------------------- snapshot
def snapshot():
    hashes = verify_release()
    if any(level == 'FAIL' for level, _ in out):
        say('FAIL', 'snapshot refused: verify the installed release first')
        return
    files = [f for f in protected() if os.path.isfile(f'{ROOT}/{f}')]
    shutil.rmtree(f'{BASE}/files', ignore_errors=True)
    for f in files:
        os.makedirs(os.path.dirname(f'{BASE}/files/{f}'), exist_ok=True)
        shutil.copy2(f'{ROOT}/{f}', f'{BASE}/files/{f}')
    meta = {'time': time.strftime('%F %T'), 'version': version(), 'excludes': protected(),
            'schema': 2, 'release_sha': hashes,
            'sha': {f: sha(f'{ROOT}/{f}') for f in files}, 'conf': conf_values()}
    os.makedirs(BASE, exist_ok=True)
    json.dump(meta, open(f'{BASE}/meta.json', 'w'), indent=1, ensure_ascii=False)
    os.chmod(BASE, 0o700)
    say('OK', f'baseline saved: version {meta["version"]}, {len(files)} files, {len(meta["conf"])} conf values ({BASE})')
    for f in protected():
        if f not in files:
            say('WARN', f'{f} is in excludes.conf but the file does not exist')


# ---------------------------------------------------------------- check
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()


def http(path):
    try:
        r = urllib.request.build_opener(NoRedirect).open(f'http://127.0.0.1:{PORT}/{path}', timeout=15)
        return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception as e:
        return 0, str(e)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def check():
    hashes = verify_release()
    base = load_base()
    ver = version()
    if not base:
        say('WARN', 'no baseline yet: run `snapshot` once while everything works')
    else:
        if ver != base['version']:
            say('WARN', f'Lampac version {base["version"]} -> {ver}: run `drift` to compare patched files with upstream')
        else:
            say('OK', f'version {ver} (same as baseline of {base["time"]})')
        if base.get('schema') != 2:
            say('FAIL', 'legacy baseline: migrate after verified manifest installation')
        if base.get('release_sha') != hashes:
            say('FAIL', 'baseline release manifest differs: review release, then snapshot')
        missing = [e for e in base['excludes'] if e not in protected()]
        say('FAIL' if missing else 'OK', f'excludes.conf lost: {", ".join(missing)}' if missing else 'excludes.conf complete')

    for f in protected():
        p = f'{ROOT}/{f}'
        if not os.path.isfile(p):
            was_present = base and f in base.get('sha', {})
            say('FAIL' if was_present else 'WARN', f'{f}: protected optional file is missing')
            continue
        txt = open(p, encoding='utf-8', errors='replace').read()
        bad = [m for m in MARKERS.get(f, []) if m not in txt]
        if bad:
            say('FAIL', f'{f}: patch marker missing ({", ".join(bad)}): file was overwritten or reverted')
        elif base and f in base['sha'] and sha(p) != base['sha'][f]:
            say('WARN', f'{f}: differs from baseline (edited after snapshot?), markers are fine')
        elif base and f not in base['sha']:
            say('WARN', f'{f}: not in baseline, re-run `snapshot`')
        else:
            say('OK', f'{f}')

    for filename in ('init.conf', 'current.conf'):
        mode = os.stat(f'{ROOT}/{filename}').st_mode & 0o777
        say('OK' if mode == 0o600 else 'FAIL', f'{filename} permissions {mode:04o} (required 0600)')
    mask = sh('systemctl', 'show', '-p', 'UMask', '--value', 'lampac')
    say('OK' if mask == '0077' else 'FAIL', f'lampac UMask {mask} (required 0077)')
    cur = conf_values()
    if base:
        for k, v in cur.items():
            if base['conf'].get(k) != v:
                say('FAIL' if k != 'customPlugins' else 'WARN', f'init.conf {k}: differs from baseline')
    say('OK', f'init.conf keys checked ({len(cur) - 1} values, {len(cur.get("customPlugins", []))} custom plugins)')

    state = sh('systemctl', 'is-active', 'lampac')
    since = sh('systemctl', 'show', '-p', 'ActiveEnterTimestamp', '--value', 'lampac')
    say('OK' if state == 'active' else 'FAIL', f'lampac {state}, started {since}, restarts {sh("systemctl", "show", "-p", "NRestarts", "--value", "lampac")}')
    log = sh('journalctl', '-u', 'lampac', '--since', since, '--no-pager') if since else ''
    errs = [l for l in log.splitlines() if re.search(r'error CS\d+|compilation (failed|error)|Unhandled exception', l, re.I)]
    say('FAIL' if errs else 'OK', f'{len(errs)} compile/unhandled errors since start' + (': ' + errs[0][-140:] if errs else ''))
    for m in ('Online', 'PidTor', 'Tracks', 'AdminPanel', 'Telemetry'):
        if any(f.startswith(f'module/{m}/') for f in hashes):
            if f'loaded module: {m}' not in log:
                say('FAIL', f'module {m} was not loaded at the last start')
    for path, ok in ENDPOINTS.items():
        code, body = http(path)
        extra = ''
        if path == 'online.js' and code == 200 and 'pfApply' not in body:
            code, extra = -1, ' (200 but no filter code: stale disk cache? see memory note: stop lampac, delete cache/static/*online-prestige*)'
        say('OK' if code in ok else 'FAIL', f'/{path} -> {code}{extra}')
    if any(p.startswith('mods/TorBox/') for p in hashes):
        say('OK' if 'loaded module: TorBox' in log else 'FAIL', 'TorBox module loaded at last start')
    code, css = http('lampa-main/css/app.css')
    say('OK' if code == 200 and '.noty { background: #303030 !important; color: #fff !important; }' in css else 'FAIL', 'served notification CSS is dark grey with white text')


# ---------------------------------------------------------------- drift / apply
def zip_for(v):
    os.makedirs(CACHE, exist_ok=True)
    z = f'{CACHE}/lampac-{v}.zip'
    if os.path.exists(z) and zipfile.is_zipfile(z):
        return z
    for tag in (f'v{v}', v):
        url = f'https://github.com/{REPO}/releases/download/{tag}/lampac-nextgen.zip'
        try:
            urllib.request.urlretrieve(url, z + '.part')
            os.replace(z + '.part', z)
            return z
        except Exception:
            continue
    return None


def member(zf, rel):
    names = zf.namelist()
    for n in names:
        if n == rel or n.endswith('/' + rel):
            return zf.read(n)
    return None


def merge3(ours, old, new):
    d = f'{BASE}/tmp'
    os.makedirs(d, exist_ok=True)
    for n, b in (('ours', ours), ('old', old), ('new', new)):
        open(f'{d}/{n}', 'wb').write(b)
    r = subprocess.run(['git', 'merge-file', '-p', '-L', 'ours', '-L', 'old-upstream', '-L', 'new-upstream',
                        f'{d}/ours', f'{d}/old', f'{d}/new'], capture_output=True)
    return r.returncode, r.stdout


def drift():
    base = load_base()
    if base and base.get('schema') == 2:
        say('FAIL', 'release-managed code: review upstream changes in the source repository and build a new tag')
        return
    if not base:
        say('FAIL', 'no baseline: run `snapshot` on a known good state first')
        return
    old, new = base['version'], version()
    if old == new:
        say('OK', f'version unchanged ({new}); nothing to compare. For a planned update: run drift after it.')
        return
    zo, zn = zip_for(old), zip_for(new)
    if not zo or not zn:
        say('FAIL', f'cannot download release zip(s): {old}={bool(zo)} {new}={bool(zn)}')
        return
    shutil.rmtree(f'{BASE}/merged', ignore_errors=True)
    with zipfile.ZipFile(zo) as a, zipfile.ZipFile(zn) as b:
        for f in base['excludes']:
            bp = f'{BASE}/files/{f}'
            if not os.path.isfile(bp):
                continue
            o, n = member(a, f), member(b, f)
            if o is None or n is None:
                say('WARN', f'{f}: not in upstream {"old" if o is None else "new"} release (file removed/renamed upstream?)')
                continue
            if o == n:
                say('OK', f'{f}: upstream unchanged {old} -> {new}')
                continue
            rc, merged = merge3(open(bp, 'rb').read(), o, n)
            os.makedirs(os.path.dirname(f'{BASE}/merged/{f}'), exist_ok=True)
            open(f'{BASE}/merged/{f}', 'wb').write(merged)
            if rc == 0:
                say('WARN', f'{f}: upstream changed, our patch merges cleanly -> {BASE}/merged/{f} (review, then `apply`)')
            else:
                say('FAIL', f'{f}: upstream changed, {rc} conflict(s) with our patch -> resolve by hand in {BASE}/merged/{f}')
            diff = list(difflib.unified_diff(o.decode('utf-8', 'replace').splitlines(), n.decode('utf-8', 'replace').splitlines(),
                                             f'{old}/{f}', f'{new}/{f}', lineterm='', n=0))
            say('INFO', f'   upstream change: {len(diff)} diff lines (first: {" | ".join(diff[2:5])[:200]})')


def apply():
    if (load_base() or {}).get('schema') == 2:
        say('FAIL', 'release-managed code: deploy a verified release instead of legacy baseline merges')
        return
    mdir = f'{BASE}/merged'
    if not os.path.isdir(mdir):
        say('FAIL', 'nothing to apply: run `drift` first')
        return
    bk = f'{BASE}/prev-{time.strftime("%Y%m%d%H%M%S")}'
    n = 0
    for dp, _, fs in os.walk(mdir):
        for fn in fs:
            m = os.path.join(dp, fn)
            rel = os.path.relpath(m, mdir)
            if b'<<<<<<< ours' in open(m, 'rb').read():
                say('FAIL', f'{rel}: still has conflict markers, skipped')
                continue
            dst = f'{ROOT}/{rel}'
            os.makedirs(os.path.dirname(f'{bk}/{rel}'), exist_ok=True)
            shutil.copy2(dst, f'{bk}/{rel}')
            st = os.stat(dst)
            shutil.copyfile(m, dst)
            os.chown(dst, st.st_uid, st.st_gid)
            n += 1
            say('OK', f'{rel}: applied (old copy in {bk})')
    say('INFO', f'{n} file(s) applied. Next: restart lampac, delete cache/static/*online-prestige* while it is stopped, then `check` and `snapshot`.')


# ---------------------------------------------------------------- driver
def run_local(cmd):
    {'check': check, 'snapshot': snapshot, 'drift': drift, 'apply': apply}[cmd]()


def run_remote(ssh, cmd):
    b = base64.b64encode(open(os.path.abspath(__file__), 'rb').read()).decode()
    code = f"import base64,sys;sys.argv=['postcheck','--local','--json','{cmd}'];exec(compile(base64.b64decode('{b}'),'<postcheck>','exec'))"
    r = subprocess.run(ssh + ['python3 -I -c "' + code + '"'],
                       capture_output=True, text=True, timeout=600)
    for line in r.stdout.replace('\r', '').splitlines():
        if line.startswith('{"level"'):
            j = json.loads(line)
            yield j['level'], j['msg']


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    cmd = args[0] if args else 'check'
    if cmd not in ('check', 'snapshot', 'drift', 'apply'):
        sys.exit(__doc__)
    if '--local' in sys.argv:
        run_local(cmd)
        if '--json' in sys.argv:
            for l, m in out:
                print(json.dumps({'level': l, 'msg': m}, ensure_ascii=False))
        else:
            for l, m in out:
                print(f'[{l}] {m}')
        if any(level == 'FAIL' for level, _ in out):
            sys.exit(2)
        return
    worst = 0
    for name, ssh in HOSTS:
        print(f'=== {name}')
        if ssh is None:
            out.clear()
            run_local(cmd)
            rows = list(out)
        else:
            try:
                rows = list(run_remote(ssh, cmd))
            except Exception as e:
                rows = [('FAIL', f'ssh failed: {e}')]
            if not rows:
                rows = [('FAIL', 'no answer from host')]
        for l, m in rows:
            print(f'[{l}] {m}')
            worst = max(worst, {'FAIL': 2, 'WARN': 1}.get(l, 0))
    print('\nRESULT:', ['all good', 'warnings, see above', 'FAILED, see above'][worst])
    sys.exit(worst)


if __name__ == '__main__':
    main()
