"""Entry point that runs the Sightread ADK graph once over one film.

The graph in `agent/graph.py` holds the topology. This module drives it through
ADK's InMemoryRunner and returns the final state plus the framework's own event
trace, so what the UI shows a reviewer is ADK's record of what ran rather than a
list assembled by hand alongside the code.

Gemini is required. There is no deterministic fallback for the coverage planner,
because a fallback that produced the same shape of plan would make the model
decorative: you could delete it and the product would behave identically. Which
silences are worth spending a listener's attention on is a judgement call, so it
is the model's. Every number stays with ffmpeg.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid
from dataclasses import dataclass, field
from typing import Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

APP_NAME = "sightread"
USER_ID = "describer"


class GeminiRequired(RuntimeError):
    """Raised when no Gemini configuration is present in this process."""


def model_available() -> bool:
    """True when Vertex AI or the Gemini API is configured in this process."""
    if os.getenv("GOOGLE_CLOUD_PROJECT"):
        return True
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def use_vertex() -> None:
    """Point ADK's model client at Vertex AI when a project is configured.

    `ad/describe.py` and `ad/render.py` construct their own client and pass
    `vertexai=True` explicitly, but ADK builds the LlmAgent's client from the
    environment. Without this the graph fails inside the coverage planner with
    "No API key was provided", which reads as a missing credential rather than as
    the wrong backend being selected, and only after several minutes of ffmpeg has
    already been spent upstream.
    """
    if os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GOOGLE_API_KEY"):
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")


def require_model() -> None:
    if not model_available():
        raise GeminiRequired(
            "Set GOOGLE_CLOUD_PROJECT for Vertex AI, or GOOGLE_API_KEY for the "
            "Gemini API. Sightread does not run without a model: the coverage "
            "planner is the only step that decides which silences are worth "
            "describing, and there is no deterministic stand-in for it."
        )


@dataclass
class DescribeRun:
    run_id: str
    media_path: str
    gaps: list[dict] = field(default_factory=list)
    survey: list[dict] = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    cues: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    verified: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)


async def _drive(state: dict, on_step: Callable[[list[dict]], None] | None):
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from agent import graph

    run_id = state["run_id"]
    if on_step is not None:
        graph._PROGRESS[run_id] = on_step

    runner = InMemoryRunner(node=graph.build_workflow(), app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, state=state
    )

    trace: list[dict] = []
    message = types.Content(
        role="user",
        parts=[types.Part(text=f"Conform audio description into {state['media_path']}.")],
    )
    try:
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session.id, new_message=message
        ):
            author = getattr(event, "author", "") or ""
            for call in event.get_function_calls() or []:
                trace.append({"agent": author, "tool": call.name, "args": dict(call.args or {})})
            content = getattr(event, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if text and text.strip() and author:
                    trace.append({"agent": author, "text": text.strip()[:1200]})
    finally:
        graph._PROGRESS.pop(run_id, None)

    final = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session.id
    )
    return dict(final.state), trace


def run_pipeline(
    media_path: str,
    *,
    out_dir: str = "out",
    noise_db: float = -26.0,
    min_gap_s: float = 4.0,
    headroom_ms: int = 250,
    chars_per_second: float = 8.6,
    max_attempts: int = 3,
    project: str | None = None,
    location: str | None = None,
    on_step: Callable[[list[dict]], None] | None = None,
) -> DescribeRun:
    """Measure, plan coverage, conform, re-verify and report over one film."""
    require_model()
    use_vertex()
    run_id = str(uuid.uuid4())
    state = {
        "media_path": media_path,
        "out_dir": out_dir,
        "noise_db": noise_db,
        "min_gap_s": min_gap_s,
        "headroom_ms": headroom_ms,
        "chars_per_second": chars_per_second,
        "max_attempts": max_attempts,
        "project": project or os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        "location": location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        "run_id": run_id,
    }
    final, trace = asyncio.run(_drive(state, on_step))
    return DescribeRun(
        run_id=run_id,
        media_path=media_path,
        gaps=final.get("gaps") or [],
        survey=final.get("survey") or [],
        plan=final.get("plan") or {},
        cues=final.get("cues") or [],
        skipped=final.get("skipped") or [],
        verified=final.get("verified") or {},
        report=final.get("report") or {},
        steps=final.get("steps") or [],
        trace=trace,
    )


def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Run the Sightread ADK graph over one film.")
    ap.add_argument("media")
    ap.add_argument("--out-dir", default="out/adk")
    ap.add_argument("--noise-db", type=float, default=-26.0)
    ap.add_argument("--min-gap-s", type=float, default=4.0)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    run = run_pipeline(
        args.media,
        out_dir=args.out_dir,
        noise_db=args.noise_db,
        min_gap_s=args.min_gap_s,
        max_attempts=args.max_attempts,
        on_step=lambda steps: print(
            f"[{steps[-1]['step']}] {'ok' if steps[-1]['ok'] else 'FAILED'}: "
            f"{steps[-1]['summary']}",
            flush=True,
        ),
    )

    print("")
    print(f"run {run.run_id}")
    print(f"planner selected {run.plan.get('selected')}")
    for gap_index, reason in (run.plan.get("reasons") or {}).items():
        print(f"  gap {gap_index}: {reason}")
    print("")
    for cue in run.cues:
        print(
            f"gap {cue['gap_index']:3d}  {cue['gap_duration_s']:6.3f}s gap  "
            f"{cue['chars']:3d} chars  rendered {cue['rendered_duration_s']:6.3f}s  "
            f"margin {cue['margin_ms']:+6d}ms  {cue['verdict']}  "
            f"{cue['attempts']} attempts"
        )
        print(f"        {cue['text']}")
    print("")
    print("verified:", json.dumps(run.verified))
    print("totals:", json.dumps((run.report.get("totals") or {})))

    if args.report:
        out = pathlib.Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "steps": run.steps,
                    "plan": run.plan,
                    "trace": run.trace,
                    "report": run.report,
                },
                indent=1,
            )
        )
        print(f"report written to {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
