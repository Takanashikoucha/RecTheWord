"""FFmpeg discovery and audio device enumeration.

On Windows the only FFmpeg input that lists *both* microphones and speakers
in a single call is ``dshow`` (``ffmpeg -f dshow -list_devices true -i dummy``).
We enumerate with ``dshow`` and capture with ``wasapi`` (shared mode).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# FFmpeg / ffprobe discovery
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    # rectheword/audio/devices.py -> repo root is two levels up
    return Path(__file__).resolve().parents[2]


def candidate_ffmpeg_paths() -> List[Path]:
    """Ordered candidate locations for ffmpeg/ffprobe binaries."""
    exe = ".exe" if os.name == "nt" else ""
    root = _repo_root()
    candidates = [
        root / "vendor" / "ffmpeg" / f"ffmpeg{exe}",
        root / "vendor" / "ffmpeg" / "bin" / f"ffmpeg{exe}",
    ]
    if os.environ.get("FFMPEG_BIN"):
        candidates.insert(0, Path(os.environ["FFMPEG_BIN"]))
    for base in ("ffmpeg", "ffprobe"):
        found = shutil.which(base)
        if found:
            candidates.append(Path(found))
    return candidates


def find_ffmpeg() -> Optional[str]:
    """Return the path to an ffmpeg executable, or None."""
    for p in candidate_ffmpeg_paths():
        try:
            if p.is_file() and os.access(str(p), os.X_OK):
                return str(p)
        except OSError:
            continue
    return None


def find_ffprobe() -> Optional[str]:
    found = shutil.which("ffprobe")
    if found:
        return found
    return None


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


# ---------------------------------------------------------------------------
# dshow device enumeration
# ---------------------------------------------------------------------------

@dataclass
class AudioDevices:
    microphones: List[str] = field(default_factory=list)
    speakers: List[str] = field(default_factory=list)

    @property
    def mic_count(self) -> int:
        return len(self.microphones)

    @property
    def spk_count(self) -> int:
        return len(self.speakers)


def _run_ffmpeg(args: List[str], timeout: int = 20) -> str:
    """Run an ffmpeg subprocess and return combined stdout+stderr text."""
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg not found")
    cmd = [ff] + args
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    raw = proc.stdout or b""
    # dshow prints UTF-8 on Windows; be tolerant of the locale codec.
    return raw.decode("utf-8", errors="replace")


_MIC_LINE = re.compile(r"Direct Show audio devices\s*:", re.IGNORECASE)
_SPK_LINE = re.compile(r"Direct Show video devices\s*:", re.IGNORECASE)
# audio lines look like:  [dshow @ ...]  "Microphone (Realtek ...)"
_DEV_ITEM = re.compile(r'^\s*(?:\[dshow[^]]*\]\s*)?["\u201c]?(.+?)["\u201d]\s*$')


def parse_dshow_listing(text: str) -> AudioDevices:
    """Parse the output of ``ffmpeg -f dshow -list_devices true -i dummy``.

    Returns microphones (audio input devices) and speakers (render devices).
    Note: dshow only lists *capture-capable* audio devices as inputs; render
    (speaker) loopback devices are discovered separately when starting capture
    (see capture.py). We still return whatever dshow reports as audio devices
    here for the microphone dropdown.
    """
    devices = AudioDevices()
    lines = text.splitlines()
    section: Optional[str] = None  # "audio" | "video" | None
    for raw_line in lines:
        line = raw_line.rstrip()
        if _MIC_LINE.search(line):
            section = "audio"
            continue
        if _SPK_LINE.search(line):
            section = "video"
            continue
        m = _DEV_ITEM.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        if not name:
            continue
        # skip obvious non-device noise
        if name.lower() in ("", "default"):
            continue
        if section == "audio":
            devices.microphones.append(name)
        # video section is ignored for audio purposes
    # de-dup, keep order
    devices.microphones = _dedup(devices.microphones)
    devices.speakers = _dedup(device_speakers_from_text(text))
    return devices


def _dedup(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def device_speakers_from_text(text: str) -> List[str]:
    """Best-effort extraction of speaker (render) device names.

    dshow audio listing typically includes the default render device and
    loopback sources; we heuristically keep names that look like render or
    loopback devices. On many systems the same "Microphone" style names are
    present for render endpoints, so we fall back to the full audio list if
    nothing render-like is found.
    """
    lines = text.splitlines()
    section = None
    render_like: List[str] = []
    all_audio: List[str] = []
    for raw_line in lines:
        line = raw_line.rstrip()
        if _MIC_LINE.search(line):
            section = "audio"
            continue
        if _SPK_LINE.search(line):
            section = "video"
            continue
        m = _DEV_ITEM.match(line)
        if not m or section != "audio":
            continue
        name = m.group(1).strip()
        if not name:
            continue
        all_audio.append(name)
        low = name.lower()
        if any(k in low for k in ("speaker", "headphone", "output", "render",
                                  "loopback", "stereo mix", "hd audio", "realtek")):
            render_like.append(name)
    if render_like:
        return render_like
    return all_audio


def list_audio_devices() -> AudioDevices:
    """Enumerate audio devices via dshow (Windows). On other platforms returns
    an empty listing (capture will be unavailable)."""
    if os.name != "nt":
        return AudioDevices()
    try:
        text = _run_ffmpeg(
            ["-hide_banner", "-f", "dshow", "-list_devices", "true", "-i", "dummy"],
            timeout=25,
        )
    except (subprocess.TimeoutExpired, RuntimeError, OSError):
        return AudioDevices()
    return parse_dshow_listing(text)


def wasapi_device_name(display_name: str, kind: str) -> str:
    """Build a WASAPI input filename.

    ``kind`` is "mic" for a normal capture device or "spk" for a render
    (speaker) loopback device.
    """
    name = display_name.strip()
    if kind == "spk":
        # WASAPI loopback capture of a render endpoint
        if not name.lower().startswith("loopback:"):
            name = f"loopback:{name}"
    return name


def escape_device_arg(name: str) -> str:
    """FFmpeg device names may contain ':' ; we pass them as a single argv item
    so no shell quoting is required. This helper is kept for clarity/tests."""
    return name


# ---------------------------------------------------------------------------
# Loopback render discovery (Windows)
# ---------------------------------------------------------------------------

def list_render_devices() -> List[str]:
    """List speaker/render endpoints usable for WASAPI loopback.

    Prefers ``-f wasapi -list_devices true`` (when the build exposes it);
    falls back to the dshow audio listing.
    """
    if os.name != "nt":
        return []
    devices: List[str] = []
    try:
        text = _run_ffmpeg(
            ["-hide_banner", "-f", "wasapi", "-list_devices", "true", "-i", "dummy"],
            timeout=25,
        )
        # wasapi -list_devices prints "Direct show audio devices" and
        # "Direct show video devices" similarly on some builds; reuse parser.
        devices = parse_dshow_listing(text).speakers
    except (subprocess.TimeoutExpired, RuntimeError, OSError):
        devices = []
    if not devices:
        devices = list_audio_devices().speakers
    return _dedup(devices)


if __name__ == "__main__":
    import sys

    ff = find_ffmpeg()
    print(f"ffmpeg: {ff}")
    devs = list_audio_devices()
    print(f"microphones ({devs.mic_count}):")
    for d in devs.microphones:
        print(f"  - {d}")
    print(f"speakers ({devs.spk_count}):")
    for d in devs.speakers:
        print(f"  - {d}")
