Continue on Sightread. One bug fix and two new modules. Production code only: no
mocks, no stubs, no placeholder data, no `TODO`, no decorative comments, no em
dashes.

## Fix 1: `ad/gaps.py`, trailing gap uses the wrong end time

In `measure_gaps`, when `duration_s` is passed, ffmpeg only analyses that window,
but the trailing-gap branch closes the open gap at `probe_duration(media_path)`,
which is the whole file's duration. For a 120 second window on a 90 minute film
that invents a gap tens of minutes long.

Fix it so the open trailing gap closes at the end of the window that was actually
analysed: `start_s + duration_s` when `duration_s` is not None, otherwise
`probe_duration(media_path)`. Pass what is needed down to `_parse_silencedetect`
rather than re-deriving it. Keep the existing behaviour when `duration_s` is None.

Also sort the returned gaps by `start_s` before assigning `index`, so `index`
always reflects chronological order even if ffmpeg's output order ever changes.

## File 2: `ad/render.py`

Renders one audio description line to a real audio file using Gemini
text-to-speech on Vertex AI. This API surface is already verified against the
installed google-genai 2.20.0 and against a live call, so use it exactly as
written below and do not substitute a different client, model or config shape.

```python
from google import genai
from google.genai import types

client = genai.Client(vertexai=True, project=project, location=location)
response = client.models.generate_content(
    model=model,
    contents=text,
    config=types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    ),
)
part = response.candidates[0].content.parts[0]
audio_bytes = part.inline_data.data
mime = part.inline_data.mime_type
```

The returned bytes are headerless signed 16 bit little endian PCM, mono. The mime
type carries the sample rate, for example `audio/L16;codec=pcm;rate=24000`. There
is no WAV header, so the bytes must be wrapped with the `wave` module before
anything can measure them.

Requirements:

- `class RenderError(RuntimeError)`.
- `@dataclass(frozen=True) class RenderedLine` with fields:
  `text: str`, `audio_path: str`, `sample_rate: int`, `model: str`, `voice: str`,
  `pcm_bytes: int`.
- `def parse_sample_rate(mime_type: str) -> int`
  - Extracts the `rate=` parameter from a mime type like
    `audio/L16;codec=pcm;rate=24000`. Raise `RenderError` when it is absent or not
    an integer. Never silently default to 24000, because a wrong sample rate makes
    every downstream duration measurement wrong in a way that still looks
    plausible.
- `def render_line(text: str, out_path: str, *, project: str, location: str = "us-central1", model: str = "gemini-2.5-flash-tts", voice: str = "Kore") -> RenderedLine`
  - Raise `RenderError` on empty or whitespace-only `text`.
  - Create the parent directory of `out_path` when it does not exist.
  - Raise `RenderError` when the response carries no inline audio data, including
    the case where `candidates` is empty or the first part has no `inline_data`.
  - Write a mono 16 bit WAV at the parsed sample rate using `wave`.
  - Return `RenderedLine` with `pcm_bytes` set to the length of the raw PCM.

## File 3: `ad/describe.py`

Writes one audio description line for one measured gap, using Gemini to look at
the actual video in that gap. The character budget is a hard constraint handed in
from `ad.fit.target_chars`, not a suggestion.

Requirements:

- `class DescribeError(RuntimeError)`.
- `@dataclass(frozen=True) class Description` with fields:
  `gap_index: int`, `text: str`, `char_budget: int`, `chars: int`, `model: str`,
  `clip_path: str`, `attempt: int`.
- `def extract_clip(media_path: str, start_s: float, duration_s: float, out_path: str, *, height: int = 360) -> str`
  - Cuts the gap's video out with ffmpeg so only the relevant seconds are sent to
    the model. Command shape: `ffmpeg -hide_banner -nostats -y -ss <start_s> -i
    <media_path> -t <duration_s> -an -vf scale=-2:<height> -c:v libx264 -preset
    veryfast -crf 30 <out_path>`. Video only, audio dropped with `-an`, because
    the model is being asked what is visible, not what is audible.
  - Raise `DescribeError` when ffmpeg is missing or exits non-zero, including the
    last stderr lines. Raise `DescribeError` when the output file is missing or
    zero bytes after a successful exit.
- `def describe_gap(media_path: str, gap_index: int, start_s: float, duration_s: float, char_budget: int, clip_path: str, *, project: str, location: str = "us-central1", model: str = "gemini-2.5-flash", attempt: int = 1, shrink_note: str = "") -> Description`
  - Calls `extract_clip`, then sends the clip bytes inline to Gemini together with
    the instruction. Inline video part shape, verified:
    `types.Part.from_bytes(data=clip_bytes, mime_type="video/mp4")`, passed in a
    `contents` list alongside the instruction string.
  - The instruction must tell the model it is writing audio description for a
    blind or partially sighted viewer: state only what is visible, present tense,
    no interpretation of motive, no naming of characters it cannot see named, no
    mention of music or sound, no phrases like "we see" or "the camera", and hard
    limit of `char_budget` characters. When `shrink_note` is non-empty, append it,
    so a retry can be told exactly how much shorter it must be.
  - Set `config=types.GenerateContentConfig(temperature=0.2)` for repeatability.
  - Strip surrounding whitespace and any wrapping quotes from the returned text.
    Collapse internal newlines to single spaces.
  - Raise `DescribeError` when the model returns no text.
  - Do NOT truncate the text to fit the budget. Return what the model wrote with
    the real `chars` count, so an over-budget line stays visible as over budget
    and the fit check downstream is the thing that decides.

## Rules

- Python 3.12+, full type annotations, `from __future__ import annotations`.
- Each module gets a one-paragraph docstring saying what it does and what it
  refuses to guess.
- Create no other files. Do not create tests. Do not run git. Do not run ffmpeg
  or any network call to try things out, just write the code.
- Reply with one line per file: the path and the public names it defines.
