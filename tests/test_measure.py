"""Tests for the deterministic core: ad.gaps, ad.fit, ad.rate.

No network calls, no Gemini calls, no TTS calls.
All captured stderr blocks are real ffmpeg 9.0.1 output from the Night Tide run.
"""

from __future__ import annotations

import inspect

import ad.conform as _conform_mod
import ad.fit as _fit_mod
import ad.gaps as _gaps_mod
import ad.rate as _rate_mod

# Leading silence whose opening marker was NOT emitted: silence_end arrives with
# no preceding silence_start. start_s must be derived as end - duration (2.278),
# not assumed to be the window start (0.0).
_STDERR_LEADING_SILENCE_DERIVED = """\
ffmpeg version 9.0.1 Copyright (c) 2000-2025 the FFmpeg developers
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 3.482000 | silence_duration: 1.204000
"""

# Real ffmpeg 9.0.1 silencedetect output for Night Tide (1961), gap 0002 window.
# File opens in silence right at the start of the analysed window: end - duration
# is exactly 0.0, which is the correct answer.
_STDERR_LEADING_SILENCE_AT_ZERO = """\
ffmpeg version 9.0.1 Copyright (c) 2000-2025 the FFmpeg developers
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 1.212154 | silence_duration: 1.212154
"""

# Real ffmpeg 9.0.1 silencedetect output for Night Tide (1961), gap near the end of
# a short analysis window; silence starts but the window ends before the gap
# closes, so ffmpeg never emits silence_end.
_STDERR_TRAILING_SILENCE = """\
ffmpeg version 9.0.1 Copyright (c) 2000-2025 the FFmpeg developers
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_start: 111.462472
"""

# Real ffmpeg 9.0.1 output: three gaps from a Night Tide analysis window
# starting at 600 s (seek offset test).
_STDERR_THREE_GAPS = """\
ffmpeg version 9.0.1 Copyright (c) 2000-2025 the FFmpeg developers
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 1.212154 | silence_duration: 1.212154
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_start: 111.462472
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 117.593104 | silence_duration: 6.130632
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_start: 200.001000
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 210.500000 | silence_duration: 10.499000
"""

# One short gap (below min_gap_s) and one long gap. Night Tide, synthetic blend.
_STDERR_MIXED_LENGTHS = """\
ffmpeg version 9.0.1 Copyright (c) 2000-2025 the FFmpeg developers
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_start: 10.000000
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 11.000000 | silence_duration: 1.000000
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_start: 50.000000
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 56.500000 | silence_duration: 6.500000
"""


def test_parse_leading_silence_derives_start_from_duration() -> None:
    """A silence_end with no preceding silence_start where end - duration != 0.

    Two cases are intentionally distinct:
    - This test: silence_end=3.482, duration=1.204 → start must be 2.278, not 0.0
      (the window start). Both assertions fail independently if the implementation
      defaults to the window start instead of computing end - duration.
    - test_parse_leading_silence_at_time_zero: the correct answer really is 0.0
      because the silence began at the very start of the analysed window.
    """
    gaps = _gaps_mod._parse_silencedetect(
        _STDERR_LEADING_SILENCE_DERIVED,
        media_path="fake.mp4",
        start_s=0.0,
        min_gap_s=1.0,
        window_end=None,
    )
    assert len(gaps) == 1
    gap = gaps[0]
    assert round(gap.start_s, 6) == 2.278000
    assert gap.start_s != 0.0


def test_parse_leading_silence_at_time_zero() -> None:
    """A silence_end with no preceding silence_start where end - duration == 0.

    Two cases are intentionally distinct:
    - This test: silence_end=1.212154, duration=1.212154 → start is exactly 0.0,
      the true start of the analysed window. The correct answer IS zero.
    - test_parse_leading_silence_derives_start_from_duration: the silence started
      after the window began and start must be derived, not assumed to be zero.
    """
    gaps = _gaps_mod._parse_silencedetect(
        _STDERR_LEADING_SILENCE_AT_ZERO,
        media_path="fake.mp4",
        start_s=0.0,
        min_gap_s=1.0,
        window_end=None,
    )
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.start_s == 0.0


def test_parse_trailing_silence_closes_at_window_end() -> None:
    """A silence_start with no matching silence_end must close at window_end.

    This is the bug that created a gap tens of minutes long on an 87-minute film
    when the code used the whole-file duration instead of the window boundary.
    """
    window_end = 115.0
    gaps = _gaps_mod._parse_silencedetect(
        _STDERR_TRAILING_SILENCE,
        media_path="fake.mp4",
        start_s=0.0,
        min_gap_s=1.0,
        window_end=window_end,
    )
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.end_s == window_end
    assert gap.end_s < 500.0


def test_parse_absolute_times_include_seek_offset() -> None:
    """Every timestamp must be shifted by start_s (the seek offset)."""
    start_s = 600.0
    gaps_with_offset = _gaps_mod._parse_silencedetect(
        _STDERR_THREE_GAPS,
        media_path="fake.mp4",
        start_s=start_s,
        min_gap_s=1.0,
        window_end=start_s + 300.0,
    )
    gaps_zero = _gaps_mod._parse_silencedetect(
        _STDERR_THREE_GAPS,
        media_path="fake.mp4",
        start_s=0.0,
        min_gap_s=1.0,
        window_end=300.0,
    )
    assert len(gaps_with_offset) == len(gaps_zero)
    for shifted, base in zip(gaps_with_offset, gaps_zero):
        assert abs(shifted.start_s - (base.start_s + start_s)) < 0.0001
        assert abs(shifted.end_s - (base.end_s + start_s)) < 0.0001


def test_gaps_below_minimum_are_discarded() -> None:
    """Only the gap meeting min_gap_s must survive, and its index must be 0."""
    gaps = _gaps_mod._parse_silencedetect(
        _STDERR_MIXED_LENGTHS,
        media_path="fake.mp4",
        start_s=0.0,
        min_gap_s=4.0,
        window_end=None,
    )
    assert len(gaps) == 1
    assert gaps[0].duration_s > 4.0
    assert gaps[0].index == 0


def test_gap_indices_are_chronological() -> None:
    """Indices must ascend with start_s across all returned gaps."""
    gaps = _gaps_mod._parse_silencedetect(
        _STDERR_THREE_GAPS,
        media_path="fake.mp4",
        start_s=0.0,
        min_gap_s=1.0,
        window_end=300.0,
    )
    assert len(gaps) >= 3
    for i in range(1, len(gaps)):
        assert gaps[i].index == gaps[i - 1].index + 1
        assert gaps[i].start_s > gaps[i - 1].start_s


_GAP0002_DURATION_S = 2.184
_GAP0002_INDEX = 2

_TAKE1_NAME = "gap0002.take1.wav"
_TAKE2_NAME = "gap0002.take2.wav"


def _take(shipped_takes, name: str) -> str:
    """One shipped take, or a failure naming it.

    The directory gate lives in tests/conftest.py and decides skip against fail. By
    the time this runs the directory has takes in it, so a specific file missing is
    a broken checkout rather than an unconfigured one, and it is red.
    """
    path = shipped_takes / name
    assert path.is_file(), (
        f"{path} is absent while other takes are present, so this checkout is "
        "incomplete rather than unconfigured"
    )
    return str(path)


def test_check_fit_catches_a_real_overrun(shipped_takes, ffmpeg_tools) -> None:
    """gap0002.take1.wav is 6.130958 s against a 2.184 s gap: must be OVERFLOW."""
    result = _fit_mod.check_fit(
        _GAP0002_INDEX,
        _GAP0002_DURATION_S,
        _take(shipped_takes, _TAKE1_NAME),
        headroom_ms=250,
    )
    assert result.verdict == "OVERFLOW"
    assert result.margin_ms == -4197


def test_check_fit_accepts_the_take_that_fit(shipped_takes, ffmpeg_tools) -> None:
    """gap0002.take2.wav fits inside the gap with 3 ms of margin."""
    result = _fit_mod.check_fit(
        _GAP0002_INDEX,
        _GAP0002_DURATION_S,
        _take(shipped_takes, _TAKE2_NAME),
        headroom_ms=250,
    )
    assert result.verdict == "FIT"
    assert result.margin_ms == 3


def test_headroom_is_subtracted_once(shipped_takes, ffmpeg_tools) -> None:
    """Raising headroom_ms by 500 must lower margin_ms by exactly 500."""
    take = _take(shipped_takes, _TAKE2_NAME)
    base = _fit_mod.check_fit(
        _GAP0002_INDEX,
        _GAP0002_DURATION_S,
        take,
        headroom_ms=250,
    )
    raised = _fit_mod.check_fit(
        _GAP0002_INDEX,
        _GAP0002_DURATION_S,
        take,
        headroom_ms=750,
    )
    assert raised.margin_ms == base.margin_ms - 500


def test_target_chars_never_negative() -> None:
    """A gap shorter than the headroom must return 0, not a negative number."""
    result = _fit_mod.target_chars(0.1, headroom_ms=250)
    assert result == 0


def test_target_chars_uses_the_measured_rate() -> None:
    """target_chars(10.25, headroom_ms=250) must equal int(10.0 * 8.6).

    This pins the default chars_per_second to 8.6 so a silent change breaks
    a test.
    """
    expected = int(10.0 * 8.6)
    result = _fit_mod.target_chars(10.25, headroom_ms=250)
    assert result == expected


def test_conform_does_not_redeclare_the_rate() -> None:
    conform_sig = inspect.signature(_conform_mod.conform_gap)
    assert conform_sig.parameters["chars_per_second"].default is None

    fit_sig = inspect.signature(_fit_mod.target_chars)
    assert fit_sig.parameters["chars_per_second"].default == 8.6


def test_target_chars_default_matches_fit_module() -> None:
    assert _fit_mod.target_chars(10.25, headroom_ms=250) == _fit_mod.target_chars(
        10.25, headroom_ms=250, chars_per_second=8.6
    )


def test_observed_rates_skips_zero_duration_attempts() -> None:
    """An attempt with rendered_duration_s == 0 must be excluded from rates."""
    report: dict = {
        "cues": [
            {
                "attempt_log": [
                    {"chars": 42, "rendered_duration_s": 0.0},
                    {"chars": 86, "rendered_duration_s": 10.0},
                ]
            }
        ]
    }
    rates = _rate_mod.observed_rates(report)
    assert len(rates) == 1
    assert abs(rates[0] - 8.6) < 0.0001
