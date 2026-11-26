#!/usr/bin/env bash
# Собирает релизный архив для install.sh: официальный архив Lampac NG + оверлей этого репозитория.
#
#   local/build-release.sh 1.87.10                   # из текущего HEAD
#   local/build-release.sh 1.87.11 upgrade/1.87.11   # из ветки обновления
#
# Результат: dist/lampac-nextgen.zip (имя ассета, которого ждёт install.sh),
# dist/overlay.sha256 и dist/build-info.txt. Установка на сервер:
#   LAMPAC_GITHUB_REPO=polons123/lampac-torbox bash install.sh --update --tag <тег релиза>
#
# Оверлеем считаются файлы, отличающиеся от базы из local/base. Соответствие путей:
#   Modules/*         -> module/*        dotnet publish: ../Modules/** -> module/
#   Online/*          -> module/Online/*
#   local/wwwroot/*   -> wwwroot/*
# При изменениях Shared собираем Shared.dll из указанного git-ref.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
UPSTREAM_REPO=${LAMPAC_UPSTREAM_REPO:-lampac-nextgen/lampac}
DIST_DIR=${LAMPAC_DIST_DIR:-"$REPO_ROOT/dist"}
ZIP_NAME=lampac-nextgen.zip

TAG=${1:?usage: build-release.sh <upstream-tag> [git-ref]}
REF=${2:-HEAD}
COMMIT=$(git -C "$REPO_ROOT" rev-parse "$REF^{commit}")

BASE=$(tr -d '[:space:]' < "$REPO_ROOT/local/base")
[ -n "$BASE" ] || { echo "local/base пуст" >&2; exit 1; }

if ! git -C "$REPO_ROOT" rev-parse --verify --quiet "$BASE" >/dev/null; then
  echo "Нет базы $BASE в репозитории: сделайте git fetch upstream --tags" >&2
  exit 1
fi

map_path() {
  case "$1" in
    local/deploy-host.sh|local/deploy-state.py) printf 'release-tools/%s\n' "${1#local/}" ;;
    local/lampac-postupdate-check.py) printf 'release-tools/lampac-postupdate-check.py\n' ;;
    local/frontend-patches/*.patch) printf 'module/LampaWeb/frontend/%s\n' "${1##*/}" ;;
    local/wwwroot/*)  printf 'wwwroot/%s\n'   "${1#local/wwwroot/}" ;;
    Modules/TorBox/*) printf 'mods/TorBox/%s\n' "${1#Modules/TorBox/}" ;;
    Modules/*)        printf 'module/%s\n'    "${1#Modules/}" ;;
    Online/*)         printf 'module/Online/%s\n' "${1#Online/}" ;;
    .gitignore|local/*) return 1 ;;
    *) return 1 ;;
  esac
}

work=$(mktemp -d /tmp/lampac-build.XXXXXX)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/app" "$DIST_DIR"
DIST_DIR=$(cd "$DIST_DIR" && pwd)

url="https://github.com/${UPSTREAM_REPO}/releases/download/${TAG}/${ZIP_NAME}"
cache_dir=${LAMPAC_BUILD_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/lampac-build}
cache_key=$(printf '%s' "$url" | sha256sum | cut -d' ' -f1)
mkdir -p "$cache_dir"
(
  # Keep concurrent builds from publishing a partial or mismatched cache entry.
  flock -x 9
  if [ -f "$cache_dir/$cache_key.zip" ] && [ -f "$cache_dir/$cache_key.sha256" ] &&
     [ "$(sha256sum "$cache_dir/$cache_key.zip" | cut -d' ' -f1)" = "$(cat "$cache_dir/$cache_key.sha256")" ]; then
    echo ">> исходный релиз $TAG из кэша (SHA-256 проверен)"
  else
    echo ">> качаю $url"
    curl -fSL --retry 3 -o "$work/upstream.zip" "$url" || {
      echo "Не удалось скачать релиз $TAG" >&2; exit 1
    }
    unzip -tq "$work/upstream.zip" > /dev/null
    digest=$(sha256sum "$work/upstream.zip" | cut -d' ' -f1)
    printf '%s\n' "$digest" > "$work/upstream.sha256"
    mv "$work/upstream.zip" "$cache_dir/$cache_key.zip"
    mv "$work/upstream.sha256" "$cache_dir/$cache_key.sha256"
  fi
  cp "$cache_dir/$cache_key.zip" "$work/upstream.zip"
) 9> "$cache_dir/$cache_key.lock"
unzip -oq "$work/upstream.zip" -d "$work/app"

# Если архив содержит единственный корневой каталог — поднимаем его наверх
mapfile -t tops < <(find "$work/app" -mindepth 1 -maxdepth 1 -type d)
if [ "${#tops[@]}" -eq 1 ] && [ "$(find "$work/app" -mindepth 1 -maxdepth 1 -type f | wc -l)" -eq 0 ]; then
  shopt -s dotglob nullglob
  mv "${tops[0]}"/* "$work/app"/
  shopt -u dotglob nullglob
  rmdir "${tops[0]}"
fi
[ -f "$work/app/Core.dll" ] || { echo "В архиве нет Core.dll — неверный релиз" >&2; exit 1; }

applied=0
skipped=0
: > "$work/overlay.list"
: > "$work/overlay.log"
shared_changes=$(git -C "$REPO_ROOT" diff --name-only "$BASE" "$COMMIT" -- Shared Directory.Build.props Directory.Build.targets Directory.Packages.props)
if [ -n "$shared_changes" ]; then
  mkdir "$work/source"
  git -C "$REPO_ROOT" archive "$COMMIT" Shared Directory.Build.props Directory.Build.targets Directory.Packages.props | tar -x -C "$work/source"
  "${DOTNET:-dotnet}" build "$work/source/Shared/Shared.csproj" --configuration Release --nologo \
    -p:Version="$TAG" -p:AssemblyVersion="${TAG%%.*}.0.0.0" -p:InformationalVersion="$TAG+$COMMIT"
  install -m 0644 "$work/source/Shared/bin/Release/net10.0/Shared.dll" "$work/app/Shared.dll"
  printf '%s\n' 'Shared -> Shared.dll' >> "$work/overlay.log"
  printf '%s\n' 'Shared.dll' >> "$work/overlay.list"
  applied=$((applied + 1))
fi

while IFS= read -r src; do
  if ! dst=$(map_path "$src"); then
    skipped=$((skipped + 1))
    continue
  fi
  mkdir -p "$work/app/$(dirname "$dst")"
  git -C "$REPO_ROOT" show "$COMMIT:$src" > "$work/app/$dst"
  printf '%s -> %s\n' "$src" "$dst" >> "$work/overlay.log"
  printf '%s\n' "$dst" >> "$work/overlay.list"
  applied=$((applied + 1))
done < <(git -C "$REPO_ROOT" diff --name-only --diff-filter=ACMR "$BASE" "$COMMIT")

while IFS= read -r src; do
  if dst=$(map_path "$src"); then
    rm -f "$work/app/$dst"
    printf '%s -> %s (удалён)\n' "$src" "$dst" >> "$work/overlay.log"
  fi
done < <(git -C "$REPO_ROOT" diff --name-only --diff-filter=D "$BASE" "$COMMIT")

rm -f "$DIST_DIR/$ZIP_NAME" "$DIST_DIR/overlay.sha256"
if command -v zip >/dev/null 2>&1; then
  (cd "$work/app" && zip -qr "$DIST_DIR/$ZIP_NAME" .)
else
  # zip может отсутствовать на сервере; python3 есть всегда
  python3 - "$work/app" "$DIST_DIR/$ZIP_NAME" <<'PY'
import os, sys, zipfile
root, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames):
            full = os.path.join(dirpath, name)
            z.write(full, os.path.relpath(full, root).replace(os.sep, '/') + '/')
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            z.write(full, os.path.relpath(full, root).replace(os.sep, '/'))
PY
fi
(cd "$work/app" && while read -r dst; do [ -f "$dst" ] && sha256sum "$dst"; done < "$work/overlay.list" \
  | sort -k2) > "$DIST_DIR/overlay.sha256"

{
  echo "upstream_tag=$TAG"
  echo "upstream_repo=$UPSTREAM_REPO"
  echo "base=$BASE"
  echo "ref=$REF"
  echo "commit=$COMMIT"
  echo "built=$(date -u +%FT%TZ)"
  echo "overlay_files=$applied"
  echo "skipped_local_paths=$skipped"
  echo "zip_sha256=$(sha256sum "$DIST_DIR/$ZIP_NAME" | cut -d' ' -f1)"
  echo "overlay_sha256=$(sha256sum "$DIST_DIR/overlay.sha256" | cut -d' ' -f1)"
} > "$DIST_DIR/build-info.txt"

echo ">> наложено файлов: $applied (пропущено служебных: $skipped)"
cat "$work/overlay.log"
echo ">> готово:"
cat "$DIST_DIR/build-info.txt"
