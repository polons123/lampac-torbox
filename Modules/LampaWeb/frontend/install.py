"""Maintain the pinned Lampa and New View overlays on opted-in Linux installations."""
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

TREE = "6fcdfab624f98ac0f7a91244e3d3b637f104a8c2"
APP = ("app.min.js", "progressive-film-card",
       "25bf4a3a69d5f4d85091633161c58eec5ec49c2748d22c745d94bc40195c358b",
       "c5eeaeeeb2ea9ba4135571682eed78b483a1a599d56a076965ae04caebac2ac3")
INDEX = ("index.html", "stable-app-url",
         "01f7cc1af819580089f0008667d9249e511a168eb0e1d2bdf539b1795b6c23a5",
         "ba4bb5580bbd9bf6d90b5a13847286a40ad3457e11c4c8eff8a593be37da4b9f")
THEME = ("new_view.js", "pause-banner-prefetch",
         "675edd65e85c8a99071449f86dd885a63b403ae0269a73897e7b24fa27c2f7a3",
         "dd2be138924687bd471b82da4f3f4d2d50561133071e1185ac7bff79bfa3ff0e")
RAW_INDEX = "f1ff4327d45645795bd5a59c1e70e6d8c9377e67ae49c6ef9d3ca8f03ba1fb25"
PATCHES = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(directory, spec):
    name, patch, before, after = spec
    path = directory / name
    if path.resolve() != path:
        raise ValueError(f"Unexpected link at {name}")
    actual = digest(path)
    if actual == after:
        return
    if spec == INDEX and actual == RAW_INDEX:
        path.write_bytes(path.read_bytes().replace(b"</body>", b'<script src="/lampainit.js"></script></body>'))
        actual = digest(path)
    if actual != before:
        raise ValueError(f"Unsupported {name} revision ({actual}); review {patch}.patch")
    subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "--silent",
                    str(path), "-i", str(PATCHES / (patch + ".patch"))], check=True)
    if digest(path) != after:
        raise ValueError(f"Unexpected result applying {patch}.patch")


def publish_directory(stage, live):
    if live.exists():
        # Linux renameat2(RENAME_EXCHANGE) keeps all reader-visible files and tree in sync.
        exchange = ctypes.CDLL(None, use_errno=True).renameat2
        if exchange(-100, os.fsencode(stage), -100, os.fsencode(live), 2):
            errno = ctypes.get_errno()
            raise OSError(errno, os.strerror(errno))
    else:
        stage.rename(live)


def update_theme(wwwroot, work):
    theme = wwwroot / "plugins/new_view" / THEME[0]
    if theme.is_file() and digest(theme) != THEME[3]:
        staged = work / theme.name
        shutil.copy2(theme, staged)
        prepare(work, THEME)
        os.replace(staged, theme)


def install(wwwroot, work, archive=None):
    live = wwwroot / "lampa-main"
    stage = work / "lampa-main"
    if live.is_dir():
        shutil.copytree(live, stage, symlinks=True)
    else:
        stage.mkdir()
    if archive:
        prefix = "lampa-" + TREE + "/"
        with zipfile.ZipFile(archive) as files:
            names = set(files.namelist())
            if not all(prefix + item[0] in names for item in (APP, INDEX)):
                raise ValueError("Missing pinned Lampa archive entries")
            for entry in files.infolist():
                if not entry.filename.startswith(prefix):
                    raise ValueError("Unexpected Lampa archive root")
                rel = entry.filename[len(prefix):]
                target = stage / rel
                if target.resolve() != target or not target.is_relative_to(stage):
                    raise ValueError("Invalid Lampa archive path")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with files.open(entry) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    for spec in (APP, INDEX):
        prepare(stage, spec)
    for name, data in (("personal.lampa", ""), ("plugins_black_list.json", "[]"), ("plugins/modification.js", "")):
        path = stage / name
        if path.resolve() != path:
            raise ValueError(f"Unexpected link at {name}")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
    marker = stage / "tree"
    if marker.is_symlink():
        raise ValueError("Unexpected tree marker link")
    marker.write_text(TREE)
    publish_directory(stage, live)


def main(args):
    mode, repo, tree, *archive = args
    if repo.lower() != "yumata/lampa" or tree != TREE:
        raise ValueError("Unsupported protected Lampa revision; review frontend patches before upgrading")
    wwwroot = Path("wwwroot").resolve()
    live = wwwroot / "lampa-main"
    with tempfile.TemporaryDirectory(prefix=".lampa-update-", dir=wwwroot.parent) as directory:
        work = Path(directory)
        if mode == "install" and len(archive) == 1:
            install(wwwroot, work, archive[0])
        elif mode == "check" and not archive:
            try:
                update_theme(wwwroot, work)
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                print(f"New View update: {exc}", file=sys.stderr)
            if not (live / "tree").is_file() or (live / "tree").read_text() != TREE:
                return 10
            if any(not (live / spec[0]).is_file() for spec in (APP, INDEX)):
                return 10
            if any(digest(live / spec[0]) != spec[3] for spec in (APP, INDEX)):
                install(wwwroot, work)
        else:
            raise ValueError("Invalid frontend update arguments")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
