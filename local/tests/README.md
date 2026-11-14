# Portable custom checks

Run from the repository root on Linux. Offline checks use Python's standard
library and Node.js; no application account or running service is needed:

```sh
python3 -m unittest discover -s local/tests -p 'test_*.py'
python3 local/test_release_tools.py
python3 local/test_build_cache.py
node local/tests/torbox_library.cjs
node local/tests/torbox_series.cjs
node local/tests/torbox_infuse.cjs
node --check Modules/TorBox/plugin.js
bash -n local/build-release.sh local/deploy-host.sh
```

The HTTP regression harness needs .NET 10 and an extracted public Lampac 1.90.0
runtime (`Core.dll`, `Shared.dll`, Core dependency/runtime JSON and `runtimes/`).
It creates a temporary instance with synthetic accounts, copies only these
runtime files and the TorBox module, and redirects TorBox/JacRed to a loopback
mock. It does not read a running installation's config or database:

```sh
python3 local/tests/torbox_http.py --runtime /path/to/extracted/public-release
```

Use `--dotnet /path/to/dotnet` when it is outside PATH. Use `--module-dir
/path/to/extracted/custom-release/mods/TorBox` to check the packaged module.
The checks cover variant continuation, ambiguity stops, ownership, lazy file
loading, active paging, batch order/schema, unsupported-endpoint fallback and
failures that must not fall back. Requests use temporary loopback ports only.
They also walk complete native `rjson=true` movie/series menus to the final
video response and verify default/explicit/nojson HTML compatibility. Movie
checks verify all release choices, selected stream parameters, media redirects,
and original-file `requestdl` playback without stream creation, including owner
isolation. Actual native-device selector rendering remains a manual check.
The JS harness checks the library UI's lazy-loading/filter request behavior.

GitHub Actions runs the same checks and downloads the public runtime without
secrets. Physical-device playback and live TorBox availability remain manual
release checks; fixture results do not substitute for those observations.

For 1.90.0, also run:

```sh
python3 local/tests/native_sync_http.py --runtime /path/to/extracted/public-release
```

This compiles the real NativeAppleTV, TimeCode, bookmark and TorBox sources in
two temporary instances. It checks legacy typed/JSON database migration, shared
device identities, case normalization, account/profile isolation, password/bot
confirmation, one-time codes, restart persistence, bans, real peer writes and
the Plex adapter against the actual capability-4 API. It does not contact Plex.
Use `--modules /path/to/package/module --torbox /path/to/package/mods/TorBox`
to check module sources extracted from a candidate ZIP.
