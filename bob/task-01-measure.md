You are authoring two production modules for Sightread, an audio description
conforming tool. Write real code. No mocks, no stubs, no placeholder data, no
`TODO`. No decorative comments. No em dashes anywhere.

Workspace root is the current directory. Create exactly these two files:

## File 1: `ad/gaps.py`

Purpose: find the real gaps in a film's dialogue where an audio description line
could physically fit, by measuring the audio with ffmpeg. Nothing is estimated.

Requirements:

- `@dataclass(frozen=True) class Gap` with fields:
  `index: int`, `start_s: float`, `end_s: float`, `duration_s: float`,
  `source_lines: tuple[str, ...]`.
  `source_lines` holds the verbatim ffmpeg `silencedetect` output lines the gap
  was parsed from, so every gap can be traced back to the measurement that
  produced it.
- `def measure_gaps(media_path: str, *, noise_db: float = -30.0, min_gap_s: float = 1.2, start_s: float = 0.0, duration_s: float | None = None) -> list[Gap]`
  - Runs ffmpeg once with the `silencedetect` audio filter. ffmpeg emits
    silencedetect results at log level `info`, so do NOT pass `-v error` or the
    output is silently discarded. Use `-hide_banner -nostats`, read stderr.
  - Build the command as: `ffmpeg -hide_banner -nostats` then, when `start_s` is
    non-zero, `-ss <start_s>`, then when `duration_s` is not None, `-t <duration_s>`,
    then `-i <media_path>`, then
    `-af silencedetect=noise=<noise_db>dB:d=<min_gap_s>`, then `-f null -`.
  - Parse the lines `silence_start: X`, `silence_end: X | silence_duration: Y`.
    ffmpeg reports these times relative to the seek point, so add `start_s` to
    every parsed timestamp to get absolute media time.
  - Handle the leading-silence case: when the media begins in silence, ffmpeg
    emits a `silence_end` line with no preceding `silence_start`. That gap starts
    at `start_s`. Derive its start from `silence_end` minus `silence_duration`
    rather than assuming zero.
  - Handle the trailing case: a `silence_start` with no matching `silence_end`
    means the media ended while still silent. Close that gap at the media's real
    end time, obtained from `probe_duration`.
  - Discard any gap whose duration is below `min_gap_s`.
  - `index` is the gap's 0-based position in the returned list, ordered by
    `start_s`.
  - Raise `GapMeasurementError` (define it, subclass of `RuntimeError`) when
    ffmpeg is missing or exits non-zero. Include ffmpeg's last stderr lines in
    the message. Never return an empty list to signal failure.
- `def probe_duration(media_path: str) -> float`
  - Uses `ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1`.
    Returns seconds as a float. Raises `GapMeasurementError` on failure or when
    the output does not parse as a number.

## File 2: `ad/fit.py`

Purpose: decide whether a rendered audio description line actually fits the gap
it was written for. This is the check the whole tool exists for, so it must be
capable of failing.

Requirements:

- `@dataclass(frozen=True) class FitResult` with fields:
  `gap_index: int`, `gap_duration_s: float`, `rendered_duration_s: float`,
  `headroom_ms: int`, `margin_ms: int`, `verdict: str`.
  `verdict` is exactly `"FIT"` or `"OVERFLOW"`.
  `margin_ms` is the signed spare room in milliseconds:
  `round((gap_duration_s - headroom_ms/1000 - rendered_duration_s) * 1000)`.
  A negative `margin_ms` means the line overruns the usable part of the gap.
- `def check_fit(gap_index: int, gap_duration_s: float, rendered_audio_path: str, *, headroom_ms: int = 250) -> FitResult`
  - Measures the rendered audio's real duration with `ad.gaps.probe_duration`.
    Do not trust any duration reported by whatever produced the audio.
  - `headroom_ms` is the deliberate silence kept at both ends of the gap so the
    description never clips the surrounding dialogue. Subtract it once from the
    usable gap.
  - `verdict` is `"FIT"` when `margin_ms >= 0`, otherwise `"OVERFLOW"`.
  - Raise `ValueError` when `gap_duration_s` or `headroom_ms` is negative.
- `def target_chars(gap_duration_s: float, *, headroom_ms: int = 250, chars_per_second: float = 14.0) -> int`
  - The character budget for a description that should fit the usable gap at a
    given speaking rate. Returns an int, never negative. This is the value handed
    to the description writer as a hard limit, and `check_fit` is what verifies
    the claim afterwards.

## Rules

- Python 3.12+, standard library plus `subprocess` only. No third-party imports.
- Full type annotations. `from __future__ import annotations` at the top.
- Every module gets a one-paragraph docstring saying what it measures and why the
  measurement is trusted over any reported value.
- Do not create any other files. Do not create tests. Do not run git.
- When both files are written, reply with one line per file: the path and the
  public names it defines.
