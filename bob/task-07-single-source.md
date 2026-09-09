Continue on Sightread. One defect, in `ad/conform.py`. Production code only: no
mocks, no stubs, no placeholder data, no `TODO`, no decorative comments, no em
dashes.

`ad/fit.py` now defaults `chars_per_second` to 8.6, which is the measured median
over 19 rendered takes. But `ad/conform.py:conform_gap` still declares its own
default of 14.0 and passes it down, so anyone calling `conform_gap` or `conform`
without naming the argument silently gets the old assumed constant that the
measurements disproved. Two copies of a number that must agree is how the wrong
one survives.

Fix it by removing the duplicate rather than by editing it to 8.6:

- Change `conform_gap`'s and `conform`'s `chars_per_second` parameter to
  `float | None = None`.
- When it is `None`, do not substitute a literal. Call `ad.fit.target_chars`
  without the argument so `ad/fit.py`'s own default applies, and let that module
  stay the single place the number lives.
- When it is not `None`, pass it through as now, since an operator overriding the
  rate for a different voice is a legitimate call.
- Whatever rate was actually in force must still reach the report. Record the
  effective value on the `ConformedCue` as a new field `chars_per_second: float`,
  resolved from `ad.fit`'s default when the caller passed `None`, so a report can
  never be read without knowing which rate produced its budgets. Add it to the
  cue dicts in `report` too.

Then add two tests to `tests/test_measure.py`:

- `test_conform_does_not_redeclare_the_rate`
  Assert, by reading the default off the function signature with
  `inspect.signature`, that `ad.conform.conform_gap`'s `chars_per_second` default
  is `None` and that `ad.fit.target_chars`'s is `8.6`. This test exists so that
  re-introducing a second copy of the constant breaks the build rather than
  quietly changing every budget.
- `test_target_chars_default_matches_fit_module`
  Assert `ad.fit.target_chars(10.25, headroom_ms=250)` equals
  `ad.fit.target_chars(10.25, headroom_ms=250, chars_per_second=8.6)`, so the
  named default and the applied default cannot drift apart.

Run `pytest tests/ -q` yourself and report the result. All tests must pass.

Do not change `ad/fit.py`'s value. Do not run git.
