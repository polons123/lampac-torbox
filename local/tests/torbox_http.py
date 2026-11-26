from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor
import json, shutil, subprocess, urllib.request, urllib.parse, urllib.error, threading, time, re, os
# Use a staged release and synthetic accounts. No running service is read.
import argparse, socket, sqlite3, tempfile
parser = argparse.ArgumentParser(description="Isolated TorBox HTTP regression checks")
parser.add_argument("--runtime", required=True, type=Path, help="extracted Lampac release (Core.dll, Shared.dll, runtimes/)")
parser.add_argument("--dotnet", default=shutil.which("dotnet"), help="dotnet executable when it is not on PATH")
parser.add_argument("--module-dir", type=Path, default=Path(__file__).resolve().parents[2] / "Modules/TorBox", help="TorBox sources, including an extracted release module")
parser.add_argument("--kino-mock", action="store_true", help="exercise KinoAPI using a local fixture and the matching Shared.dll")
args = parser.parse_args()
if not args.dotnet:
    parser.error("dotnet is not on PATH; supply --dotnet")
r = Path(__file__).resolve().parent
repo = r.parents[1]
work = tempfile.TemporaryDirectory(prefix="lampac-torbox-test-")
p = Path(work.name)
runtime = args.runtime.resolve()
assert (runtime / "Core.dll").is_file(), "runtime must contain Core.dll"
assert (runtime / "runtimes").is_dir(), "runtime must contain runtimes/"
for name in ["Core.dll", "Shared.dll", "Core.deps.json", "Core.runtimeconfig.json"]:
    shutil.copyfile(runtime / name, p / name)
(p / "runtimes").symlink_to(runtime / "runtimes", target_is_directory=True)
(p / "appsettings.Production.json").write_text("{}")
(p / "base.conf").write_text("{}")
(p / "passwd").write_text("isolated-test-password")
def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
app_port, mock_port = free_port(), free_port()
app_url = "http://127.0.0.1:" + str(app_port)
mock_url = "http://127.0.0.1:" + str(mock_port)
uid, second, outsider = "viewer@example.test", "other@example.test", "outsider@example.test"
cfg = {"listen": {"ip": "127.0.0.1", "port": app_port, "localhost": "127.0.0.1", "scheme": "http"},
       "BaseModule": {"LoadModules": [".*"], "SkipModules": []},
       "accsdb": {"enable": True, "accounts": {user: "2099-01-01T00:00:00" for user in [uid, second, outsider]}},
       "WAF": {"enable": False}, "openstat": {"enable": False}, "chromium": {"enable": False}, "serilog": False}
if args.kino_mock:
    cfg["PidTor"] = {"enable": True, "emptyVoice": True, "min_sid": 0, "redapi": mock_url, "torrs": [mock_url]}
    cfg["globalproxy"] = [{"pattern": "^" + re.escape(mock_url + "/api/v1/"), "list": [mock_url], "BypassOnLocal": False}]
    for name, source in [("Online", repo / "Online"), ("PidTor", repo / "Modules/PidTor")]:
        shutil.copytree(source, p / "module" / name, ignore=shutil.ignore_patterns("bin", "obj"))
    (p / "data").mkdir()
    (p / "data/externalids.json").write_text("{}")
    credentials = p / "credentials"
    credentials.mkdir(mode=0o700)
    (credentials / "kinoapi").write_text("fixture-kino-credential")
    (credentials / "kinoapi").chmod(0o600)
(p / "init.conf").write_text(json.dumps(cfg))
os.chmod(p / "init.conf", 0o600)
shutil.copytree(args.module_dir, p / "mods/TorBox")
f = p / "mods/TorBox/Controller.cs"
f.write_text(f.read_text().replace("https://api.torbox.app/v1/api/torrents/", mock_url + "/v1/api/torrents/"))
private = {"api_key": "fixture-key", "allowed_uids": [uid, second], "allow_external_playlists": True,
           "jacred": {"base_url": mock_url, "api_key": "fixture-parser-key"}}
(p / "torbox-private.json").write_text(json.dumps(private))
os.chmod(p / "torbox-private.json", 0o600)
(p / "mods" / "TorBox" / "Test.cs").write_text("""using Microsoft.AspNetCore.Mvc;using System;namespace TorBox;public partial class TorBoxController {
[HttpGet,Route("test/reset")]public ActionResult Reset(){lock(inventoryLock)inventories.Clear();lock(badgeBatchGate){badgeBatches.Clear();badgeBackoff.Clear();badgeHashes.Clear();}searchCache.Clear();return Payload(new{success=true});}
}""")
m = p / "mods" / "TorBox" / "manifest.json"
j = json.loads(m.read_text())
if "Test.cs" not in j["tree"]:
    j["tree"].append("Test.cs")
m.write_text(json.dumps(j))
A = "a" * 40
B = "b" * 40
C = "c" * 40
source = "https://tracker.example/viewtopic.php?t=123&passkey=MOCK_TRACKER_SECRET"

def row(h, details=source):
    return {"Title": "Mock Show S01 1080p", "Details": details, "MagnetUri": "magnet:?xt=urn:btih:" + h, "Seeders": 10, "info": {"voices": ["Studio A"]}}

def torrent(h, id, episodes):
    return {"id": id, "hash": h, "name": "Mock Show S01 1080p", "download_present": True, "files": [{"id": e, "name": "Mock.Show.S01E%02d.mkv" % e} for e in episodes]}

items = {10: torrent(A, 10, [1, 2])}
templates = {B: torrent(B, 20, [1, 2, 3])}
rows = [row(A)]
audioA = {"language": "ru", "title": "Studio A", "codec": "ac3", "channels": 6}
audioB = {"language": "ru", "title": "Studio B", "codec": "ac3", "channels": 6}
english = {"language": "en", "title": "Original", "codec": "aac", "channels": 2}
subRu = {"language": "ru", "title": "Full", "codec": "srt"}
state = {"mutations": [], "streams": [], "metadata_mode": "normal", "cached": True, "expire": False, "parser_calls": 0, "stream_fail": False, "mylist": [], "links": [], "batch_mode": "ok", "pause": 0}
episode_library_started = threading.Event()


def safe_query(q):
    return {k: v for k, v in q.items() if k != "token"}


class Mock(BaseHTTPRequestHandler):
    def respond(self, payload, status=200):
        b = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def batch(self, method, u):
        if state["pause"]:
            time.sleep(state["pause"])
        q = urllib.parse.parse_qs(u.query)
        body_ids = q.get("file_ids", [])
        if method == "POST":
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            try:
                body_ids = [str(x) for x in json.loads(raw).get("file_ids", [])]
            except ValueError:
                body_ids = []
        state["links"].append((method.lower() + "-batch", body_ids, "user_ip" in q))
        mode = state["batch_mode"]
        if mode == "unauthorized":
            return self.respond({"success": False}, 401)
        if mode == "limited":
            return self.respond({"success": False}, 429)
        if mode == "invalid":
            return self.respond({"detail": "field required"}, 422)
        if mode == "missing" or (mode == "post-missing" and method == "POST"):
            return self.respond({"success": False, "error": "ENDPOINT_NOT_FOUND", "detail": "Endpoint not found"}, 404)
        if mode == "active-limit":
            return self.respond({"success": False, "error": "ACTIVE_LIMIT"})
        if mode == "unfinished":
            return self.respond({"success": False, "error": "DOWNLOAD_NOT_FINISHED"})
        if mode == "partial":
            return self.respond({"success": True, "data": [{"file_id": int(body_ids[0]), "url": "https://cdn.example/only"}]})
        if mode == "duplicate":
            return self.respond({"success": True, "data": [
                {"file_id": int(body_ids[0]), "url": "https://cdn.example/a"},
                {"file_id": int(body_ids[0]), "url": "https://cdn.example/b"}]})
        if mode == "http":
            return self.respond({"success": True, "data": [{"file_id": int(i), "url": "http://cdn.example/" + i} for i in body_ids]})
        if mode == "live-sample":
            sample = json.loads((r / "fixtures/batch-response.json").read_text())
            remap = dict(zip(sample["requested"], [int(i) for i in body_ids]))
            data = [dict(entry, file_id=remap[entry["file_id"]]) for entry in sample["data"]]
            return self.respond({"success": True, "data": list(reversed(data))})
        if mode == "map":
            return self.respond({"success": True, "data": {i: "https://cdn.example/map/" + i for i in body_ids}})
        data = [{"file_id": int(i), "url": "https://cdn.example/list/" + i,
                 "success": True, "error": None} for i in reversed(body_ids)]
        if mode == "entry-failed":
            data[0]["success"] = False
        if mode == "entry-error":
            data[0]["error"] = "DOWNLOAD_NOT_FINISHED"
        return self.respond({"success": True, "data": data})

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path.startswith("/api/v1/"):
            return self.kino(u.path, q)
        if u.path.endswith("/indexers/all/results"):
            state["parser_calls"] += 1
            if state.get("kino_search"):
                state["kino_search"].append(q.get("title", [""])[0])
                if q.get("title") == ["Теория большого взрыва"] and q.get("year") == ["2007"]:
                    return self.respond({"Results": [dict(row(A), Title="Теория большого взрыва S01 1080p", Tracker="fixture", PublishDate="2026-01-01", Size=100000000, info={"voices": ["Studio A"], "seasons": [1]})]})
                return self.respond({"Results": []})
            return self.respond({} if rows is None else {"Results": rows})
        if u.path.endswith("/requestdl/batch"):
            return self.batch("GET", u)
        if u.path.endswith("/requestdl"):
            if state["pause"]:
                time.sleep(state["pause"])
            state["links"].append(("single", q.get("file_id", [""])[0], "user_ip" in q))
            return self.respond({"success": True, "data": "https://cdn.example/file/" + q.get("file_id", [""])[0]})
        if u.path.endswith("/mylist"):
            if state.get("episode_parallel"):
                episode_library_started.set()
            state["mylist"].append(safe_query(q))
            if "id" in q:
                ident = int(q["id"][0])
                return self.respond({"success": True, "data": [items[ident]] if ident in items else []})
            found = list(items.values())
            if (q.get("active") or ["false"])[0].lower() == "true":
                found = [item for item in found if item.get("active") is True]
            offset = int((q.get("offset") or ["0"])[0])
            limit = int((q.get("limit") or ["1000"])[0])
            page = found[offset:offset + limit]
            if (q.get("files") or ["true"])[0].lower() == "false":
                page = [{k: v for k, v in item.items() if k != "files"} for item in page]
            return self.respond({"success": True, "data": page})
        if u.path.endswith("/checkcached"):
            if state.get("episode_parallel"):
                state["episode_overlap"] = episode_library_started.wait(timeout=2)
                return self.respond({"success": True, "data": {A: {"cached": False}, B: {"cached": True}}})
            return self.respond({"success": True, "data": {h: {"cached": state.get("quality_cache",{}).get(h,state["cached"])} for h in q.get("hash", [""])[0].split(",")}})
        if u.path.endswith("/createstream"):
            state["streams"].append(q)
            if state["stream_fail"]:
                return self.respond({"success": False, "error": "FAIL"})
            newer = q["id"][0] == "20"
            audios = [english, audioB, audioA] if newer else [audioA, audioB, english]
            if state["metadata_mode"] == "missing" and newer:
                audios = [english, audioB]
            if state["metadata_mode"] == "ambiguous" and newer:
                audios = [audioA, audioA]
            height = 2160 if state["metadata_mode"] == "height" and newer else 1080
            return self.respond({"success": True, "data": {"metadata": {"video": {"height": height}, "audios": audios, "subtitles": [subRu]}, "hls_url": "https://media.example/" + q["id"][0] + "/" + q["file_id"][0] + ".m3u8"}})
        raise AssertionError(u.path)

    def kino(self, path, query):
        assert self.headers.get("X-API-Key") == "fixture-kino-credential"
        assert self.path.startswith(mock_url + "/api/v1/"), "KinoAPI fixture must receive the proxied absolute URL"
        state.setdefault("kino_requests", []).append(path)
        season = re.fullmatch(r"/api/v1/tv/by/tmdb/1418/seasons/([1-6])/episodes", path)
        if season:
            number = int(season[1])
            title = "Пилотная серия" if number == 1 else f"Название серии сезона {number}"
            return self.respond({"data": [{"episode_number": 1, "name_ru": title}]})
        cards = {
            "550": (550, "tt0137523", 361, "movie", "Бойцовский клуб", "Fight Club", 1999),
            "361": (550, "tt0137523", 361, "movie", "Бойцовский клуб", "Fight Club", 1999),
            "1418": (1418, "tt0898266", 306084, "tv", "Теория большого взрыва", "The Big Bang Theory", 2007),
            "4242": (4242, "tt9876543210", 987654321, "movie", "Фильм", "Movie", 2001),
        }
        card = cards.get(path.rsplit("/", 1)[-1])
        if not card:
            return self.respond({}, 404)
        tmdb, imdb, kp, kind, ru, original, year = card
        assert query == {"type": [kind], "include": ["images"]}
        if tmdb == 4242:
            time.sleep(.2)
        return self.respond({"data": {
            "id": {"tmdb": tmdb, "imdb": imdb, "kinopoisk": kp}, "type": kind,
            "title": {"ru": ru, "original": original}, "release": {"year": year},
            "images": {"kinopoisk": {"poster": f"https://kinopoiskapiunofficial.tech/images/posters/kp/{kp}.jpg"}},
        }})

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path.endswith("/requestdl/batch"):
            return self.batch("POST", u)
        body = self.rfile.read(int(self.headers["Content-Length"]))
        from email.parser import BytesParser
        message = BytesParser().parsebytes(("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + body)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message.get_payload()}
        state["mutations"].append((self.path, fields))
        assert self.path.endswith("/createtorrent")
        assert fields.get("add_only_if_cached") == b"true" and fields.get("as_queued") == b"false"
        if state["expire"]:
            return self.respond({"success": False, "error": "NOT_CACHED"})
        h = next(h for h in templates if h.encode() in body)
        t = templates[h]
        items[t["id"]] = t
        return self.respond({"success": True, "data": {"torrent_id": t["id"]}})

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", mock_port), Mock)
threading.Thread(target=server.serve_forever, daemon=True).start()

def get(path, body=None, identity=uid):
    url = app_url + path + ("&" if "?" in path else "?") + "account_email=" + urllib.parse.quote(identity)
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def api(path, body=None, identity=uid):
    code, raw = get(path, body, identity)
    return code, json.loads(raw)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def external_relay(url):
    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=20) as response:
            return response.status, response.headers.get('Location')
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            print('Bad-request fixture response:', exc.read().decode(errors='replace')[:600], flush=True)
        return exc.code, exc.headers.get('Location')

checks = []

def check(name, ok):
    checks.append([name, bool(ok)])
    print(name, "PASS" if ok else "FAIL", flush=True)
    assert ok, name

proc = None
log = (p / "probe-server.log").open("w")

def start():
    global proc
    env = os.environ.copy()
    env.pop("CREDENTIALS_DIRECTORY", None)
    env.pop("LAMPAC_KINOAPI_BASE_URL", None)
    if args.kino_mock:
        env["CREDENTIALS_DIRECTORY"] = str(credentials)
        env["LAMPAC_KINOAPI_BASE_URL"] = mock_url + "/api/v1/"
    proc = subprocess.Popen([args.dotnet, "Core.dll"], cwd=p, stdout=log, stderr=subprocess.STDOUT, env=env)
    for _ in range(100):
        try:
            if api("/torbox/status")[0] == 200:
                return
        except Exception:
            pass
        if proc.poll() is not None:
            break
        time.sleep(.3)
    raise RuntimeError("Probe compile/start failure: " + (p / "probe-server.log").read_text()[-4000:])

def stop():
    if proc and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=15)

def reset():
    api("/test/reset")

def initial(resolution=720):
    rows[:] = [row(A)]
    reset()
    code, html = get("/lite/torbox?serial=1&title=Mock%20Show&original_title=Mock%20Show&year=2020&tmdb_id=123")
    assert code == 200, (code, html)
    ticket = re.search(r"choose\?pick=([a-f0-9]{32})", html)[1]
    code, raw = get("/lite/torbox/choose?pick=" + ticket + "&file=2")
    assert code == 200, (code, raw)
    code, j = api("/lite/torbox/play?pick=" + ticket + "&file=2&resolution=" + str(resolution) + "&audio=0&sub=0")
    assert code == 200, (code, j)
    return j

lib = p / "database" / "torbox-series.json"

try:
    if lib.exists():
        lib.unlink()
    start()
    check("current module compiles", True)
    if args.kino_mock:
        code, ids = api("/externalids?id=KP_361&serial=0")
        check("KinoAPI resolves the numeric suffix of a KP_ identifier through Lampac globalproxy",
              code == 200 and ids == {"imdb_id": "tt0137523", "kinopoisk_id": "361"})
        code, poster = api("/kino/poster?id=550&serial=0&source=tmdb")
        check("KinoAPI returns the Kinopoisk poster", code == 200 and poster.get("poster", "").endswith("/kp/361.jpg"))
        code, poster = api("/kino/poster?id=550&serial=0&source=tmdb&kinopoisk_id=301")
        check("cached title is reused and conflicting IDs do not replace the poster",
              code == 200 and poster.get("poster") is None and state["kino_requests"].count("/api/v1/titles/by/tmdb/550") == 1)
        code, ids = api("/externalids?id=1418&serial=1&source=tmdb")
        check("KinoAPI maps TMDB to IMDb and Kinopoisk", code == 200 and ids.get("imdb_id") == "tt0898266" and str(ids.get("kinopoisk_id")) == "306084")
        with sqlite3.connect(p / "cache/Externalids.sql") as db:
            check("confirmed KinoAPI IMDb mapping is persisted", db.execute("SELECT value FROM ExternalidsSqlModel WHERE id=?", ("1418_1",)).fetchone() == ("tt0898266",))
            db.executemany("INSERT INTO ExternalidsSqlModel (id, value) VALUES (?, ?)", [("4242_0", ""), ("tt9876543210", "0")])
        with ThreadPoolExecutor(max_workers=8) as pool:
            concurrent = list(pool.map(lambda i: api(f"/externalids?id=fixture-{i}&tmdb_id=4242"), range(8)))
        check("concurrent lookups all resolve IDs and share a single upstream request",
              all(reply == (200, {"imdb_id": "tt9876543210", "kinopoisk_id": "987654321"}) for reply in concurrent)
              and state["kino_requests"].count("/api/v1/titles/by/tmdb/4242") == 1)
        with sqlite3.connect(p / "cache/Externalids.sql") as db:
            check("both SQLite placeholders are replaced after concurrent lookups",
                  db.execute("SELECT value FROM ExternalidsSqlModel WHERE id='4242_0'").fetchone() == ("tt9876543210",) and
                  db.execute("SELECT value FROM ExternalidsSqlModel WHERE id='tt9876543210'").fetchone() == ("987654321",))
        for _ in range(2):
            code, poster = api("/kino/poster?id=777777&source=tmdb")
            assert code == 200 and poster.get("poster") is None
        check("KinoAPI 404 is cached", state["kino_requests"].count("/api/v1/titles/by/tmdb/777777") == 1)
        query = "?" + urllib.parse.urlencode({"serial": 1, "title": "Fixture Alternative", "year": 2007, "tmdb_id": 1418, "rjson": "true"})
        state["kino_search"] = ["start"]
        code, html = get("/lite/torbox" + query)
        check("TorBox retries a valid empty search with KinoAPI metadata", code == 200 and state["kino_search"][1:] == ["Fixture Alternative", "Теория большого взрыва"])
        ticket = re.search(r"choose\?pick=([a-f0-9]{32})", html)[1]
        code, html = get("/lite/torbox/choose?pick=" + ticket)
        check("TorBox episode menu includes the localized episode name", code == 200 and "Пилотная серия" in html)
        code, html = get("/lite/torbox" + query)
        check("TorBox caches the successful fallback with its matched titles", code == 200 and len(state["kino_search"]) == 3)
        code, body = api("/lite/pidtor" + query)
        check("PidTor retries a valid empty search with KinoAPI metadata",
              code == 200 and state["kino_search"][3:] == ["Fixture Alternative", "Теория большого взрыва"] and bool(body.get("data")))
        code, html = get("/lite/torbox" + query + "&clarification=1")
        check("manual search remains unchanged", code == 200 and "choose?pick=" not in html and state["kino_search"][5:] == ["Fixture Alternative"])
        state.pop("kino_search")
        reset()

        # Let the earlier fixture requests leave the integration's one-second rate window.
        time.sleep(1.1)
        initial_torrent = items[10]
        items[10] = dict(initial_torrent, files=[
            {"id": season, "name": f"Multi.Pack.S{season:02}E01.mkv"} for season in range(1, 7)
        ])
        rows[:] = [dict(row(A), Title="Multi Pack 1080p")]
        code, html = get("/lite/torbox?title=Multi%20Pack&serial=1&tmdb_id=1418")
        assert code == 200
        ticket = re.search(r"choose\?pick=([a-f0-9]{32})", html)[1]
        code, html = get("/lite/torbox/choose?pick=" + ticket)
        check("full TorBox pack exposes every season and localizes only the first four",
              code == 200 and "Открыть сезон 6" in html and "Название серии сезона 4" in html
              and "Multi.Pack.S06E01.mkv" in html and "Название серии сезона 6" not in html
              and "/api/v1/tv/by/tmdb/1418/seasons/6/episodes" not in state["kino_requests"])
        before = len(state["kino_requests"])
        code, html = get("/lite/torbox/choose?pick=" + ticket + "&season=6")
        check("opening season six fetches only its title data and lists only its episodes",
              code == 200 and "Название серии сезона 6" in html and "Все сезоны" in html
              and "Пилотная серия" not in html and "file=6" in html
              and state["kino_requests"][before:] == ["/api/v1/tv/by/tmdb/1418/seasons/6/episodes"])
        nav, episode = [json.loads(raw) for raw in re.findall(r"data-json='([^']+)'", html)]
        check("season navigation uses a folder and the episode keeps its preview coordinates",
              not nav.get("torbox_preview") and episode.get("torbox_preview")
              and (episode.get("season"), episode.get("episode")) == (6, 1))
        code, _ = get("/lite/torbox/choose?pick=" + ticket + "&season=99")
        check("a season missing from the pack gets a 404", code == 404)
        items[10] = initial_torrent
        reset()

    search = "/lite/torbox?title=Mock%20Empty"
    rows = None
    code, body = api(search)
    check("missing JacRed Results reports a source error", code == 502 and "JacRed" in body["detail"])
    rows = []
    previous_calls = state["parser_calls"]
    code, body = get(search)
    check("empty Results reports no releases after an uncached error",
          code == 200 and "tb_possible=false" in body and "choose?pick=" not in body and state["parser_calls"] == previous_calls + 1)
    year_search = "/lite/torbox?title=1917&original_title=1917&year=2019"
    for label, release, expected in [
        ("numeric film title isn't treated as release year", {"Title": "1917 1080p"}, 200),
        ("conflicting year in release title still filters remakes", {"Title": "1917 (2021) 1080p"}, 404),
        ("structured JacRed year takes precedence", {"Title": "1917 (2021) 1080p", "info": {"relased": 2019}}, 200),
        ("conflicting structured year filters remakes", {"Title": "1917 1080p", "info": {"relased": 2021}}, 404),
    ]:
        rows[:] = [dict(row(A), **release)]
        reset()
        code, html = get(year_search)
        check(label, code == 200 and ("choose?pick=" in html) == (expected == 200))
        code, manual = get(year_search+"&tb_possible=true")
        check(label+" in manual group", code == 200 and ("choose?pick=" in manual) == (expected == 404))
    fourth="d"*40
    rows[:] = [dict(row(h),Title="Mock Quality "+str(res)+"p "+name) for h,res,name in [
        (A,1080,"One"), (B,1080,"Two"), (C,720,"Available"), (fourth,480,"Small")]]
    state["quality_cache"]={A:True,B:True,C:False,fourth:True}
    reset()
    code,html=get("/lite/torbox?title=Mock%20Quality")
    check("multiple cached HD copies demote SD beneath available 720p",code==200 and html.index("720p Available")<html.index("480p Small"))
    state["quality_cache"][B]=False
    reset()
    code,html=get("/lite/torbox?title=Mock%20Quality")
    check("scarce HD copies keep cached SD ahead of uncached alternatives",code==200 and html.index("480p Small")<html.index("720p Available"))
    del state["quality_cache"]
    rows[:] = [dict(row(B), Title="Mock Show S01 2160p Cached"), dict(row(A), Title="Mock Show S01 1080p Own")]
    reset()
    state["episode_parallel"] = True
    code, html = get("/lite/torbox?serial=1&title=Mock%20Show&tb_season=1&tb_episode=2")
    state["episode_parallel"] = False
    check("episode library starts while cache badges are still in flight", code == 200 and state.get("episode_overlap"))
    check("an owned exact episode still ranks before a higher resolution cached release",
          "1080p Own" in html and "2160p Cached" in html and html.index("1080p Own") < html.index("2160p Cached"))
    initial()
    watch = api("/torbox/series/followed")[1]["items"][0]["id"]

    def open_file(file):
        rows[:] = [row(A)]
        reset()
        code, raw = get("/lite/torbox?serial=1&title=Mock%20Show&year=2020")
        ticket = re.search(r"choose\?pick=([a-f0-9]{32})", raw)[1]
        code, choice = get("/lite/torbox/choose?pick=" + ticket)
        get("/lite/torbox/choose?pick=" + ticket + "&file=" + str(file))
        code, j = api("/lite/torbox/play?pick=" + ticket + "&file=" + str(file) + "&resolution=720&audio=0&sub=0")
        assert code == 200, (code, j)
        return j, choice

    items[10]["files"] = [{"id": 1, "name": "Mock.Show.S01E01.A.mkv"}, {"id": 4, "name": "Mock.Show.S01E01.B.mkv"}, {"id": 2, "name": "Mock.Show.S01E02.A.mkv"}, {"id": 5, "name": "Mock.Show.S01E02.B.mkv"}]
    j, choice = open_file(4)
    check("manual choices retain both variants", "S01E01.A" in choice and "S01E01.B" in choice)
    check("autoplay is one file per episode", [x["episode"] for x in j["torbox_series"]["items"]] == [1, 2])
    code, n = api("/torbox/series/play?session=" + j["torbox_series"]["session"] + "&index=1")
    check("next episode keeps B variant", code == 200 and n["url"].endswith("/5.m3u8"))
    items[20] = torrent(B, 20, [1, 2, 3])
    items[20]["files"] = [{"id": 6, "name": "Mock.Show.S01E02.A.mkv"}, {"id": 7, "name": "Mock.Show.S01E02.B.mkv"}]
    rows[:] = [row(B)]
    reset()
    o = api("/torbox/series/updates?id=" + watch)[1]
    code, n = api("/torbox/series/update-play", {"offer": o["offer"], "next": False})
    check("pack update preserves B variant", code == 200 and n["url"].endswith("/7.m3u8"))
    open_file(4)
    items[20]["files"] = [{"id": 6, "name": "Mock.Show.S01E01.C.mkv"}, {"id": 7, "name": "Mock.Show.S01E01.D.mkv"}]
    rows[:] = [row(B)]
    reset()
    o = api("/torbox/series/updates?id=" + watch)[1]
    check("ambiguous update rejects instead of taking first file", api("/torbox/series/update-play", {"offer": o["offer"], "next": False})[0] == 409)
    items[10] = torrent(A, 10, [1, 2])
    initial()
    items[20] = torrent(B, 20, [1, 2, 3])
    rows[:] = [row(B)]
    reset()
    one = api("/torbox/series/updates?id=" + watch)[1]["offer"]
    items[30] = torrent(C, 30, [1, 2, 3])
    rows[:] = [row(C)]
    reset()
    two = api("/torbox/series/updates?id=" + watch)[1]["offer"]
    previous = Mock.do_GET

    def slow_get(self):
        if urllib.parse.urlsplit(self.path).path.endswith("/createstream"):
            time.sleep(.2)
        return previous(self)

    Mock.do_GET = slow_get
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda offer: api("/torbox/series/update-play", {"offer": offer, "next": True}), [one, two]))
    Mock.do_GET = previous
    codes = [x[0] for x in responses]
    check("two same-season offers yield one winner", codes.count(200) == 1 and all(c in [200, 409, 429] for c in codes))
    winner = responses[codes.index(200)][1]["url"].split("/")[3]
    check("saved hash equals winning offer", json.loads(lib.read_text())[watch]["Hash"] == (B if winner == "20" else C))
    items[10]["files"] = [{"id": 1, "name": "Mock.Show.S01E01.A.mkv"}, {"id": 2, "name": "Mock.Show.S01E02.B.mkv"}, {"id": 4, "name": "Mock.Show.S01E02.C.mkv"}, {"id": 3, "name": "Mock.Show.S01E03.A.mkv"}]
    j, _ = open_file(1)
    order = [x["episode"] for x in j["torbox_series"]["items"]]
    check("autoplay stops before ambiguous episode 2", order == [1])
    session = j["torbox_series"]["session"]
    choice = j["torbox_series"]["choice"]
    check("choice contains both episode 2 files", choice["episode"] == 2 and {f["file"] for f in choice["files"]} == {2, 4})
    check("cannot jump to episode 3 by next index", api("/torbox/series/play?session=" + session + "&index=1")[0] == 400)
    check("cannot resume a file outside offered episode", api("/torbox/series/resume?session=" + session + "&file=3")[0] == 409)
    code, resumed = api("/torbox/series/resume?session=" + session + "&file=2")
    check("explicit choice resumes episode 2", code == 200 and resumed["url"].endswith("/2.m3u8") and resumed["torbox_series"]["items"][resumed["torbox_series"]["selected_index"]]["episode"] == 2)
    check("resumed playlist includes episode 3 after episode 2", [x["episode"] for x in resumed["torbox_series"]["items"]] == [1, 2, 3])
    check("saved episode numbers include stopped portion", json.loads(lib.read_text())[watch]["Episodes"] == [1, 2, 3])
    code, external = api("/torbox/series/external", {"session": session, "index": 0})
    check("external playlist cannot bypass ambiguity", code == 200 and [x["episode"] for x in external["playlist"]] == [1])
    external_url = external["playlist"][0]["url"]
    named_filename = 'Mock Show (2020) S01E01 - Часть & #1.mkv'
    named_source = 'https://media.example/uuid?token=named-fixture&x=1%2F2'
    code, named = api('/torbox/infuse/named', {'links': [{'url': named_source, 'filename': named_filename}]})
    check('named Infuse URL carries the encoded episode name', code == 200 and urllib.parse.unquote(named['links'][0]['url'].rsplit('/', 1)[1]) == named_filename)
    named_url = named['links'][0]['url']
    named_redirect = external_relay(named_url)
    if named_redirect != (302, named_source):
        print('Named redirect fixture result:', named_redirect, flush=True)
    check('named Infuse redirect preserves signed target', named_redirect == (302, named_source))
    check("external playlist is saved privately on disk", (p / 'database/torbox-external.json').stat().st_mode & 0o777 == 0o600)
    stop()
    start()
    check("same anonymous external URL works after Lampac restart", external_relay(external_url) == (302, 'https://media.example/10/1.m3u8'))
    check('same named Infuse URL works after Lampac restart', external_relay(named_url) == (302, named_source))
    check("other account cannot revoke a saved external link", api('/torbox/series/external/revoke', {}, identity=second)[1]['revoked'] == 0 and external_relay(external_url)[0] == 302)
    check("owner revokes saved external and named Infuse links", api('/torbox/series/external/revoke', {})[1]['revoked'] == 2)
    stop()
    start()
    check("external revocation survives another Lampac restart", external_relay(external_url)[0] == 410)
    check('named Infuse revocation survives another Lampac restart', external_relay(named_url)[0] == 410)
    check("forget rejects another account", api("/torbox/series/forget", {"id": watch}, identity=second)[0] == 404)
    check("forget succeeds for owner", api("/torbox/series/forget", {"id": watch})[0] == 200)
    inventory = [q for q in state["mylist"] if q.get("limit") == ["1000"]]
    check("shared inventory does not use files=false", bool(inventory) and all(q.get("files") != ["false"] for q in inventory))

    items.clear()
    items[1] = {"id": 1, "hash": "1" * 40, "name": "Idle", "download_present": True, "download_state": "cached", "active": False, "files": [{"id": 3, "name": "A.mkv", "size": 3}, {"id": 1, "name": "B.mkv", "size": 1}, {"id": 2, "name": "C.mkv", "size": 2}]}
    items[2] = {"id": 2, "hash": "2" * 40, "name": "Busy", "download_present": False, "download_finished": False, "download_state": "downloading", "active": True, "progress": 0.4, "files": [{"id": 8, "name": "D.mkv", "size": 8}]}
    reset()
    code, listed = api("/torbox/list?offset=0")
    check("library list omits files", code == 200 and listed["data"] and all("files" not in row for row in listed["data"]))
    check("library list asks files=false without active", state["mylist"][-1].get("files") == ["false"] and "active" not in state["mylist"][-1])
    code, active = api("/torbox/list?offset=0&active=true")
    check("active filter is a separate upstream query", code == 200 and [row["id"] for row in active["data"]] == [2] and state["mylist"][-1].get("active") == ["true"])
    code, empty = api("/torbox/list?offset=100&active=true")
    check("offset is honored for the active filter", code == 200 and empty["data"] == [] and empty["more"] is False)
    code, detail = api("/torbox/item?id=1")
    check("opening a torrent requests and returns files", code == 200 and state["mylist"][-1].get("files") == ["true"] and [f["id"] for f in detail["data"]["files"]] == [3, 1, 2])
    before_mylist = len(state["mylist"])
    unknown_code, unknown_body = api("/torbox/list?offset=0", identity="not-a-user@example")
    unknown_upstream = len(state["mylist"]) != before_mylist
    unknown_library = isinstance(unknown_body, dict) and isinstance(unknown_body.get("data"), list)
    unknown_ok = unknown_code == 200 and isinstance(unknown_body, dict) and unknown_body.get("accsdb") is True and not unknown_library and not unknown_upstream
    before_mylist = len(state["mylist"])
    outside_code, outside_body = api("/torbox/list?offset=0", identity=outsider)
    outside_upstream = len(state["mylist"]) != before_mylist
    outside_library = isinstance(outside_body, dict) and isinstance(outside_body.get("data"), list)
    outside_ok = outside_code == 403 and isinstance(outside_body, dict) and outside_body.get("success") is False and not outside_library and not outside_upstream
    (p / "account-gate.json").write_text(json.dumps({"unknown": {"code": unknown_code, "library": unknown_library, "upstream": unknown_upstream}, "outside_allow": {"code": outside_code, "library": outside_library, "upstream": outside_upstream}}))
    check("unknown lampac identity is denied before torbox", unknown_ok)
    check("lampac account outside allowed_uids gets torbox 403", outside_ok)

    def links(mode, files=(3, 1, 2)):
        state["batch_mode"] = mode
        before = len(state["links"])
        code, payload = api("/torbox/infuse/links", {"id": 1, "files": list(files)})
        return code, payload, state["links"][before:]

    code, payload, calls = links("ok")
    check("batch keeps request order", code == 200 and [x["id"] for x in payload["data"]] == [3, 1, 2] and [x["url"] for x in payload["data"]] == ["https://cdn.example/list/3", "https://cdn.example/list/1", "https://cdn.example/list/2"])
    check("batch uses one post and no per-file request", [c[0] for c in calls] == ["post-batch"] and calls[0][1] == ["3", "1", "2"] and calls[0][2] is False)
    check("batch names come from the torrent files", [x["name"] for x in payload["data"]] == ["A.mkv", "B.mkv", "C.mkv"])
    code, payload, calls = links("map")
    check("batch accepts an id map", code == 200 and [x["url"] for x in payload["data"]] == ["https://cdn.example/map/3", "https://cdn.example/map/1", "https://cdn.example/map/2"] and [c[0] for c in calls] == ["post-batch"])
    code, payload, calls = links("post-missing")
    check("explicit missing post falls through to batch get", code == 200 and [c[0] for c in calls] == ["post-batch", "get-batch"] and [x["id"] for x in payload["data"]] == [3, 1, 2])
    code, payload, calls = links("missing")
    check("unsupported batch uses the previous per-file request", code == 200 and [c[0] for c in calls] == ["post-batch", "get-batch", "single", "single", "single"] and [x["url"] for x in payload["data"]] == ["https://cdn.example/file/3", "https://cdn.example/file/1", "https://cdn.example/file/2"])
    for mode, expected in (("unauthorized", 502), ("limited", 429), ("invalid", 502), ("active-limit", 502), ("unfinished", 502), ("partial", 502), ("duplicate", 502), ("http", 502)):
        code, payload, calls = links(mode)
        check("batch " + mode + " does not fall back", code == expected and not any(c[0] == "single" for c in calls))
    code, payload, calls = links("ok", [1, 1])
    check("duplicate file ids are rejected first", code == 400 and calls == [])
    code, payload, calls = links("ok", list(range(1, 10)))
    check("more than eight files are rejected first", code == 400 and calls == [])
    code, payload, calls = links("ok", [99])
    check("a file outside the torrent makes no link request", code == 400 and calls == [])

    code, payload, calls = links("live-sample", [3, 1])
    check("sanitized live batch schema parses and restores requested order", code == 200 and [x["id"] for x in payload["data"]] == [3, 1] and [c[0] for c in calls] == ["post-batch"])

    state["pause"] = 0.02
    for mode in ["entry-failed", "entry-error"]:
        code, payload, calls = links(mode)
        check("per-file " + mode + " rejects whole batch without fallback", code == 502 and [c[0] for c in calls] == ["post-batch"])
    print("PASS: %d TorBox HTTP assertions" % len(checks), flush=True)

finally:
    stop()
    server.shutdown()
    log.close()
    work.cleanup()
