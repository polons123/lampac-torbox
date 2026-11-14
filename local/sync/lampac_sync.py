#!/usr/bin/env python3
"""Opt-in peer sync using Lampac's HTTP dump/set APIs.

Read LAMPAC_SYNC_CONFIG (default ~/.config/lampac-sync/peer.json). Host names,
accounts, paths and optional rclone storage are operator-owned. No configuration
is read on import. Run with --dry-run before enabling writes; see README.md.
"""
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

now_ms = lambda: int(time.time() * 1000)

BOOKMARK_CATEGORIES = ["history", "like", "watch", "wath", "book", "look",
                        "viewed", "scheduled", "continued", "thrown"]

HOSTNAME = sys.argv[1] if len(sys.argv) > 1 else None

LOCAL_BASE = "http://127.0.0.1:19092"
CONFIG_PATH = Path(os.environ.get("LAMPAC_SYNC_CONFIG", Path.home() / ".config/lampac-sync/peer.json"))
META_PATH = str(CONFIG_PATH.parent / "state/peer-meta.json")
FULL_EVERY_SEC = 7 * 24 * 3600

ROLE_FILE = str(CONFIG_PATH.parent / "role")
ACCOUNTS_PATH = str(CONFIG_PATH.parent / "accounts.json")

TIMECODE_EPS = 0.01
MAX_ATTEMPTS = 3
RETRY_DELAYS = (1, 3)
RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})
REQUEST_TIMEOUT = 30
RUN_BUDGET_SEC = 300
RCLONE_TIMEOUT = 180
RCLONE_FLAGS = ["--retries", "3", "--low-level-retries", "5",
                "--contimeout", "10s", "--timeout", "5m"]

# hostname -> public HTTPS base URL (peer address for cross-host calls)
PEERS = {}
FULL_RECONCILE_ROLE = None
REMOTE_BASE = ""
DB_DIR = ""
WORK_DIR = str(CONFIG_PATH.parent / "state/work")


def configure(path=None):
    """Load explicit operator settings without starting I/O or creating directories."""
    global LOCAL_BASE, PEERS, ROLE_FILE, ACCOUNTS_PATH, META_PATH, WORK_DIR
    global DB_DIR, REMOTE_BASE, FULL_RECONCILE_ROLE
    path = Path(path or CONFIG_PATH).expanduser().resolve()
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError("configuration must be an object")
    hosts = cfg.get("hosts")
    if not isinstance(hosts, dict) or len(hosts) < 2:
        raise ValueError("at least two named hosts are required")
    def valid_url(value):
        if not isinstance(value, str):
            return False
        parsed = urllib.parse.urlsplit(value)
        return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    if not all(isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_-]+", name) and valid_url(url) for name, url in hosts.items()):
        raise ValueError("invalid host name or base URL")
    local = cfg.get("local_base", "http://127.0.0.1:19092")
    if not valid_url(local):
        raise ValueError("invalid local base URL")
    full_role = cfg.get("full_reconcile_role")
    if full_role is not None and full_role not in hosts:
        raise ValueError("full reconcile role must be a configured host")
    def resolve(value):
        candidate = Path(value).expanduser()
        return str((path.parent / candidate).resolve())
    state = resolve(cfg.get("state_dir", "state"))
    remote = cfg.get("rclone_remote", "")
    if not isinstance(remote, str) or (remote and not cfg.get("database_dir")):
        raise ValueError("rclone requires an explicit database directory")
    LOCAL_BASE = local.rstrip("/")
    PEERS = {name: url.rstrip("/") for name, url in hosts.items()}
    ROLE_FILE = resolve(cfg.get("role_file", "role"))
    ACCOUNTS_PATH = resolve(cfg.get("accounts_file", "accounts.json"))
    META_PATH = resolve(cfg.get("meta_file", str(Path(state) / "peer-meta.json")))
    WORK_DIR = resolve(cfg.get("work_dir", str(Path(state) / "work")))
    DB_DIR = resolve(cfg["database_dir"]) if cfg.get("database_dir") else ""
    REMOTE_BASE = remote
    FULL_RECONCILE_ROLE = full_role


_deadline = None


def set_deadline(seconds):
    global _deadline
    _deadline = time.monotonic() + seconds


def _budget_left():
    return None if _deadline is None else _deadline - time.monotonic()


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")


class AttemptError(Exception):
    """One HTTP attempt failed. retryable/uncertain drive the retry policy."""

    def __init__(self, message, retryable, uncertain):
        super().__init__(message)
        self.retryable = retryable
        self.uncertain = uncertain


# ---------------------------------------------------------------- preconditions
def read_role():
    """-> role string. Missing/unknown role file is a hard error (exit 2)."""
    try:
        with open(ROLE_FILE) as f:
            role = f.read().strip()
    except OSError as e:
        print(f"ERROR role file {ROLE_FILE} unreadable ({type(e).__name__}); refusing to run",
              file=sys.stderr)
        sys.exit(2)
    if role not in PEERS:
        print(f"ERROR role file {ROLE_FILE} has unknown role {role!r}; "
              f"expected one of {sorted(PEERS)}; refusing to run", file=sys.stderr)
        sys.exit(2)
    return role


def load_accounts():
    """-> list of accounts. Anything malformed is a hard error (exit 2)."""
    try:
        with open(ACCOUNTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        print(f"ERROR accounts config {ACCOUNTS_PATH} unreadable ({type(e).__name__}); "
              f"refusing to run", file=sys.stderr)
        sys.exit(2)
    except ValueError as e:
        print(f"ERROR accounts config {ACCOUNTS_PATH} is not valid JSON ({e}); refusing to run",
              file=sys.stderr)
        sys.exit(2)

    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        print(f"ERROR accounts config {ACCOUNTS_PATH} must be an object with an "
              f'"accounts" array; refusing to run', file=sys.stderr)
        sys.exit(2)

    raw = data["accounts"]
    if not raw:
        print(f"ERROR accounts config {ACCOUNTS_PATH} has an empty accounts list; "
              f"refusing to run", file=sys.stderr)
        sys.exit(2)

    accounts = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            print(f"ERROR accounts config {ACCOUNTS_PATH} has a non-string or empty entry; "
                  f"refusing to run", file=sys.stderr)
            sys.exit(2)
        accounts.append(item.strip())

    if len(set(accounts)) != len(accounts):
        print(f"ERROR accounts config {ACCOUNTS_PATH} has duplicate entries; refusing to run",
              file=sys.stderr)
        sys.exit(2)
    return accounts


def check_peers(role):
    """-> list of (peer_name, base) to sync with. Refuses self/same-address peers."""
    peers = [(name, base) for name, base in PEERS.items() if name != role]
    if not peers:
        print(f"ERROR no peer configured for role {role!r}; refusing to run", file=sys.stderr)
        sys.exit(2)
    for name, base in peers:
        if base.rstrip("/") == PEERS[role].rstrip("/"):
            print(f"ERROR peer {name!r} points at this host's own address ({base}); "
                  f"refusing to run", file=sys.stderr)
            sys.exit(2)
    return peers


# ---------------------------------------------------------------- HTTP
def _request_once(method, url, payload):
    left = _budget_left()
    if left is not None and left <= 0:
        raise AttemptError("run budget exhausted", retryable=False, uncertain=False)
    timeout = REQUEST_TIMEOUT if left is None else max(1, min(REQUEST_TIMEOUT, left))
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise AttemptError(f"HTTP {e.code}", retryable=e.code in RETRYABLE_HTTP,
                           uncertain=e.code >= 500)
    except urllib.error.URLError as e:
        reason = e.reason
        timeoutish = (isinstance(reason, (socket.timeout, TimeoutError, ssl.SSLError))
                      or "timed out" in str(reason).lower())
        if timeoutish:
            raise AttemptError("timeout", retryable=True, uncertain=True)
        raise AttemptError(f"connection problem ({type(reason).__name__})",
                           retryable=True, uncertain=False)
    except (socket.timeout, TimeoutError):
        raise AttemptError("timeout", retryable=True, uncertain=True)
    except (ConnectionResetError, BrokenPipeError, http.client.RemoteDisconnected):
        raise AttemptError("connection lost", retryable=True, uncertain=True)
    except ConnectionRefusedError:
        raise AttemptError("connection refused", retryable=True, uncertain=False)
    except ssl.SSLError:
        raise AttemptError("TLS error", retryable=True, uncertain=True)
    except OSError as e:
        raise AttemptError(f"network problem ({type(e).__name__})", retryable=True,
                           uncertain=False)

    try:
        return json.loads(raw)
    except ValueError:
        raise AttemptError("malformed JSON response", retryable=False, uncertain=False)


def _sleep_before_retry(attempt, what):
    delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
    left = _budget_left()
    if left is not None and left <= delay + 5:
        raise AttemptError("run budget exhausted", retryable=False, uncertain=False)
    log(f"  {what}: retry {attempt + 1}/{MAX_ATTEMPTS} in {delay}s after transient failure")
    time.sleep(delay)


def get_json(base, path, params, what):
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    attempt = 0
    while True:
        attempt += 1
        try:
            return _request_once("GET", url, None)
        except AttemptError as e:
            if not e.retryable or attempt >= MAX_ATTEMPTS:
                raise
            _sleep_before_retry(attempt, what)


def post_json_once(base, path, params, rows):
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    return _request_once("POST", url, {"rows": rows})


# ---------------------------------------------------------------- API wrappers
def dump(base, kind, account):
    """kind: 'bookmark' or 'timecode'. -> list of rows, or None on failure/unknown account."""
    try:
        data = get_json(base, f"/{kind}/dump", {"account_email": account},
                        f"{account}/{kind} dump")
    except AttemptError as e:
        log(f"  {account}/{kind}: dump from {base} failed: {e}")
        return None
    if not isinstance(data, dict) or data.get("accsdb"):
        log(f"  {account}/{kind}: {base} rejected account ({data.get('msg') if isinstance(data, dict) else data})")
        return None
    if not isinstance(data.get("rows"), list):
        log(f"  {account}/{kind}: {base} returned no rows array")
        return None
    return data["rows"]


def _bookmarks_applied(current_rows, rows, full):
    """True = the whole requested row is present, False = not applied, None = unconfirmable.

    A full reconcile deliberately rewrites rows whose content already matches, so its outcome
    leaves no observable difference; such a write is never treated as confirmed.
    """
    if full:
        return None
    current = {bookmark_id(row): row for row in current_rows}
    for row in rows:
        existing = current.get(bookmark_id(row))
        if existing is None:
            return False
        if cat_map(existing) != cat_map(row):
            return False
        if changed_at_of(existing) != changed_at_of(row):
            return False
        if existing.get("card") != row.get("card"):
            return False
    return True


def _same_number(left, right):
    try:
        return abs(float(left) - float(right)) <= 0.01
    except (TypeError, ValueError):
        return left == right


def _timecodes_applied(current_rows, rows, full):
    if full:
        return None
    current = {timecode_key(row): row for row in current_rows}
    for row in rows:
        existing = current.get(timecode_key(row))
        if existing is None:
            return False
        if not _same_number(existing.get("watched_at"), row.get("watched_at")):
            return False
        for field in ("position", "percent", "duration"):
            if not _same_number(existing.get(field), row.get(field)):
                return False
        if bool(existing.get("deleted")) != bool(row.get("deleted")):
            return False
        if "changed_at" in row and changed_at_of(existing) != changed_at_of(row):
            return False
    return True


def _rows_applied(current_rows, kind, rows, full):
    if kind == "bookmark":
        return _bookmarks_applied(current_rows, rows, full)
    return _timecodes_applied(current_rows, rows, full)


def push(base, kind, account, rows, dry_run, full=False):
    """True when the server accepted the write (or there was nothing to send)."""
    if not rows:
        return True
    path = "/bookmark/sync" if kind == "bookmark" else "/timecode/set"
    if dry_run:
        log(f"  [dry-run] would push {len(rows)} {kind} row(s) for {account} to {base}")
        return True

    params = {"account_email": account}
    attempt = 0
    while True:
        attempt += 1
        try:
            result = post_json_once(base, path, params, rows)
        except AttemptError as e:
            if e.uncertain:
                # The write may already be applied. Re-read that side before resending so an
                # accepted POST is not repeated (a repeat rewrites rows and bumps updated_at).
                current = dump(base, kind, account)
                if current is None:
                    log(f"  {account}/{kind} -> {base}: outcome unknown ({e}), re-read failed; "
                        f"not resending")
                    return False
                applied = _rows_applied(current, kind, rows, full)
                if applied is True:
                    log(f"  {account}/{kind} -> {base}: already applied after transient {e}; skipped")
                    return True
                if applied is None:
                    log(f"  {account}/{kind} -> {base}: outcome unknown ({e}); forced rewrite "
                        f"cannot be confirmed, not resending")
                    return False
            if not e.retryable or attempt >= MAX_ATTEMPTS:
                log(f"  {account}/{kind} -> {base}: push failed: {e}")
                return False
            try:
                _sleep_before_retry(attempt, f"{account}/{kind} push")
            except AttemptError as stop:
                log(f"  {account}/{kind} -> {base}: push aborted: {stop}")
                return False
            continue

        if not isinstance(result, dict) or result.get("success") is False or (
            "accepted" not in result and result.get("success") is not True
        ):
            log(f"  {account}/{kind} -> {base}: push rejected: {result}")
            return False
        accepted = result.get("accepted")
        if "accepted" in result:
            if isinstance(accepted, bool) or not isinstance(accepted, int) or accepted < 0:
                log(f"  {account}/{kind} -> {base}: push rejected, invalid accepted={accepted!r}")
                return False
            skipped = result.get("skipped")
            if skipped is not None and (isinstance(skipped, bool)
                                        or not isinstance(skipped, int) or skipped < 0):
                log(f"  {account}/{kind} -> {base}: push rejected, invalid skipped={skipped!r}")
                return False
            covered = accepted + (skipped or 0)
            if covered < len(rows):
                log(f"  {account}/{kind} -> {base}: push incomplete: accepted={accepted} "
                    f"skipped={skipped} of {len(rows)}")
                return False
        elif result.get("success") is not True:
            log(f"  {account}/{kind} -> {base}: push rejected, no accepted count: {result}")
            return False
        if kind == "timecode" and (result.get("skipped", 0) or any("changed_at" in row for row in rows)):
            # A successful HTTP response may describe a rejected stale row. Never
            # advance the caller's snapshot unless the requested state is present.
            current = dump(base, kind, account)
            if current is None or _timecodes_applied(current, rows, False) is not True:
                log(f"  {account}/{kind} -> {base}: stored state differs or could not be read; "
                    "conflict must be replanned, not restamped")
                return False
        log(f"  {account}/{kind} -> {base}: accepted={accepted} "
            f"skipped={result.get('skipped')}")
        return True


def cat_map(row):
    if not row:
        return {}
    cats = row.get("categories") or {}
    if not isinstance(cats, dict):
        return {}
    out = {}
    for key, value in cats.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def changed_at_of(row):
    if not row:
        return 0
    try:
        return int(row.get("changed_at") or 0)
    except (TypeError, ValueError):
        return 0


def merge_bookmark_categories(local_row, peer_row):
    """-> (categories, changed_at). See module docstring for why this is not per-category ts."""
    local_cats = cat_map(local_row)
    peer_cats = cat_map(peer_row)
    local_ca = changed_at_of(local_row)
    peer_ca = changed_at_of(peer_row)

    if local_row is None:
        return peer_cats, peer_ca
    if peer_row is None:
        return local_cats, local_ca

    if local_ca == peer_ca:
        merged = dict(local_cats)
        for key, value in peer_cats.items():
            merged[key] = max(merged.get(key, 0), value)
        ca = local_ca
    else:
        if local_ca > peer_ca:
            newer, older, newer_ca = local_cats, peer_cats, local_ca
        else:
            newer, older, newer_ca = peer_cats, local_cats, peer_ca
        merged = dict(newer)
        for key, value in older.items():
            if key in merged:
                merged[key] = max(merged[key], value)
            elif value > newer_ca:
                merged[key] = value
        ca = max(local_ca, peer_ca)

    if merged:
        ca = max(ca, max(merged.values()))
    return merged, ca


def _card_key(card):
    try:
        return json.dumps(card, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(card)


def updated_at_of(row):
    try:
        return int(row.get("updated_at") or 0)
    except (TypeError, ValueError):
        return 0


def pick_card(local_row, peer_row):
    """Order-independent choice: later changed_at wins. On an exact tie the row written last
    (larger updated_at) wins, because a metadata-only edit may keep changed_at unchanged; if
    even that ties, the lexicographically smaller serialized card wins. All three criteria are
    computed from the same two rows, so both hosts pick the same card from swapped inputs."""
    local_ca = changed_at_of(local_row)
    peer_ca = changed_at_of(peer_row)
    local_card = local_row.get("card") if local_row else None
    peer_card = peer_row.get("card") if peer_row else None
    if local_ca > peer_ca:
        return local_card if local_card is not None else peer_card
    if peer_ca > local_ca:
        return peer_card if peer_card is not None else local_card
    if local_card is None:
        return peer_card
    if peer_card is None:
        return local_card
    local_u = updated_at_of(local_row)
    peer_u = updated_at_of(peer_row)
    if local_u != peer_u:
        return local_card if local_u > peer_u else peer_card
    return local_card if _card_key(local_card) <= _card_key(peer_card) else peer_card


def bookmark_id(row):
    return str(row.get("id"))


def plan_bookmarks(local_rows, peer_rows, full):
    local = {bookmark_id(row): row for row in local_rows}
    peer = {bookmark_id(row): row for row in peer_rows}
    to_local, to_peer = [], []
    for cid in set(local) | set(peer):
        local_row = local.get(cid)
        peer_row = peer.get(cid)
        merged, ca = merge_bookmark_categories(local_row, peer_row)
        if ca <= 0 and merged:
            ca = now_ms()
        base = local_row or peer_row
        card = pick_card(local_row, peer_row)
        row = {
            "id": base.get("id"),
            "card": card,
            "categories": merged,
            "changed_at": ca,
        }
        if _bookmark_needs_push(local_row, merged, ca, card, full):
            to_local.append(row)
        if _bookmark_needs_push(peer_row, merged, ca, card, full):
            to_peer.append(row)
    return to_local, to_peer


def _bookmark_needs_push(existing, merged, ca, card, full):
    if existing is None:
        return bool(merged)
    if full:
        return True
    return (cat_map(existing) != merged
            or changed_at_of(existing) != ca
            or existing.get("card") != card)


def timecode_key(row):
    card = str(row.get("card") or "")
    hash_value = row.get("hash")
    if hash_value is not None and hash_value != "":
        return card + "|" + str(hash_value)
    return card + "|id:" + str(row.get("id"))


def watched_at_of(row):
    try:
        return int(row.get("watched_at") or 0)
    except (TypeError, ValueError):
        return 0


def timecode_payload(row):
    payload = dict(row)
    payload.pop("updated_at", None)
    return payload


def _tc_value(row, field):
    try:
        return float(row.get(field) or 0)
    except (TypeError, ValueError):
        return 0.0


def _tc_same(left, right):
    return (abs(_tc_value(left, "position") - _tc_value(right, "position")) <= TIMECODE_EPS
            and abs(_tc_value(left, "percent") - _tc_value(right, "percent")) <= TIMECODE_EPS
            and abs(_tc_value(left, "duration") - _tc_value(right, "duration")) <= TIMECODE_EPS
            and bool(left.get("deleted")) == bool(right.get("deleted")))


def timecode_tie_winner(local_row, peer_row):
    """Symmetric winner for an equal watched_at tie (see module docstring)."""
    local_deleted, peer_deleted = bool(local_row.get("deleted")), bool(peer_row.get("deleted"))
    if local_deleted != peer_deleted:
        return local_row if local_deleted else peer_row
    local_pos, peer_pos = _tc_value(local_row, "position"), _tc_value(peer_row, "position")
    if abs(local_pos - peer_pos) > TIMECODE_EPS:
        return local_row if local_pos < peer_pos else peer_row
    local_pct, peer_pct = _tc_value(local_row, "percent"), _tc_value(peer_row, "percent")
    if abs(local_pct - peer_pct) > TIMECODE_EPS:
        return local_row if local_pct < peer_pct else peer_row
    local_dur, peer_dur = _tc_value(local_row, "duration"), _tc_value(peer_row, "duration")
    if abs(local_dur - peer_dur) > TIMECODE_EPS:
        return local_row if local_dur > peer_dur else peer_row
    key = lambda row: json.dumps([_tc_value(row, "position"), _tc_value(row, "percent"),
                                  _tc_value(row, "duration"), bool(row.get("deleted"))],
                                 sort_keys=True)
    return local_row if key(local_row) <= key(peer_row) else peer_row


def plan_timecodes(local_rows, peer_rows, full, local_cap=3, peer_cap=3):
    try:
        local_cap = int(local_cap or 0)
        peer_cap = int(peer_cap or 0)
    except (TypeError, ValueError):
        local_cap = peer_cap = 3
    if local_cap < 4 and peer_cap < 4:
        return _plan_timecodes_watched(local_rows, peer_rows, full)
    return _plan_timecodes_changed(local_rows, peer_rows, local_cap, peer_cap)


def _plan_timecodes_watched(local_rows, peer_rows, full):
    local = {timecode_key(row): row for row in local_rows}
    peer = {timecode_key(row): row for row in peer_rows}
    to_local, to_peer = [], []
    ties_differ = 0
    ties_resolved = 0
    for key in set(local) | set(peer):
        local_row = local.get(key)
        peer_row = peer.get(key)
        if local_row and peer_row:
            local_ts = watched_at_of(local_row)
            peer_ts = watched_at_of(peer_row)
            if local_ts == peer_ts:
                if _tc_same(local_row, peer_row):
                    continue
                ties_differ += 1
                winner = timecode_tie_winner(local_row, peer_row)
                payload = timecode_payload(winner)
                if not _tc_same(local_row, winner):
                    to_local.append(payload)
                if not _tc_same(peer_row, winner):
                    to_peer.append(payload)
                if not _tc_same(local_row, winner) or not _tc_same(peer_row, winner):
                    ties_resolved += 1
                continue
            winner = local_row if local_ts > peer_ts else peer_row
            if local_ts < peer_ts or full:
                to_local.append(timecode_payload(winner))
            if peer_ts < local_ts or full:
                to_peer.append(timecode_payload(winner))
            continue
        only = local_row or peer_row
        if only.get("deleted"):
            continue
        payload = timecode_payload(only)
        if local_row is None:
            to_local.append(payload)
        else:
            to_peer.append(payload)
    return to_local, to_peer, ties_differ, ties_resolved


def _arbiter(row):
    changed = changed_at_of(row)
    return changed if changed > 0 else watched_at_of(row)


def _timecode_payload_for(row, dest_cap):
    payload = dict(row)
    payload.pop("updated_at", None)
    if int(dest_cap) < 4 or changed_at_of(row) <= 0:
        payload.pop("changed_at", None)
    else:
        payload["changed_at"] = changed_at_of(row)
    return payload


def _cap3_skips(winner, dest):
    incoming = watched_at_of(winner)
    stored = watched_at_of(dest)
    # Legacy Write replaces a missing/zero stamp with its current clock. That
    # would manufacture a new action rather than preserve the winning one.
    return incoming <= 0 or (stored > 0 and incoming < stored)


def _plan_timecodes_changed(local_rows, peer_rows, local_cap, peer_cap):
    """Capability 4 path. Equal playback state is not rewritten, including on a full run."""
    local = {timecode_key(row): row for row in local_rows}
    peer = {timecode_key(row): row for row in peer_rows}
    to_local, to_peer = [], []
    ties_differ = ties_resolved = unresolved = 0

    def push_side(bucket, dest, winner, dest_cap):
        nonlocal unresolved
        if dest is not None and _tc_same(dest, winner) and watched_at_of(dest) == watched_at_of(winner):
            if int(dest_cap) < 4 or changed_at_of(winner) <= 0 or changed_at_of(dest) == changed_at_of(winner):
                return
        if int(dest_cap) < 4 and _cap3_skips(winner, dest or {}):
            unresolved += 1
            return
        bucket.append(_timecode_payload_for(winner, dest_cap))

    for key in set(local) | set(peer):
        local_row = local.get(key)
        peer_row = peer.get(key)
        if local_row and peer_row:
            local_ts = _arbiter(local_row)
            peer_ts = _arbiter(peer_row)
            if local_ts == peer_ts:
                if _tc_same(local_row, peer_row):
                    continue
                ties_differ += 1
                winner = timecode_tie_winner(local_row, peer_row)
                before_local, before_peer = len(to_local), len(to_peer)
                if not _tc_same(local_row, winner) or watched_at_of(local_row) != watched_at_of(winner):
                    push_side(to_local, local_row, winner, local_cap)
                if not _tc_same(peer_row, winner) or watched_at_of(peer_row) != watched_at_of(winner):
                    push_side(to_peer, peer_row, winner, peer_cap)
                if len(to_local) > before_local or len(to_peer) > before_peer:
                    ties_resolved += 1
                continue
            winner = local_row if local_ts > peer_ts else peer_row
            if local_ts < peer_ts:
                push_side(to_local, local_row, winner, local_cap)
            else:
                push_side(to_peer, peer_row, winner, peer_cap)
            continue
        only = local_row or peer_row
        if only.get("deleted"):
            continue
        if local_row is None:
            push_side(to_local, None, only, local_cap)
        else:
            push_side(to_peer, None, only, peer_cap)
    if unresolved:
        log("  timecode: %s row(s) kept; a newer changed_at cannot be stored "
            "while that server still decides by watched_at" % unresolved)
    return to_local, to_peer, ties_differ, ties_resolved


def timecode_capability(base):
    """Unknown is not capability 3: never downgrade conflict rules after a failed read."""
    try:
        data = _request_once("GET", f"{base}/api/capabilities", None)
    except AttemptError:
        return None
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, dict):
        return None
    try:
        value = int(features.get("timecode") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def brief_rows(rows):
    shown = []
    for row in rows[:12]:
        if row.get("hash") is not None:
            shown.append(f"{row.get('card')}/{row.get('hash')}")
        else:
            shown.append(str(row.get("id")))
    text = ",".join(shown)
    extra = len(rows) - len(shown)
    if extra > 0:
        text += f" +{extra}"
    return text


def sync_account_kind(local_base, peer_base, kind, account, dry_run, full):
    local_rows = dump(local_base, kind, account)
    peer_rows = dump(peer_base, kind, account)
    if local_rows is None or peer_rows is None:
        log(f"  {account}/{kind}: dump failed, nothing pushed")
        return False
    ties_differ = ties_resolved = 0
    if kind == "bookmark":
        to_local, to_peer = plan_bookmarks(local_rows, peer_rows, full)
    else:
        local_cap = timecode_capability(local_base)
        peer_cap = timecode_capability(peer_base)
        if local_cap is None or peer_cap is None:
            log(f"  {account}/{kind}: could not determine capabilities; no timecodes written")
            return False
        to_local, to_peer, ties_differ, ties_resolved = plan_timecodes(
            local_rows, peer_rows, full, local_cap, peer_cap)
        if local_cap >= 4 or peer_cap >= 4:
            log(f"  {account}/{kind}: timecode capabilities local={local_cap} peer={peer_cap}")
    log(
        f"  {account}/{kind}: local={len(local_rows)} peer={len(peer_rows)} "
        f"push_to_local={len(to_local)} push_to_peer={len(to_peer)}"
        + (f" equal_ts_differ={ties_differ} equal_ts_resolved={ties_resolved}" if ties_differ else "")
    )
    if to_local:
        log(f"    to local: {brief_rows(to_local)}")
    if to_peer:
        log(f"    to peer: {brief_rows(to_peer)}")
    force_write = full if kind == "bookmark" else full and local_cap < 4 and peer_cap < 4
    ok_peer = push(peer_base, kind, account, to_peer, dry_run, force_write)
    ok_local = push(local_base, kind, account, to_local, dry_run, force_write)
    return ok_peer and ok_local


def load_meta():
    if not os.path.exists(META_PATH):
        return {}
    try:
        with open(META_PATH) as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_meta(meta):
    tmp = META_PATH + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(meta, handle)
    os.replace(tmp, META_PATH)


def full_reconcile_due(hostname, dry_run):
    """Only the configured coordinator performs automatic weekly full rewrites."""
    if "--full" in sys.argv:
        return True
    if hostname != FULL_RECONCILE_ROLE:
        return False
    meta = load_meta()
    last = int(meta.get("last_full_reconcile") or 0)
    now = int(time.time())
    if last <= 0:
        if not dry_run:
            meta["last_full_reconcile"] = now
            save_meta(meta)
            log("Full reconcile baseline stamped; this run is diff-only, next full push in 7 days.")
        return False
    return now - last >= FULL_EVERY_SEC


def stamp_full_reconcile():
    meta = load_meta()
    meta["last_full_reconcile"] = int(time.time())
    save_meta(meta)


def rclone(args, what):
    """Run rclone with bounded retries/timeouts. True only on exit 0."""
    left = _budget_left()
    if left is not None and left <= 0:
        log(f"  {what}: run budget exhausted; rclone not started")
        return False
    timeout = RCLONE_TIMEOUT if left is None else max(1, min(RCLONE_TIMEOUT, left))
    cmd = ["rclone"] + args + RCLONE_FLAGS
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"  {what}: rclone timed out after {timeout}s")
        return False
    except OSError as e:
        log(f"  {what}: rclone could not start ({type(e).__name__})")
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        log(f"  {what}: rclone failed (exit={proc.returncode}): {tail[0][:200]}")
        return False
    return True


# Keys in a storage/backup blob that hold this host's own public URLs. They must never be taken
# from a peer: the plugin list stays tied to each host's own endpoint.
HOST_KEYS = ("plugins",)


def _load_blob(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return text, json.loads(text)
    except (OSError, ValueError):
        return None, None


def keep_host_keys(lpath, rpath, r_mtime, peer_base):
    """Copy a peer blob over lpath without letting it overwrite HOST_KEYS. An existing local
    value is kept; for a brand-new file the peer's value gets the peer domain swapped for ours."""
    _text, old = _load_blob(lpath)
    keep = {k: old[k] for k in HOST_KEYS if isinstance(old, dict) and k in old}
    shutil.copy2(rpath, lpath)
    text, data = _load_blob(lpath)
    if not isinstance(data, dict):
        return
    for k in HOST_KEYS:
        if k in keep:
            data[k] = keep[k]
        elif isinstance(data.get(k), str) and peer_base and HOSTNAME in PEERS:
            data[k] = data[k].replace(peer_base, PEERS[HOSTNAME])
    out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if out != text:
        with open(lpath, "w", encoding="utf-8") as f:
            f.write(out)
        os.utime(lpath, (r_mtime, r_mtime))


def sync_extension_blobs(local_dir, remote_dir, peer_base=None):
    """storage/backup/* localStorage exports: whole-file, newest mtime wins, except HOST_KEYS
    (see keep_host_keys) which stay per host."""
    if os.path.isdir(remote_dir):
        for root, _dirs, files in os.walk(remote_dir):
            for fn in files:
                rpath = os.path.join(root, fn)
                rel = os.path.relpath(rpath, remote_dir)
                lpath = os.path.join(local_dir, rel)
                os.makedirs(os.path.dirname(lpath), exist_ok=True)
                r_mtime = os.path.getmtime(rpath)
                if not os.path.exists(lpath) or os.path.getmtime(lpath) < r_mtime:
                    keep_host_keys(lpath, rpath, r_mtime, peer_base)
                    log(f"  extension blob updated from remote: {rel}")
    if os.path.isdir(local_dir):
        for root, _dirs, files in os.walk(local_dir):
            for fn in files:
                lpath = os.path.join(root, fn)
                rel = os.path.relpath(lpath, local_dir)
                rpath = os.path.join(remote_dir, rel)
                if not os.path.exists(rpath):
                    os.makedirs(os.path.dirname(rpath), exist_ok=True)
                    shutil.copy2(lpath, rpath)


def publish_blobs(remote_dir, dry_run):
    """Upload the live storage_backup directory. Missing/empty source is a warning: nothing to
    publish, and the hub copy is left untouched (no sync/delete)."""
    source = os.path.join(DB_DIR, "storage", "backup")
    if not os.path.isdir(source):
        log(f"  blobs: {source} is missing; nothing uploaded, hub copy left as is")
        return True
    has_file = any(files for _root, _dirs, files in os.walk(source))
    if not has_file:
        log(f"  blobs: {source} is empty; nothing uploaded, hub copy left as is")
        return True
    if dry_run:
        log(f"  [dry-run] would upload {source} to {remote_dir}/storage_backup")
        return True
    return rclone(["copy", source, f"{remote_dir}/storage_backup"], "blob upload")


def publish_heartbeat(remote_dir, dry_run):
    """Upload state.json last. The monitor keys off its ModTime, so this is the commit point."""
    if dry_run:
        log(f"  [dry-run] would publish heartbeat to {remote_dir}/state.json")
        return True
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({}, handle)
        tmp = handle.name
    try:
        return rclone(["copyto", tmp, f"{remote_dir}/state.json"], "heartbeat")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main():
    if "--help" in sys.argv:
        print("Usage: lampac_sync.py <role> [--dry-run] [--full]; configure LAMPAC_SYNC_CONFIG")
        return 0
    try:
        configure()
    except (OSError, ValueError, TypeError) as exc:
        print("ERROR peer configuration unavailable/invalid: " + type(exc).__name__, file=sys.stderr)
        return 2
    dry_run = "--dry-run" in sys.argv
    if not HOSTNAME:
        print(f"Usage: lampac_sync.py <hostname> [--dry-run] [--full] -- hostname must be one of "
              f"{list(PEERS)}", file=sys.stderr)
        return 2

    role = read_role()                       # exits before any network/write on bad role
    if HOSTNAME != role:
        print(f"ERROR hostname argument {HOSTNAME!r} does not match this host's role {role!r}; "
              f"refusing to run", file=sys.stderr)
        return 2
    peers = check_peers(role)                # exits on self/same-address peers
    accounts = load_accounts()               # exits before any network/write on bad config
    set_deadline(RUN_BUDGET_SEC)

    os.makedirs(WORK_DIR, exist_ok=True)
    remote_dir_base = os.path.join(WORK_DIR, "remote")
    os.makedirs(remote_dir_base, exist_ok=True)

    full = full_reconcile_due(HOSTNAME, dry_run)
    if full:
        log("Full reconcile: merged rows are pushed even when content already matches.")
    ok = True

    for peer_name, peer_base in peers:
        log(f"Syncing bookmarks + timecodes with {peer_name} ({peer_base})...")
        for account in accounts:
            for kind in ("bookmark", "timecode"):
                if not sync_account_kind(LOCAL_BASE, peer_base, kind, account, dry_run, full):
                    ok = False

        if not REMOTE_BASE:
            continue

        # Optional extension/localStorage blobs go through the configured rclone hub
        host_remote_dir = os.path.join(remote_dir_base, peer_name)
        if os.path.isdir(host_remote_dir):
            shutil.rmtree(host_remote_dir)
        os.makedirs(host_remote_dir)
        pulled = rclone(["copy", f"{REMOTE_BASE}/{peer_name}", host_remote_dir], "blob pull")
        if not pulled:
            ok = False
        if not dry_run and pulled:
            sync_extension_blobs(os.path.join(DB_DIR, "storage", "backup"),
                                 os.path.join(host_remote_dir, "storage_backup"), peer_base)

    if dry_run:
        shutil.rmtree(remote_dir_base, ignore_errors=True)
        log("Dry run complete, nothing pushed, nothing published.")
        return 1 if not ok else 0

    if not ok:
        # The morning summary treats a fresh state.json as "this host's cron ran".
        # Refreshing it after a failed dump or push hides the failure for hours.
        log("Not publishing heartbeat: bookmark or timecode sync failed.")
        return 1

    if not REMOTE_BASE:
        if full and "--full" not in sys.argv:
            stamp_full_reconcile()
        log("Done (HTTP sync only).")
        return 0

    log("Uploading blobs, then heartbeat marker last...")
    own_remote = f"{REMOTE_BASE}/{HOSTNAME}"
    if not publish_blobs(own_remote, dry_run):
        log("Not publishing heartbeat: blob upload failed.")
        return 1
    if not publish_heartbeat(own_remote, dry_run):
        log("Heartbeat upload failed; peers will see this host as stale.")
        return 1

    if full and "--full" not in sys.argv:
        stamp_full_reconcile()
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
