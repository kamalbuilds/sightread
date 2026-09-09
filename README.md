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

## What actually runs

    measure            ffmpeg silencedetect over the film            deterministic
    survey             ffmpeg scdet, visual activity per silence     deterministic
    coverage_planner   which silences are worth describing                  MODEL
    conform            describe, render, re-measure, retry shorter   deterministic
    verify             re-measure every accepted WAV from disk       deterministic
    report             assemble the conform report                   deterministic

That is a `google.adk.workflow.Workflow` in `agent/graph.py`, driven by
`agent/pipeline.py`. The model never produces a number that reaches a verdict. It
decides which silences deserve a listener's attention, and ffmpeg decides whether
anything fit.

`verify` re-measures every accepted WAV independently and recomputes its verdict,
because a conform step that reported a fit it never measured would otherwise be
invisible. There is no route around it.

Gemini is required. There is no deterministic fallback for the coverage planner,
because a fallback that produced the same shape of plan would make the model
decorative: you could delete it and the product would behave identically.

## Results on a real film

Night Tide (1961), public domain, from archive.org. The full 87 minute film,
5256.567s by ffprobe. Every figure below came from a measurement, and the report
JSON and every rendered WAV, accepted and rejected, are in `out/`.

Nine silences of 4s or longer, at a -26dB threshold:

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
python agent/pipeline.py <film.mp4> --min-gap-s 4.0    # the full ADK graph
python -m uvicorn web.server:app --port 8177           # the evidence page
pytest tests/ -q
```

ffmpeg and ffprobe must be on PATH. Built against ffmpeg 9.0.1, google-adk 2.8.0,
google-genai 2.20.0.

The speaking rate lives in exactly one place, the default on
`ad.fit.target_chars`. Nothing else declares it: `ad/conform.py` reads it off that
signature at call time and `run_conform.py` has no default on the flag at all.
Two tests fail if a second copy appears, because this number has already been
wrong once and a duplicate is how the wrong one survives a fix.

### The hosted instance

The hosted copy serves the completed runs and does not carry the 290MB source
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
failing, then restored.

- The fit verdict. Mutating `verdict = "FIT" if margin_ms >= 0 else "OVERFLOW"` to
  always return `"FIT"` makes the real 6.131s take against its 2.184s silence
  report `FIT` with `margin_ms` still -4197. The mutant survives, so that line is
  load-bearing.
- The trailing-gap fix. Disabling the `window_end` branch in `ad/gaps.py` turns
  `test_parse_trailing_silence_closes_at_window_end` red and leaves the other
  eleven green, so that test guards exactly the bug it was written for.
- The audio chain. `GET /audio/nighttide/gap0002.take1.wav` returns 294,330 bytes
  and ffprobe measures the downloaded file at 6.130958s, which is the duration the
  report claims for that rejected take. The number on the page and the file you
  can hear are the same measurement.
- The narrow viewport, at 375px: `scrollWidth` 375 so no horizontal overflow, the
  tally collapses to two columns, the take rows collapse to the mobile grid, the
  node graph wraps, and all seven rejected takes render. This was measured in a
  real 375px frame after the screenshot tool was found to be reporting a pass at
  375 while its own JSON said `width_applied: false`, meaning that check had never
  actually run.
- The hosted degradation, tested by moving the film aside: `/api/runs` keeps
  serving both completed runs, the page states that it serves completed runs only
  and prints the reproduce command, and `POST /api/conform` returns 503 naming the
  source rather than starting a job it cannot finish.

## Honest limits

- Gap detection is a fixed dBFS threshold. -26dB suits a 1961 optical soundtrack;
  a modern mix wants a lower one. There is no adaptive threshold yet, so the
  operator picks it and the report records what was used.
- Descriptions are written from the video inside the silence alone. The model does
  not see the surrounding scene, so it cannot know a character's name or carry
  continuity between lines.
- The coverage planner's selections are not yet evaluated against a describer's
  choices. It is a reasonable ranking, not a validated one.
- The accepted lines are placed in time but not mixed into the master. The output
  is a conform report plus per-silence WAVs, which is what a mixer needs, not a
  finished described print.
- No Confluent. The per-silence renders are genuinely independent and would fan
  out across a catalogue, but on one film with twelve silences a broker would be
  decoration rather than architecture, and a decorative integration is worse than
  an absent one.

## License

MIT. See `LICENSE`.
