"""Isolated 1.90+ authorization, database migration and real HTTP sync checks.

Uses synthetic accounts only. No running installation, private config or media API
is read. All writes go to temporary instances and are removed on exit.
"""
import argparse
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[2]
VIEWER, OTHER = "viewer@example.test", "other@example.test"
checks = 0


def check(name, condition):
    global checks
    assert condition, name
    checks += 1
    print("PASS", name, flush=True)


def denied(status, data):
    return status in (400, 401, 402, 403) or isinstance(data, dict) and bool(data.get("accsdb"))


class Instance:
    def __init__(self, root, args, bot_secret="fixture-bot-secret", legacy_blob=False):
        self.root, self.args, self.proc = root, args, None
        root.mkdir()
        for name in ("Core.dll", "Shared.dll", "Core.deps.json", "Core.runtimeconfig.json"):
            shutil.copyfile(args.runtime / name, root / name)
        (root / "runtimes").symlink_to(args.runtime.resolve() / "runtimes", target_is_directory=True)
        for module in ("NativeAppleTV", "Sync/Sync", "Sync/TimeCode", "Sync/SyncEvents"):
            shutil.copytree(args.modules / module, root / "module" / module)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = "http://127.0.0.1:" + str(self.port)
        self.config = {
            "listen": {"ip": "127.0.0.1", "port": self.port, "localhost": "127.0.0.1", "scheme": "http"},
            "BaseModule": {"LoadModules": [".*"], "SkipModules": []},
            "accsdb": {"enable": True, "accounts": {VIEWER.upper(): "2099-01-01", OTHER.upper(): "2099-01-01"},
                       "users": [{"id": "banned-fixture", "ban": True, "expires": "2099-01-01"},
                                 {"id": "expired-fixture", "expires": "2000-01-01"}]},
            "NativeAppleTV": {"bot_secret": bot_secret, "poll_interval": 1},
            "WAF": {"enable": False}, "openstat": {"enable": False},
            "chromium": {"enable": False}, "serilog": False,
        }
        (root / "init.conf").write_text(json.dumps(self.config))
        (root / "base.conf").write_text("{}")
        (root / "appsettings.Production.json").write_text("{}")
        (root / "passwd").write_text("fixture-admin-password")
        (root / "database").mkdir()
        with sqlite3.connect(root / "database/TimeCode.sql") as db:
            if legacy_blob:
                db.execute("CREATE TABLE timecodes (Id INTEGER PRIMARY KEY, user TEXT NOT NULL, card TEXT NOT NULL, item TEXT, data TEXT, updated TEXT NOT NULL)")
                db.execute("INSERT INTO timecodes VALUES (1,?,?,?,?,?)", ("viewerexample.test", "10_movie", "777", json.dumps({"time": 12, "duration": 100, "percent": 12, "updated": 5000}), "2026-01-01"))
            else:
                # Actual capability-3 typed schema: changed_at does not exist yet.
                db.execute("CREATE TABLE timecodes (Id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, identity TEXT, card TEXT NOT NULL, item TEXT, position REAL NOT NULL DEFAULT 0, duration REAL NOT NULL DEFAULT 0, percent REAL NOT NULL DEFAULT 0, profile INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0, watched_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0, extra TEXT)")
                db.execute("INSERT INTO timecodes (user,identity,card,item,position,duration,percent,watched_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", ("viewerexample.test", "movie-10", "10_movie", "777", 12, 100, 12, 5000, 6000))
        self.log = (root / "server.log").open("w")

    def request(self, path, body=None, identity=None, token=None, form=None, headers=None):
        params = {"account_email": identity} if identity else {"token": token} if token else {}
        url = self.url + path
        if params:
            url += ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
        payload = urllib.parse.urlencode(form).encode() if form is not None else json.dumps(body).encode() if body is not None else None
        content_type = "application/x-www-form-urlencoded" if form is not None else "application/json"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": content_type, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                status, raw = response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read().decode()
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw

    def start(self):
        self.proc = subprocess.Popen([self.args.dotnet, "Core.dll"], cwd=self.root, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(140):
            try:
                data = self.request("/api/capabilities", identity=VIEWER)[1]
                if isinstance(data, dict) and data.get("features", {}).get("timecode") == 4:
                    return
            except (OSError, ValueError):
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(.25)
        raise RuntimeError("Temporary instance did not start: " + (self.root / "server.log").read_text()[-5000:])

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=20)

    def close(self):
        self.stop()
        self.log.close()

    def issue(self, user=VIEWER, method="web"):
        code = self.request("/nativeappletv/auth/start", {})[1]["code"]
        if method == "web":
            status, _ = self.request("/nativeappletv/confirm", form={"code": code, "passwd": "fixture-admin-password", "user": user})
        else:
            status, _ = self.request("/nativeappletv/bot/confirm", {"code": code, "user": user}, headers={"X-Bot-Secret": "fixture-bot-secret"})
        assert status == 200
        status, result = self.request("/nativeappletv/auth/poll", {"code": code})
        assert status == 200 and result["status"] == "success"
        return result["token"], code

    def rows(self, identity=VIEWER, token=None, path="/timecode/dump"):
        return self.request(path, identity=None if token else identity, token=token)[1]["rows"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--dotnet", default=shutil.which("dotnet"))
    parser.add_argument("--modules", type=Path, default=REPO / "Modules")
    args = parser.parse_args()
    if not args.dotnet:
        parser.error("dotnet is required")
    with tempfile.TemporaryDirectory(prefix="lampac-native-sync-") as folder, ExitStack() as stack:
        a = Instance(Path(folder) / "primary", args)
        stack.callback(a.close)
        a.start()
        cap = a.request("/api/capabilities")[1]
        check("public manifest advertises native sign-in and TimeCode 4", cap["features"]["timecode"] == 4 and cap["source"]["protocol"] == "luxo-source/1")
        row = a.rows()[0]
        check("capability-3 database migrates without changing viewing state", row["watched_at"] == 5000 and row["position"] == 12 and row.get("changed_at", 0) == 0)
        with sqlite3.connect(a.root / "database/TimeCode.sql") as db:
            check("migration adds changed_at with zero for old rows", db.execute("SELECT changed_at FROM timecodes").fetchone()[0] == 0)
        check("anonymous and unknown accounts cannot read playback state", denied(*a.request("/timecode/dump")) and denied(*a.request("/timecode/dump", identity="unknown-fixture")))
        check("invalid token is rejected", a.request("/nativeappletv/auth/check", {"token": "invalid-fixture"})[0] == 400)
        check("configured bans and expiry are enforced", a.request("/nativeappletv/auth/check", {"token": "banned-fixture"})[0] == 400 and a.request("/nativeappletv/auth/check", {"token": "expired-fixture"})[0] == 402)
        code = a.request("/nativeappletv/auth/start", {})[1]["code"]
        check("unconfirmed challenge stays pending", a.request("/nativeappletv/auth/poll", {"code": code})[1]["status"] == "pending")
        check("rapid polling is rate limited", a.request("/nativeappletv/auth/poll", {"code": code})[0] == 429)
        check("wrong admin password cannot confirm a challenge", a.request("/nativeappletv/confirm", form={"code": code, "passwd": "wrong-fixture"})[0] == 401)
        check("bot confirmation requires the configured secret", a.request("/nativeappletv/bot/confirm", {"code": code}, headers={"X-Bot-Secret": "wrong-fixture"})[0] == 401)
        first, used_code = a.issue()
        second, _ = a.issue(" " + VIEWER.upper() + " ", method="bot")
        check("web and bot confirmation issue distinct tokens", first != second and len(first) == len(second) == 32)
        check("a consumed code cannot return a token again", a.request("/nativeappletv/auth/poll", {"code": used_code})[0] == 410)
        check("both device tokens resolve to the existing account", all(a.request("/api/capabilities", token=t)[1]["uid"] == VIEWER for t in [first, second]))
        outside, _ = a.issue(OTHER)
        check("native tokens see the existing viewing history", a.rows(token=first) == a.rows(token=second) == a.rows())
        watched = {"id": "movie-10", "card": "10_movie", "hash": "777", "position": 0, "percent": 100, "duration": 100, "watched_at": 1000, "changed_at": 9000}
        result = a.request("/timecode/set", {"rows": [watched]}, token=first)[1]
        check("new action wins with an older viewing date", result["accepted"] == 1 and a.rows()[0]["watched_at"] == 1000 and a.rows()[0]["changed_at"] == 9000)
        a.request("/timecode/set", {"rows": [dict(watched, watched_at=8000, changed_at=8000)]}, token=second)
        check("late stale native action cannot overwrite current state", a.rows()[0]["changed_at"] == 9000)
        a.request("/timecode/add?card_id=10_movie", form={"id": "777", "data": json.dumps({"updated": 8500, "time": 5, "percent": 5})}, identity=VIEWER)
        check("late web update cannot overwrite a newer native mark", a.rows()[0]["changed_at"] == 9000)
        a.request("/timecode/set", {"rows": [dict(watched, changed_at=10000, deleted=True, percent=0)]}, token=second)
        check("native unwatch is shared and omitted from legacy viewed output", a.rows(token=first)[0]["deleted"] and a.request("/timecode/all?card_id=10_movie", identity=VIEWER)[1] == {})
        a.request("/timecode/set", {"rows": [dict(watched, changed_at=11000)]}, token=first)
        check("re-marking with the original viewing date works", not a.rows()[0].get("deleted") and a.rows()[0]["watched_at"] == 1000)
        check("other accounts do not share viewing history", a.rows(identity=OTHER) == [])
        a.request("/timecode/set?profile_id=child", {"rows": [dict(watched, changed_at=12000)]}, token=first)
        check("profiles stay separate with native token authentication", a.rows(path="/timecode/dump?profile_id=child", token=second)[0]["changed_at"] == 12000 and a.rows()[0]["changed_at"] == 11000)
        bookmark = {"id": "10_movie", "card": {"id": 10, "source": "tmdb", "media_type": "movie", "title": "Fixture"}, "categories": {"like": 15000}, "changed_at": 15000}
        a.request("/bookmark/sync", {"rows": [bookmark]}, token=first)
        check("device aliases and web account share bookmarks", a.rows(token=second, path="/bookmark/dump") == a.rows(path="/bookmark/dump") and len(a.rows(path="/bookmark/dump")) == 1)
        check("bookmarks stay isolated from another user", a.rows(identity=OTHER, path="/bookmark/dump") == [])
        a.stop()
        a.start()
        check("both device aliases and watch state survive restart", all(a.request("/nativeappletv/auth/check", {"token": t})[0] == 200 for t in [first, second]) and a.rows(token=second)[0]["changed_at"] == 11000)
        b = Instance(Path(folder) / "secondary", args, bot_secret="", legacy_blob=True)
        stack.callback(b.close)
        b.start()
        check("legacy JSON-blob database migrates without losing progress", b.rows()[0]["position"] == 12 and b.rows()[0]["watched_at"] == 5000)
        check("bot hook is closed when no secret is configured", b.request("/nativeappletv/bot/confirm", {"code": "fixture"}, headers={"X-Bot-Secret": "fixture-bot-secret"})[0] == 401)
        spec = importlib.util.spec_from_file_location("native_test_peer", REPO / "local/sync/lampac_sync.py")
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        for kind in ["timecode", "bookmark"]:
            with redirect_stdout(io.StringIO()):
                okay = sync.sync_account_kind(a.url, b.url, kind, VIEWER, False, False)
            check("real peer sync confirms stored " + kind + " writes", okay)
        check("peer sync preserves action and viewing timestamps separately", b.rows()[0]["watched_at"] == 1000 and b.rows()[0]["changed_at"] == 11000)
        with redirect_stdout(io.StringIO()):
            sync.sync_account_kind(a.url, b.url, "timecode", VIEWER, False, False)
        check("repeat peer pass does not advance the action timestamp", b.rows()[0]["changed_at"] == 11000)
        sys.path.insert(0, str(REPO / "local/sync"))
        import plex_lampac_sync as plex
        plex.LOCAL_BASE = a.url
        progress = {"ts": 700, "changed_at": 16000, "time": 60, "duration": 100, "percent": 60}
        with redirect_stdout(io.StringIO()):
            okay = plex.push_to_lampac(VIEWER, "10_movie", 777, progress, False, 4)
        check("Plex adapter confirms real capability-4 writes", okay and a.rows()[0]["watched_at"] == 700 and a.rows()[0]["changed_at"] == 16000)
        tombstone = dict(progress, ts=17000, view_ts=700, changed_at=17000, deleted=True, time=0, percent=0)
        with redirect_stdout(io.StringIO()):
            okay = plex.push_to_lampac(VIEWER, "10_movie", 777, tombstone, False, 4)
        check("Plex unwatch keeps the viewing date separate on the real API", okay and a.rows()[0]["deleted"] and a.rows()[0]["watched_at"] == 700 and a.rows()[0]["changed_at"] == 17000)
        observed = plex.fetch_lampac_state(VIEWER, 4)[("10_movie", 777)]
        check("Plex readback retains tombstones and both timestamps", observed["deleted"] and observed["ts"] == 700 and observed["changed_at"] == 17000)
        records = json.loads((a.root / "users.json").read_text())
        next(user for user in records if user["id"] == VIEWER)["ban"] = True
        (a.root / "users.json").write_text(json.dumps(records))
        for _ in range(30):
            if a.request("/nativeappletv/auth/check", {"token": first})[0] == 400:
                break
            time.sleep(.1)
        check("banning the existing user revokes every linked Apple TV", all(a.request("/nativeappletv/auth/check", {"token": t})[0] == 400 and denied(*a.request("/timecode/dump", token=t)) for t in [first, second]))
        check("revoking one family does not revoke another account", a.request("/nativeappletv/auth/check", {"token": outside})[0] == 200)
    print("PASS:", checks, "native authorization / TimeCode / sync assertions")


if __name__ == "__main__":
    main()
