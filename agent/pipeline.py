"""Entry point that runs the Sightread ADK graph once over one film.

The graph in `agent/graph.py` holds the topology. This module drives it through
ADK's InMemoryRunner and returns the final state plus the framework's own event
trace, so what the UI shows a reviewer is ADK's record of what ran rather than a
list assembled by hand alongside the code.

Gemini is required, and the check is in `agent/credentials.py` rather than here so
that both this entry point and `build_workflow` go through the same one. There is
no deterministic fallback for any of the three model nodes: a fallback that
produced the same shape of output would make the model decorative, because you
could delete it and the product would behave identically. Which silences are
worth a listener's attention, what to cut from a line that overran, and whether
the pass is deliverable are all judgement calls. Every number stays with ffmpeg.
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

from agent.credentials import (  # noqa: E402
    GeminiRequired,
    model_available,
    require_model,
    resolve_model,
    use_vertex,
)

APP_NAME = "sightread"
USER_ID = "describer"

__all__ = [
    "APP_NAME",
    "USER_ID",
    "DescribeRun",
    "GeminiRequired",
    "model_available",
    "require_model",
    "resolve_model",
    "run_pipeline",
    "use_vertex",
]


@dataclass
class DescribeRun:
    run_id: str
    media_path: str
    model: str = ""
    gaps: list[dict] = field(default_factory=list)
    survey: list[dict] = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    cues: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    shorten: dict = field(default_factory=dict)
    rounds: int = 0
    verified: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    escalation: dict = field(default_factory=dict)
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
    """Measure, plan coverage, draft, shorten, re-measure, adjudicate and report."""
    require_model()
    use_vertex()
    model = resolve_model()
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
        model=model,
        gaps=final.get("gaps") or [],
        survey=final.get("survey") or [],
        plan=final.get("plan") or {},
        cues=final.get("cues") or [],
        skipped=final.get("skipped") or [],
        shorten=final.get("shorten") or {},
        rounds=int(final.get("rounds") or 0),
        verified=final.get("verified") or {},
        verdict=final.get("verdict") or {},
        escalation=final.get("escalation") or {},
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
            f"[{steps[-1]['node']}] {'ok' if steps[-1]['ok'] else 'FAILED'}: "
            f"{steps[-1]['summary']}",
            flush=True,
        ),
    )

    print("")
    print(f"run {run.run_id} on {run.model}")
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
        for entry in cue["attempt_log"]:
            if entry.get("dropped"):
                print(f"        take {entry['attempt']} dropped: {entry['dropped']}")
    print("")
    print(f"shortening rounds: {run.rounds}")
    print("verified:", json.dumps({k: v for k, v in run.verified.items() if k != "table"}))
    print("verdict:", json.dumps(run.verdict))
    if run.escalation:
        print("escalation:", json.dumps(run.escalation.get("hand_back")))
        print("residual work:", run.escalation.get("residual_work"))
    print("totals:", json.dumps((run.report.get("totals") or {})))

    if args.report:
        out = pathlib.Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "model": run.model,
                    "steps": run.steps,
                    "plan": run.plan,
                    "rounds": run.rounds,
                    "verdict": run.verdict,
                    "escalation": run.escalation,
                    "trace": run.trace,
                    "report": run.report,
                },
                indent=1,
            )
        )
        print(f"report written to {out}")

    return 0 if run.verdict.get("disposition") == "publish" else 3


if __name__ == "__main__":
    raise SystemExit(main())
