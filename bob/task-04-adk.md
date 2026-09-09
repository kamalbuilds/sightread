Continue on Sightread. A measured constant, a new measurement module, and the ADK
graph. Production code only: no mocks, no stubs, no placeholder data, no `TODO`,
no decorative comments, no em dashes.

Read `ad/gaps.py`, `ad/fit.py`, `ad/describe.py`, `ad/render.py` and
`ad/conform.py` first, and use their real signatures.

## Change 1: `ad/fit.py`, replace an assumed constant with a measured one

`target_chars` currently defaults `chars_per_second` to 14.0. That number came
from the subtitle reading-speed convention and it is wrong for this pipeline.
Measured across 7 real takes rendered by Gemini 2.5 Flash TTS, voice Kore, on
Night Tide (1961): 5.7, 7.3, 7.3, 8.6, 10.1, 10.7, 11.4 characters per second.
Minimum 5.7, median 8.6, maximum 11.4.

Change the default to 8.6, and put the distribution, the sample size, the model
and the voice in the docstring next to it, so a later reader can see where the
number came from and knows it is a median over 7 samples rather than a constant
someone liked. Say in the docstring that at the median half of first attempts are
expected to overflow and the retry loop is what makes that safe.

## Change 2: new file `ad/rate.py`

So the constant above is checkable against data rather than trusted.

- `def observed_rates(report: dict) -> list[float]`
  - Given a report dict produced by `ad.conform.report`, return the observed
    speaking rate `chars / rendered_duration_s` for every attempt in every cue's
    `attempt_log`, skipping any attempt whose `rendered_duration_s` is zero.
- `def summarise(rates: list[float]) -> dict`
  - Returns `{"n": int, "min": float, "median": float, "max": float, "mean": float}`,
    each rounded to 2 decimals. Return
    `{"n": 0, "min": None, "median": None, "max": None, "mean": None}` for an
    empty list. Use `statistics` from the standard library.
- `def main() -> int` plus an `if __name__ == "__main__":` guard: takes one or
  more report JSON paths as arguments, pools every attempt across all of them,
  prints the summary and the full sorted rate list. This is how the default in
  `target_chars` gets re-derived when there is more data.

## Change 3: new file `ad/visual.py`

The coverage planner below needs to know which gaps actually contain a visual
change worth describing. Silence alone does not mean something happened on
screen. This measures it with ffmpeg's scene change detector.

- `class VisualError(RuntimeError)`.
- `def scene_scores(media_path: str, start_s: float, duration_s: float) -> list[float]`
  - Runs `ffmpeg -hide_banner -nostats -ss <start_s> -i <media_path> -t
    <duration_s> -an -vf scdet=threshold=0 -f null -` and parses every
    `lavfi.scd.score: <float>` value out of stderr, in order. Verified: this
    filter prints one score per frame at log level info, so do not pass `-v error`.
  - Raise `VisualError` when ffmpeg is missing or exits non-zero.
  - An empty list is a legitimate result for a gap too short to hold two frames.
    Return it rather than raising.
- `def visual_activity(media_path: str, start_s: float, duration_s: float) -> dict`
  - Returns `{"frames": int, "peak": float, "mean": float}` from `scene_scores`,
    each float rounded to 4 decimals, with `peak` and `mean` set to 0.0 when there
    are no scores. `peak` is the strongest single frame-to-frame change in the
    gap, which is the signal that a cut or a significant movement happened.

## Change 4: new file `agent/graph.py`

The pipeline as a real ADK graph. `google.adk.workflow.Workflow` is the current
orchestration primitive in ADK 2.8 and is what this must use. `SequentialAgent`
and friends are deprecated in the installed wheel. This API shape is verified
against a working pipeline in a sibling project, so use it exactly:

```python
from google.adk.workflow import RetryConfig, Workflow, node, START
from google.adk.agents import LlmAgent
from pydantic import BaseModel, Field

step = node(_some_async_fn, name="step")
agent_step = node(SomeLlmAgent(), retry_config=RetryConfig(...), timeout=120)

Workflow(name="...", state_schema=SomeStateModel, edges=[(START, first), (first, second)])
```

Function nodes are `async def fn(ctx, param: str, other: int)`. Every parameter
name must exist as a field on the state schema, because Workflow validates
function-node signatures against the schema at construction time. Nodes read
state with `ctx.state.get("key")` and write it with `ctx.state["key"] = value`.
State changes are tracked by assignment, so never mutate a list in place; build a
new list and assign it.

`LlmAgent(name=..., model=..., instruction=..., output_schema=SomePydanticModel,
output_key="state_key")`. The instruction string is a template that interpolates
`{field_name}` out of state, so any value an instruction needs must be on the
state schema as a preformatted string.

Build this graph:

```
START
  |
measure            ffmpeg silencedetect over the whole film        deterministic
  |
survey             ffmpeg scdet per gap, visual activity           deterministic
  |
coverage_planner   which gaps are worth describing, and why              MODEL
  |
conform            describe, render, re-measure, retry shorter     deterministic
  |
verify             re-measure every accepted WAV from disk         deterministic
  |
report             assemble the conform report                     deterministic
```

Requirements:

- `class ADState(BaseModel)` with at least: `media_path: str`, `noise_db: float =
  -26.0`, `min_gap_s: float = 4.0`, `headroom_ms: int = 250`, `chars_per_second:
  float = 8.6`, `max_attempts: int = 3`, `project: str = ""`, `location: str =
  "us-central1"`, `out_dir: str = "out"`, `gaps: list[dict]`, `survey: list[dict]`,
  `gaps_json: str = "[]"`, `plan: dict`, `selected: list[int]`, `cues: list[dict]`,
  `skipped: list[dict]`, `verified: dict`, `report: dict`, `steps: list[dict]`.
  Use `Field(default_factory=...)` for the mutable ones.
- `measure` calls `ad.gaps.measure_gaps` and stores each Gap as a dict.
- `survey` calls `ad.visual.visual_activity` for each gap and stores the results
  aligned by gap index. It also writes `gaps_json`, a compact JSON string with one
  object per gap carrying `index`, `start_s`, `end_s`, `duration_s`, `char_budget`
  from `ad.fit.target_chars`, and `peak` and `mean` from the survey. That string
  is what the planner's instruction interpolates.
- `class CoveragePlan(BaseModel)` with `selected: list[int]` and
  `reasons: dict[str, str]` and `note: str`. `coverage_planner` is an `LlmAgent`
  with `output_schema=CoveragePlan` and `output_key="plan"`. Its instruction, in
  its own words but covering all of this: it is an audio description coverage
  planner working to a fixed budget of gaps; it receives every measured gap with
  its duration, character budget and visual activity; it must select the gaps
  where a described line earns its place, preferring gaps with high peak visual
  activity and enough character budget to say something useful, and skipping gaps
  that are long but visually static because narrating a still frame wastes the
  listener's attention; it must return only indices that appear in the input; and
  it must give a one-line reason per selected index. It holds no tools, so it
  cannot invent a gap.
- `select` is folded into the node after the planner: a deterministic node named
  `conform` that first filters the planner's `selected` list down to indices that
  genuinely exist in `gaps`, discarding any the model invented, then runs
  `ad.conform.conform` over exactly those gaps. Record any discarded index in
  `skipped` with a reason. Never trust the model's index list unchecked.
- `verify` independently re-measures every accepted cue's WAV from disk with
  `ad.gaps.probe_duration` and recomputes the verdict with `ad.fit.check_fit`,
  comparing against what `conform` reported. It stores
  `{"checked": int, "agreed": int, "disagreed": list[dict]}`. This node exists
  because a conform step that reported a fit it never measured would otherwise be
  invisible. Any disagreement is recorded, not swallowed.
- `report` calls `ad.conform.report` and stores the dict, adding the `verified`
  block to it.
- Each node appends one entry to `steps` via a module-level `_step(ctx, name, ok,
  summary, data)` helper that reassigns the list rather than appending in place.
- `def build_workflow() -> Workflow` returns the assembled graph.

## Rules

- Python 3.12+, full type annotations, `from __future__ import annotations`.
- Long ffmpeg and network work inside async nodes goes through
  `asyncio.to_thread` so a node never blocks the loop.
- Module docstrings saying what each measures and what it refuses to take on
  trust. In `agent/graph.py` say plainly which nodes hold a model and which do
  not, and that no model can skip `verify`.
- Create no other files. Do not create tests. Do not run git. Do not execute
  anything.
- Reply with one line per file changed or created and the public names it defines.
