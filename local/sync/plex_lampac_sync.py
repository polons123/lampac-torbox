#!/usr/bin/env python3
"""Opt-in Plex/Lampac HTTP watch-state sync.

Uses LAMPAC_PLEX_CONFIG (default ~/.config/lampac-sync/plex.json). Viewing dates
and capability-4 action stamps remain distinct; see README.md for limitations.
"""
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lampac_sync import LOCAL_BASE, dump, push, timecode_capability  # noqa: E402

CONFIG_PATH = Path(os.environ.get("LAMPAC_PLEX_CONFIG", Path.home() / ".config/lampac-sync/plex.json"))
MIN_DIFF_SECONDS = 30
MIN_PERCENT_DIFF = 2
WATCHED_PERCENT = 95
MIN_SEED_SECONDS = 60  # Plex silently ignores /:/progress for near-zero offsets -- not worth seeding
STATE_PATH = str(CONFIG_PATH.parent / "state/plex.json")
FULL_EVERY_SEC = 6 * 3600      # periodic full reconcile (also catches unwatch/deletions)
OVERLAP_SEC = 30 * 60          # re-read window for incremental passes
PAGE_SIZE = 100
MAX_PAGES = 20
COMPARE_WINDOW_SEC = 7 * 86400


def worth_pushing(entry):
    return entry["percent"] >= WATCHED_PERCENT or entry["time"] >= MIN_SEED_SECONDS


def log(msg):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")


def java_hashcode(s):
    """Lampa's Java-style signed 32-bit hash, normalized with Math.abs()."""
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return abs(h)


def load_config():
    global LOCAL_BASE, STATE_PATH
    path = Path(CONFIG_PATH).expanduser().resolve()
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError("configuration must be an object")
    local = cfg.get("lampac_url", "http://127.0.0.1:19092")
    for url in (local, cfg.get("plex_url", "")):
        if not isinstance(url, str):
            raise ValueError("base URL must be a string")
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("invalid base URL")
    LOCAL_BASE = local.rstrip("/")
    STATE_PATH = str((path.parent / cfg.get("state_file", "state/plex.json")).resolve())
    return cfg


# ---------- Plex ----------

class PlexRequestError(Exception):
    """Sanitized Plex failure: never carries the request URL or the token."""

    def __init__(self, operation, kind, status=None):
        self.operation = operation
        self.kind = kind
        self.status = status
        text = f"{operation}: {kind}"
        if status is not None:
            text += f" (HTTP {status})"
        super().__init__(text)


def _plex_request(operation, request, parse):
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return parse(resp.read())
    except urllib.error.HTTPError as e:
        raise PlexRequestError(operation, "HTTP error", e.code)
    except urllib.error.URLError as e:
        raise PlexRequestError(operation, f"connection problem ({type(e.reason).__name__})")
    except ET.ParseError:
        raise PlexRequestError(operation, "malformed XML")
    except Exception as e:  # noqa: BLE001 - message is replaced, never str(e)
        raise PlexRequestError(operation, f"unexpected {type(e).__name__}")


def plex_get_xml(base, token, path, params=None, operation="plex"):
    q = dict(params or {})
    q["X-Plex-Token"] = token
    url = f"{base}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"Accept": "application/xml"})
    return _plex_request(operation, req, ET.fromstring)


def plex_call(base, token, path, params=None, method="GET", operation="plex"):
    q = dict(params or {})
    q["X-Plex-Token"] = token
    url = f"{base}{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, method=method)
    return _plex_request(operation, req, lambda raw: raw)


def tmdb_guid(el):
    for g in el.findall("Guid"):
        gid = g.attrib.get("id", "")
        if gid.startswith("tmdb://"):
            try:
                return int(gid.split("://", 1)[1])
            except ValueError:
                return None
    return None


def view_state(el):
    view_offset = el.attrib.get("viewOffset")
    view_count = el.attrib.get("viewCount")
    if view_offset is None and view_count is None:
        return None
    offset_ms = int(view_offset or 0)
    count = int(view_count or 0)
    if count <= 0 and offset_ms <= 0:
        return None  # Plex may retain lastViewedAt after unscrobble.
    last_viewed = el.attrib.get("lastViewedAt")
    ts = int(last_viewed) * 1000 if last_viewed else 0
    if count > 0 and offset_ms <= 0:
        # fully watched, no in-progress offset -- mirror Lampac's own zeroed convention
        return {"ts": ts, "time": 0.0, "duration": 0.0, "percent": 100}
    time_s = offset_ms / 1000.0
    dur_s = int(el.attrib.get("duration") or 0) / 1000.0
    percent = round(time_s / dur_s * 100) if dur_s else 0
    return {"ts": ts, "time": time_s, "duration": dur_s, "percent": percent}


def fetch_plex_state(base, token):
    """-> (catalog, viewed, incomplete). `incomplete` lists skipped section/allLeaves reads so
    main() can fail the run explicitly instead of treating a partial catalog as complete."""
    catalog = {}
    viewed = {}
    incomplete = []

    sections = plex_get_xml(base, token, "/library/sections", operation="sections")
    movie_keys = [d.attrib["key"] for d in sections.findall("Directory") if d.attrib.get("type") == "movie"]
    show_keys = [d.attrib["key"] for d in sections.findall("Directory") if d.attrib.get("type") == "show"]

    for key_ in movie_keys:
        try:
            r = plex_get_xml(base, token, f"/library/sections/{key_}/all", {"includeGuids": 1},
                             operation=f"section {key_} all")
        except PlexRequestError as e:
            log(f"  WARN section {key_} skipped: {e}")
            incomplete.append(str(e))
            continue
        for v in r.findall("Video"):
            tmdb = tmdb_guid(v)
            if tmdb is None:
                continue
            orig = v.attrib.get("originalTitle") or v.attrib.get("title")
            k = (f"{tmdb}_movie", java_hashcode(orig))
            catalog[k] = v.attrib["ratingKey"]
            st = view_state(v)
            if st:
                viewed[k] = st

    for key_ in show_keys:
        try:
            r = plex_get_xml(base, token, f"/library/sections/{key_}/all", {"includeGuids": 1},
                             operation=f"section {key_} all")
        except PlexRequestError as e:
            log(f"  WARN section {key_} skipped: {e}")
            incomplete.append(str(e))
            continue
        for d in r.findall("Directory"):
            tmdb = tmdb_guid(d)
            if tmdb is None:
                continue
            show_orig = d.attrib.get("originalTitle") or d.attrib.get("title")
            show_rk = d.attrib["ratingKey"]
            try:
                er = plex_get_xml(base, token, f"/library/metadata/{show_rk}/allLeaves",
                                  operation=f"allLeaves ratingKey={show_rk}")
            except PlexRequestError as e:
                log(f"  WARN allLeaves skipped ratingKey={show_rk}: {e}")
                incomplete.append(str(e))
                continue
            for e in er.findall("Video"):
                season = e.attrib.get("parentIndex")
                episode = e.attrib.get("index")
                if season is None or episode is None:
                    continue
                season_i, episode_i = int(season), int(episode)
                sep = ":" if season_i > 10 else ""
                k = (f"{tmdb}_tv", java_hashcode(f"{season_i}{sep}{episode_i}{show_orig}"))
                catalog[k] = e.attrib["ratingKey"]
                st = view_state(e)
                if st:
                    viewed[k] = st

    return catalog, viewed, incomplete


def push_to_plex(base, token, rating_key, entry, dry_run):
    """True when the write was sent, or this is a dry-run.

    urlopen raises on HTTP errors, and the response body is not a success
    schema, so a return without an exception is success. One bad item must
    not abort the rest of the run; the caller exits 1 after the loop.
    The exception text can carry the request URL, token included, so only
    the type and the HTTP status are logged.
    """
    try:
        if entry.get("deleted"):
            log(f"  -> Plex unscrobble (mark unwatched) ratingKey={rating_key}")
            if not dry_run:
                plex_call(base, token, "/:/unscrobble", {"key": rating_key, "identifier": "com.plexapp.plugins.library"}, operation="unscrobble")
        elif entry["percent"] >= WATCHED_PERCENT:
            log(f"  -> Plex scrobble (mark watched) ratingKey={rating_key}")
            if not dry_run:
                plex_call(base, token, "/:/scrobble", {"key": rating_key, "identifier": "com.plexapp.plugins.library"}, operation="scrobble")
        else:
            time_ms = int(entry["time"] * 1000)
            log(f"  -> Plex progress ratingKey={rating_key} time={entry['time']:.0f}s ({entry['percent']}%)")
            if not dry_run:
                plex_call(base, token, "/:/progress", {
                    "key": rating_key, "identifier": "com.plexapp.plugins.library",
                    "time": time_ms, "state": "stopped",
                }, operation="progress")
    except PlexRequestError as e:
        if e.status == 404:
            log(f"  WARN Plex item missing ratingKey={rating_key}; dropping mapping")
            return "gone"
        log(f"  ERROR Plex write failed ratingKey={rating_key}: {e}")
        return False
    except Exception as e:  # noqa: BLE001 - type only, message may carry request data
        log(f"  ERROR Plex write failed ratingKey={rating_key}: unexpected {type(e).__name__}")
        return False
    return True


# ---------- Lampac ----------

def fetch_lampac_state(user, capability=3):
    """-> {(card, item_int): {...}} or None when the dump itself failed.

    None is not an empty library. An empty dict is a valid dump with no live
    rows: zero rows, or only deleted ones. main() refuses both, because either
    one would make the next loop copy every Plex position into Lampac.
    """
    rows = dump(LOCAL_BASE, "timecode", user)
    if rows is None:
        return None
    out = {}
    for r in rows:
        if r.get("deleted") and capability < 4:
            continue
        item = r.get("hash")
        if item is None:
            continue
        try:
            item_i = int(item)
        except (TypeError, ValueError):
            continue
        out[(r["card"], item_i)] = {
            "ts": r.get("watched_at", 0),
            "time": r.get("position", 0),
            "duration": r.get("duration", 0),
            "percent": r.get("percent", 0),
        }
        if capability >= 4:
            out[(r["card"], item_i)].update(
                changed_at=r.get("changed_at", 0), deleted=bool(r.get("deleted")))
    return out


def unwatch_entry(previous, capability):
    """Plex does not timestamp an unwatch.

    The stable mark is one millisecond after the last saved view. It is not the
    click time and it does not move on a later poll. Capability 3 has to put
    that mark in watched_at, because that server skips a strictly older stamp.
    Capability 4 keeps watched_at as the viewing date and puts the mark in
    changed_at. The first run has no saved view, so it does not emit this row.
    """
    view = int(previous.get("ts") or 0)
    entry = {"time": 0.0, "duration": float(previous.get("duration") or 0),
             "percent": 0, "deleted": True}
    if int(capability) >= 4:
        entry["ts"] = view
        entry["view_ts"] = view
        entry["changed_at"] = action_stamp(previous, capability) + 1
    else:
        entry["ts"] = view + 1
    return entry


def action_stamp(entry, capability=4):
    if not entry:
        return 0
    changed = int(entry.get("changed_at") or 0) if capability >= 4 else 0
    return changed if changed > 0 else int(entry.get("ts") or 0)


def preserve_plex_stamps(current, previous):
    """Untimed changes get a stable logical successor, never the current poll time.

    Failed passes keep the prior snapshot, so retries derive the same stamp.
    This is an ordering rule; Plex does not expose the time of every manual mark.
    """
    for key, cached in current.items():
        # Unchanged incremental entries may share objects with the old snapshot.
        # Stamp the new copy, never the comparison baseline.
        entry = dict(cached)
        old = previous.get(key)
        same = old is not None and bool(entry.get("deleted")) == bool(old.get("deleted")) and all(
            entry.get(field) == old.get(field) for field in ("ts", "time", "percent", "duration"))
        if same:
            entry["changed_at"] = action_stamp(old)
        elif old:
            entry["changed_at"] = max(int(entry.get("ts") or 0), action_stamp(old) + 1)
        else:
            entry["changed_at"] = int(entry.get("ts") or 0)
        current[key] = entry


def read_plex_item(base, token, rating_key):
    node = plex_get_xml(base, token, f"/library/metadata/{rating_key}", operation="verify playback")
    video = node.find("Video")
    if video is None:
        raise PlexRequestError("verify playback", "missing video")
    observed = view_state(video) or {"ts": 0, "time": 0.0, "percent": 0, "duration": 0.0}
    observed["deleted"] = int(video.attrib.get("viewCount") or 0) == 0 and int(video.attrib.get("viewOffset") or 0) == 0
    return observed


def push_to_lampac(user, card, item, entry, dry_run, capability=3):
    watched = entry["ts"]
    if entry.get("deleted") and int(capability) >= 4 and entry.get("view_ts") is not None:
        watched = entry["view_ts"]
    row = {
        "card": card, "hash": str(item),
        "position": entry["time"], "duration": entry["duration"],
        "percent": entry["percent"], "watched_at": watched,
    }
    if int(capability) >= 4:
        row["changed_at"] = int(entry["changed_at"] if entry.get("changed_at") is not None else entry["ts"])
    if entry.get("deleted"):
        row["deleted"] = True
        log(f"  -> Lampac TimeCode {card}/{item} clearing watch state")
    else:
        log(f"  -> Lampac TimeCode {card}/{item} time={entry['time']:.0f}s ({entry['percent']}%)")
    return push(LOCAL_BASE, "timecode", user, [row], dry_run)



# ---------- incremental state ----------

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("catalog", {})
            data.setdefault("plex_state", {})
            data.setdefault("show_meta", {})
            return data
    except (OSError, ValueError):
        pass
    return {"catalog": {}, "plex_state": {}, "show_meta": {}, "cursor": 0, "last_full": 0}


def save_state(state):
    Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)


def has_playback(entry):
    """True when the snapshot entry represents real playback progress."""
    if not entry or entry.get("deleted"):
        return False
    return (int(entry.get("ts") or 0) > 0 or float(entry.get("time") or 0) > 0
            or float(entry.get("percent") or 0) > 0)


def cleared(entry):
    """True when Plex has no playback state left (unwatched)."""
    return not has_playback(entry)


def catalog_from_full(catalog_map):
    return {f"{card}|{item}": {"rating_key": str(rk)} for (card, item), rk in catalog_map.items()}


def fetch_show_meta(base, token, show_rk):
    """-> {'tmdb': int|None, 'original_title': str}, or None for an empty response.
    PlexRequestError propagates so the caller can mark the pass incomplete."""
    node = plex_get_xml(base, token, f"/library/metadata/{show_rk}", {"includeGuids": 1},
                        operation=f"show metadata {show_rk}")
    dirs = node.findall("Directory")
    if not dirs:
        return None
    d = dirs[0]
    return {"tmdb": tmdb_guid(d),
            "original_title": d.attrib.get("originalTitle") or d.attrib.get("title")}


def section_keys(base, token):
    sections = plex_get_xml(base, token, "/library/sections", operation="sections")
    movies = [d.attrib["key"] for d in sections.findall("Directory")
              if d.attrib.get("type") == "movie"]
    shows = [d.attrib["key"] for d in sections.findall("Directory")
             if d.attrib.get("type") == "show"]
    return movies, shows


def _incremental_page(base, token, section, kind, start):
    params = {"sort": "lastViewedAt:desc", "X-Plex-Container-Start": start,
              "X-Plex-Container-Size": PAGE_SIZE, "includeGuids": 1}
    if kind == "episode":
        params["type"] = 4
    return plex_get_xml(base, token, f"/library/sections/{section}/all", params,
                        operation=f"section {section} {kind} page {start}")


def fetch_incremental(base, token, state, cutoff):
    """Pages section lists sorted by lastViewedAt:desc and stops at the cutoff.
    -> (viewed, catalog_updates, max_seen_seconds, pages, incomplete). `incomplete` lists
    skipped metadata reads and sections that hit MAX_PAGES before reaching the cutoff, so the
    caller can refuse to advance the cursor or fall back to a full scan."""
    movies, shows = section_keys(base, token)
    show_meta = state.setdefault("show_meta", {})
    viewed = {}
    catalog = {}
    max_seen = 0
    pages = 0
    incomplete = []

    def collect_movie(v, section):
        nonlocal max_seen
        ts = int(v.attrib.get("lastViewedAt") or 0)
        if ts <= 0:
            return
        max_seen = max(max_seen, ts)
        tmdb = tmdb_guid(v)
        if tmdb is None:
            return
        orig = v.attrib.get("originalTitle") or v.attrib.get("title")
        key = (f"{tmdb}_movie", java_hashcode(orig))
        catalog[f"{key[0]}|{key[1]}"] = {"rating_key": v.attrib["ratingKey"]}
        st = view_state(v)
        if st:
            viewed[key] = st

    def collect_episode(e):
        nonlocal max_seen
        ts = int(e.attrib.get("lastViewedAt") or 0)
        if ts <= 0:
            return
        max_seen = max(max_seen, ts)
        gp = e.attrib.get("grandparentRatingKey")
        season, episode = e.attrib.get("parentIndex"), e.attrib.get("index")
        if not gp or season is None or episode is None:
            return
        meta = show_meta.get(gp)
        if meta is None:
            try:
                meta = fetch_show_meta(base, token, gp)
            except PlexRequestError as e:
                log(f"  WARN show metadata skipped ratingKey={gp}: {e}")
                incomplete.append(str(e))
                return
            if meta is None:
                log(f"  WARN show metadata empty ratingKey={gp}")
                incomplete.append(f"show metadata ratingKey={gp}: empty response")
                return
            show_meta[gp] = meta
        if meta.get("tmdb") is None or not meta.get("original_title"):
            return
        si, ei = int(season), int(episode)
        sep = ":" if si > 10 else ""
        key = (f"{meta['tmdb']}_tv", java_hashcode(f"{si}{sep}{ei}{meta['original_title']}"))
        catalog[f"{key[0]}|{key[1]}"] = {"rating_key": e.attrib["ratingKey"]}
        st = view_state(e)
        if st:
            viewed[key] = st

    for section in movies:
        completed = False
        for page in range(MAX_PAGES):
            node = _incremental_page(base, token, section, "movie", page * PAGE_SIZE)
            items = node.findall("Video")
            pages += 1
            stop = False
            for item in items:
                ts = int(item.attrib.get("lastViewedAt") or 0)
                if ts and ts < cutoff:
                    stop = True
                    continue
                collect_movie(item, section)
            if stop or len(items) < PAGE_SIZE:
                completed = True
                break
        if not completed:
            log(f"  WARN section {section} movie: page limit {MAX_PAGES} reached before cutoff")
            incomplete.append(f"section {section} movie: page limit {MAX_PAGES} reached")

    for section in shows:
        completed = False
        for page in range(MAX_PAGES):
            node = _incremental_page(base, token, section, "episode", page * PAGE_SIZE)
            items = node.findall("Video")
            pages += 1
            stop = False
            for item in items:
                ts = int(item.attrib.get("lastViewedAt") or 0)
                if ts and ts < cutoff:
                    stop = True
                    continue
                collect_episode(item)
            if stop or len(items) < PAGE_SIZE:
                completed = True
                break
        if not completed:
            log(f"  WARN section {section} episode: page limit {MAX_PAGES} reached before cutoff")
            incomplete.append(f"section {section} episode: page limit {MAX_PAGES} reached")

    return viewed, catalog, max_seen, pages, incomplete


def compare_mode(base, token, state):
    """Read-only: full scan vs incremental window, no writes and no state save."""
    cutoff = int(time.time()) - COMPARE_WINDOW_SEC
    full_catalog, full_viewed, full_incomplete = fetch_plex_state(base, token)
    inc_viewed, inc_catalog, max_seen, pages, inc_incomplete = fetch_incremental(
        base, token, {"show_meta": dict(state.get("show_meta") or {})}, cutoff)

    expected = {k: v for k, v in full_viewed.items()
                if int(v.get("ts") or 0) // 1000 >= cutoff}
    missing = set(expected) - set(inc_viewed)
    extra = set(inc_viewed) - set(expected)
    field_diff = []
    for k in set(expected) & set(inc_viewed):
        e, i = expected[k], inc_viewed[k]
        if (abs(float(e["time"]) - float(i["time"])) > 0.5
                or e["percent"] != i["percent"] or e["ts"] != i["ts"]):
            field_diff.append(k)

    def anon(keys):
        return sorted(hashlib.sha1(f"{c}|{i}".encode()).hexdigest()[:8] for c, i in list(keys)[:5])

    summary = {"window_sec": COMPARE_WINDOW_SEC, "incremental_pages": pages,
               "incremental_max_seen": max_seen,
               "full_viewed_in_window": len(expected),
               "incremental_viewed": len(inc_viewed),
               "missing": len(missing), "extra": len(extra), "field_diff": len(field_diff),
               "missing_sample": anon(missing), "extra_sample": anon(extra),
               "incomplete_full": len(full_incomplete),
               "incomplete_incremental": len(inc_incomplete)}
    print(json.dumps(summary, indent=1))
    ok = (not missing and not extra and not field_diff
          and not full_incomplete and not inc_incomplete)
    return 0 if ok else 1

# ---------- main ----------

def _run():
    if "--help" in sys.argv:
        print("Usage: plex_lampac_sync.py [--dry-run] [--full] [--compare]; configure LAMPAC_PLEX_CONFIG")
        return 0
    dry_run = "--dry-run" in sys.argv
    bootstrap = "--bootstrap" in sys.argv  # allow copying Plex into an empty Lampac library
    force_full = "--full" in sys.argv
    try:
        cfg = load_config()
    except (OSError, ValueError, TypeError) as e:
        log(f"ERROR config {CONFIG_PATH} unreadable ({type(e).__name__})")
        return 1
    missing = [k for k in ("plex_url", "plex_token", "lampac_user")
               if not isinstance(cfg.get(k), str) or not cfg.get(k)]
    if missing:
        log(f"ERROR config {CONFIG_PATH} is missing required fields: {', '.join(missing)}")
        return 1
    base, token, user = cfg["plex_url"], cfg["plex_token"], cfg["lampac_user"]

    state = load_state()
    if "--compare" in sys.argv:
        return compare_mode(base, token, state)

    capability = timecode_capability(LOCAL_BASE)
    if capability is None:
        log("ERROR Lampac capability unavailable; no playback state written")
        return 1
    log(f"  Lampac timecode capability {capability}")

    now = int(time.time())
    catalog_state = dict(state.get("catalog") or {})
    plex_state = dict(state.get("plex_state") or {})
    prev_plex = dict(plex_state)
    use_full = (force_full or not catalog_state
                or now - int(state.get("last_full") or 0) >= FULL_EVERY_SEC)
    did_full = False

    if use_full:
        did_full = True
        log("Reading Plex state (full scan)...")
        catalog_map, plex_viewed, incomplete = fetch_plex_state(base, token)
        catalog_state = catalog_from_full(catalog_map)
        plex_state = {f"{card}|{item}": {f: entry.get(f) for f in ("ts", "time", "percent", "duration")}
                      for (card, item), entry in plex_viewed.items()}
        cursor = max((int(entry.get("ts") or 0) // 1000 for entry in plex_viewed.values()), default=0)
        last_full = now
        mode = "full"
    else:
        cutoff = max(0, int(state.get("cursor") or 0) - OVERLAP_SEC)
        log(f"Reading Plex changes since {cutoff} (overlap {OVERLAP_SEC}s)...")
        plex_viewed, catalog_updates, max_seen, pages, inc_incomplete = fetch_incremental(
            base, token, state, cutoff)
        if inc_incomplete:
            shown = "; ".join(inc_incomplete[:3])
            log(f"WARN incremental read incomplete ({len(inc_incomplete)}): {shown}; "
                f"falling back to a full scan")
            log("Reading Plex state (full scan, incremental fallback)...")
            did_full = True
            catalog_map, plex_viewed, incomplete = fetch_plex_state(base, token)
            catalog_state = catalog_from_full(catalog_map)
            plex_state = {f"{card}|{item}": {f: entry.get(f) for f in
                                             ("ts", "time", "percent", "duration")}
                          for (card, item), entry in plex_viewed.items()}
            cursor = max((int(entry.get("ts") or 0) // 1000
                          for entry in plex_viewed.values()), default=0)
            last_full = now
            mode = "full (incremental fallback)"
        else:
            incomplete = []
            catalog_state.update(catalog_updates)
            for (card, item), entry in plex_viewed.items():
                plex_state[f"{card}|{item}"] = {f: entry.get(f) for f in
                                                ("ts", "time", "percent", "duration")}
            cursor = max(int(state.get("cursor") or 0), max_seen)
            last_full = int(state.get("last_full") or 0)
            mode = f"incremental pages={pages} cutoff={cutoff}"

    if capability >= 4:
        preserve_plex_stamps(plex_state, prev_plex)
    unwatch = {}
    if did_full and not incomplete:
        for stored_key, previous in prev_plex.items():
            if not has_playback(previous):
                continue
            if stored_key not in catalog_state:
                continue  # item left the library -- not an unwatch event
            if not cleared(plex_state.get(stored_key)):
                continue
            card, _, item = stored_key.rpartition("|")
            try:
                item_i = int(item)
            except ValueError:
                continue
            unwatch[stored_key] = (card, item_i, previous)
            if capability >= 4:
                plex_state[stored_key] = unwatch_entry(previous, capability)
            else:
                plex_state.pop(stored_key, None)
        if capability >= 4:
            for stored_key, previous in prev_plex.items():
                if previous.get("deleted") and stored_key in catalog_state and cleared(plex_state.get(stored_key)):
                    plex_state[stored_key] = dict(previous)
        if unwatch:
            log(f"  Plex unwatch detected for {len(unwatch)} item(s); will clear Lampac state")

    log(f"  {mode}: {len(catalog_state)} catalog items, {len(plex_viewed)} changed/known playback entries")
    if incomplete:
        shown = "; ".join(incomplete[:3])
        log(f"ERROR Plex read incomplete: {len(incomplete)} request(s) skipped ({shown})")
        log("Aborting before any write: the Plex state is incomplete.")
        return 1

    log("Reading Lampac state...")
    lampac_state = fetch_lampac_state(user, capability)
    if lampac_state is None:
        log(f"ERROR Lampac timecode dump failed for {user}, refusing to write")
        return 1
    if not lampac_state:
        if not bootstrap:
            log(f"ERROR Lampac timecode library for {user} has no live rows, refusing to copy Plex playback into it (use --bootstrap for a first load)")
            return 1
        log(f"Lampac timecode library for {user} is empty, --bootstrap: copying Plex playback into it")
    log(f"  {len(lampac_state)} Lampac timecode rows for {user}")

    pushed_to_plex = pushed_to_lampac = 0
    lampac_write_failed = False
    plex_write_failed = False

    def skey(key):
        return f"{key[0]}|{key[1]}"

    all_keys = set(lampac_state)
    all_keys.update((card, item) for card, item, _prev in unwatch.values())
    for stored_key in plex_state:
        card, _, item = stored_key.rpartition("|")
        try:
            all_keys.add((card, int(item)))
        except ValueError:
            continue

    def mapping(key):
        return (catalog_state.get(skey(key)) or {}).get("rating_key")

    def drop_mapping(key):
        catalog_state.pop(skey(key), None)
        plex_state.pop(skey(key), None)

    def send_to_plex(key, entry):
        nonlocal pushed_to_plex, plex_write_failed
        rk = mapping(key)
        if rk is None or (not entry.get("deleted") and not worth_pushing(entry)):
            return
        result = push_to_plex(base, token, rk, entry, dry_run)
        if result == "gone":
            drop_mapping(key)
            return
        if result:
            observed = None
            if capability >= 4 and not dry_run:
                try:
                    observed = read_plex_item(base, token, rk)
                except PlexRequestError as exc:
                    log(f"  ERROR Plex readback failed: {exc}")
                    plex_write_failed = True
                    return
                matches = (bool(observed.get("deleted")) == bool(entry.get("deleted")) and
                           (bool(entry.get("deleted")) or
                            (observed["percent"] >= WATCHED_PERCENT if entry["percent"] >= WATCHED_PERCENT else
                             abs(observed["time"] - entry["time"]) <= MIN_DIFF_SECONDS)))
                if not matches:
                    log("  ERROR Plex readback differs; snapshot not advanced")
                    plex_write_failed = True
                    return
            pushed_to_plex += 1
            if not dry_run:
                if capability >= 4:
                    # Keep the source action stamp across Plex's timestamp change
                    # caused by our own scrobble/progress request.
                    plex_state[skey(key)] = dict(observed, changed_at=action_stamp(entry))
                else:
                    plex_state[skey(key)] = {f: entry.get(f) for f in
                                             ("ts", "time", "percent", "duration")}
        else:
            plex_write_failed = True

    for key in sorted(all_keys):
        card, item = key
        p = plex_state.get(skey(key))
        l = lampac_state.get(key)

        if skey(key) in unwatch and l is not None:
            previous = unwatch[skey(key)][2]
            prev_ts = action_stamp(previous, capability)
            lampac_ts = action_stamp(l, capability)
            if lampac_ts > prev_ts:
                # Conflict rule: Plex has no timestamp for "unwatched", so the previous snapshot's
                # ts is the reference. Lampac activity strictly newer than it wins; never delete it.
                log(f"  Plex unwatch conflicts with newer Lampac progress {card}/{item} "
                    f"(lampac={lampac_ts} > snapshot={prev_ts}); Lampac wins")
                send_to_plex(key, l)
                continue
            tombstone = unwatch_entry(previous, capability)
            if dry_run:
                log(f"  [dry-run] would clear Lampac watch state {card}/{item}")
            elif push_to_lampac(user, card, item, tombstone, False, capability):
                pushed_to_lampac += 1
            else:
                lampac_write_failed = True
            continue

        if p is None:
            # No recorded Plex playback state. Full scans push any Lampac progress; incremental
            # passes only push rows touched after the last full reconcile, so new Lampac
            # activity still reaches Plex without a full scan.
            if l is not None and not l.get("deleted") and (did_full or action_stamp(l, capability) >= last_full * 1000):
                send_to_plex(key, l)
            continue

        if l is None:
            if p.get("deleted"):
                continue
            if push_to_lampac(user, card, item, p, dry_run, capability):
                pushed_to_lampac += 1
            else:
                lampac_write_failed = True
            continue

        diff = (bool(l.get("deleted")) != bool(p.get("deleted"))
                or abs(float(l["time"]) - float(p["time"])) > MIN_DIFF_SECONDS
                or abs(float(l["percent"]) - float(p["percent"])) > MIN_PERCENT_DIFF)
        if not diff:
            continue
        if action_stamp(l, capability) >= action_stamp(p, capability):
            send_to_plex(key, l)
        else:
            if push_to_lampac(user, card, item, p, dry_run, capability):
                pushed_to_lampac += 1
            else:
                lampac_write_failed = True

    log(f"Done. pushed_to_plex={pushed_to_plex} pushed_to_lampac={pushed_to_lampac}{' (dry-run)' if dry_run else ''}")

    failed = lampac_write_failed or plex_write_failed or bool(incomplete)
    if not dry_run and not failed:
        state.update({"cursor": cursor, "last_full": last_full, "catalog": catalog_state,
                      "plex_state": plex_state, "show_meta": state.get("show_meta") or {}})
        save_state(state)
    elif not dry_run:
        log("State not saved: pass had failures.")

    return 1 if failed else 0


def main():
    """Never lets an exception escape: prints a sanitized reason and returns a non-zero code."""
    try:
        return _run()
    except PlexRequestError as e:
        log(f"ERROR Plex request failed: {e}")
        return 1
    except Exception as e:  # noqa: BLE001 - type only; messages may carry request data/token
        log(f"ERROR unexpected {type(e).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
