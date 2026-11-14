# Custom 1.90.0 upgrade notes

This candidate includes the official 1.90.0 Core, NativeAppleTV and TimeCode 4,
with the existing custom TorBox, media and client changes. It requires a full
custom package upgrade; replacing only `mods/TorBox/` leaves the old Core running.

## Before installing

Back up the previous application/release state and take consistent snapshots of
the databases, including SQLite WAL state. Also preserve `init.conf`, `users.json`
when present, all private TorBox configuration, `excludes.conf` and the configured
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

## Bind Apple TV to an existing account

NativeAppleTV is enabled by its upstream module manifest. Its public source
manifest is under `/api/capabilities`. Code confirmation requires the Lampac
administrator password; the bot hook is closed until `bot_secret` is configured.

During confirmation, enter the **existing Lampac user ID** in the optional user
field, for example `viewer@example.test`. Matching normalizes case and whitespace.
This appends the device token to that user's aliases, preserving the canonical
identity, history, bookmarks, expiry, ban and TorBox allowlist membership. Use
the same canonical user on every instance where that person's history belongs.
An empty user field creates a separate account; it does not inherit an existing
user's data or TorBox access. Tokens issued on one host are not automatically
issued on its peer; sign in separately or use an operator-managed account setup.

Behind a reverse proxy, explicitly set `NativeAppleTV.public_url` to the intended
public HTTPS base URL if automatic QR links use the wrong scheme, host or port.
Only configure a bot secret when integrating a trusted bot. No credentials,
tokens, public URLs or bot settings are supplied by this custom release.

## Verify and roll back

Confirm version, manifest hashes, module compilation, `/api/capabilities`
(`features.timecode = 4` and the native source manifest), account authentication,
TorBox list/item/filter, and configured sync dry-runs. Bind a device to the
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
