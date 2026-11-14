#!/usr/bin/env bash
# Install an immutable custom release, including files skipped by install.sh.
# The installed manifest remains outside the application's rsync tree.
set -euo pipefail
umask 077
REPO=${LAMPAC_GITHUB_REPO:-polons123/lampac-torbox}
TAG=${1:?usage: deploy-host.sh <release tag>}
ROOT=${LAMPAC_ROOT:-/opt/lampac}
INSTALLER=${LAMPAC_INSTALLER:-/root/lampac_install.sh}
STATE=${LAMPAC_RELEASE_STATE:-/root/lampac-release-state}
ASSET_URL="https://github.com/${REPO}/releases/download/${TAG}"
work=$(mktemp -d /tmp/lampac-deploy.XXXXXX)
trap 'rm -rf "$work"' EXIT

echo '>> Downloading and verifying release before installation'
curl -fsSL --retry 3 -o "$work/app.zip" "${ASSET_URL}/lampac-nextgen.zip"
curl -fsSL --retry 3 -o "$work/overlay.sha256" "${ASSET_URL}/overlay.sha256"
curl -fsSL --retry 3 -o "$work/build-info.txt" "${ASSET_URL}/build-info.txt"
python3 - "$work" <<'PY'
import hashlib,pathlib,re,sys,zipfile
work=pathlib.Path(sys.argv[1]);info=dict(line.split('=',1) for line in (work/'build-info.txt').read_text().splitlines())
assert hashlib.sha256((work/'app.zip').read_bytes()).hexdigest()==info['zip_sha256'],'archive checksum mismatch'
assert hashlib.sha256((work/'overlay.sha256').read_bytes()).hexdigest()==info['overlay_sha256'],'manifest checksum mismatch'
seen=set()
for line in (work/'overlay.sha256').read_text().splitlines():
 digest,path=line.split(None,1)
 assert re.fullmatch('[a-f0-9]{64}',digest) and path.startswith(('mods/','module/','wwwroot/','release-tools/'))
 assert not any(p in ('','.','..') for p in path.split('/')) and path not in seen,'invalid manifest path'
 seen.add(path)
assert seen and len(seen)==int(info['overlay_files']),'incomplete manifest'
with zipfile.ZipFile(work/'app.zip') as z:
 names=z.namelist();assert len(names)==len(set(names)),'duplicate archive entries'
 assert all(not n.startswith('/') and '..' not in pathlib.PurePosixPath(n).parts for n in names),'unsafe archive path'
 assert 'Core.dll' in names,'missing Core.dll'
 z.extractall(work/'app')
PY
(cd "$work/app" && sha256sum --strict -c "$work/overlay.sha256")

# Tighten an existing file too: umask alone only affects newly created files.
mkdir -p /etc/systemd/system/lampac.service.d
printf '[Service]\nUMask=0077\n' > /etc/systemd/system/lampac.service.d/20-private-files.conf
chmod 0644 /etc/systemd/system/lampac.service.d/20-private-files.conf
if [ -f "$ROOT/current.conf" ]; then chmod 0600 "$ROOT/current.conf"; fi
systemctl daemon-reload

# --force avoids the same-version interactive prompt.
LAMPAC_GITHUB_REPO="$REPO" bash "$INSTALLER" --update --tag "$TAG" --force < /dev/null

changed=0
while read -r hash path; do
  src="$work/app/$path"
  dst="$ROOT/$path"
  if [ -f "$dst" ] && [ "$(sha256sum "$dst" | cut -d' ' -f1)" = "$hash" ]; then continue; fi
  if [ "$changed" -eq 0 ]; then systemctl stop lampac; fi
  mkdir -p "$(dirname "$dst")"
  install -o lampac -g lampac -m 0644 "$src" "$dst"
  echo "   + $path"
  changed=$((changed + 1))
done < "$work/overlay.sha256"
(cd "$ROOT" && sha256sum --strict -c "$work/overlay.sha256")
[ "$(cat "$ROOT/version.txt")" = "$TAG" ] || { echo 'Installed version mismatch' >&2; exit 1; }

mkdir -p "$STATE"
chmod 0700 "$STATE"
install -m 0600 "$work/overlay.sha256" "$STATE/overlay.sha256"
install -m 0600 "$work/build-info.txt" "$STATE/build-info.txt"
printf '%s\n' "$TAG" > "$STATE/version.txt"
# A fully verified release replaces previously recorded local patches.
rm -f "$STATE/overrides.json"
install -m 0700 "$ROOT/release-tools/lampac-postupdate-check.py" /root/lampac_postupdate_check.py
# Retire the removed custom TorBox integration (mods/ is installer-protected).
systemctl stop lampac
python3 - "$ROOT" <<'PYREMOVE'
import json,pathlib,shutil,sys
root=pathlib.Path(sys.argv[1])
for name in ['mods/TorBox','wwwroot/plugins/torbox','torbox-private.json','torbox-settings.json','database/torbox-library.json','database/torbox-series.json']:
 p=root/name
 if p.is_dir():shutil.rmtree(p)
 elif p.exists():p.unlink()
p=root/'init.conf';d=json.loads(p.read_text(encoding='utf-8-sig'))
plugins=d.get('LampaWeb',{}).get('customPlugins',[])
d.get('LampaWeb',{})['customPlugins']=[x for x in plugins if 'torbox' not in str(x).lower()]
d.pop('TorBox',None);p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
p=root/'excludes.conf'
if p.exists():p.write_text('\n'.join(x for x in p.read_text().splitlines() if 'torbox' not in x.lower())+'\n')
for p in (root/'cache').rglob('*'):
 if p.is_file() and p.stat().st_size<3000000 and b'torbox' in p.read_bytes().lower():p.unlink()
PYREMOVE
# Reload the process even for an equal-source reinstall, to discard in-memory scripts.
if [ "$changed" -gt 0 ]; then systemctl start lampac; else systemctl restart lampac; fi
systemctl is-active --quiet lampac
[ "$(systemctl show -p UMask --value lampac)" = 0077 ]
echo ">> Installed $TAG; all overlay hashes verified. Review checks before refreshing baseline."
