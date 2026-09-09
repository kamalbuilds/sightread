"""Measures visual activity inside a media file segment using ffmpeg's scdet
(scene change detection) filter. Reports the peak and mean frame-to-frame change
score for a time range, which the coverage planner uses to distinguish gaps where
something happened on screen from gaps that are visually static. This module does
not interpret what changed; it measures only that a change occurred and how strong
it was.
"""

from __future__ import annotations

import re
import subprocess


class VisualError(RuntimeError):
    """Raised when ffmpeg is missing or exits non-zero."""


_RE_SCORE = re.compile(r"lavfi\.scd\.score:\s*([\d.]+(?:e[+-]?\d+)?)")


def scene_scores(media_path: str, start_s: float, duration_s: float) -> list[float]:
    """Return per-frame scene-change scores for a segment of *media_path*.

    Runs ffmpeg's scdet filter with threshold=0 so every frame gets a score,
    not only frames that exceed a detection threshold. Parses every
    'lavfi.scd.score: <float>' value from stderr, in order.

    Raises VisualError when ffmpeg is missing or exits non-zero. Returns an
    empty list when the segment is too short to contain two frames; that is a
    legitimate result, not an error.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner", "-nostats",
        "-ss", str(start_s),
        "-i", media_path,
        "-t", str(duration_s),
        "-an",
        "-vf", "scdet=threshold=0",
        "-f", "null", "-",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise VisualError("ffmpeg not found on PATH") from exc

    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-10:]
        raise VisualError(
            f"ffmpeg exited {result.returncode}:\n" + "\n".join(tail)
        )

    return [float(m.group(1)) for m in _RE_SCORE.finditer(result.stderr)]


def visual_activity(media_path: str, start_s: float, duration_s: float) -> dict:
    """Return a summary of visual activity for a segment of *media_path*.

    Returns {"frames": int, "peak": float, "mean": float}. Both floats are
    rounded to 4 decimal places. When there are no scores, peak and mean are
    0.0. peak is the strongest single frame-to-frame change in the segment,
    which is the signal that a cut or significant motion occurred.
    """
    scores = scene_scores(media_path, start_s, duration_s)
    if not scores:
        return {"frames": 0, "peak": 0.0, "mean": 0.0}
    peak = round(max(scores), 4)
    mean = round(sum(scores) / len(scores), 4)
    return {"frames": len(scores), "peak": peak, "mean": mean}
