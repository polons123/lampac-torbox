# Custom release workflow

Commit reviewed sources, create an immutable custom tag, then build from that tag:

```sh
local/build-release.sh 1.87.10 custom-v1.87.10-2
python3 local/test_release_tools.py
```

The first argument is the upstream release; the second is the committed custom ref.
Upload all three files in `dist/` to the custom release. Never publish an archive
built from an older ref while uncommitted runtime patches are installed.

On an existing host, use `local/deploy-host.sh <custom-tag>` from the same tag.
It verifies the archive and overlay manifest before invoking the installer, installs
all overlay paths (including protected `mods/` and `wwwroot/`), verifies every hash,
and retains the manifest and version in `/root/lampac-release-state/`.
It also sets the Lampac service's UMask to 0077 and tightens existing current.conf
to 0600. Application files created later are private to the service user by default.
Installation briefly restarts Lampac; arrange reviewed rollback copies first.

`/root/lampac_postupdate_check.py --local check` checks the installed code against
that manifest independently of data-only excludes.conf. After a reviewed deployment
and functional checks, run `--local snapshot` to migrate/refresh the baseline.
Snapshot refuses changed release code. Do not exempt module/ files from updates:
the final manifest verification detects stock or mixed-version installations.

Configuration is host-owned and retained. In particular, LampaWeb.cssReplace and
its served dark notification CSS are checked by the post-update tool. Client and
TorBox account configuration are never copied from another host or published.
An optional private `/root/lampac-check-hosts.json` supplies the checker fleet roster
as `[["local", null], ["peer", ["ssh", "-tt", "user@host"]]]`.
