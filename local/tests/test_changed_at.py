#!/usr/bin/env python3
"""Offline checks for capability-aware timecode synchronization."""
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock

PREP = Path(__file__).resolve().parents[1] / "sync"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SYNC = load(f"{PREP}/lampac_sync.py", "lampac_sync")
PLEX = load(f"{PREP}/plex_lampac_sync.py", "plex_prep")


def row(**kw):
    base = {"card": "10_movie", "hash": "777", "position": 100.0, "percent": 40,
            "duration": 1000.0, "watched_at": 5000, "deleted": False}
    base.update(kw)
    return base


class LegacyPathTests(unittest.TestCase):
    def test_default_caps_keep_weekly_rewrite(self):
        newer = row(watched_at=200, position=10)
        older = row(watched_at=100, position=80)
        full = SYNC.plan_timecodes([newer], [older], True)
        diff = SYNC.plan_timecodes([newer], [older], False)
        self.assertEqual(len(full[0]), 1)
        self.assertEqual(len(full[1]), 1)
        self.assertEqual(diff[0], [])
        self.assertEqual(len(diff[1]), 1)
        self.assertNotIn("changed_at", full[1][0])
        self.assertEqual(full[1][0]["watched_at"], 200)

    def test_default_equal_rows_are_not_pushed(self):
        same = row()
        self.assertEqual(SYNC.plan_timecodes([same], [dict(same)], False)[:2], ([], []))

    def test_capability_read_failure_does_not_guess_three(self):
        with mock.patch.object(SYNC, "_request_once", side_effect=SYNC.AttemptError("down", False, False)):
            self.assertIsNone(SYNC.timecode_capability("http://127.0.0.1:9"))


class ChangedAtTests(unittest.TestCase):
    def plan(self, local, peer, local_cap=4, peer_cap=4, full=False):
        return SYNC.plan_timecodes([local], [peer], full, local_cap, peer_cap)

    def test_newer_changed_at_keeps_old_view_date(self):
        local = row(watched_at=1000, changed_at=9000, deleted=True, position=0, percent=0)
        peer = row(watched_at=8000, changed_at=2000, deleted=False)
        to_local, to_peer, _d, _r = self.plan(local, peer)
        self.assertEqual(to_local, [])
        self.assertEqual(to_peer[0]["watched_at"], 1000)
        self.assertEqual(to_peer[0]["changed_at"], 9000)
        self.assertTrue(to_peer[0]["deleted"])
        self.assertLess(to_peer[0]["changed_at"], 10**12)

    def test_zero_changed_at_falls_back_to_watched_at(self):
        local = row(watched_at=100, changed_at=0, position=10)
        peer = row(watched_at=50, position=80)
        _to_local, to_peer, _d, _r = self.plan(local, peer)
        self.assertEqual(to_peer[0]["watched_at"], 100)
        self.assertEqual(to_peer[0]["position"], 10)

    def test_repeat_after_apply_is_empty(self):
        local = row(watched_at=1000, changed_at=9000, position=10)
        peer = row(watched_at=8000, changed_at=2000, position=80)
        _a, to_peer, _d, _r = self.plan(local, peer)
        again = self.plan(local, to_peer[0])
        self.assertEqual(again[:2], ([], []))

    def test_full_run_does_not_rewrite_equal_rows(self):
        same = row(changed_at=50)
        self.assertEqual(self.plan(same, dict(same), full=True)[:2], ([], []))

    def test_full_run_does_not_rewrite_the_side_that_already_matches(self):
        newer = row(watched_at=100, changed_at=500, position=10)
        older = row(watched_at=400, changed_at=100, position=80)
        to_local, to_peer, _d, _r = self.plan(newer, older, full=True)
        self.assertEqual(to_local, [])
        self.assertEqual(to_peer[0]["watched_at"], 100)
        self.assertEqual(to_peer[0]["changed_at"], 500)
        self.assertEqual(to_peer[0]["position"], 10)

    def test_cap3_destination_strips_changed_at_and_skips_older_view(self):
        local = row(watched_at=100, changed_at=9000, deleted=True, position=0, percent=0)
        peer = row(watched_at=5000, deleted=False)
        with redirect_stdout(io.StringIO()):
            to_local, to_peer, _d, _r = self.plan(local, peer, local_cap=4, peer_cap=3)
        self.assertEqual(to_local, [])
        self.assertEqual(to_peer, [])

    def test_cap3_destination_receives_newer_view_without_changed_at(self):
        local = row(watched_at=9000, changed_at=9000, position=20)
        peer = row(watched_at=1000, position=80)
        _a, to_peer, _d, _r = self.plan(local, peer, local_cap=4, peer_cap=3)
        self.assertNotIn("changed_at", to_peer[0])
        self.assertEqual(to_peer[0]["watched_at"], 9000)
        self.assertEqual(to_peer[0]["position"], 20)

    def test_one_sided_deletion_is_not_created(self):
        only = row(deleted=True, changed_at=10)
        self.assertEqual(SYNC.plan_timecodes([only], [], False, 4, 4)[:2], ([], []))

    def test_applied_check_requires_changed_at_when_sent(self):
        sent = row(changed_at=10)
        current = row(changed_at=9)
        self.assertFalse(SYNC._timecodes_applied([current], [sent], False))
        current["changed_at"] = 10
        self.assertTrue(SYNC._timecodes_applied([current], [sent], False))


class PlexStampTests(unittest.TestCase):
    def test_unwatch_stamp_is_stable_and_split(self):
        previous = {"ts": 5_000_000, "time": 30.0, "duration": 100.0, "percent": 30}
        first = PLEX.unwatch_entry(previous, 3)
        second = PLEX.unwatch_entry(previous, 3)
        self.assertEqual(first, second)
        self.assertEqual(first["ts"], 5_000_001)
        self.assertNotIn("changed_at", first)
        newer = PLEX.unwatch_entry(previous, 4)
        self.assertEqual(newer["view_ts"], 5_000_000)
        self.assertEqual(newer["changed_at"], 5_000_001)
        self.assertTrue(newer["deleted"])
        self.assertEqual(newer["time"], 0.0)

    def test_push_uses_view_time_not_poll_time(self):
        sent = []

        def fake_push(base, kind, account, rows, dry_run, full=False):
            sent.append(rows[0])
            return True

        entry = {"ts": 4_000_000, "time": 12.0, "duration": 100.0, "percent": 12}
        with mock.patch.object(PLEX, "push", fake_push), redirect_stdout(io.StringIO()):
            self.assertTrue(PLEX.push_to_lampac("u", "10_movie", 7, entry, False, 4))
            clear = PLEX.unwatch_entry({"ts": 4_000_000, "duration": 100.0}, 4)
            self.assertTrue(PLEX.push_to_lampac("u", "10_movie", 7, clear, False, 4))
            self.assertTrue(PLEX.push_to_lampac("u", "10_movie", 7, entry, False, 3))
        self.assertEqual(sent[0]["watched_at"], 4_000_000)
        self.assertEqual(sent[0]["changed_at"], 4_000_000)
        self.assertEqual(sent[1]["watched_at"], 4_000_000)
        self.assertEqual(sent[1]["changed_at"], 4_000_001)
        self.assertTrue(sent[1]["deleted"])
        self.assertNotIn("changed_at", sent[2])
        self.assertEqual(sent[2]["watched_at"], 4_000_000)


if __name__ == "__main__":
    unittest.main(verbosity=1)
