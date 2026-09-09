"""Decides whether a rendered audio description line fits inside a measured
silence gap. Duration is taken from the rendered audio file itself via ffprobe,
not from any tag or filename, because encoding pipelines routinely pad or trim
output and the only trustworthy answer comes from reading the waveform. The
margin value is signed so an overflow is always visible as a negative number
rather than a silent discard.
"""

from __future__ import annotations

from dataclasses import dataclass

from ad.gaps import probe_duration


@dataclass(frozen=True)
class FitResult:
    gap_index: int
    gap_duration_s: float
    rendered_duration_s: float
    headroom_ms: int
    margin_ms: int
    verdict: str


def check_fit(
    gap_index: int,
    gap_duration_s: float,
    rendered_audio_path: str,
    *,
    headroom_ms: int = 250,
) -> FitResult:
    """Check whether the rendered audio file fits inside the gap.

    Raises ValueError when *gap_duration_s* or *headroom_ms* is negative.
    Raises GapMeasurementError when ffprobe cannot read *rendered_audio_path*.
    """
    if gap_duration_s < 0:
        raise ValueError(f"gap_duration_s must be non-negative, got {gap_duration_s}")
    if headroom_ms < 0:
        raise ValueError(f"headroom_ms must be non-negative, got {headroom_ms}")

    rendered_duration_s = probe_duration(rendered_audio_path)

    margin_ms = round(
        (gap_duration_s - headroom_ms / 1000 - rendered_duration_s) * 1000
    )
    verdict = "FIT" if margin_ms >= 0 else "OVERFLOW"

    return FitResult(
        gap_index=gap_index,
        gap_duration_s=gap_duration_s,
        rendered_duration_s=rendered_duration_s,
        headroom_ms=headroom_ms,
        margin_ms=margin_ms,
        verdict=verdict,
    )


def target_chars(
    gap_duration_s: float,
    *,
    headroom_ms: int = 250,
    chars_per_second: float = 8.6,
) -> int:
    """Return the character budget for a description that fits the usable gap.

    The result is the floor of usable seconds multiplied by *chars_per_second*,
    clamped to zero so a gap shorter than the headroom never returns a negative
    budget.

    Default *chars_per_second* is the median of 7 observed speaking rates
    rendered by gemini-2.5-flash-tts, voice Kore, on Night Tide (1961):

        samples (n=7): 5.7, 7.3, 7.3, 8.6, 10.1, 10.7, 11.4 chars/s
        min 5.7  median 8.6  max 11.4

    At the median, half of first attempts are expected to overflow the gap.
    That is by design: the retry loop in conform_gap is what makes that safe.
    Re-derive this constant with ad.rate when more data is available.
    """
    usable_s = gap_duration_s - headroom_ms / 1000
    if usable_s <= 0:
        return 0
    return int(usable_s * chars_per_second)
