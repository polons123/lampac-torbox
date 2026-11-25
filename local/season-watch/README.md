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
