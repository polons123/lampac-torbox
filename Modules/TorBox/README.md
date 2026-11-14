# TorBox custom module

This dynamically compiled module lives in `mods/TorBox/` in a release. Its source
list is in `manifest.json`; it has no standalone csproj. The custom release is
based on Lampac 1.87.10. Replacing this module does not upgrade Core.

## Configuration

Keep `torbox-private.json` in Lampac's working directory, readable only by the
service user. Replace these example values on the server:

```json
{
  "api_key": "YOUR_TORBOX_KEY",
  "allowed_uids": ["viewer@example.test"],
  "allow_client_credentials": false,
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
