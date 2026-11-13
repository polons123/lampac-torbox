"""Offline regressions for the portable synchronization scripts."""
import copy
import io
import json
import sys
import unittest
import xml.etree.ElementTree as ET
from contextlib import ExitStack, redirect_stdout
from unittest import mock

from test_changed_at import SYNC, PLEX, row


class WriteConfirmation(unittest.TestCase):
    def run_push(self, response, actual, sent=None):
        sent = sent or row(changed_at=9000)
        with mock.patch.object(SYNC, 'post_json_once', return_value=response) as post, \
                mock.patch.object(SYNC, 'dump', return_value=actual) as reread, \
                redirect_stdout(io.StringIO()):
            result = SYNC.push('http://fixture', 'timecode', 'fixture', [sent], False)
        self.assertEqual(post.call_count, 1)
        return result, reread.call_count

    def test_skipped_different_row_is_failure(self):
        self.assertEqual(self.run_push({'accepted': 0, 'skipped': 1}, [row(changed_at=10000)]), (False, 1))

    def test_skipped_already_identical_is_success(self):
        self.assertEqual(self.run_push({'accepted': 0, 'skipped': 1}, [row(changed_at=9000)]), (True, 1))

    def test_accepted_modern_write_requires_actual_state(self):
        self.assertEqual(self.run_push({'accepted': 1, 'skipped': 0}, None), (False, 1))
        self.assertEqual(self.run_push({'accepted': 1, 'skipped': 0}, [row(changed_at=9000)]), (True, 1))

    def test_skipped_legacy_write_is_checked_too(self):
        self.assertEqual(self.run_push({'accepted': 0, 'skipped': 1}, [row(watched_at=6000)], row()), (False, 1))

    def test_unknown_capability_prevents_peer_writes(self):
        with mock.patch.object(SYNC, 'dump', return_value=[row()]), \
                mock.patch.object(SYNC, 'timecode_capability', return_value=None), \
                mock.patch.object(SYNC, 'push') as push, redirect_stdout(io.StringIO()):
            self.assertFalse(SYNC.sync_account_kind('http://one', 'http://two', 'timecode', 'fixture', False, False))
        push.assert_not_called()

    def test_zero_view_date_is_not_restamped_by_legacy_peer(self):
        winner = row(watched_at=0, changed_at=9000, deleted=True)
        with redirect_stdout(io.StringIO()):
            plan = SYNC.plan_timecodes([winner], [row(watched_at=5000)], False, 4, 3)
        self.assertEqual(plan[:2], ([], []))


class PlexIntegration(unittest.TestCase):
    key = ('10_movie', 777)
    skey = '10_movie|777'

    def run_case(self, native, plex=None, previous=None, cap=4, observed=None,
                 full=True, push_ok=True, incremental_incomplete=False,
                 full_incomplete=False, plex_push_ok=True):
        state = previous or {'catalog': {self.skey: {'rating_key': 'rk'}}, 'plex_state': {},
                             'show_meta': {}, 'last_full': 999, 'cursor': 0}
        playback = {} if plex is None else {self.key: plex}
        saved = []
        with ExitStack() as stack:
            def patch(name, **kw):
                return stack.enter_context(mock.patch.object(PLEX, name, **kw))
            patch('load_config', return_value={'plex_url': 'http://fixture', 'plex_token': 'TEST', 'lampac_user': 'fixture'})
            patch('load_state', return_value=copy.deepcopy(state))
            patch('timecode_capability', return_value=cap)
            patch('fetch_plex_state', return_value=({self.key: 'rk'}, playback,
                                                  ['full read failed'] if full_incomplete else []))
            patch('fetch_incremental', return_value=(playback, {}, 0, 1,
                                                   ['page limit'] if incremental_incomplete else []))
            patch('dump', return_value=native)
            to_plex = patch('push_to_plex', return_value=plex_push_ok)
            to_lampac = patch('push_to_lampac', return_value=push_ok)
            patch('read_plex_item', return_value=observed)
            patch('save_state', side_effect=lambda s: saved.append(copy.deepcopy(s)))
            stack.enter_context(mock.patch.object(PLEX.time, 'time', return_value=1000))
            stack.enter_context(mock.patch.object(sys, 'argv', ['plex_sync'] + (['--full'] if full else [])))
            stack.enter_context(redirect_stdout(io.StringIO()))
            code = PLEX._run()
        return code, saved, to_plex, to_lampac

    @staticmethod
    def playback(ts=5000, position=800, percent=80, **kw):
        return dict(ts=ts, time=position, percent=percent, duration=1000, **kw)

    def test_historical_native_view_with_new_action_wins_and_echo_converges(self):
        native = [row(watched_at=1000, changed_at=9000, position=100, percent=10)]
        observed = self.playback(ts=100000, position=100, percent=10, deleted=False)
        code, saved, outgoing, incoming = self.run_case(native, self.playback(), observed=observed)
        self.assertEqual(code, 0)
        outgoing.assert_called_once()
        incoming.assert_not_called()
        self.assertEqual(saved[0]['plex_state'][self.skey]['changed_at'], 9000)
        self.assertEqual(saved[0]['plex_state'][self.skey]['ts'], 100000)
        code, again, outgoing, incoming = self.run_case(native, observed, previous=saved[0])
        self.assertEqual(code, 0)
        outgoing.assert_not_called()
        incoming.assert_not_called()

    def test_native_tombstone_is_retained_and_unmarks_plex(self):
        native = [row(watched_at=1000, changed_at=9000, position=0, percent=0, deleted=True)]
        observed = self.playback(ts=5000, position=0, percent=0, deleted=True)
        code, saved, outgoing, incoming = self.run_case(native, self.playback(), observed=observed)
        self.assertEqual(code, 0)
        self.assertTrue(outgoing.call_args.args[3]['deleted'])
        incoming.assert_not_called()
        code, _, outgoing, incoming = self.run_case(native, None, previous=saved[0])
        self.assertEqual(code, 0)
        outgoing.assert_not_called()
        incoming.assert_not_called()

    def test_new_plex_view_wins_over_native_tombstone(self):
        native = [row(watched_at=1000, changed_at=4000, deleted=True)]
        code, _, outgoing, incoming = self.run_case(native, self.playback(ts=5000))
        self.assertEqual(code, 0)
        outgoing.assert_not_called()
        self.assertEqual(incoming.call_args.args[3]['changed_at'], 5000)

    def test_incremental_eligibility_uses_action_time(self):
        native = [row(watched_at=1000, changed_at=999500)]
        observed = self.playback(ts=1000000, position=100, percent=40, deleted=False)
        code, _, outgoing, _ = self.run_case(native, full=False, observed=observed)
        self.assertEqual(code, 0)
        outgoing.assert_called_once()

    def test_failed_write_does_not_advance_snapshot(self):
        native = [row(watched_at=1000, changed_at=4000)]
        code, saved, _, incoming = self.run_case(native, self.playback(), push_ok=False)
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        incoming.assert_called_once()

    def test_plex_readback_mismatch_does_not_advance_snapshot(self):
        native = [row(watched_at=1000, changed_at=9000)]
        observed = self.playback(position=900)
        code, saved, _, _ = self.run_case(native, self.playback(), observed=observed)
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])

    def test_same_date_remark_after_unwatch_has_stable_successor(self):
        old = {self.skey: self.playback(ts=1000, position=0, percent=0, deleted=True, changed_at=9000)}
        current = {self.skey: self.playback(ts=1000, position=0, percent=100)}
        retry = copy.deepcopy(current)
        PLEX.preserve_plex_stamps(current, old)
        PLEX.preserve_plex_stamps(retry, old)
        self.assertEqual(current[self.skey]['changed_at'], 9001)
        self.assertEqual(retry, current)

    def test_native_deleted_does_not_resurrect_in_legacy_mode(self):
        with mock.patch.object(PLEX, 'dump', return_value=[row(deleted=True)]):
            self.assertEqual(PLEX.fetch_lampac_state('fixture', 3), {})

    def test_unmark_uses_plex_unscrobble(self):
        with mock.patch.object(PLEX, 'plex_call') as call, redirect_stdout(io.StringIO()):
            self.assertTrue(PLEX.push_to_plex('http://fixture', 'TEST', 'rk', {'deleted': True}, False))
        self.assertEqual(call.call_args.args[2], '/:/unscrobble')

    def test_unknown_capability_prevents_plex_writes(self):
        code, saved, outgoing, incoming = self.run_case([row()], cap=None)
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        outgoing.assert_not_called()
        incoming.assert_not_called()

    def test_zero_view_count_with_retained_date_is_cleared(self):
        video = ET.fromstring('<Video viewCount="0" viewOffset="0" lastViewedAt="9000"/>')
        self.assertIsNone(PLEX.view_state(video))

    def test_unwatch_preserves_native_view_date_and_retry_stamp(self):
        old = self.playback(ts=1000, changed_at=9000)
        previous = {'catalog': {self.skey: {'rating_key': 'rk'}},
                    'plex_state': {self.skey: old}, 'last_full': 0}
        native = [row(watched_at=1000, changed_at=9000)]
        code, saved, outgoing, incoming = self.run_case(native, previous=previous)
        self.assertEqual(code, 0)
        tombstone = incoming.call_args.args[3]
        self.assertEqual(tombstone['ts'], 1000)
        self.assertEqual(tombstone['changed_at'], 9001)
        self.assertTrue(tombstone['deleted'])
        code, _, _, retry = self.run_case(native, previous=previous, push_ok=False)
        self.assertEqual(code, 1)
        self.assertEqual(retry.call_args.args[3], tombstone)

    def test_successful_full_fallback_reconciles_native_only_historical_row(self):
        for cap in (3, 4):
            with self.subTest(capability=cap):
                native = [row(watched_at=1000, changed_at=9000)]
                observed = self.playback(ts=1000000, position=100, percent=40, deleted=False)
                code, saved, outgoing, incoming = self.run_case(
                    native, cap=cap, full=False, incremental_incomplete=True, observed=observed)
                self.assertEqual(code, 0)
                outgoing.assert_called_once()
                incoming.assert_not_called()
                self.assertEqual(saved[0]['last_full'], 1000)

    def test_failed_full_fallback_never_writes_or_advances_snapshot(self):
        code, saved, outgoing, incoming = self.run_case(
            [row()], full=False, incremental_incomplete=True, full_incomplete=True)
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        outgoing.assert_not_called()
        incoming.assert_not_called()

    def test_failed_native_only_push_after_fallback_keeps_snapshot(self):
        code, saved, outgoing, incoming = self.run_case(
            [row(watched_at=1000, changed_at=9000)], full=False,
            incremental_incomplete=True, plex_push_ok=False)
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        outgoing.assert_called_once()
        incoming.assert_not_called()

    def test_shared_tombstone_keeps_stamp_without_mutating_previous(self):
        previous = {self.skey: self.playback(ts=1000, position=0, percent=0,
                                           deleted=True, changed_at=9000)}
        original = copy.deepcopy(previous)
        current = dict(previous)
        PLEX.preserve_plex_stamps(current, previous)
        self.assertEqual(current[self.skey]['changed_at'], 9000)
        self.assertEqual(previous, original)
        self.assertIsNot(current[self.skey], previous[self.skey])

    def test_repeated_incrementals_do_not_age_tombstone_or_block_new_native_mark(self):
        previous = {'catalog': {self.skey: {'rating_key': 'rk'}}, 'show_meta': {},
                    'plex_state': {self.skey: self.playback(ts=1000, position=0, percent=0,
                                                          deleted=True, changed_at=9000)},
                    'last_full': 999, 'cursor': 0}
        native = [row(watched_at=1000, changed_at=9000, deleted=True, position=0, percent=0)]
        for _ in range(5):
            code, saved, outgoing, incoming = self.run_case(native, previous=previous, full=False)
            self.assertEqual(code, 0)
            outgoing.assert_not_called()
            incoming.assert_not_called()
            self.assertEqual(saved[0]['plex_state'][self.skey]['changed_at'], 9000)
            previous = saved[0]
        # A native event only 1ms newer must still win after those quiet polls.
        native = [row(watched_at=1000, changed_at=9001, position=0, percent=100)]
        observed = self.playback(ts=1000000, position=0, percent=100, deleted=False)
        code, saved, outgoing, incoming = self.run_case(
            native, previous=previous, full=False, observed=observed)
        self.assertEqual(code, 0)
        outgoing.assert_called_once()
        incoming.assert_not_called()
        self.assertEqual(saved[0]['plex_state'][self.skey]['changed_at'], 9001)


if __name__ == '__main__':
    unittest.main()
