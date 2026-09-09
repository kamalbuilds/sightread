"""Renders one audio description line to a WAV file using Gemini text-to-speech
on Vertex AI. The only TTS API shape this module supports is the google-genai
2.20.0 inline-audio path; it does not fall back to any other client, model, or
audio encoding, and it does not guess a sample rate when the API omits one.
"""

from __future__ import annotations

import os
import re
import wave
from dataclasses import dataclass

from google import genai
from google.genai import types


class RenderError(RuntimeError):
    """Raised when TTS fails or the response carries no usable audio."""


@dataclass(frozen=True)
class RenderedLine:
    text: str
    audio_path: str
    sample_rate: int
    model: str
    voice: str
    pcm_bytes: int


def parse_sample_rate(mime_type: str) -> int:
    """Extract the rate= parameter from a mime type like audio/L16;codec=pcm;rate=24000."""
    match = re.search(r"rate=(\d+)", mime_type)
    if not match:
        raise RenderError(
            f"No rate= parameter in mime type {mime_type!r}; cannot determine sample rate."
        )
    try:
        return int(match.group(1))
    except ValueError as exc:
        raise RenderError(
            f"rate= parameter in {mime_type!r} is not an integer."
        ) from exc


def render_line(
    text: str,
    out_path: str,
    *,
    project: str,
    location: str = "us-central1",
    model: str = "gemini-2.5-flash-tts",
    voice: str = "Kore",
) -> RenderedLine:
    """Synthesise *text* to a mono 16-bit WAV at *out_path* via Gemini TTS."""
    if not text or not text.strip():
        raise RenderError("text must be non-empty and not whitespace-only.")

    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

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

    if not response.candidates:
        raise RenderError("Gemini TTS returned no candidates.")

    parts = response.candidates[0].content.parts
    if not parts or parts[0].inline_data is None:
        raise RenderError("Gemini TTS response contains no inline audio data.")

    part = parts[0]
    audio_bytes: bytes = part.inline_data.data
    mime: str = part.inline_data.mime_type

    sample_rate = parse_sample_rate(mime)

    with wave.open(out_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio_bytes)

    return RenderedLine(
        text=text,
        audio_path=out_path,
        sample_rate=sample_rate,
        model=model,
        voice=voice,
        pcm_bytes=len(audio_bytes),
    )
