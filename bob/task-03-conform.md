Continue on Sightread. One new module: the conforming loop that ties the existing
pieces together. Production code only: no mocks, no stubs, no placeholder data,
no `TODO`, no decorative comments, no em dashes.

Read `ad/gaps.py`, `ad/fit.py`, `ad/describe.py` and `ad/render.py` first. Use
their real signatures. Do not re-implement anything they already do.

## File: `ad/conform.py`

This is the product. For each measured gap it writes a description, renders it,
measures the rendered audio, and either accepts it or rejects it and tries again
shorter. Nothing is accepted on the model's word; acceptance requires a
measurement of the rendered file.

Requirements:

- `@dataclass(frozen=True) class ConformedCue` with fields:
  `gap_index: int`, `gap_start_s: float`, `gap_end_s: float`,
  `gap_duration_s: float`, `char_budget: int`, `text: str`, `chars: int`,
  `audio_path: str`, `clip_path: str`, `rendered_duration_s: float`,
  `margin_ms: int`, `verdict: str`, `attempts: int`, `attempt_log: tuple[dict, ...]`.
  `attempt_log` holds one dict per attempt with keys `attempt`, `char_budget`,
  `chars`, `rendered_duration_s`, `margin_ms`, `verdict`. Every attempt is kept,
  including the failures, because the rejected takes are the evidence the check
  can fail.

- `def conform_gap(media_path: str, gap: Gap, out_dir: str, *, project: str, location: str = "us-central1", headroom_ms: int = 250, chars_per_second: float = 14.0, max_attempts: int = 3, describe_model: str = "gemini-2.5-flash", tts_model: str = "gemini-2.5-flash-tts", voice: str = "Kore") -> ConformedCue`
  - Attempt 1 budget comes from `ad.fit.target_chars(gap.duration_s,
    headroom_ms=headroom_ms, chars_per_second=chars_per_second)`.
  - Raise `ConformError` (define it, subclass `RuntimeError`) when the attempt 1
    budget is zero, because a gap shorter than the headroom cannot hold anything.
  - Per attempt: `describe_gap`, then `render_line`, then `check_fit`.
    Clip path `{out_dir}/gap{index:04d}.clip.mp4`, reused across attempts since the
    video does not change. Audio path
    `{out_dir}/gap{index:04d}.take{attempt}.wav`, one file per attempt so the
    rejected takes remain on disk.
  - On `verdict == "FIT"`, stop and return.
  - On `verdict == "OVERFLOW"`, compute the next budget from the measurement, not
    from a fixed step: the line overran by `-margin_ms`, so at the observed
    speaking rate of `chars / rendered_duration_s` characters per second, it must
    lose at least `ceil(-margin_ms / 1000 * observed_cps)` characters. Next budget
    is `chars` minus that, minus a further 5 percent of `chars` as a safety
    margin, floored at 1. Never raise the budget between attempts.
  - Build the `shrink_note` for the retry from the real numbers, naming the
    measured rendered duration, the gap's usable duration and the new limit, so
    the model is told what actually happened rather than just being asked again.
  - After `max_attempts` overflows, return the ConformedCue with the last
    attempt's values and `verdict` `"OVERFLOW"`. Do not raise. An unfittable gap
    is a real result that belongs in the report, not an exception.
  - `attempts` is the number of attempts actually made.

- `def conform(media_path: str, gaps: list[Gap], out_dir: str, **kwargs) -> list[ConformedCue]`
  - Runs `conform_gap` over the gaps in order, passing `**kwargs` through.
  - A `ConformError` on one gap must not abort the rest. Skip that gap and carry
    on. Collect the skips.

- `def report(media_path: str, cues: list[ConformedCue], *, noise_db: float, min_gap_s: float, headroom_ms: int, skipped: list[dict] | None = None) -> dict`
  - Returns a JSON-serialisable dict with:
    - `media`: the path, and its real duration from `ad.gaps.probe_duration`
    - `settings`: `noise_db`, `min_gap_s`, `headroom_ms`
    - `totals`: `gaps`, `fit`, `overflow`, `skipped`, `described_seconds`
      (sum of `rendered_duration_s` over cues whose verdict is `"FIT"`, rounded to
      3 decimals), `tightest_margin_ms` (the smallest `margin_ms` among FIT cues,
      or `None` when there are none)
    - `cues`: every cue as a dict including its full `attempt_log`
    - `skipped`: the skip records
  - Every number in the report must come from a measurement already taken. Do not
    recompute a duration by any other means and do not round anything except
    `described_seconds`.

## Rules

- Python 3.12+, full type annotations, `from __future__ import annotations`.
- Import from `ad.gaps`, `ad.fit`, `ad.describe`, `ad.render`. No other
  third-party imports beyond what those already pull in.
- Module docstring saying what it measures and what it refuses to accept on trust.
- Create no other files. Do not create tests. Do not run git. Do not execute
  anything, just write the code.
- Reply with the path and the public names it defines.
