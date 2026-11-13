# Optional Lampac and Plex synchronization

These scripts are opt-in examples for Python 3.10+ on Linux. They are not included
in the application overlay and deployment does not install them or create timers.
Importing either script does not read operator configuration or start a sync.
Keep configuration outside the checkout, normally in `~/.config/lampac-sync/`,
with directory mode 0700 and file mode 0600. Back up playback data before the first
write. Each endpoint must expose the Lampac dump/set API to the intended accounts.

## Peer sync

Copy `peer.example.json` to `~/.config/lampac-sync/peer.json`, replace the example
host URLs and set `local_base` to this instance's HTTP address. Paths are resolved
relative to that configuration file. Create the `role` file with this host's name
(`primary` or `secondary`) and copy `accounts.example.json` to `accounts.json`,
using account IDs that both instances recognize.

```sh
python3 local/sync/lampac_sync.py primary --dry-run
python3 local/sync/lampac_sync.py primary
```

`LAMPAC_SYNC_CONFIG` selects another configuration file. The command's role must
match the role file. Configuration failures stop before network requests or state
directory creation. `--dry-run` reads endpoints and may create temporary work
directories, but does not push data or publish a heartbeat. `--full` requests a
full reconciliation. An optional `full_reconcile_role` selects one coordinator
for automatic weekly full reconciliation; by default there is no coordinator.
Prevent overlapping invocations with an external lock such as `flock`.

HTTP sync covers bookmarks and timecodes. Optional extension/localStorage blob
sync requires an operator-configured rclone remote, `rclone_remote`, and explicit
`database_dir`. It is disabled by default. This feature copies backups and can
replace local blobs; use matching account configuration on both peers. Heartbeats
are published only after successful sync and blob upload. Do not run two jobs
against the same state directory concurrently.

## Plex sync

Copy `plex.example.json` to `~/.config/lampac-sync/plex.json`, supply your token and
account, then run:

```sh
python3 local/sync/plex_lampac_sync.py --dry-run --full
python3 local/sync/plex_lampac_sync.py
```

`LAMPAC_PLEX_CONFIG` selects another config. Keep `lampac_sync.py` beside this
script; its HTTP helpers are imported without loading the peer configuration.
`state_file` is relative to the Plex config directory. Run one Plex synchronizer
for a logical account, then let peer sync distribute its Lampac state. Use
`--bootstrap` only when intentionally populating an empty Lampac account.
`--compare` compares incremental and full reads without changing playback state.
Full catalog reconciliation normally runs every six hours. A failed incremental
read falls back to full processing for the entire run. Repeated unwatch detection
retains the original action timestamp instead of making each poll a new action.

## Timestamp policy and limits

The scripts use Lampac HTTP APIs, not direct SQLite writes. They probe the
timecode capability and stop if it cannot be determined. Capability 3 retains
legacy `watched_at` ordering. Capability 4 treats nonzero `changed_at` as the
action timestamp while retaining `watched_at` as the viewing date; old rows with
zero or absent `changed_at` use legacy ordering. Sending to a capability-3 peer
necessarily collapses that distinction.

Plex `lastViewedAt` supplies a viewing date, not a reliable timestamp for every
unwatch or progress change. Locally observed state changes have separate action
stamps, and persisted snapshots prevent unchanged observations from advancing
them. This cannot reconstruct historical action times that Plex never supplied.
Native clients sending `changed_at` and web clients that omit it can therefore
still differ. Upgrade Core separately to gain capability 4; installing these
scripts does not enable it. Offline timestamp, deletion and fallback checks live
in [../tests](../tests/README.md).
