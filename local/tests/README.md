# Portable custom checks

Run from the repository root:

```sh
python3 -m unittest discover -s local/tests -p 'test_*.py'
python3 local/test_release_tools.py
python3 local/test_build_cache.py
node --check Online/plugin.js
bash -n local/build-release.sh local/deploy-host.sh
python3 local/tests/native_sync_http.py --runtime /path/to/extracted/public-release
```

The HTTP check needs .NET 10 and a public Lampac 1.90.0 runtime. It compiles
NativeAppleTV and sync modules in temporary instances with synthetic accounts,
and verifies authentication, identity isolation, TimeCode migration, peer writes
and Plex timestamp arbitration. It does not read production configuration or
contact Plex. Use `--dotnet` for a custom runtime executable and `--modules`
for sources extracted from a candidate package.

The release-tool check also runs the actual deployment shell script against
temporary directories and fake systemctl/installer commands. It covers repeat
migration, malformed configuration before downtime, installer/overlay/atomic
write/backup/post-start verification failures, retained backups and restoration
of a previously active or inactive service. No production services are called.
