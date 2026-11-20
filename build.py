"""Builds the release files into releases/ (committed to the repo, stable names):

  AgentsLab-update.pyz     the single updater file (Windows + Linux)
  AgentsLab-windows.zip    fresh install for Windows
  AgentsLab-linux.zip      fresh install for Linux
  AgentsLab-android.zip    fresh install for Android (Termux; see android/get.sh)

Usage: bump VERSION, then  python build.py
"""
import json
import shutil
import tempfile
import zipapp
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = (HERE / "VERSION").read_text(encoding="utf-8").strip()
DIST = HERE / "releases"
GROUPS = ("common", "windows", "linux", "android")


def listing(group):
    base = HERE / group
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)


def build_updater(files):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copy2(HERE / "updater" / "updater.py", t / "__main__.py")
        for g in GROUPS:
            for rel in files[g]:
                dst = t / "payload" / g / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(HERE / g / rel, dst)
        (t / "payload" / "manifest.json").write_text(
            json.dumps({"version": VERSION, **files}, indent=1), encoding="utf-8")
        out = DIST / "AgentsLab-update.pyz"
        zipapp.create_archive(t, out, interpreter="/usr/bin/env python3", compressed=True)
    return out


def build_install_zip(plat, files):
    out = DIST / f"AgentsLab-{plat}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for g in ("common", plat):
            for rel in files[g]:
                info = zipfile.ZipInfo.from_file(HERE / g / rel, rel)
                if rel.endswith(".sh"):
                    info.external_attr = 0o100755 << 16
                z.writestr(info, (HERE / g / rel).read_bytes(), zipfile.ZIP_DEFLATED)
        z.writestr("VERSION", VERSION + "\n")
        for d in ("inbox", "logs", "prompts"):
            z.writestr(f"{d}/.keep", "")
    return out


if __name__ == "__main__":
    DIST.mkdir(exist_ok=True)
    files = {g: listing(g) for g in GROUPS}
    for p in [build_updater(files)] + [build_install_zip(x, files) for x in ("windows", "linux", "android")]:
        print(f"{p.name}  ({p.stat().st_size // 1024} KB)")
