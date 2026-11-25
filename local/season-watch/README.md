# SeasonWatch for Lampa

This local CT104 component checks saved releases on two TorrServers and the shared TorBox library. The systemd timer schedules the worker. The dynamic Lampac module exposes the private catalog and registers its client through the host's existing Lampa plugin configuration.

| Source files | Installed location |
| --- | --- |
| `SeasonWatch/` | `/opt/lampac/mods/SeasonWatch/` |
| `worker.py`, `providers.py`, `magnet_meta.py` | `/usr/local/lib/season-watch/` |
| `season-watch.service`, `season-watch.timer` | `/etc/systemd/system/` |

The worker runs as `lampac` with system Python 3 and the libtorrent Python bindings. It reads the two TorrServer endpoints and their authorization headers, JacRed settings and a TMDB API key from `/etc/season-watch/config.json`. The module reads permitted Lampac user IDs from `/etc/season-watch/accounts.json`. TorBox uses the existing Lampac configuration. Keep these files host-managed. The worker writes its state and report in `/var/lib/season-watch/`.

Register the host's `/season-watch.js` URL under `LampaWeb.customPlugins`. The module is loaded dynamically by Lampac. The timer runs at 00:43, 06:43, 12:43 and 18:43 UTC.

Run `python3 -B test_worker.py` from this directory for the isolated worker tests. The browser fixture uses Chromium, Node.js with WebSocket support and the host's installed Lampa web assets.

## Updating a release

Opening **Обновить** previews the candidate, tracker label, new episodes and the versions to be removed in that library. **Подтвердить замену** rechecks the selection and the old versions. A different candidate or deletion list requires a fresh preview. For a series, the destination must contain the newly offered episodes of the selected season. A new release can omit earlier episodes or entire seasons. Previous versions are removed from the provider after verification. Restoring the plugin code cannot restore removed torrents.

Ambiguous series have a **Выбрать сериал в TMDB** action. During a provider outage, the last successful season data is marked with its observation time. The scheduled scan reconciles outstanding removals even after the client closes.

The worker reads the local Lampac HTTP port from `listen.port` in `/opt/lampac/init.conf`. Set the optional integer `lampac_port` in `/etc/season-watch/config.json` to override it. TorBox deletion requests go to loopback.
