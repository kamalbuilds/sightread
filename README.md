# Sightread

Audio description has to fit in the silence between two lines of dialogue, to the
millisecond. Sightread measures those silences in the actual audio, writes a line
for each one, renders it to speech, then measures the rendered audio again and
rejects the takes that overrun.

The fourth step is the product. Everything else is a description generator, and
there are plenty of those.

```
python run_conform.py NightTide.mp4 --noise-db -26 --min-gap-s 4.0 \
  --out-dir out/nighttide-long --report out/nighttide-long/report.json
```

## The bottleneck

Audio description is mandated by the FCC in the United States and by Ofcom in the
UK, and today it is three people in sequence. A describer watches the film and
writes lines. A narrator records them. A mixer lays them in and discovers the line
does not fit the gap, so it goes back to the describer. That round trip is the
cost, and it exists because nobody in the chain knows the true length of the
silence or the true length of the recorded line until the last step.

Both of those are measurable before anyone speaks.

Live: <https://sightread-387894104564.us-central1.run.app>

## What actually runs

A `google.adk.workflow.Workflow` in `agent/graph.py`, driven by
`agent/pipeline.py`. Seven function nodes, three `LlmAgent` nodes, three routed
branches, one of which closes a cycle.

    measure       ffmpeg silencedetect over the film              ffmpeg decides
    survey        ffmpeg scdet, visual activity per silence       ffmpeg decides
    coverage      which silences are worth describing             GEMINI decides
    draft         describe the picture, speak it, measure it      gemini writes, ffprobe decides
    shorten       what to cut from a line that overran            GEMINI writes
    retake        speak the cut line, measure it again            ffprobe decides
    verify        re-measure every rendered WAV from disk         ffprobe decides
    adjudicate    deliverable, or back to a describer             GEMINI decides
    dispatch      takes the branch, and can overrule it           measurement overrules
    escalate      hand the gaps back with the numbers             no model call
    report        assemble the conform report                     no model call

```
START -> measure -> survey -> coverage_planner -> draft
draft      -> shorten     a take overran
           -> verify      every take fit
shorten    -> retake
retake     -> shorten     a take still overran and rounds remain
           -> verify      otherwise
verify     -> adjudicate -> dispatch
dispatch   -> escalate    the run needs a human describer
           -> report      it is deliverable as measured
escalate   -> report
```

The three model nodes decide three different things, and none of them produces a
number that reaches a verdict.

`coverage_planner` picks which measured silences earn a line, from the gap lengths
and the per-gap scene-change scores. It cannot invent a gap index; `draft` drops
any index that was not in the measurement and records the discard.

`shorten` decides which words leave a line whose real spoken length is already
known. It is handed the line, the measured overrun in milliseconds, and a character
ceiling computed from that overrun at the observed speaking rate. Choosing whether
to lose the adjective, the second sentence or the set dressing is the judgement;
the ceiling is arithmetic. Coming in under the ceiling does not make a line
accepted and coming in over it does not make one rejected, because `retake` renders
whatever comes back and ffprobe decides.

`adjudicate` decides whether the pass is deliverable to a mixer or goes back to a
human describer, and names the gaps a person has to take over. `dispatch` takes the
branch on that one field and can overrule it: an unrecognised disposition routes to
a person, a re-measurement disagreement overrides any verdict, and `publish` is not
available while a row is OVERFLOW. `escalate` hands back every non-FIT gap whether
the adjudicator listed it or not, so the model can widen that set and never narrow
it.

Because `retake` routes back into `shorten` on a routed edge, the number of model
turns in a run is set at runtime by ffprobe rather than written into the topology.
A film whose silences all fit on the first take spends one model turn per gap; one
that does not spends as many as the attempt cap allows.

`verify` re-measures every rendered WAV independently and recomputes its verdict,
because a step that reported a fit it never measured would otherwise be invisible.
There is no route around it: every path out of `draft` reaches `verify`, and
`report` is only reachable through it.

Gemini is required and there is no deterministic stand-in for any of the three
model nodes, because a stand-in that produced the same shape of output would make
the model decorative: you could delete it and the product would behave identically.
`build_workflow` raises before it constructs a single node when this process cannot
reach a model, rather than discovering it inside the first `LlmAgent` after the
whole silencedetect pass over an 87 minute film has already been spent.

## Results on a real film

Night Tide (1961), public domain, from archive.org. The full 87 minute film,
5256.567s by ffprobe. Every figure below came from a measurement, and the report
JSON and every rendered WAV, accepted and rejected, are in `out/`.

### One graph run, end to end

```
python agent/pipeline.py nighttide.mp4 --min-gap-s 4.0 --noise-db -26 \
  --out-dir out/adk-nighttide --report out/adk-nighttide/run.json
```

Nine silences of 4s or longer measured, six selected, six fit, all six verdicts
reproduced by an independent re-measurement from disk. Run
`1c212e66-9ecd-4095-b605-5c6a742f8a79`, on `gemini-2.5-flash`, served at
`/api/adk`:

| silence | length | line | chars | spoken | margin | takes |
|---|---|---|---|---|---|---|
| 00:09:08.681 | 4.932s | A man kisses a woman. | 21 | 2.371s | +2311ms | 1 |
| 00:44:13.262 | 6.935s | A man stands in a room with shelves. Jars are on a shelf. | 57 | 5.531s | +1154ms | 1 |
| 00:44:43.530 | 6.034s | A man in a sailor suit looks down. | 34 | 3.331s | +2453ms | 1 |
| 00:59:57.802 | 9.693s | A man lies in bed, face tense, clenching his fist. A closed door appears. He's back in bed. | 91 | 8.291s | +1152ms | 1 |
| 01:13:57.808 | 4.684s | Man in bed, shirtless, with object. | 35 | 4.091s | +343ms | 1 |
| 01:25:17.804 | 4.375s | Dark clouds, bright light behind. | 33 | 4.011s | +114ms | 1 |

The three silences the planner left out, in its own words:

    gap 3   Low peak indicates minimal significant visual change
    gap 5   Extremely low peak indicates very little visual change
    gap 6   Very low peak suggests minimal visual interest during this silence

Those are the long, visually static silences. Narrating a still frame spends a
listener's attention on nothing, and the scene-change scores that decision was made
from are ffmpeg `scdet` output recorded per gap in the run record.

Then the part a formula would not have produced. Every line fit, nothing disagreed,
and `adjudicate` still refused to call the pass deliverable:

    disposition     escalate
    reason          One description (gap 8) has a very tight margin of 114ms,
                    which requires human review.
    hand_back       [8]
    residual_work   Review description for gap 8 to ensure it fits comfortably
                    or can be shortened.

114ms is a real number from a real WAV. Nothing in the code says a margin under some
threshold is too tight, because nobody has measured what a mixer will tolerate.
`dispatch` then routed on that field, `escalate` recorded the hand-back, and the CLI
exited 3 rather than 0.

### The same graph at a 2s threshold, where the shortening cycle earns its place

```
python agent/pipeline.py nighttide.mp4 --min-gap-s 2.0 --noise-db -26 \
  --out-dir out/adk-nighttide-tight --report out/adk-nighttide-tight/run.json
```

Seventy-six silences at 2 seconds or longer. The path the run took, from its own
step records, served at `/api/adk?run=adk-nighttide-tight`:

```
       measure           found 76 gaps
       survey            surveyed 76 gaps
GEMINI coverage_planner  selected 25 of 76 silences
       draft             13 of 25 first takes fit, 10 to shorten   -> shorten
GEMINI shorten           round 1: rewrote 10 lines
       retake            round 1: rendered 9, 8 now fit, 1 still over  -> shorten
GEMINI shorten           round 2: rewrote 1 line
       retake            round 2: rendered 1, 1 now fit, 0 still over  -> settled
       verify            checked 25, agreed 25, disagreed 0
GEMINI adjudicate        escalate, 5 to hand back
       dispatch          escalate -> escalate                     -> escalate
       escalate          5 gaps handed back to a describer
       report            report assembled, 22 cues deliverable
```

Two turns round the cycle, because `retake` measured a take that still overran and
routed back. Round 1 rewrote 10 lines and rendered 9 of them: the tenth came back
as `'Man sits.'`, 9 characters, and was refused before the TTS call.

Nine silences never reached the shortener at all. Their measured overrun left a
ceiling of 1 or 2 characters, which is not a description, and they were handed to a
describer with the arithmetic attached:

    gap 8   the measured overrun leaves 1 characters, under the 11 a
            description needs. This 2.581s silence cannot hold a spoken
            line at the measured rate.

22 lines fit, 3 stayed OVERFLOW, and the shortest accepted line is exactly 11
characters. The adjudicator handed back all three overflows plus two lines it
judged too tight, and `dispatch` would have overridden a `publish` here regardless,
because a row `ffprobe` calls OVERFLOW cannot be published.

### The same film at a 4s threshold, take by take

Nine silences of 4s or longer, at a -26dB threshold, from the deterministic CLI:

| silence | length | line | chars | rendered | margin | takes |
|---|---|---|---|---|---|---|
| 00:09:08.681 | 4.932s | A man kisses a woman. | 21 | 2.531s | +2151ms | 1 |
| 00:44:13.262 | 6.935s | A man stands in a room. A shelf with jars is visible. | 53 | 5.011s | +1674ms | 1 |
| 00:44:43.530 | 6.034s | A man in a sailor suit looks at a statue. | 41 | 3.771s | +2013ms | 1 |
| 00:45:30.638 | 4.173s | Woman faces man. | 16 | 2.451s | +1472ms | 2 |
| 00:59:57.802 | 9.693s | A man lies in bed, distressed, his hand clenching. A closed door. He lies distressed. | 85 | 8.211s | +1232ms | 1 |
| 01:00:07.860 | 5.072s | Man lies on his side, head on pillow. | 37 | 3.891s | +931ms | 1 |
| 01:05:52.358 | 4.566s | Figure walks, exits, basket hangs. | 34 | 4.131s | +185ms | 2 |
| 01:13:57.808 | 4.684s | Man in bed with creature, then curtains. | 40 | 4.251s | +183ms | 1 |
| 01:25:17.804 | 4.375s | Bright light behind dark clouds. | 32 | 3.451s | +674ms | 1 |

Nine fit, none overflowed, 37.699s of description placed, tightest surviving
margin 183ms.

### The takes that did not survive

Twelve silences across both runs produced nineteen takes, of which seven were
rejected on measurement. The worst:

| silence | length | wrote | rendered | overran by |
|---|---|---|---|---|
| 00:45:30.638 | 4.173s | 111 chars | 11.251s | 7328ms |
| 00:12:18.517 | 2.184s | 45 chars | 6.131s | 4197ms |
| 00:11:02.693 | 2.147s | 21 chars | 2.891s | 994ms |
| 00:11:31.396 | 2.033s | 28 chars | 2.771s | 988ms |
| 00:11:02.693 | 2.147s | 27 chars | 2.371s | 474ms |
| 00:11:31.396 | 2.033s | 16 chars | 1.851s | 68ms |
| 01:05:52.358 | 4.566s | 32 chars | 4.331s | 15ms |

Each rejection is a real WAV on disk that a person can play. The 15ms one matters
as much as the 7328ms one: it is the case a person mixing by ear would let
through.

Each retry is sized from the overrun that was measured, not from a fixed step.
The line overran by some number of milliseconds at an observed speaking rate of
`chars / rendered_duration_s`, so it must lose at least that many characters, plus
five percent. The budget never rises between attempts.

### A short silence is a hard constraint, not a bug

Run the same film at a 2 second threshold and the silences hold 11 to 17
characters. "Man in hall" fits a real 2.184s silence with 3ms to spare. That is a
true result and a thin piece of audio description, and it is a fact about the film
rather than a defect in the tool. The two runs ship together for that reason.

## The character budget was wrong, and measuring is what caught it

The first version assumed 14 characters per second, which is roughly subtitle
reading speed. The rendered audio disagreed. Measured across 19 real takes from
Gemini 2.5 Flash TTS, voice Kore:

    min 5.70   median 9.27   mean 8.88   max 11.39   characters per second

14 sits outside the observed range entirely, which is why the first attempt on the
first three silences overran by 474ms, 988ms and 4197ms. The default is now 8.6.
After that change, 7 of 9 silences fit on the first take, against 0 of 3 before.

A wrong constant costs one Gemini call and one speech call per silence, on every
silence, and nothing but a second measurement makes it visible. Re-derive it from
whatever reports you have:

```
python -m ad.rate out/nighttide/report.json out/nighttide-long/report.json
```

The number in `ad/fit.py` carries its sample size and its distribution in the
docstring so nobody later reads 8.6 as a constant somebody liked.

## Built with IBM Bob

Bob Shell 2.0.2, authenticated through browser SSO. `bob run` headless requires a
`BOB_API_KEY`, so this project never used it. `bob acp` starts Bob as an
[Agent Client Protocol](https://agentclientprotocol.com) server over stdio and
authenticates from the cached SSO session instead, and `tools/bob_acp.py` is a
JSON-RPC client that drives it and writes a complete transcript for every session.

Seven Bob sessions, all transcripts in `bob/transcripts/`, all task prompts in
`bob/`. Bob's own SQLite ledger at `~/.bob/db/bob.db` recorded them independently:
22,158 output tokens across the six build sessions, 4.5244 total cost, which fits
comfortably inside the trial allowance.

**Bob authored:**

| file | what it does | session |
|---|---|---|
| `ad/gaps.py` | ffmpeg silencedetect, gap parsing, absolute timing | `64e03b75` |
| `ad/fit.py` | the fit verdict and the character budget | `64e03b75` |
| `ad/render.py` | Gemini TTS to a measurable WAV | `275a655d` |
| `ad/describe.py` | gap clip extraction and the Gemini description call | `275a655d` |
| `ad/conform.py` | the describe, render, re-measure, retry loop | `4b5f08db` |
| `ad/rate.py` | re-derives the speaking rate from report files | `36b5b2f4` |
| `ad/visual.py` | ffmpeg scdet scene activity per silence | `36b5b2f4` |
| `agent/graph.py` | the ADK Workflow, its nodes and the coverage planner | `36b5b2f4` |
| `tests/test_measure.py` | the parser and fit tests | `3498c1e3` |

**A human authored** `tools/bob_acp.py`, the ACP client that drives Bob,
`agent/pipeline.py`, `run_conform.py`, `web/server.py` and `web/index.html`.

Two of those sessions exist because Bob's work was sent back to Bob rather than
patched by hand:

- `measure_gaps` closed an open trailing gap at the whole file's duration even
  when only a short window had been analysed, which on an 87 minute film invents
  a gap tens of minutes long. Bob fixed it and added a chronological sort.
- Bob's own test `test_parse_leading_silence_derives_start_from_duration` asserted
  `start_s != 0.0` against a fixture whose correct answer is exactly 0.0, so the
  assertion could not distinguish working code from broken code. Bob replaced the
  fixture with one where the two possible answers differ, and split the true
  zero case into its own test.

### What this evidence is, precisely

Each transcript in `bob/transcripts/` is the raw ACP event stream. Every file Bob
wrote appears as a `tool_call` event whose `rawInput.content` holds the complete
byte-for-byte text, next to the session id. Bob's `tasks` table carries the same
session ids with their prompts, token counts and cost. Two independent records,
one of them IBM's own.

Bob also ships an `attribution_logs` table with per-file line ranges. It is empty
here, and this project does not claim those rows: that table is populated by the
Bob IDE, and the shell and ACP paths do not write to it.

## Running it

```
pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT=your-project        # Vertex AI, via gcloud ADC
gcloud auth application-default login

python run_conform.py <film.mp4> --min-gap-s 4.0 --report out/run/report.json
python agent/pipeline.py <film.mp4> --min-gap-s 4.0    # the full agent graph
python -m uvicorn web.server:app --port 8177           # the evidence page
pytest tests/ -q
```

ffmpeg and ffprobe must be on PATH. Built against ffmpeg 9.0.1, google-adk 2.8.0,
google-genai 2.20.0.

The speaking rate lives in exactly one place, the default on
`ad.fit.target_chars`. Nothing else declares it: `ad/conform.py` reads it off that
signature at call time, `agent/graph.py` resolves it through the same function, and
neither CLI has a default on the flag. `tests/test_single_rate.py` walks the AST of
every module under `ad/` and `agent/` and fails if the number appears as a literal
anywhere else, because it has already been wrong once and a duplicate is how the
wrong one survives a fix.

### What a green test run means here

`pytest` counts a skip as a non-failure, so a tally can read as success while the
thing it claims to cover was never touched. Every gate lives in
`tests/conftest.py`, each one two stage, and the run states what it exercised:

```
$ pytest tests/ -q                       # nothing configured
exercised: ffmpeg measurement
exercised: rendered takes on disk
========================== integrations NOT exercised ==========================
  gemini on vertex: none of GOOGLE_CLOUD_PROJECT, GOOGLE_API_KEY, GEMINI_API_KEY is set
48 passed, 5 skipped in 4.40s                                          exit 0

$ GOOGLE_CLOUD_PROJECT=<project> pytest tests/ -q
exercised: ffmpeg measurement
exercised: gemini on vertex
exercised: rendered takes on disk
53 passed in 154.82s                                                   exit 0
```

4.4 seconds to 154.8 seconds is the model path actually running: real descriptions
from real clips, real TTS, and the overrun gate deciding both ways on one measured
WAV. Naming a project and failing to reach it is a broken configuration rather than
an absent one, so it goes red:

```
$ GOOGLE_CLOUD_PROJECT=no-such-project GOOGLE_APPLICATION_CREDENTIALS=/tmp/nope.json pytest tests/ -q
48 passed, 5 errors in 5.70s                                           exit 1
Failed: this run was configured to exercise gemini on vertex and could not
```

`SIGHTREAD_REQUIRE_FFMPEG=1` and `SIGHTREAD_REQUIRE_TAKES=1` do the same for the
measurement chain and the shipped takes. The container image sets both and runs the
suite during its build, so an image that could only serve unmeasurable pages does
not get pushed.

### The hosted instance

The hosted copy serves the completed runs and does not carry the 354MB source
film, so it cannot measure anything live. It says so on the page, names the file
and its archive.org URL, and prints the command that reproduces the runs. The
evidence, including every rejected take as playable audio, is served either way,
because it is on disk in `out/`. `POST /api/conform` returns 503 with that same
command rather than accepting a job it cannot run.

Note that `silencedetect` and `scdet` both print at ffmpeg log level `info`. Pass
`-v error` and the output vanishes with a zero exit code, which reads as a film
containing no silence at all.

## What was verified, and how

Every check below was broken on purpose first, to confirm it was capable of
failing, then restored and confirmed green.

- **The fit verdict.** Mutating `verdict = "FIT" if margin_ms >= 0 else "OVERFLOW"`
  to always return `"FIT"` makes the real 6.131s take against its 2.184s silence
  report `FIT` with `margin_ms` still -4197. The mutant survives, so that line is
  load-bearing.
- **The measurement's log level.** Adding `-v error` to the silencedetect command in
  `ad/gaps.py` turns `test_measure_gaps_finds_a_silence_that_is_there` red on a file
  built with a 5 second digital silence in it: ffmpeg still exits zero, stderr is
  empty, and the parser reports no silence at all. Two tests catch it, the
  behavioural one and a source guard on the two functions that read a filter's log.
  `probe_duration` keeps `-v error` deliberately, because ffprobe writes the
  duration to stdout and the flag only quiets its banner.
- **The credential gate.** Turning `require_model` into a no-op turns
  `test_no_credentials_stops_the_workflow_from_being_built` and
  `test_no_credentials_stops_run_pipeline` red, 2 failed and 7 passed. Restored, 9
  passed.
- **The overrule in `dispatch`.** Removing all three override branches turns six
  tests red: a model verdict of `publish` on a run holding an OVERFLOW row, a
  `publish` over a re-measurement disagreement, and four unparseable dispositions
  that must route to a person. Restored, 17 passed. A seventh test asserts a clean
  run still reaches `publish`, so the override cannot pass by rejecting everything.
- **The hand-back floor in `escalate`.** Emptying the set of non-FIT rows the node
  adds turns `test_escalate_adds_every_overflow_the_adjudicator_left_out` red. The
  model can widen that set and never narrow it.
- **The cue contract.** Deleting `chars_per_second` from the dict the graph writes
  turns `test_cue_dicts_carry_every_field_conformed_cue_requires` red. That test
  reads the required fields off the dataclass, so a field added to one and not the
  other fails in milliseconds rather than in the last node of a paid-for run.
- **The single-source rate.** Writing `8.6` back into `ADState` as a literal turns
  two tests in `tests/test_single_rate.py` red, one on the AST walk and one that
  patches the declaration and asserts every reader follows.
- **The audio chain, against the live service.**
  `GET /audio/nighttide/gap0002.take1.wav` returns 200 and 294,330 bytes, and
  ffprobe measures the downloaded file at 6.130958s, which is the duration the
  report claims for that rejected take. The number on the page and the file you can
  hear are the same measurement.
- **The narrow viewport, at 375px:** `scrollWidth` 375 so no horizontal overflow,
  the tally collapses to two columns, the take rows collapse to the mobile grid, the
  node graph wraps, and all seven rejected takes render. This was measured in a real
  375px frame after the screenshot tool was found to be reporting a pass at 375
  while its own JSON said `width_applied: false`, meaning that check had never
  actually run.
- **The hosted degradation,** tested by moving the film aside: `/api/runs` keeps
  serving both completed runs, the page states that it serves completed runs only
  and prints the reproduce command, and `POST /api/conform` returns 503 naming the
  source rather than starting a job it cannot finish.

## What it refuses to guess

- **The silence threshold.** Gap detection runs at a dBFS threshold the operator
  passes, and the report records the value that produced every figure in it. There
  is no adaptive default, because -26dB suits a 1961 optical soundtrack and a modern
  mix wants a lower one, and a tool that picks silently would put a number nobody
  chose underneath every margin on the page.
- **Anything outside the silence.** A description is written from the video inside
  the gap and nothing else. The model is not shown the surrounding scene, so it
  never asserts a character's name or a continuity it cannot see. `ad/describe.py`
  instructs it to describe only what is on screen, in the present tense, and not to
  name a character unless the name is visible.
- **Whether a line that overran can be saved.** `shorten` cuts to a measured
  ceiling, `retake` renders the cut line, and ffprobe decides. When the attempt cap
  is reached with the line still long, the run does not quietly widen the gap or
  drop the cue: `escalate` hands that gap to a human describer with the numbers
  attached, and `dispatch` will not let a model publish past it.
- **A verdict it did not measure.** `verify` re-reads every rendered WAV from disk
  and recomputes the verdict with no knowledge of what was reported. A mismatch goes
  into `verified.disagreed`, which is a defect in this tool rather than in the film,
  and it overrides any model verdict.

## Scope

The output is a conform report plus per-silence WAVs at absolute timecodes, which is
what a mixer lays in. It is not a mixed described print: the accepted lines are
placed in time and are not summed into the master, because that is a mix decision
about ducking and level that belongs to the person doing the mix.

The coverage planner's selections are a ranking from measured gap length and
measured scene-change activity, with its reason recorded per gap. They are not
scored against a professional describer's choices, so the page shows the reasoning
next to the selection rather than presenting the selection as correct.

## License

MIT. See `LICENSE`.
