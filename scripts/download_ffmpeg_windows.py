#!/usr/bin/env python3
"""Download a static Windows FFmpeg build into ``vendor/ffmpeg``.

Usage::

    python scripts/download_ffmpeg_windows.py [output_dir]

Defaults to ``<repo>/vendor/ffmpeg``. Downloads the Gyan 'full' build which
includes both ``ffmpeg.exe`` and ``ffprobe.exe`` and the WASAPI / dshow
devices needed for this app.
"""

from __future__ import annotations

import os
import sys
import urllib.request
import zipfile
from pathlib import Path

# Gyan's official static builds (x64). Pinned to a known-good release.
VERSION = "7.1"
BASE_URL = f"https://www.gyan.dev/ffmpeg/builds/ffmpeg-{VERSION}-full_build.zip"
MIRROR_URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def download(url: str, dest: Path) -> None:
    print(f"Downloading {url} ...")
    with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as fh:
        total = resp.headers.get("Content-Length")
        total = int(total) if total else None
        got = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if total:
                pct = 100 * got // total
                print(f"\r  {got / 1e6:.1f} / {total / 1e6:.1f} MB ({pct}%)",
                      end="", flush=True)
    print()


def extract(zip_path: Path, out_dir: Path) -> None:
    print(f"Extracting to {out_dir} ...")
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(out_dir)
    # The zip contains a top-level folder like ffmpeg-7.1-full_build/bin/
    bin_dirs = [p for p in out_dir.rglob("bin") if p.is_dir()]
    if not bin_dirs:
        raise RuntimeError("no 'bin' folder found after extraction")
    # Move the bin contents up so vendor/ffmpeg/ffmpeg.exe exists directly.
    src = bin_dirs[0]
    for item in src.iterdir():
        dest = out_dir / item.name
        if dest.exists():
            if dest.is_dir():
                import shutil
                shutil.rmtree(dest)
            else:
                dest.unlink()
        import shutil
        shutil.move(str(item), str(dest))
    # remove the nested build folder to save space
    import shutil
    top = out_dir / [d for d in os.listdir(out_dir)
                     if d.startswith("ffmpeg") and (out_dir / d).is_dir()][0]
    shutil.rmtree(top, ignore_errors=True)


def main() -> int:
    out_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
        else repo_root() / "vendor" / "ffmpeg"
    zip_path = out_dir / "_ffmpeg_download.zip"
    # try primary, then mirror
    last_err = None
    for url in (BASE_URL, MIRROR_URL):
        try:
            download(url, zip_path)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  (failed: {exc}) trying next mirror ...")
    else:
        print(f"ERROR: all downloads failed. Last error: {last_err}")
        return 1
    try:
        extract(zip_path, out_dir)
    finally:
        zip_path.unlink(missing_ok=True)
    ff = out_dir / "ffmpeg.exe"
    if ff.exists():
        print(f"OK: ffmpeg installed at {ff}")
    else:
        # maybe 32-bit naming or no exe; just report
        print(f"OK: extracted to {out_dir} (look for ffmpeg binary there)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
