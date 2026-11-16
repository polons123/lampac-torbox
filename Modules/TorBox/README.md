# TorBox custom module

This dynamically compiled module lives in `mods/TorBox/` in a release. Its source
list is in `manifest.json`; it has no standalone csproj. The custom release is
based on Lampac 1.90.0. Replacing this module does not upgrade Core.

The restored balancer returns browser HTML. It accepts the legacy `rjson` query
parameter for compatibility, but that parameter does not enable native JSON menus.
Native Apple TV uses its built-in TorBox connection; Lampac account sign-in remains
available separately.

## Configuration

Keep `torbox-private.json` in Lampac's working directory, readable only by the
service user. Replace these example values on the server:

```json
{
  "api_key": "YOUR_TORBOX_KEY",
  "allowed_uids": ["viewer@example.test"],
  "allow_client_credentials": false,
  "allow_external_playlists": true,
  "jacred": {
    "base_url": "https://jacred.example",
    "api_key": "YOUR_JACRED_KEY"
  }
}
```

Lampac account authentication and the TorBox allowlist both apply. Server keys
stay on the server. Client credential mode requires the administrator to set
`allow_client_credentials: true` explicitly in `torbox-private.json` or, when
that file is absent, `torbox-settings.json`. It allows authenticated users to
provide their own TorBox/JacRed settings. Malformed policy files fail closed.
Client-selected parser addresses are restricted to public HTTP/HTTPS addresses.
Never commit either runtime configuration file or database state.

## External playlists

Before installing this module update, set `allow_external_playlists: true` in
`torbox-private.json` if external-player playlists are in use. This is an
administrator-only boolean, also available in `torbox-settings.json` when the
private file is absent. The private file takes precedence. Missing/false disables
creation and resolution of external playlists; an invalid type fails closed.
Client headers cannot override the permission. Policy is read on each request,
so changing it requires no restart. Disabling it suspends access; re-enabling it
restores links that have not been revoked. It does not revoke them.

Links contain a 256-bit random bearer token (32 cryptographic random bytes,
64 hexadecimal characters). Anyone holding a link can use it without signing in.
They have no idle or absolute expiry and survive Lampac/server restarts. Fixed
episode IDs and playback choices are saved atomically in
`database/torbox-external.json` (mode 0600). Include this host-owned file in
backups; the installer preserves `database/`. Only token hashes are stored, and
temporary TorBox stream URLs are regenerated when needed. Server API keys stay
in the existing private configuration. Client-key mode saves the personal key
in this private playlist file so playback can resume after a restart.
Links created before this persistence update must be generated again once.
Server-key changes and removal from the server allowlist invalidate access.
Changing a personal key on a client does not revoke previously issued links.

Use “Отозвать внешние плейлисты” in TorBox settings or authenticated
`POST /torbox/series/external/revoke` to revoke all existing playlists for the
current Lampac UID, including playlists issued with an older personal key.
Revocation remains available when external playlists are disabled. It removes
saved playlists and personal credentials from disk before reporting success,
then clears memory and stream caches without waiting for busy relay locks.
In-flight relays check revocation and clear detached caches on exit. Other
UIDs are unaffected.
Already delivered TorBox stream URLs and playback in progress cannot be revoked
through Lampac. There is no limit on the number of saved external playlists.
The 5000-episode limit per playlist and saved series position behavior remain.

The shared Infuse saver also uses this durable store for series names: it asks
`POST /torbox/infuse/named` to wrap its prepared links before launching Infuse.
Each returned URL ends with the episode filename so Infuse can retain the name
across iCloud sync. This works with links from any saver source when the TorBox
integration is loaded and the account/external-link policy permits it. Movies
keep their existing direct handoff. The wrapper redirects without fetching media
and keeps the exact original signed URL privately on disk. It does not extend
the original provider URL's lifetime. The existing manual revoke button covers
both these named links and external-player playlists. Previously saved Infuse
entries must be saved again. Reload Lampa to load both updated client scripts.

JSON responses through the module guard and external relay responses send
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`. External players
may not honor browser policies. This module-only update does not redact Core,
proxy or JacRed logs: configure those sinks to exclude bearer paths and query
`apikey` values. Never share raw playlist URLs in diagnostic reports.

## Library and playback

- “Мой TorBox” requests `mylist` with `files=false`. Opening a torrent requests
  `files=true`. Inventory used for episode matching keeps file details.
- “Сейчас качается” uses a separate `active=true` request and independent paging.
- Infuse requests up to eight video files from one torrent through `requestdl/batch`.
  Results are validated and restored to the selected order. Unsupported POST
  falls back to GET batch; an unsupported batch endpoint falls back to individual
  requests with concurrency two. Authorization, rate limits, partial responses
  and file-level errors remain errors and do not trigger extra link requests.
- Episode continuation preserves the chosen file variant. An ambiguous next
  episode stops autoplay and requires an explicit choice. Pack updates reject
  ambiguous matches. Forgetting saved series metadata is restricted to its owner.

The batch fixture preserves the observed `{file_id, success, url, error}` schema
with synthetic file IDs and URLs. Regression checks use a local mock API and do
not download real media or change a TorBox account.

## Verification

See [portable checks](../../local/tests/README.md) and the
[release checklist](../../local/README.md). Before a stable release, also check
on actual devices: library paging and active filter; opening a torrent; episode
choice and continuation after reopening; sending one and eight files to Infuse;
playback, seeking and resuming. Automated fixtures do not measure TorBox storage
availability or prove playback on a physical device.
