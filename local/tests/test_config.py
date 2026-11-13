"""Operator configuration is portable and fails before network access."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_changed_at import SYNC, PLEX


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "peer.json"
        self.settings = {"hosts": {"primary": "https://primary.example/", "secondary": "https://secondary.example"}}
        for module, names in [(SYNC, "LOCAL_BASE PEERS ROLE_FILE ACCOUNTS_PATH META_PATH WORK_DIR DB_DIR REMOTE_BASE FULL_RECONCILE_ROLE"), (PLEX, "LOCAL_BASE STATE_PATH")]:
            for name in names.split():
                patch = mock.patch.object(module, name, getattr(module, name))
                patch.start()
                self.addCleanup(patch.stop)

    def configure(self):
        self.config.write_text(json.dumps(self.settings))
        SYNC.configure(self.config)

    def test_relative_paths_and_no_default_blob_backend(self):
        self.configure()
        self.assertEqual(SYNC.PEERS["primary"], "https://primary.example")
        self.assertEqual(SYNC.ACCOUNTS_PATH, str(self.root / "accounts.json"))
        self.assertEqual(SYNC.META_PATH, str(self.root / "state/peer-meta.json"))
        self.assertEqual(SYNC.REMOTE_BASE, "")
        self.assertIsNone(SYNC.FULL_RECONCILE_ROLE)
        self.assertFalse((self.root / "state").exists())

    def test_named_coordinator_and_optional_blob_backend(self):
        self.settings.update(full_reconcile_role="secondary", state_dir="custom-state", rclone_remote="backup:lampac", database_dir="database")
        self.configure()
        self.assertEqual(SYNC.FULL_RECONCILE_ROLE, "secondary")
        self.assertEqual(SYNC.DB_DIR, str(self.root / "database"))
        self.assertEqual(SYNC.META_PATH, str(self.root / "custom-state/peer-meta.json"))

    def test_invalid_configuration_rejected(self):
        variants = [[], {}, {"hosts": {"only": "https://one.example"}},
                    {"hosts": {"primary": "https://user:secret@one.example", "secondary": "https://two.example"}},
                    dict(self.settings, full_reconcile_role="absent"),
                    dict(self.settings, rclone_remote="backup:lampac")]
        for settings in variants:
            with self.subTest(settings=settings):
                self.settings = settings
                with self.assertRaises(ValueError):
                    self.configure()

    def test_missing_config_does_not_contact_peer(self):
        with mock.patch.object(SYNC, "CONFIG_PATH", self.config), mock.patch.object(SYNC, "_request_once") as request, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(SYNC.main(), 2)
            request.assert_not_called()
        self.assertFalse((self.root / "state").exists())

    def test_plex_uses_own_config_and_state_path(self):
        self.config.write_text(json.dumps({"plex_url": "http://127.0.0.1:32400", "lampac_url": "https://lampac.example/", "state_file": "private/state.json"}))
        with mock.patch.object(PLEX, "CONFIG_PATH", self.config):
            PLEX.load_config()
        self.assertEqual(PLEX.LOCAL_BASE, "https://lampac.example")
        self.assertEqual(PLEX.STATE_PATH, str(self.root / "private/state.json"))
        self.assertFalse((self.root / "private").exists())

    def test_plex_invalid_config_fails_without_network(self):
        self.config.write_text(json.dumps({"plex_url": "http://user:secret@plex.example"}))
        with mock.patch.object(PLEX, "CONFIG_PATH", self.config), mock.patch.object(PLEX, "timecode_capability") as request, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(PLEX._run(), 1)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
