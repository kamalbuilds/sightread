"""Conforming loop: for each measured silence gap, writes a description,
renders it to audio, measures the rendered file, and accepts or rejects it.
Nothing is accepted on the model's word; every verdict comes from a measurement
of the rendered WAV via ffprobe. Rejected takes remain on disk so the evidence
that a gap could not be fitted is preserved in full.
"""

from __future__ import annotations

import inspect
import math
import os
from dataclasses import dataclass

import ad.fit as _fit_mod
from ad.describe import describe_gap
from ad.fit import FitResult, check_fit, target_chars
from ad.gaps import Gap, probe_duration
from ad.render import render_line


class ConformError(RuntimeError):
    """Raised when a gap is too short to hold any description."""


@dataclass(frozen=True)
class ConformedCue:
    gap_index: int
    gap_start_s: float
    gap_end_s: float
    gap_duration_s: float
    char_budget: int
    chars_per_second: float
    text: str
    chars: int
    audio_path: str
    clip_path: str
    rendered_duration_s: float
    margin_ms: int
    verdict: str
    attempts: int
    attempt_log: tuple[dict, ...]


def _default_chars_per_second() -> float:
    sig = inspect.signature(_fit_mod.target_chars)
    return sig.parameters["chars_per_second"].default


def conform_gap(
    media_path: str,
    gap: Gap,
    out_dir: str,
    *,
    project: str,
    location: str = "us-central1",
    headroom_ms: int = 250,
    chars_per_second: float | None = None,
    max_attempts: int = 3,
    describe_model: str = "gemini-2.5-flash",
    tts_model: str = "gemini-2.5-flash-tts",
    voice: str = "Kore",
) -> ConformedCue:
    os.makedirs(out_dir, exist_ok=True)

    if chars_per_second is None:
        initial_budget = target_chars(gap.duration_s, headroom_ms=headroom_ms)
        effective_cps = _default_chars_per_second()
    else:
        initial_budget = target_chars(
            gap.duration_s,
            headroom_ms=headroom_ms,
            chars_per_second=chars_per_second,
        )
        effective_cps = chars_per_second
    if initial_budget == 0:
        raise ConformError(
            f"Gap {gap.index} is {gap.duration_s:.3f}s, which is shorter than the "
            f"{headroom_ms}ms headroom; no description can fit."
        )

    clip_path = os.path.join(out_dir, f"gap{gap.index:04d}.clip.mp4")

    attempt_log: list[dict] = []
    char_budget = initial_budget
    shrink_note = ""

    last_description_text = ""
    last_chars = 0
    last_audio_path = ""
    last_fit: FitResult | None = None

    for attempt in range(1, max_attempts + 1):
        audio_path = os.path.join(out_dir, f"gap{gap.index:04d}.take{attempt}.wav")

        description = describe_gap(
            media_path,
            gap.index,
            gap.start_s,
            gap.duration_s,
            char_budget,
            clip_path,
            project=project,
            location=location,
            model=describe_model,
            attempt=attempt,
            shrink_note=shrink_note,
        )

        render_line(
            description.text,
            audio_path,
            project=project,
            location=location,
            model=tts_model,
            voice=voice,
        )

        fit = check_fit(
            gap.index,
            gap.duration_s,
            audio_path,
            headroom_ms=headroom_ms,
        )

        last_description_text = description.text
        last_chars = description.chars
        last_audio_path = audio_path
        last_fit = fit

        log_entry: dict = {
            "attempt": attempt,
            "char_budget": char_budget,
            "chars": description.chars,
            "rendered_duration_s": fit.rendered_duration_s,
            "margin_ms": fit.margin_ms,
            "verdict": fit.verdict,
        }
        attempt_log.append(log_entry)

        if fit.verdict == "FIT":
            return ConformedCue(
                gap_index=gap.index,
                gap_start_s=gap.start_s,
                gap_end_s=gap.end_s,
                gap_duration_s=gap.duration_s,
                char_budget=char_budget,
                chars_per_second=effective_cps,
                text=last_description_text,
                chars=last_chars,
                audio_path=last_audio_path,
                clip_path=clip_path,
                rendered_duration_s=fit.rendered_duration_s,
                margin_ms=fit.margin_ms,
                verdict="FIT",
                attempts=attempt,
                attempt_log=tuple(attempt_log),
            )

        if attempt < max_attempts:
            observed_cps = description.chars / fit.rendered_duration_s
            overrun_chars = math.ceil(-fit.margin_ms / 1000 * observed_cps)
            next_budget_raw = description.chars - overrun_chars
            safety_cut = math.ceil(description.chars * 0.05)
            next_budget = max(1, next_budget_raw - safety_cut)
            next_budget = min(next_budget, char_budget)

            usable_s = gap.duration_s - headroom_ms / 1000
            shrink_note = (
                f"Your previous take was {fit.rendered_duration_s:.2f}s but the usable "
                f"window is only {usable_s:.2f}s. Please keep this take to "
                f"{next_budget} characters or fewer."
            )
            char_budget = next_budget

    return ConformedCue(
        gap_index=gap.index,
        gap_start_s=gap.start_s,
        gap_end_s=gap.end_s,
        gap_duration_s=gap.duration_s,
        char_budget=char_budget,
        chars_per_second=effective_cps,
        text=last_description_text,
        chars=last_chars,
        audio_path=last_audio_path,
        clip_path=clip_path,
        rendered_duration_s=last_fit.rendered_duration_s,
        margin_ms=last_fit.margin_ms,
        verdict="OVERFLOW",
        attempts=max_attempts,
        attempt_log=tuple(attempt_log),
    )


def conform(
    media_path: str,
    gaps: list[Gap],
    out_dir: str,
    **kwargs,
) -> tuple[list[ConformedCue], list[dict]]:
    cues: list[ConformedCue] = []
    skipped: list[dict] = []
    for gap in gaps:
        try:
            cue = conform_gap(media_path, gap, out_dir, **kwargs)
            cues.append(cue)
        except ConformError as exc:
            skipped.append({"gap_index": gap.index, "reason": str(exc)})
    return cues, skipped


def report(
    media_path: str,
    cues: list[ConformedCue],
    *,
    noise_db: float,
    min_gap_s: float,
    headroom_ms: int,
    skipped: list[dict] | None = None,
) -> dict:
    media_duration = probe_duration(media_path)

    fit_cues = [c for c in cues if c.verdict == "FIT"]
    overflow_cues = [c for c in cues if c.verdict == "OVERFLOW"]

    described_seconds = round(sum(c.rendered_duration_s for c in fit_cues), 3)
    tightest_margin_ms: int | None = (
        min(c.margin_ms for c in fit_cues) if fit_cues else None
    )

    return {
        "media": {
            "path": media_path,
            "duration_s": media_duration,
        },
        "settings": {
            "noise_db": noise_db,
            "min_gap_s": min_gap_s,
            "headroom_ms": headroom_ms,
        },
        "totals": {
            "gaps": len(cues),
            "fit": len(fit_cues),
            "overflow": len(overflow_cues),
            "skipped": len(skipped) if skipped else 0,
            "described_seconds": described_seconds,
            "tightest_margin_ms": tightest_margin_ms,
        },
        "cues": [
            {
                "gap_index": c.gap_index,
                "gap_start_s": c.gap_start_s,
                "gap_end_s": c.gap_end_s,
                "gap_duration_s": c.gap_duration_s,
                "char_budget": c.char_budget,
                "chars_per_second": c.chars_per_second,
                "text": c.text,
                "chars": c.chars,
                "audio_path": c.audio_path,
                "clip_path": c.clip_path,
                "rendered_duration_s": c.rendered_duration_s,
                "margin_ms": c.margin_ms,
                "verdict": c.verdict,
                "attempts": c.attempts,
                "attempt_log": list(c.attempt_log),
            }
            for c in cues
        ],
        "skipped": skipped or [],
    }
