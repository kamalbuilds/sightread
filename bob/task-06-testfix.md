Continue on Sightread. One test in `tests/test_measure.py` is failing, and it is
the test that is wrong, not the code.

Run of `pytest tests/ -q`: 10 passed, 1 failed.

```
test_parse_leading_silence_derives_start_from_duration
    assert gap.start_s != 0.0
E   AssertionError: assert 0.0 != 0.0
    Gap(index=0, start_s=0.0, end_s=1.212154, duration_s=1.212154, ...)
```

The fixture `_STDERR_LEADING_SILENCE` uses a real captured line where
`silence_end: 1.212154 | silence_duration: 1.212154`. That is a genuine leading
silence at the very start of the file, so `end - duration` is exactly 0.0 and the
correct answer IS zero. The first assertion, that `start_s == end_s - duration`,
passes and is the one that matters. The added `!= 0.0` assertion cannot
distinguish a correct implementation from a broken one on that fixture, so it is
worse than no assertion.

Fix it so the test discriminates. Replace the fixture for this test with a
silencedetect block where a `silence_end` line arrives with no preceding
`silence_start` and where the two possible answers are different numbers:

```
[Parsed_silencedetect_0 @ 0xbdd061b00] silence_end: 3.482000 | silence_duration: 1.204000
```

With `start_s=0.0` the correct start is 2.278, while an implementation that
assumed the window start would say 0.0. Assert the correct value to 6 decimal
places and assert it is not the window start. Both assertions can then fail
independently.

Keep the original all-zeros capture as a second test named
`test_parse_leading_silence_at_time_zero`, asserting the start is exactly 0.0 for
that input, so the true-leading-silence case stays covered. Note in a docstring
that these are two different cases: a silence that really begins at the start of
the analysed window, and a silence whose opening marker was not emitted.

Then run `pytest tests/ -q` yourself and report the result. All tests must pass.

No other changes. Do not touch `ad/gaps.py`; the code is correct. Do not run git.
