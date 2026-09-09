"""The single place that decides whether this process may run at all.

Sightread has three Gemini call sites and no offline equivalent for any of them:
the coverage planner chooses which silences deserve a listener's attention, the
line shortener decides which clause to cut when a take overran, and the
adjudicator decides whether a run is deliverable to a mixer. A deterministic
stand-in for any of those would make the model decorative, so there isn't one.

The consequence is that a missing credential has to stop the run before it starts
rather than degrade it. Both entry points go through `require_model`, and
`build_workflow` calls it again before the first `LlmAgent` is constructed, so no
route into the graph can reach a node that would have needed a model.
"""

from __future__ import annotations

import os

#: The one model id for every LlmAgent node. Overridable so a run can be pinned
#: to a specific snapshot, but never blank: an empty value stops the run.
DEFAULT_MODEL = "gemini-2.5-flash"

CREDENTIAL_VARS = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_API_KEY", "GEMINI_API_KEY")


class GeminiRequired(RuntimeError):
    """Raised when no Gemini configuration is present in this process."""


def model_available() -> bool:
    """True when Vertex AI or the Gemini API is configured in this process."""
    return any(os.getenv(name) for name in CREDENTIAL_VARS)


def require_model() -> None:
    """Stop the run when nothing in this process can reach Gemini."""
    if not model_available():
        raise GeminiRequired(
            "No Gemini configuration in this process. Set GOOGLE_CLOUD_PROJECT "
            "for Vertex AI, or GOOGLE_API_KEY for the Gemini API. Sightread does "
            "not run without a model: the coverage planner decides which silences "
            "are worth describing, the line shortener decides what to cut when a "
            "take overran, and the adjudicator decides whether the run is "
            "deliverable. None of the three has a deterministic stand-in, so "
            "there is nothing to fall back to."
        )


def resolve_model() -> str:
    """The model id every LlmAgent node uses, or raise rather than guess."""
    model = os.getenv("SIGHTREAD_MODEL", DEFAULT_MODEL).strip()
    if not model:
        raise GeminiRequired(
            "SIGHTREAD_MODEL is set to an empty value. Unset it to use "
            f"{DEFAULT_MODEL}, or name a model. Sightread will not pick one for "
            "you: the model that wrote a line is part of the evidence."
        )
    return model


def use_vertex() -> None:
    """Point ADK's model client at Vertex AI when a project is configured.

    `ad/describe.py` and `ad/render.py` construct their own client and pass
    `vertexai=True` explicitly, but ADK builds an LlmAgent's client from the
    environment. Without this the graph fails inside the first LlmAgent with
    "No API key was provided", which reads as a missing credential rather than as
    the wrong backend being selected, and only after several minutes of ffmpeg has
    already been spent upstream.
    """
    if os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GOOGLE_API_KEY"):
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
