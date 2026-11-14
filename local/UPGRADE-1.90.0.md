# Custom 1.90.0 upgrade notes

This candidate includes the official 1.90.0 Core, NativeAppleTV and TimeCode 4,
with custom media and client changes. The custom TorBox integration has been
removed; use the native client connection. A full package upgrade is required.

## Before installing

Back up the previous application/release state and take consistent snapshots of
the databases, including SQLite WAL state. Also preserve `init.conf`, `users.json`
when present, `excludes.conf` and the configured
sync scripts/state. Keep each host's own configuration and credentials.
Deploy one host at a time and verify it before upgrading its peer.

TimeCode adds `changed_at` automatically. A row with zero or absent `changed_at`
continues to use `watched_at` for arbitration; native clients may provide separate
action and viewing timestamps. The web client continues using its legacy stamp.
The timestamp-aware peer/Plex scripts support both capabilities 3 and 4. Keep
the existing configured scripts; source examples in `local/sync/` do not install
themselves and must not overwrite operator configuration.

The installer protects `users.json` from rsync deletion. The custom deployer
sets service UMask 0077. Preserve `users.json` in subsequent backups because it
contains the device tokens issued by NativeAppleTV. Do not publish that file.

## One-time removal of the custom TorBox integration

Before stopping Lampac, the deployer requires strict JSON `init.conf`, validates
its structure and prepares changes. Unsupported/commented JSON or `init.yaml`
requires manual review; no files are deleted and no service is stopped on that error.

The migration retires these paths, after saving the full stopped installation:

- `mods/TorBox/` and `wwwroot/plugins/torbox/`;
- `torbox-private.json` and `torbox-settings.json`;
- `database/torbox-library.json` and `database/torbox-series.json`.

It removes the top-level `TorBox` setting, plugin URLs whose exact path is
`/torbox.js` or `/plugins/torbox/torbox-cache-refresh.js`, and only known
TorBox file/directory entries in `excludes.conf`. Comments and unrelated entries
containing the word TorBox remain. Only changed configs are replaced atomically,
retaining permissions and ownership. No remote TorBox account or files are changed.

Only a detected legacy installation triggers a pre-downtime scan of
`cache/static/*.js` (including subdirectories). Files must contain the explicit
old Online/TorBox script markers; a plain mention of TorBox is insufficient.
After a successful deployment, `migrations/torbox-removal-v1.done` in the release
state prevents subsequent scans, unless legacy paths have reappeared. With no
legacy configuration or paths, configs and cache remain untouched.

Backups are retained under `${LAMPAC_RELEASE_STATE}-backups/deploy.*` by default
(actual default: `/root/lampac-release-state-backups/`). The directory is 0700
and contains private data; never publish it. On failure the EXIT handler restores
the application, release metadata, checker and systemd drop-in, reloads systemd,
and attempts to restart a previously active Lampac. If recovery fails, use the
reported backup manually. The original error code is returned. Catchable INT/TERM
signals use the same recovery; SIGKILL and power loss cannot run this handler.

## Bind Apple TV to an existing account

NativeAppleTV is enabled by its upstream module manifest. Its public source
manifest is under `/api/capabilities`. Code confirmation requires the Lampac
administrator password; the bot hook is closed until `bot_secret` is configured.

During confirmation, enter the **existing Lampac user ID** in the optional user
field, for example `viewer@example.test`. Matching normalizes case and whitespace.
This appends the device token to that user's aliases, preserving the canonical
identity, history, bookmarks, expiry and ban. Use
the same canonical user on every instance where that person's history belongs.
An empty user field creates a separate account; it does not inherit an existing
user's data. Tokens issued on one host are not automatically
issued on its peer; sign in separately or use an operator-managed account setup.

Behind a reverse proxy, explicitly set `NativeAppleTV.public_url` to the intended
public HTTPS base URL if automatic QR links use the wrong scheme, host or port.
Only configure a bot secret when integrating a trusted bot. No credentials,
tokens, public URLs or bot settings are supplied by this custom release.

## Verify and roll back

Confirm version, manifest hashes, module compilation, `/api/capabilities`
(`features.timecode = 4` and the native source manifest), account authentication,
configured sync dry-runs. Bind a device to the
intended account and verify its history before adding more devices. Check real
playback, seeking and resume separately on the device.

For rollback, stop the application and relevant sync jobs, restore the matching
previous application/release state and consistent database snapshots, then
restore the host's configuration/token files if those changed. Do not replace
an active SQLite database or assume downgrading binaries reverses migrations.
Resuming old sync state against newer data needs a reviewed reconciliation.

The existing 1.87.10 custom release remains the rollback baseline. This document
does not authorize deployment, account creation or playback-state writes on a
running installation.
