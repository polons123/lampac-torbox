# Infuse named links

Provider-independent redirects for the shared `infuseSave.js` saver, including
TorBox and TorrServer. No TorBox module, configuration, key, permission or API
call is required. Creation and manual revocation require a valid, active Lampac
account. Playback checks the owning account too.

`POST /infuse/named` accepts `{ "links": [{ "url": "https://media.example/file",
"filename": "Show S01E01.mkv" }] }`. It atomically saves validated links before
returning named URLs. `GET`/`HEAD /infuse/file/{filename}?token=...` redirects to
the exact original URL, without fetching media. Names are carried in the actual
URL path to survive Infuse sync. Movies retain their direct handoff.

Tokens contain 256 random bits. Only hashes are saved in the private, atomically
written `database/infuse-links.json` (0600). There is no expiry or link-count
limit. Links survive server restarts; the original media URL can still expire or
become unavailable. TorrServer must remain reachable from the playback device.

Settings → Infuse → «Отозвать ссылки Infuse» calls `POST /infuse/revoke` and
durably removes this account's named links across providers. Deleting an entry
inside Infuse does not notify Lampac. Already started playback may continue.

Previously issued `/torbox/series/external/.../file/...` links remain handled by
the legacy TorBox module and its revoke button; new links use this module only.
Fully reopen Lampa after updating. Existing UUID entries need to be saved again.
Access logs must mask query `token` values and omit redirect `Location` headers.
