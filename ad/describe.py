"""Writes one audio description line for one measured silence gap by extracting
the gap's video with ffmpeg and sending the clip inline to Gemini. This module
does not truncate model output to fit the character budget; it returns whatever
the model wrote and the real character count, leaving over-budget detection to
the caller. It does not guess at what is visible from metadata, filenames, or
surrounding context.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from google import genai
from google.genai import types


class DescribeError(RuntimeError):
    """Raised when ffmpeg fails, the clip is missing, or the model returns no text."""


@dataclass(frozen=True)
class Description:
    gap_index: int
    text: str
    char_budget: int
    chars: int
    model: str
    clip_path: str
    attempt: int


def extract_clip(
    media_path: str,
    start_s: float,
    duration_s: float,
    out_path: str,
    *,
    height: int = 360,
) -> str:
    """Cut a video-only clip from *media_path* and write it to *out_path*."""
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-hide_banner", "-nostats", "-y",
        "-ss", str(start_s),
        "-i", media_path,
        "-t", str(duration_s),
        "-an",
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "30",
        out_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise DescribeError("ffmpeg not found on PATH") from exc

    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-10:]
        raise DescribeError(
            f"ffmpeg exited {result.returncode}:\n" + "\n".join(tail)
        )

    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise DescribeError(
            f"ffmpeg exited 0 but output file is missing or empty: {out_path!r}"
        )

    return out_path


def describe_gap(
    media_path: str,
    gap_index: int,
    start_s: float,
    duration_s: float,
    char_budget: int,
    clip_path: str,
    *,
    project: str,
    location: str = "us-central1",
    model: str = "gemini-2.5-flash",
    attempt: int = 1,
    shrink_note: str = "",
) -> Description:
    """Describe what is visible in a gap's video clip using Gemini."""
    extract_clip(media_path, start_s, duration_s, clip_path)

    with open(clip_path, "rb") as fh:
        clip_bytes = fh.read()

    instruction = (
        "You are writing audio description for a blind or partially sighted viewer. "
        "Describe only what is visible on screen, in the present tense. "
        "Do not interpret characters' motives. "
        "Do not name characters unless their name is visible on screen. "
        "Do not mention music, sound, or anything audible. "
        "Do not use phrases like 'we see' or 'the camera'. "
        f"Your description must be no longer than {char_budget} characters in total."
    )
    if shrink_note:
        instruction = instruction + " " + shrink_note

    client = genai.Client(vertexai=True, project=project, location=location)
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=clip_bytes, mime_type="video/mp4"),
            instruction,
        ],
        config=types.GenerateContentConfig(temperature=0.2),
    )

    if not response.candidates:
        raise DescribeError(f"Gemini returned no candidates for gap {gap_index}.")

    raw = ""
    for part in response.candidates[0].content.parts:
        if part.text:
            raw = part.text
            break

    if not raw:
        raise DescribeError(f"Gemini returned no text for gap {gap_index}.")

    cleaned = raw.strip().strip('"').strip("'").strip()
    cleaned = re.sub(r"\s*\n\s*", " ", cleaned)

    return Description(
        gap_index=gap_index,
        text=cleaned,
        char_budget=char_budget,
        chars=len(cleaned),
        model=model,
        clip_path=clip_path,
        attempt=attempt,
    )
