# Custom candidate based on 1.87.10

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
