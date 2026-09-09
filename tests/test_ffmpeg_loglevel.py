"""Silence and scene detection must survive the ffmpeg log level they print at.

`silencedetect` and `scdet` both write their findings to stderr at ffmpeg log
level `info`. Pass `-v error` and every line vanishes while ffmpeg still exits
zero, so the parser sees an empty stderr and reports a film containing no silence
at all. Nothing else fails: no exception, no non-zero exit, no empty output file.
A run against a real film would print "measured 0 gaps" and stop.

These tests build a file whose silence is known by construction and assert the
measurement finds it, so raising the log level anywhere in the chain turns them
red. They are the only checks in this repo that would notice.
"""

from __future__ import annotations

import subprocess

import pytest

import ad.gaps as _gaps_mod
import ad.visual as _visual_mod

# tone, silence, tone. The silence is 5 s long and starts at 3 s.
_TONE_S = 3.0
_SILENCE_S = 5.0
_EXPECTED_START_S = _TONE_S
_EXPECTED_DURATION_S = _SILENCE_S


@pytest.fixture(scope="module")
def tone_gap_tone(tmp_path_factory: pytest.TempPathFactory, ffmpeg_tools) -> str:
    """A real media file: 3 s of tone, 5 s of digital silence, 3 s of tone.

    Built with ffmpeg rather than committed, so the fixture cannot drift away
    from the ffmpeg the tests actually run against.
    """
    out = tmp_path_factory.mktemp("loglevel") / "tone-gap-tone.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={_TONE_S}",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:duration={_SILENCE_S}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={_TONE_S}",
        "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=10:d={_TONE_S + _SILENCE_S + _TONE_S}",
        "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]",
        "-map", "[a]", "-map", "3:v",
        "-c:a", "aac", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-shortest",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    assert out.is_file() and out.stat().st_size > 0
    return str(out)


def test_measure_gaps_finds_a_silence_that_is_there(tone_gap_tone: str) -> None:
    """The 5 s silence at 3 s must come back, at the length it was built with.

    Raise the log level in ad.gaps.measure_gaps and this returns an empty list.
    """
    gaps = _gaps_mod.measure_gaps(tone_gap_tone, noise_db=-40.0, min_gap_s=1.0)
    assert len(gaps) == 1, f"expected exactly one silence, got {gaps}"
    gap = gaps[0]
    assert abs(gap.start_s - _EXPECTED_START_S) < 0.25, gap
    assert abs(gap.duration_s - _EXPECTED_DURATION_S) < 0.25, gap
    assert gap.source_lines, "the raw ffmpeg lines must be kept for audit"
    assert any("silence" in line for line in gap.source_lines)


def test_error_log_level_would_hide_the_silence(tone_gap_tone: str) -> None:
    """The gotcha itself, measured rather than asserted from memory.

    Runs the same detection twice on the same file: once as ad.gaps runs it, once
    with `-v error` added. The second exits zero and prints nothing, which is what
    makes the mistake invisible in production. If a future ffmpeg moves
    silencedetect to the error level this test goes red and the comment in
    ad.gaps needs rewriting.
    """
    base = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", tone_gap_tone,
        "-af", "silencedetect=noise=-40dB:d=1.0",
        "-f", "null", "-",
    ]
    quiet = base[:1] + ["-v", "error"] + base[1:]

    loud_run = subprocess.run(base, capture_output=True, text=True)
    quiet_run = subprocess.run(quiet, capture_output=True, text=True)

    assert loud_run.returncode == 0
    assert quiet_run.returncode == 0, "the suppressed run must still exit zero"
    assert "silence_end" in loud_run.stderr
    assert "silence_end" not in quiet_run.stderr
    assert "silence_start" not in quiet_run.stderr


def test_no_detection_function_suppresses_its_own_output(ffmpeg_tools) -> None:
    """The two functions that read a filter's log must not raise the log level.

    Scoped to those functions rather than to whole modules, because `-v error` on
    ffprobe in `probe_duration` is correct: ffprobe writes the duration to stdout
    and the flag only quiets its banner. The mistake is specific to a filter whose
    findings ARE the log.
    """
    import inspect

    for func, needle in (
        (_gaps_mod.measure_gaps, "silencedetect"),
        (_visual_mod.scene_scores, "scdet"),
    ):
        source = inspect.getsource(func)
        assert needle in source, func.__name__
        assert '"-v"' not in source, f"{func.__name__} sets an ffmpeg log level"
        assert "loglevel" not in source, f"{func.__name__} sets an ffmpeg log level"
        assert "-hide_banner" in source, func.__name__
        assert "-nostats" in source, func.__name__


def test_scene_scores_are_measured_not_empty(tone_gap_tone: str) -> None:
    """scdet must return a score per frame across the silence window.

    The fixture's video is a constant black frame, so every score is near zero.
    That is the point: a real number list proves the filter's log was read, and an
    empty list is what `-v error` produces.
    """
    scores = _visual_mod.scene_scores(tone_gap_tone, _EXPECTED_START_S, _EXPECTED_DURATION_S)
    assert len(scores) >= 10, f"expected a score per frame, got {len(scores)}"
    activity = _visual_mod.visual_activity(
        tone_gap_tone, _EXPECTED_START_S, _EXPECTED_DURATION_S
    )
    assert activity["frames"] == len(scores)
    assert activity["peak"] < 1.0, "a constant black frame cannot be a scene change"
