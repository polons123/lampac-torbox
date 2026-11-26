"""Run the Lampa bundle cache tests with a disposable copy of the dynamic module."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--runtime", type=Path, required=True)
parser.add_argument("--dotnet", required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parents[2] / "Modules/LampaWeb"
runtime = args.runtime.resolve()

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for name in ("Core.dll", "Shared.dll", "Core.deps.json", "Core.runtimeconfig.json"):
        shutil.copy2(runtime / name, root / name)
    (root / "runtimes").symlink_to(runtime / "runtimes", target_is_directory=True)
    module = root / "module/LampaWeb"
    shutil.copytree(source, module, ignore=shutil.ignore_patterns("bin", "obj"))

    # Count real renders in the test process, without instrumenting the deployed controller.
    controller = module / "Controllers/ApiController.cs"
    code = controller.read_text(encoding="utf-8-sig")
    needle = "using var reader = new IO.StreamReader(stream);"
    assert code.count(needle) == 1
    controller.write_text(code.replace(needle, 'IO.File.AppendAllText("bundle-renders", "1\\n"); ' + needle))

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = {
        "listen": {"ip": "127.0.0.1", "port": port, "scheme": "http"},
        "BaseModule": {"LoadModules": [".*"], "SkipModules": []},
        "accsdb": {"enable": False}, "WAF": {"enable": False}, "openstat": {"enable": False},
        "chromium": {"enable": False}, "serilog": False,
        "LampaWeb": {"enable": True, "autoupdate": False, "appReplace": {"fixtureMarker": "file:rule.txt"}},
    }
    for name, content in (("init.conf", config), ("base.conf", {}), ("appsettings.Production.json", {})):
        (root / name).write_text(json.dumps(content))
    assets = root / "wwwroot/lampa-main"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<head></head>")
    bundle = assets / "app.min.js"
    bundle.write_text('fixtureMarker + "{localhost}"')
    rule = root / "rule.txt"
    rule.write_text("one")

    def request(path="/app.min.js", tag=None, host=None):
        headers = {}
        if tag:
            headers["If-None-Match"] = tag
        if host:
            headers["Host"] = host
        query = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
        try:
            response = urllib.request.urlopen(query, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def renders():
        return len((root / "bundle-renders").read_text().splitlines())

    with (root / "server.log").open("w") as log:
        proc = subprocess.Popen([args.dotnet, "Core.dll"], cwd=root, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(160):
                try:
                    status, headers, body = request()
                    if status == 200 and headers.get("ETag"):
                        break
                except OSError:
                    pass
                if proc.poll() is not None:
                    raise AssertionError("Fixture process exited")
                time.sleep(.25)
            else:
                raise AssertionError("Fixture did not start")
            assert b"one" in body and {"private", "no-cache"} <= set(headers["Cache-Control"].split(", "))
            tag = headers["ETag"]
            assert tag == 'W/"' + hashlib.sha256(body).hexdigest().upper() + '"'
            for path in ("/app.min.js", "/lampa-main/app.min.js", "/app.min.js?legacy=1"):
                status, headers, body = request(path, tag)
                assert (status, headers["ETag"], body) == (304, tag, b"")
            assert request(tag='"unrelated", ' + tag.removeprefix("W/"))[0] == 304

            original = bundle.stat()
            bundle.write_text('fixtureMarker - "{localhost}"')
            os.utime(bundle, ns=(original.st_atime_ns, original.st_mtime_ns))
            status, headers, body = request(tag=tag)
            assert status == 200 and headers["ETag"] != tag and b"one - " in body
            tag = headers["ETag"]
            rule.write_text("two")
            status, headers, body = request(tag=tag)
            assert status == 200 and headers["ETag"] != tag and b"two - " in body
            status, alternate, alternate_body = request(tag=headers["ETag"], host="alternate.test")
            assert status == 200 and alternate["ETag"] != headers["ETag"] and b"alternate.test" in alternate_body

            before = renders()
            with ThreadPoolExecutor(max_workers=8) as pool:
                replies = list(pool.map(lambda _: request(host="cold.test"), range(8)))
            assert all(status == 200 and body == replies[0][2] for status, _, body in replies)
            for _ in range(3):
                assert request(host="alternate.test")[2] == alternate_body
                assert request(host="cold.test")[2] == replies[0][2]
            assert renders() == before + 1
            for i in range(5):
                request(host=f"variant{i}.test")
            before = renders()
            request(host="cold.test")
            assert renders() == before + 1
            print("PASS both routes, ETag validation, same-stat edit, rule edit, warm origins, single render and eviction")
        except Exception:
            print((root / "server.log").read_text()[-3600:])
            raise
        finally:
            proc.terminate()
            proc.wait(timeout=10)
