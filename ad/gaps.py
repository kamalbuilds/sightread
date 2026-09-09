"""Measures real silence gaps in a media file using ffmpeg's silencedetect
filter. Every gap is derived from actual audio waveform analysis, not from
metadata timestamps or reported durations, which are often wrong or absent for
broadcast and streaming sources. The raw ffmpeg output lines are preserved on
each Gap so the measurement that produced it can be audited without re-running.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


class GapMeasurementError(RuntimeError):
    """Raised when ffmpeg or ffprobe fails or produces unusable output."""


@dataclass(frozen=True)
class Gap:
    index: int
    start_s: float
    end_s: float
    duration_s: float
    source_lines: tuple[str, ...]


def probe_duration(media_path: str) -> float:
    """Return the media file's duration in seconds via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        media_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise GapMeasurementError("ffprobe not found on PATH") from exc

    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-5:]
        raise GapMeasurementError(
            f"ffprobe exited {result.returncode}:\n" + "\n".join(tail)
        )

    raw = result.stdout.strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise GapMeasurementError(
            f"ffprobe returned non-numeric duration: {raw!r}"
        ) from exc


def measure_gaps(
    media_path: str,
    *,
    noise_db: float = -30.0,
    min_gap_s: float = 1.2,
    start_s: float = 0.0,
    duration_s: float | None = None,
) -> list[Gap]:
    """Return silence gaps in *media_path* that are at least *min_gap_s* long.

    Times in the returned Gap objects are absolute media timestamps (seconds
    from the start of the file), regardless of any seek offset used internally.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start_s:
        cmd += ["-ss", str(start_s)]
    if duration_s is not None:
        cmd += ["-t", str(duration_s)]
    cmd += [
        "-i", media_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_gap_s}",
        "-f", "null", "-",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise GapMeasurementError("ffmpeg not found on PATH") from exc

    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-10:]
        raise GapMeasurementError(
            f"ffmpeg exited {result.returncode}:\n" + "\n".join(tail)
        )

    window_end: float | None = (start_s + duration_s) if duration_s is not None else None
    return _parse_silencedetect(result.stderr, media_path, start_s, min_gap_s, window_end)


_RE_START = re.compile(r"silence_start:\s*([\d.]+(?:e[+-]?\d+)?)")
_RE_END = re.compile(r"silence_end:\s*([\d.]+(?:e[+-]?\d+)?)")
_RE_DUR = re.compile(r"silence_duration:\s*([\d.]+(?:e[+-]?\d+)?)")


def _parse_silencedetect(
    stderr: str,
    media_path: str,
    start_s: float,
    min_gap_s: float,
    window_end: float | None = None,
) -> list[Gap]:
    lines = stderr.splitlines()

    pending_start: float | None = None
    pending_lines: list[str] = []
    raw_gaps: list[tuple[float, float, tuple[str, ...]]] = []

    for line in lines:
        m_start = _RE_START.search(line)
        if m_start:
            pending_start = float(m_start.group(1)) + start_s
            pending_lines = [line]
            continue

        m_end = _RE_END.search(line)
        if m_end:
            abs_end = float(m_end.group(1)) + start_s
            m_dur = _RE_DUR.search(line)

            if pending_start is not None:
                abs_start = pending_start
            else:
                if m_dur:
                    abs_start = abs_end - float(m_dur.group(1))
                else:
                    abs_start = start_s

            src = tuple(pending_lines + [line])
            raw_gaps.append((abs_start, abs_end, src))
            pending_start = None
            pending_lines = []

    if pending_start is not None:
        if window_end is not None:
            media_end = window_end
        else:
            try:
                media_end = probe_duration(media_path)
            except GapMeasurementError:
                media_end = pending_start
        src = tuple(pending_lines)
        raw_gaps.append((pending_start, media_end, src))

    raw_gaps.sort(key=lambda t: t[0])

    gaps: list[Gap] = []
    for abs_start, abs_end, src in raw_gaps:
        dur = abs_end - abs_start
        if dur < min_gap_s:
            continue
        gaps.append(
            Gap(
                index=len(gaps),
                start_s=abs_start,
                end_s=abs_end,
                duration_s=dur,
                source_lines=src,
            )
        )

    return gaps
