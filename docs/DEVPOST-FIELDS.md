# Sightread — Devpost field payload (ready to paste)

Hackathon: Agentic Cinema (Devpost 30721)
Account: `kamalbuilds` (same profile currently holding Checkprint; Interlock also pending here — do not overwrite Interlock draft if one exists)
Track: **IBM**
Human-only step: **reCAPTCHA / Create project** on Devpost. Everything below is paste-ready after that unlocks.

## Create project (manual)

1. Open `https://devpost.com/submit-to/30721-agentic-cinema-the-blockbuster-hackathon/manage/submissions`
2. Solve captcha, Create project
3. Fill fields below, Save, Submit

## Fields

### Project name
Sightread

### Tagline / Elevator pitch (179 chars)
Audio description must fit the silence between two lines of dialogue. Sightread measures the silence, writes a line, speaks it, then re-measures the audio: 7 of 19 takes rejected.

### Submission type / Submitter
Team (match Checkprint) · Country: United States · Project: New

### Partner track
IBM

### Built with (tags / chips — paste as list)
Python, Google ADK, google-genai, Gemini 2.5 Flash, Gemini 2.5 Flash TTS, Vertex AI, Google Cloud Run, FastAPI, Uvicorn, Pydantic, ffmpeg, ffprobe, IBM Bob, Agent Client Protocol, pytest

### Try it out / Hosted project URL
https://sightread-387894104564.us-central1.run.app

### Demo video URL
**BLOCKER until uploaded:** local file ready at `video/sightread/sightread-demo.mp4` (57 MB, 171.8s, English narration + `sightread-demo.srt`).
Upload via Vimeo account `vimeo-upload` (same account as Checkprint/Redslip/Cuepass/Interlock demos), set public, then paste `https://vimeo.com/XXXXXXXX`.
Do not paste a YouTube channel URL.

### Repository URL
https://github.com/kamalbuilds/sightread

### About sidebar checks (already verified)
- Public: yes
- License: MIT (detected)
- Homepage: https://sightread-387894104564.us-central1.run.app (set 2026-09-10)

### Inspiration
Audio description is mandated by the FCC in the United States and by Ofcom in the UK, and the way it gets made is three people in sequence. A describer watches the film and writes lines. A narrator records them. A re-recording mixer lays them into the silences and finds out that a line runs 400 milliseconds past the point where dialogue comes back, so it goes back up the chain to be rewritten and re-recorded.

That round trip is the whole cost, and it exists for one reason: nobody upstream knows the true length of the silence, and nobody knows the true length of the recorded line, until the last step.

Both of those are measurable before anyone speaks. ffmpeg silencedetect will tell you where every silence in a master is and how long it runs. ffprobe will tell you exactly how long a rendered WAV is. The measurement that is currently made last can be made first, and again at the end, and a line that does not fit can be thrown out before a mixer ever hears it.

The first version of the fitting logic assumed 14 characters per second (subtitle reading speed). Measured across 19 real takes the observed rate was min 5.70, median 9.27, mean 8.88, max 11.39. 14 sits outside the observed range. That is why the first three silences overran by 474ms, 988ms and 4197ms. The default is now 8.6, re-derived from the reports.

### What it does
Sightread takes a film, measures every silence in its audio, decides which of those silences are worth describing, writes one line for each, speaks it, measures the speech, and rejects the takes that overrun. The output is a conform report plus one WAV per silence at absolute timecode, which is what a mixer lays in.

The fourth step is the product. Everything else is a description generator.

One run over Night Tide (1961), the full 87 minute public-domain master: nine silences of 4 seconds or longer, six selected, all six fit, all six verdicts reproduced by independent re-measurement from disk. The agent still escalated on a 114ms margin that requires human review (CLI exit 3).

Drop the threshold to 2 seconds and the shortening cycle earns its place: 76 silences, 25 selected, two shorten/retake cycles driven by ffprobe, 22 lines fit, 9 handed back because the measured overrun left a ceiling too small for a real description.

Live proof surfaces: rejected takes as playable audio, the ADK graph run at `/api/adk`, measured speaking rate at `/api/rate`, and Bob transcripts at `/api/bob`.

### How we built it (IBM Bob evidence — paste this block for judges)
Bob Shell 2.0.2, driven over the Agent Client Protocol from `tools/bob_acp.py` (browser SSO, not `bob run` API key). Seven sessions. Task prompts in `bob/*.md`. Complete ACP transcripts in `bob/transcripts/*.json`, each holding Bob's `sessionId`, tool calls, and the byte-for-byte content Bob wrote for every file. Bob's own SQLite ledger at `~/.bob/db/bob.db` recorded the same session ids independently (22,158 output tokens across the build sessions).

Files Bob authored include `ad/gaps.py`, `ad/fit.py`, `ad/render.py`, `ad/describe.py`, `ad/conform.py`, `ad/rate.py`, `ad/visual.py`, `agent/graph.py`, and the measurement tests. Two sessions exist because Bob's work went back to Bob rather than being patched by hand (trailing-silence bug; a test that could not distinguish working code from broken code).

Judges can open `https://sightread-387894104564.us-central1.run.app/api/bob` or browse `https://github.com/kamalbuilds/sightread/tree/master/bob`.

Runtime stack: Google ADK 2.8 Workflow with three Gemini 2.5 Flash LlmAgent nodes (coverage_planner, shorten, adjudicate), Gemini multimodal description from silence clips, Gemini 2.5 Flash TTS, Vertex AI, Cloud Run.

### Challenges we ran into
(Use the Challenges section from `docs/STORY.md` verbatim: ffmpeg `-v error` swallowing silencedetect; green pytest with zero Gemini calls; report node TypeError after paid model calls; decorative ADState defaults; two-character "Mn" FIT that was not a description; speaking rate literal duplicated in four places.)

### Accomplishments that we're proud of
(Use Accomplishments from `docs/STORY.md`: model loses to measurement; seven intentional mutations turned red then green; rejected takes ship as playable audio; wrong 14 char/s constant caught by second measurement.)

### What we learned
(Use What we learned from `docs/STORY.md`.)

### What's next for Sightread
Placing accepted lines into a mixed print with ducking. Scoring the coverage planner against a professional describer. Running the graph across a catalogue where per-silence renders fan out.

### Cover image
Prefer a still from the live page showing rejected takes or the ADK graph (files under `projects/sightread/docs/img/`: `graph-and-verdict.png`, `bob-provenance.png`, `gemini-nodes-live.png`).

### Additional links (optional)
- Graph run: https://sightread-387894104564.us-central1.run.app/api/adk
- Bob API: https://sightread-387894104564.us-central1.run.app/api/bob
- Rate API: https://sightread-387894104564.us-central1.run.app/api/rate
- Sample rejected take: https://sightread-387894104564.us-central1.run.app/audio/nighttide/gap0002.take1.wav
- Source film: https://archive.org/download/NightTide16x9CorrectedAudio/NightTide_512kb.mp4

## Human checklist (only these)

1. [ ] Solve Devpost captcha and Create project on `kamalbuilds`
2. [ ] Upload `sightread-demo.mp4` to Vimeo (`vimeo-upload`), set Public, paste URL
3. [ ] Confirm track = IBM (not Clickhouse)
4. [ ] Tick terms and Submit

Everything else above is verified and paste-ready.
