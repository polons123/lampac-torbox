# Custom candidate based on 1.90.0

- Merge official Lampac 1.90.0, retaining the existing custom TorBox/media/UI overlay.
- Include TimeCode capability 4: separate action and viewing dates, with automatic
  migration of old typed and legacy JSON databases.
- Include NativeAppleTV sign-in, persistent tokens and binding multiple devices
  to an existing user. See [upgrade notes](UPGRADE-1.90.0.md).
- Verify shared bookmarks/timecodes, account/profile isolation, bans, expiry,
  restart persistence and real HTTP peer/Plex-adapter writes in temporary servers.
- Exclude `custom-*` tags from the inherited upstream Build workflow so it cannot
  overwrite the custom ZIP. CI uses the upstream version from `local/base`.

Physical Apple TV playback and live Plex writes remain device/operator checks.

---

# Previous custom release based on 1.87.10

## TorBox

- Load library rows without file lists; fetch files when opening a torrent.
- Add an independent active-downloads filter with paging reset.
- Request Infuse links in batches of up to eight, retaining file order and
  falling back only when the upstream endpoint is explicitly unsupported.
- Preserve episode variants, stop at ambiguous episodes and reject ambiguous
  pack updates. Serialize competing offers for the same followed season.
- Keep the remove-followed-season action available after a failed update check.
- Correct release quality parsing, unknown-resolution ordering and tag retention.

## Operations and synchronization

- Cache and verify the upstream release ZIP; serialize concurrent cache writers.
- Track reviewed local overrides against the original release manifest and hashes.
- Provide opt-in, configurable peer/Plex sync sources with offline regression checks.
- Separate capability-4 action timestamps from viewing dates, preserve repeated
  tombstone timestamps and process incremental-to-full fallbacks as full runs.
- Add portable module/client tests and a custom-branch CI workflow.

Core remains based on 1.87.10. Capability 4 requires a separate Core upgrade.
Fixtures do not establish physical-device playback or TorBox service reliability.
