#!/usr/bin/env bash
# Установка сборки из репозитория на хост, где уже стоит Lampac.
#
#   local/deploy-host.sh custom-v1.87.10-1
#
# Зачем нужен отдельный шаг: install.sh сам защищает каталоги `mods/` и
# `wwwroot/` (см. build_rsync_excludes) и никогда их не синхронизирует.
# А именно там лежат модуль TorBox (mods/TorBox/*) и клиентские файлы
# (wwwroot/its/its.js, wwwroot/infuseSave.js и т.д.). Поэтому после штатного
# обновления этот скрипт докладывает такие файлы из того же релиза,
# сравнивая sha256, и перезапускает сервис, если что-то изменилось.
set -euo pipefail

REPO=${LAMPAC_GITHUB_REPO:-polons123/lampac-torbox}
TAG=${1:?usage: deploy-host.sh <release tag>}
ROOT=${LAMPAC_ROOT:-/opt/lampac}
INSTALLER=${LAMPAC_INSTALLER:-/root/lampac_install.sh}
ZIP_NAME=lampac-nextgen.zip
ASSET_URL="https://github.com/${REPO}/releases/download/${TAG}"

work=$(mktemp -d /tmp/lampac-deploy.XXXXXX)
trap 'rm -rf "$work"' EXIT

echo ">> [1/3] штатное обновление через install.sh --tag $TAG"
# --force: иначе install.sh спрашивает "already installed, continue?" и ждёт ввода
LAMPAC_GITHUB_REPO="$REPO" bash "$INSTALLER" --update --tag "$TAG" --force < /dev/null

echo ">> [2/3] файлы, которые install.sh не синхронизирует (mods/, wwwroot/)"
curl -fSL --retry 3 -o "$work/app.zip" "${ASSET_URL}/${ZIP_NAME}"
curl -fSL --retry 3 -o "$work/overlay.sha256" "${ASSET_URL}/overlay.sha256"
mkdir -p "$work/app"
unzip -oq "$work/app.zip" -d "$work/app"
mapfile -t tops < <(find "$work/app" -mindepth 1 -maxdepth 1 -type d)
if [ "${#tops[@]}" -eq 1 ] && [ -z "$(find "$work/app" -mindepth 1 -maxdepth 1 -type f -print -quit)" ]; then
  shopt -s dotglob nullglob
  mv "${tops[0]}"/* "$work/app"/
  shopt -u dotglob nullglob
  rmdir "${tops[0]}"
fi

changed=0
while read -r hash path; do
  case "$path" in
    mods/*|wwwroot/*) ;;
    *) continue ;;
  esac
  src="$work/app/$path"
  dst="$ROOT/$path"
  [ -f "$src" ] || { echo "   ! в архиве нет $path"; continue; }
  if [ -f "$dst" ] && [ "$(sha256sum "$dst" | cut -d' ' -f1)" = "$hash" ]; then
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  install -o lampac -g lampac -m 0644 "$src" "$dst"
  echo "   + $path"
  changed=$((changed + 1))
done < <(tr -s ' ' < "$work/overlay.sha256")

echo ">> [3/3] обновлено файлов в защищённых каталогах: $changed"
if [ "$changed" -gt 0 ]; then
  systemctl restart lampac
  sleep 3
  systemctl is-active --quiet lampac && echo ">> сервис активен" || { echo ">> сервис НЕ запустился" >&2; exit 1; }
else
  echo ">> перезапуск не требуется"
fi
echo ">> версия: $(cat "$ROOT/version.txt" 2>/dev/null || echo '?')"
