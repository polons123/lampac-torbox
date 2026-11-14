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
HELPER="$(dirname "${BASH_SOURCE[0]}")/deploy-state.py"
CHECKER=${LAMPAC_CHECKER:-/root/lampac_postupdate_check.py}
DROPIN=${LAMPAC_SYSTEMD_DROPIN:-/etc/systemd/system/lampac.service.d/20-private-files.conf}
BACKUP_ROOT=${LAMPAC_BACKUP_ROOT:-${STATE}-backups}
work=$(mktemp -d /tmp/lampac-deploy.XXXXXX)
backup=''
was_active=0
touched_service=0
mutating=0
complete=0
systemctl is-active --quiet lampac && was_active=1
finish() {
  rc=$?
  trap - EXIT
  set +e
  if [ "$complete" -eq 0 ] && [ "$touched_service" -eq 1 ]; then
    if [ "$mutating" -eq 1 ]; then
      if systemctl stop lampac; then
        python3 "$HELPER" restore "$backup" || echo "ERROR: rollback failed; backup: $backup" >&2
        systemctl daemon-reload
      else
        echo "ERROR: cannot stop Lampac for rollback; backup retained: $backup" >&2
      fi
    fi
    if [ "$was_active" -eq 1 ]; then
      systemctl start lampac || echo "ERROR: Lampac could not be started; backup: $backup" >&2
    fi
  fi
  rm -rf "$work"
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

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

# Validate and prepare configuration before any host writes or downtime.
marker="$STATE/migrations/reserved"
python3 "$HELPER" prepare "$ROOT" "$work" "$marker"
# Coordinate with the two optional sync tools while copying/restoring SQLite files.
exec 8> "${LAMPAC_LOCK_DIR:-/tmp}/lampac_sync.lock"
exec 9> "${LAMPAC_LOCK_DIR:-/tmp}/plex_lampac_sync.lock"
flock -w 60 8
flock -w 60 9
mkdir -p "$BACKUP_ROOT"
chmod 0700 "$BACKUP_ROOT"
backup=$(mktemp -d "$BACKUP_ROOT/deploy.XXXXXXXX")
touched_service=1
systemctl stop lampac
python3 "$HELPER" snapshot "$backup" "$ROOT" "$STATE" "$CHECKER" "$DROPIN"
mutating=1
# All writes from this point are covered by the stopped snapshot.
mkdir -p "$(dirname "$DROPIN")"
printf '[Service]\nUMask=0077\n' > "$DROPIN"
chmod 0644 "$DROPIN"
if [ -f "$ROOT/current.conf" ]; then chmod 0600 "$ROOT/current.conf"; fi
systemctl daemon-reload
LAMPAC_GITHUB_REPO="$REPO" bash "$INSTALLER" --update --tag "$TAG" --force < /dev/null
# The upstream installer may have started the service.
systemctl stop lampac
while read -r hash path; do
  src="$work/app/$path"
  dst="$ROOT/$path"
  if [ -f "$dst" ] && [ "$(sha256sum "$dst" | cut -d' ' -f1)" = "$hash" ]; then continue; fi
  mkdir -p "$(dirname "$dst")"
  install -o lampac -g lampac -m 0644 "$src" "$dst"
  echo "   + $path"
done < "$work/overlay.sha256"
(cd "$ROOT" && sha256sum --strict -c "$work/overlay.sha256")
[ "$(cat "$ROOT/version.txt")" = "$TAG" ] || { echo 'Installed version mismatch' >&2; exit 1; }
python3 "$HELPER" apply "$ROOT" "$work"

mkdir -p "$STATE"
chmod 0700 "$STATE"
install -m 0600 "$work/overlay.sha256" "$STATE/overlay.sha256"
install -m 0600 "$work/build-info.txt" "$STATE/build-info.txt"
printf '%s\n' "$TAG" > "$STATE/version.txt"
rm -f "$STATE/overrides.json"
install -m 0700 "$ROOT/release-tools/lampac-postupdate-check.py" "$CHECKER"
systemctl start lampac
systemctl is-active --quiet lampac
[ "$(systemctl show -p UMask --value lampac)" = 0077 ]
if [ "$was_active" -eq 0 ]; then systemctl stop lampac; fi
complete=1
echo ">> Installed $TAG; overlay verified; backup: $backup"
