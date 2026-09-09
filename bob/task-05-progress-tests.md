Continue on Sightread. One addition to `agent/graph.py` and one new test file.
Production code only: no mocks, no stubs, no placeholder data, no `TODO`, no
decorative comments, no em dashes.

## Change 1: `agent/graph.py`, a progress hook

`agent/pipeline.py` needs to push partial progress to a browser while a node is
still running, so the web layer does not show a spinner for four minutes and then
everything at once. It expects a module-level registry in `agent/graph.py` that
does not currently exist:

```python
graph._PROGRESS[run_id] = on_step     # callable(list[dict]) -> None
graph._PROGRESS.pop(run_id, None)
```

Add `_PROGRESS: dict[str, object] = {}` at module level, keyed by run id, and make
`_step` look up `ctx.state.get("run_id")` and call the registered callable with
the new steps list after assigning it. A missing key is normal and must not raise.
An exception raised by the callable must not take the pipeline down, since a
browser disconnecting is not a reason to abandon a run that has already spent
minutes of ffmpeg. Add `run_id: str = ""` to `ADState` if it is not already there.

## Change 2: new file `tests/test_measure.py`

Tests for the deterministic core, using pytest. These must be able to fail, so
every assertion has to be one that a wrong implementation would break. Do not
write a test that passes on an empty result.

- `test_parse_leading_silence_derives_start_from_duration`
  Feed `_parse_silencedetect` a captured stderr block containing a `silence_end`
  line with a `silence_duration` and no preceding `silence_start`, which is what
  ffmpeg emits when the media opens in silence. Assert the resulting gap's
  `start_s` equals `end_s` minus the reported duration, not zero.
- `test_parse_trailing_silence_closes_at_window_end`
  Feed a stderr block with a `silence_start` and no matching `silence_end`, with
  `window_end` set. Assert the gap closes at `window_end`. This is the bug that
  invented a gap tens of minutes long on an 87 minute film, so the assertion must
  fail if the code reverts to using the whole file's duration.
- `test_parse_absolute_times_include_seek_offset`
  Same input parsed with `start_s=600.0`. Assert every timestamp is offset by 600.
- `test_gaps_below_minimum_are_discarded`
  A stderr block containing one gap shorter than `min_gap_s` and one longer.
  Assert only the longer one is returned, and that its `index` is 0.
- `test_gap_indices_are_chronological`
  Assert indices ascend with `start_s` across at least three gaps.
- `test_check_fit_catches_a_real_overrun`
  Use the real WAV at `out/nighttide/gap0002.take1.wav`, which is 6.130958s of
  rendered speech, against its real gap duration of 2.184s. Assert
  `verdict == "OVERFLOW"` and `margin_ms == -4197`. Skip the test with
  `pytest.skip` when that file is absent, rather than passing vacuously.
- `test_check_fit_accepts_the_take_that_fit`
  Use `out/nighttide/gap0002.take2.wav` against the same 2.184s gap. Assert
  `verdict == "FIT"` and `margin_ms == 3`. Same skip rule.
- `test_headroom_is_subtracted_once`
  Assert that raising `headroom_ms` by 500 lowers `margin_ms` by exactly 500 for
  the same gap and same rendered file.
- `test_target_chars_never_negative`
  A gap shorter than the headroom returns 0, not a negative budget.
- `test_target_chars_uses_the_measured_rate`
  Assert `target_chars(10.25, headroom_ms=250)` equals `int(10.0 * 8.6)`, so the
  measured default is pinned and a silent change to it breaks a test.
- `test_observed_rates_skips_zero_duration_attempts`
  Build a report dict with one attempt whose `rendered_duration_s` is 0 and one
  with a real duration. Assert `ad.rate.observed_rates` returns exactly one value.

Put the captured ffmpeg stderr blocks in the test file as module-level string
constants with a comment naming which real run they came from. Use this real
shape, which is what ffmpeg 9.0.1 emits:

```
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 1.212154 | silence_duration: 1.212154
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_start: 111.462472
```

## Rules

- Python 3.12+, pytest, full type annotations on helpers.
- Import the code under test from `ad.gaps`, `ad.fit`, `ad.rate`.
- No network calls, no Gemini calls, no TTS calls in the tests.
- Create no other files. Do not run git. Do not run the tests.
- Reply with the path and the test names.
