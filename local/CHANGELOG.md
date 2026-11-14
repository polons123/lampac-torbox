# custom-v1.90.0-2 — restore existing TorBox integration

Restored TorBox balancer, browser playback, My TorBox and settings from the November 13 sources. Removed only November 14 TorBox native Apple TV experiments. Deployment preserves TorBox configuration and library data. Current Lampac 1.90, TimeCode 4, authentication and media fixes remain.

# Local changes

## Deployment review follow-up

- Budget free space before download, extraction and downtime, including the
  full snapshot, installer staging, application growth and a 512 MiB reserve.
  Cap the archive download; never delete backups to make room automatically.
- Verify directory access as the service user and repair only manifest-managed
  code directories. Preserve accessible private modes and reject inaccessible
  installation roots or symlinked code directories before downtime.
- Cover real unprivileged traversal and low-space failures; remove the unused
  migration marker and empty migration/apply plumbing. TorBox data is retained.
- Keep `rjson` accepted for compatibility and document its browser HTML response.

## Media reliability follow-up

- Bound ffprobe execution and queue time with a shared deadline; drain stdout and
  stderr concurrently, kill timed-out process trees, and retain 64 permanent lock
  stripes instead of racing semaphore removal.
- Read swarm status with TorrServer's read-only torrent-get operation. Include
  serial URLs and update only the active web player's statistics, cancelling on close.
- Add structured PidTor resolution/HDR/voice/tracker metadata for HTML and native
  menus; support 480p/1440p and literal punctuation, restore the upstream selezen
  exclusion, and label list seed/peer counts as JacRed snapshots.
- Replace the duplicate Infuse implementation with a small compatibility loader.
- Allow existing sync metadata/work paths in private configuration; optional sync
  scripts remain outside the application overlay.
- Retain both Rutube categories 4 (Movies) and 13 (Other) by explicit operator
  choice; retain null-category protection.

Browser JS statistics/filters do not extend the native tvOS interface. Direct MP4
bitrate remains unavailable unless reliable metadata is supplied. AdminPanel,
Telemetry and Tracks remain enabled; this follow-up does not change their exposure.

## Superseded: erroneous TorBox removal in custom-v1.90.0-1

The removal was broader than requested. The correction above restores the prior web integration; only the native TorBox experiments should have been removed.

## custom-v1.90.0-1-local — installed locally, not published

- Merge official Lampac 1.90.0, retaining custom media and client changes.
- Include TimeCode capability 4: separate action and viewing dates, with automatic
  migration of old typed and legacy JSON databases.
- Include NativeAppleTV sign-in, persistent tokens and binding multiple devices
  to an existing user. See [upgrade notes](UPGRADE-1.90.0.md).
- Verify shared bookmarks/timecodes, account/profile isolation, bans, expiry,
  restart persistence and real HTTP peer/Plex-adapter writes in temporary servers.
- Exclude `custom-*` tags from the inherited upstream Build workflow so it cannot
  overwrite the custom ZIP. CI uses the upstream version from `local/base`.

Deployment retains a stopped snapshot and restores it on failure. The correction
disables TorBox removal; subsequent deployments preserve its configuration and data.

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
