# Custom release workflow

Commit reviewed sources, create an immutable custom tag, then build from that tag:

```sh
local/build-release.sh 1.90.0 custom-v1.90.0-2
python3 local/test_release_tools.py
```

The first argument is the upstream release; the second is the committed custom ref.
The upstream ZIP is cached by its full download URL under
`${XDG_CACHE_HOME:-$HOME/.cache}/lampac-build/` (override with `LAMPAC_BUILD_CACHE`).
Each reuse verifies its saved SHA-256. A missing or damaged entry is downloaded
again; interrupted/invalid downloads are not cached. Concurrent builds share a lock.
Upload all three files in `dist/` to the custom release. Never publish an archive
built from an older ref while uncommitted runtime patches are installed.

On an existing host, use `local/deploy-host.sh <custom-tag>` from the same tag.
The archive also bundles `release-tools/deploy-host.sh` and its adjacent
`deploy-state.py` helper for subsequent upgrades. Keep this pair together.
It verifies the archive and overlay manifest before invoking the installer, installs
all overlay paths (including protected `mods/` and `wwwroot/`), verifies every hash,
and retains the manifest and version in `/root/lampac-release-state/`.
It also sets the Lampac service's UMask to 0077 and tightens existing current.conf
to 0600. Application files created later are private to the service user by default.
The deployer validates host configuration before downtime. It then stops
Lampac and retains a complete application snapshot (including SQLite files), release
state, checker and systemd drop-in under `${STATE}-backups/deploy.*` (0700).
It holds the optional peer/Plex sync locks during this operation. Other external
writers must be paused separately. Allow disk space for a full copy of the installation.
An error restores that snapshot and attempts to restart a previously active service;
the original failure status is preserved and the backup remains available. An
initially inactive service is left inactive. Uncatchable termination or power loss
requires manual recovery from the retained backup.

Before downloading, extracting and stopping the service, the deployer budgets
space per filesystem for the full snapshot, extracted candidate, the installer's
second download/extraction and application growth, with 512 MiB reserved for
runtime writes. The ZIP download is capped by the remaining budget. Insufficient
space aborts before downtime; existing backups are never deleted automatically.
This conservative estimate does not reserve disk blocks against concurrent writers.

The service identity defaults to `lampac` (`LAMPAC_SERVICE_USER` overrides it and
must match the service unit). Directory access is checked as that user. Only
manifest-managed code directories are created or repaired inside the snapshot;
an accessible private directory keeps its mode. The installation root and
unrelated private directories are not relaxed. Inaccessible roots and symlinked
overlay directories require operator review before downtime.

`/root/lampac_postupdate_check.py --local check` checks the installed code against
that manifest independently of data-only excludes.conf. After a reviewed deployment
and functional checks, run `--local snapshot` to migrate/refresh the baseline.
Snapshot refuses changed release code. Do not exempt module/ files from updates:
the final manifest verification detects stock or mixed-version installations.

For a reviewed small patch deployed without rebuilding the archive, retain the
original release manifest and record `/root/lampac-release-state/overrides.json`:
`release_manifest_sha256` binds the ledger to that exact manifest; `files` maps each
patched path to its original `base_sha256` and reviewed replacement `sha256`.
The checker validates both identities before using the replacement hashes. Refresh
the baseline only after functional checks. The next full verified release deployment
removes this ledger. This records runtime differences without rewriting a published
release or silently accepting arbitrary source drift.

Configuration is host-owned. TorBox settings, plugins and library data are retained. In particular, LampaWeb.cssReplace and
its served dark notification CSS are checked by the post-update tool. Client and
account configuration are never copied from another host or published.
An optional private `/root/lampac-check-hosts.json` supplies the checker fleet roster
as `[["local", null], ["peer", ["ssh", "-tt", "user@host"]]]`.

## Before publication

Run the [portable checks](tests/README.md), including the isolated HTTP harness
against the module extracted from the candidate ZIP. Verify every manifest hash
and confirm `build-info.txt` names the intended source commit. Scan both source
and ZIP for credentials and runtime data; neither belongs in a release. Compare
runtime overlay hashes with the reviewed installation or its deployment receipt.
Preserve the previous three release files for rollback. A release built again
can have different ZIP timestamps; the recorded source commit and per-file
manifest identify its code.

The [optional sync tools](sync/README.md) have separate configuration. Sync examples are source-only and never installed
by the overlay. Actual device playback, seeking, resuming and Infuse handoff
remain manual checks before promoting a candidate to a stable release.

The custom CI workflow checks `custom` pushes and pull requests with synthetic
accounts. It does not deploy, push commits, create tags or publish releases.

See [the 1.90.0 upgrade notes](UPGRADE-1.90.0.md) for TimeCode migration,
Apple TV account binding and rollback requirements. Custom tags are excluded
from the upstream Build workflow to protect the assembled custom ZIP.

## Media components

Tracks bounds ffprobe waiting/execution to 30 seconds by default (operator setting
`Tracks.ffprobe_timeout_seconds`, clamped to 1–120). Successful probes are cached;
only quick empty results retry. A fixed set of 64 lock stripes avoids unbounded
per-URL synchronization state. Swarm status reads existing torrents only, with a
10-second cache. Web playback polls every 10 seconds while active.

PidTor supplies structured metadata alongside its standard HTML/native menu. Web
filters use these fields, not title parsing. List peer counts are JacRed snapshots;
player statistics are separate. `selezen` retains the upstream exclusion.
`infuseSave.js` is the sole Infuse implementation; `/its/its.js` only loads it for
older clients. Rutube accepts categories 4 and 13 by operator choice. AdminPanel,
Telemetry and Tracks are enabled in this custom package. Player metrics obtain
bitrate from HLS; unknown direct-file bitrate is displayed as unavailable.
