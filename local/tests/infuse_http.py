"""Isolated real Lampac checks: Infuse works with TorBox absent or unavailable."""
import argparse, json, os, shutil, socket, subprocess, tempfile, time
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', required=True, type=Path)
parser.add_argument('--dotnet', required=True)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args): return None
opener = build_opener(NoRedirect)
with tempfile.TemporaryDirectory(prefix='lampac-infuse-test-') as tmp:
    app = Path(tmp)
    for name in ['Core.dll', 'Shared.dll', 'Core.deps.json', 'Core.runtimeconfig.json']:
        shutil.copyfile(args.runtime / name, app / name)
    (app / 'runtimes').symlink_to(args.runtime / 'runtimes', target_is_directory=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    uid, other = 'viewer@example.test', 'other@example.test'
    config = {'listen': {'ip': '127.0.0.1', 'port': port, 'localhost': '127.0.0.1', 'scheme': 'http'},
        'BaseModule': {'LoadModules': ['.*'], 'SkipModules': []},
        'accsdb': {'enable': True, 'accounts': {uid: '2099-01-01', other: '2099-01-01'}},
        'WAF': {'enable': False}, 'openstat': {'enable': False}, 'chromium': {'enable': False}, 'serilog': False}
    (app / 'init.conf').write_text(json.dumps(config))
    (app / 'base.conf').write_text('{}')
    (app / 'passwd').write_text('isolated-infuse-test')
    (app / 'appsettings.Production.json').write_text('{}')
    shutil.copytree(repo / 'Modules/Infuse', app / 'module/Infuse', ignore=shutil.ignore_patterns('bin', 'obj'))
    proc = None
    log = (app / 'test.log').open('w')
    checks = 0
    def check(ok):
        global checks
        assert ok
        checks += 1
    def request(path, body=None, user=None, method=None):
        url = path if path.startswith('http') else base + path
        if user is not None:
            from urllib.parse import quote
            url += ('&' if '?' in url else '?') + 'account_email=' + quote(user)
        req = Request(url, data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'}, method=method)
        try:
            with opener.open(req, timeout=15) as res: return res.status, res.headers, res.read()
        except HTTPError as e: return e.code, e.headers, e.read()
    def stop():
        if proc and proc.poll() is None:
            proc.terminate(); proc.wait(timeout=20)
    def start():
        global proc
        proc = subprocess.Popen([args.dotnet, 'Core.dll'], cwd=app, stdout=log, stderr=subprocess.STDOUT)
        for _ in range(140):
            try:
                if request('/infuse/file/missing?token=invalid')[0] == 410: return
            except OSError: pass
            if proc.poll() is not None: break
            time.sleep(.25)
        raise RuntimeError((app / 'test.log').read_text()[-4000:])
    filename = 'Тест & Show (2026) S01E01 - Серия #1.mkv'
    target = 'http://torrserver.example/stream?link=hash&index=1&play&signature=abc%2Fdef'
    batch = {'links': [{'url': target, 'filename': filename}]}
    def prepare(user=uid):
        code, headers, raw = request('/infuse/named', batch, user)
        check(code == 200 and headers['Cache-Control'] == 'no-store')
        return json.loads(raw)['links'][0]['url']
    try:
        start()
        check(not (app / 'mods/TorBox').exists())
        check(json.loads(request('/infuse/named', batch)[2]).get('success') is not True)
        check(json.loads(request('/infuse/named', batch, 'unknown@example.test')[2]).get('success') is not True)
        for bad in ['file:///etc/passwd', 'https://user:pass@example.test/file', 'javascript:alert(1)', 'https://example.test/\nfile']:
            check(request('/infuse/named', {'links': [batch['links'][0], {'url': bad, 'filename': filename}]}, uid)[0] == 400)
        check(not (app / 'database/infuse-links.json').exists())
        named, second = prepare(), prepare(other)
        check(request(named)[0:1] == (302,))
        code, headers, _ = request(named)
        check(code == 302 and headers['Location'] == target and headers['Referrer-Policy'] == 'no-referrer')
        check(request(named.replace('S01E01', 'S01E99'))[0] == 404)
        check(request(named[:-1] + ('0' if named[-1] != '0' else '1'))[0] == 410)
        check((app / 'database/infuse-links.json').stat().st_mode & 0o777 == 0o600)
        check(named.split('token=')[1] not in (app / 'database/infuse-links.json').read_text())
        stop(); start()
        check(request(named)[1]['Location'] == target)
        # TorBox exists but has no private settings/key; generic links must still work.
        stop()
        shutil.copytree(repo / 'Modules/TorBox', app / 'mods/TorBox')
        start()
        check(request('/torbox/status', user=uid)[0] != 200)
        third = prepare()
        check(request(named)[1]['Location'] == target and request(third)[0] == 302)
        # Present key, unreachable TorBox API: original torrent flow is still independent.
        stop()
        (app / 'torbox-private.json').write_text(json.dumps({'api_key': 'fixture', 'allowed_uids': [uid]}))
        controller = app / 'mods/TorBox/Controller.cs'
        controller.write_text(controller.read_text().replace('https://api.torbox.app/v1/api/torrents/', 'http://127.0.0.1:1/'))
        start()
        check(request('/torbox/list', user=uid)[0] == 502)
        fourth = prepare()
        check(request(fourth)[1]['Location'] == target)
        check(request('/infuse/revoke', {}, other)[0] == 200)
        check(request(second)[0] == 410 and request(named)[0] == 302)
        check(request('/infuse/revoke', {}, uid)[0] == 200)
        stop(); start()
        check(all(request(url)[0] == 410 for url in [named, third, fourth]))
        # Corruption fails closed and never overwrites the store.
        stop(); (app / 'database/infuse-links.json').write_text('broken')
        start()
        check(request('/infuse/named', batch, uid)[0] == 503)
        check((app / 'database/infuse-links.json').read_text() == 'broken')
        print(f'PASS: {checks} independent Infuse HTTP checks, TorBox absent/missing key/offline, restore and revoke')
    finally:
        stop(); log.close()
