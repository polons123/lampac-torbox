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

The HTTP regression harness needs .NET 10 and an extracted public Lampac 1.87.10
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
The JS harness checks the library UI's lazy-loading/filter request behavior.

GitHub Actions runs the same checks and downloads the public runtime without
secrets. Physical-device playback and live TorBox availability remain manual
release checks; fixture results do not substitute for those observations.
