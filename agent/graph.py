"""ADK workflow graph for the audio description pipeline.

Deterministic nodes: measure, survey, conform, verify, report.
Model node: coverage_planner (LlmAgent, gemini-2.5-flash).

No model can skip verify. Every accepted WAV is re-measured from disk by an
independent node that has no knowledge of what conform reported; any discrepancy
between conform's verdict and the re-measurement is recorded in verified.disagreed
rather than silently discarded.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.workflow import RetryConfig, Workflow, node, START
from pydantic import BaseModel, Field

import ad.conform as _conform_mod
import ad.fit as _fit_mod
import ad.gaps as _gaps_mod
import ad.visual as _visual_mod


_PROGRESS: dict[str, object] = {}


class ADState(BaseModel):
    media_path: str
    noise_db: float = -26.0
    min_gap_s: float = 4.0
    headroom_ms: int = 250
    chars_per_second: float = 8.6
    max_attempts: int = 3
    project: str = ""
    location: str = "us-central1"
    out_dir: str = "out"
    run_id: str = ""
    gaps: list[dict] = Field(default_factory=list)
    survey: list[dict] = Field(default_factory=list)
    gaps_json: str = "[]"
    plan: dict = Field(default_factory=dict)
    selected: list[int] = Field(default_factory=list)
    cues: list[dict] = Field(default_factory=list)
    skipped: list[dict] = Field(default_factory=list)
    verified: dict = Field(default_factory=dict)
    report: dict = Field(default_factory=dict)
    steps: list[dict] = Field(default_factory=list)


class CoveragePlan(BaseModel):
    selected: list[int]
    reasons: dict[str, str]
    note: str


def _step(ctx: Any, name: str, ok: bool, summary: str, data: dict) -> None:
    """Append one step record to ctx.state['steps'] without mutating in place."""
    entry = {"node": name, "ok": ok, "summary": summary, **data}
    new_steps = list(ctx.state.get("steps") or []) + [entry]
    ctx.state["steps"] = new_steps
    run_id: str = ctx.state.get("run_id") or ""
    if run_id:
        callback = _PROGRESS.get(run_id)
        if callback is not None:
            try:
                callback(new_steps)
            except Exception:
                pass


async def measure(ctx: Any, media_path: str, noise_db: float, min_gap_s: float) -> None:
    """Detect silence gaps across the entire film via ffmpeg silencedetect."""
    gaps = await asyncio.to_thread(
        _gaps_mod.measure_gaps,
        media_path,
        noise_db=noise_db,
        min_gap_s=min_gap_s,
    )
    ctx.state["gaps"] = [
        {
            "index": g.index,
            "start_s": g.start_s,
            "end_s": g.end_s,
            "duration_s": g.duration_s,
        }
        for g in gaps
    ]
    _step(ctx, "measure", True, f"found {len(gaps)} gaps", {"gap_count": len(gaps)})


async def survey(
    ctx: Any,
    media_path: str,
    headroom_ms: int,
    chars_per_second: float,
) -> None:
    """Run ffmpeg scdet per gap and record visual activity scores."""
    gaps: list[dict] = list(ctx.state.get("gaps") or [])
    results: list[dict] = []
    for gap in gaps:
        activity = await asyncio.to_thread(
            _visual_mod.visual_activity,
            media_path,
            gap["start_s"],
            gap["duration_s"],
        )
        results.append(activity)

    ctx.state["survey"] = list(results)

    gap_objects: list[dict] = []
    for gap, activity in zip(gaps, results):
        budget = _fit_mod.target_chars(
            gap["duration_s"],
            headroom_ms=headroom_ms,
            chars_per_second=chars_per_second,
        )
        gap_objects.append(
            {
                "index": gap["index"],
                "start_s": gap["start_s"],
                "end_s": gap["end_s"],
                "duration_s": gap["duration_s"],
                "char_budget": budget,
                "peak": activity["peak"],
                "mean": activity["mean"],
            }
        )

    ctx.state["gaps_json"] = json.dumps(gap_objects, separators=(",", ":"))
    _step(ctx, "survey", True, f"surveyed {len(gaps)} gaps", {"surveyed": len(gaps)})


_PLANNER_INSTRUCTION = (
    "You are an audio description coverage planner. "
    "Your task is to select which silence gaps in a film are worth describing "
    "for a blind or partially sighted viewer.\n\n"
    "You will receive a JSON array of gaps. Each object has:\n"
    "  index         integer gap identifier\n"
    "  start_s       gap start in seconds\n"
    "  end_s         gap end in seconds\n"
    "  duration_s    gap length in seconds\n"
    "  char_budget   maximum characters a description may use\n"
    "  peak          strongest frame-to-frame scene-change score in the gap\n"
    "  mean          mean scene-change score across all frames in the gap\n\n"
    "Select gaps where a described line earns its place. "
    "Prefer gaps with high peak visual activity and enough char_budget to say "
    "something useful. Skip gaps that are long but visually static, because "
    "narrating a still frame wastes the listener's attention. "
    "A char_budget below 20 rarely fits a meaningful sentence; avoid those. "
    "You must return only indices that appear in the input array. "
    "You cannot invent a gap index. "
    "Give a one-line reason for each selected index.\n\n"
    "Gaps:\n{gaps_json}"
)

_coverage_planner = LlmAgent(
    name="coverage_planner",
    model="gemini-2.5-flash",
    instruction=_PLANNER_INSTRUCTION,
    output_schema=CoveragePlan,
    output_key="plan",
)


async def conform(
    ctx: Any,
    media_path: str,
    out_dir: str,
    project: str,
    location: str,
    headroom_ms: int,
    chars_per_second: float,
    max_attempts: int,
) -> None:
    """Filter planner output, discard invented indices, run the conform loop."""
    plan: dict = dict(ctx.state.get("plan") or {})
    gaps: list[dict] = list(ctx.state.get("gaps") or [])

    valid_indices = {g["index"] for g in gaps}
    gaps_by_index = {g["index"]: g for g in gaps}

    raw_selected: list[int] = list(plan.get("selected") or [])
    reasons: dict[str, str] = dict(plan.get("reasons") or {})

    accepted: list[int] = []
    discarded_entries: list[dict] = []

    for idx in raw_selected:
        if idx in valid_indices:
            accepted.append(idx)
        else:
            discarded_entries.append(
                {"gap_index": idx, "reason": "index not present in measured gaps"}
            )

    gap_objects = [
        _gaps_mod.Gap(
            index=gaps_by_index[i]["index"],
            start_s=gaps_by_index[i]["start_s"],
            end_s=gaps_by_index[i]["end_s"],
            duration_s=gaps_by_index[i]["duration_s"],
            source_lines=(),
        )
        for i in accepted
    ]

    cue_objects, skipped_from_conform = await asyncio.to_thread(
        _conform_mod.conform,
        media_path,
        gap_objects,
        out_dir,
        project=project,
        location=location,
        headroom_ms=headroom_ms,
        chars_per_second=chars_per_second,
        max_attempts=max_attempts,
    )

    cues_as_dicts = [
        {
            "gap_index": c.gap_index,
            "gap_start_s": c.gap_start_s,
            "gap_end_s": c.gap_end_s,
            "gap_duration_s": c.gap_duration_s,
            "char_budget": c.char_budget,
            "text": c.text,
            "chars": c.chars,
            "audio_path": c.audio_path,
            "clip_path": c.clip_path,
            "rendered_duration_s": c.rendered_duration_s,
            "margin_ms": c.margin_ms,
            "verdict": c.verdict,
            "attempts": c.attempts,
            "attempt_log": list(c.attempt_log),
        }
        for c in cue_objects
    ]

    ctx.state["cues"] = cues_as_dicts
    ctx.state["selected"] = list(accepted)
    ctx.state["skipped"] = list(discarded_entries) + list(skipped_from_conform)

    _step(
        ctx,
        "conform",
        True,
        f"conformed {len(cues_as_dicts)} cues, skipped {len(ctx.state['skipped'])}",
        {
            "accepted": len(accepted),
            "discarded_by_planner": len(discarded_entries),
            "skipped_by_conform": len(skipped_from_conform),
        },
    )


async def verify(ctx: Any, headroom_ms: int) -> None:
    """Re-measure every accepted WAV from disk; compare against conform's report."""
    cues: list[dict] = list(ctx.state.get("cues") or [])

    checked = 0
    agreed = 0
    disagreed: list[dict] = []

    for cue in cues:
        audio_path: str = cue.get("audio_path", "")
        if not audio_path:
            continue
        gap_duration_s: float = cue.get("gap_duration_s", 0.0)
        gap_index: int = cue.get("gap_index", -1)

        fit = await asyncio.to_thread(
            _fit_mod.check_fit,
            gap_index,
            gap_duration_s,
            audio_path,
            headroom_ms=headroom_ms,
        )

        checked += 1
        reported_verdict: str = cue.get("verdict", "")
        reported_duration: float = cue.get("rendered_duration_s", -1.0)

        verdicts_match = fit.verdict == reported_verdict
        duration_close = abs(fit.rendered_duration_s - reported_duration) < 0.005

        if verdicts_match and duration_close:
            agreed += 1
        else:
            disagreed.append(
                {
                    "gap_index": gap_index,
                    "audio_path": audio_path,
                    "conform_verdict": reported_verdict,
                    "conform_duration_s": reported_duration,
                    "verify_verdict": fit.verdict,
                    "verify_duration_s": fit.rendered_duration_s,
                }
            )

    ctx.state["verified"] = {
        "checked": checked,
        "agreed": agreed,
        "disagreed": disagreed,
    }

    ok = len(disagreed) == 0
    _step(
        ctx,
        "verify",
        ok,
        f"checked {checked}, agreed {agreed}, disagreed {len(disagreed)}",
        {"checked": checked, "agreed": agreed, "disagreements": len(disagreed)},
    )


async def report(
    ctx: Any,
    media_path: str,
    noise_db: float,
    min_gap_s: float,
    headroom_ms: int,
) -> None:
    """Assemble the conform report and attach the verify block."""
    cues_as_dicts: list[dict] = list(ctx.state.get("cues") or [])
    skipped: list[dict] = list(ctx.state.get("skipped") or [])
    verified: dict = dict(ctx.state.get("verified") or {})

    conformed_cues = [
        _conform_mod.ConformedCue(
            gap_index=c["gap_index"],
            gap_start_s=c["gap_start_s"],
            gap_end_s=c["gap_end_s"],
            gap_duration_s=c["gap_duration_s"],
            char_budget=c["char_budget"],
            text=c["text"],
            chars=c["chars"],
            audio_path=c["audio_path"],
            clip_path=c["clip_path"],
            rendered_duration_s=c["rendered_duration_s"],
            margin_ms=c["margin_ms"],
            verdict=c["verdict"],
            attempts=c["attempts"],
            attempt_log=tuple(c["attempt_log"]),
        )
        for c in cues_as_dicts
    ]

    result = await asyncio.to_thread(
        _conform_mod.report,
        media_path,
        conformed_cues,
        noise_db=noise_db,
        min_gap_s=min_gap_s,
        headroom_ms=headroom_ms,
        skipped=skipped,
    )
    result["verified"] = verified

    ctx.state["report"] = result
    _step(ctx, "report", True, "report assembled", {"fit": result["totals"]["fit"]})


_measure_node = node(measure, name="measure")
_survey_node = node(survey, name="survey")
_coverage_planner_node = node(
    _coverage_planner,
    retry_config=RetryConfig(max_attempts=2),
    timeout=120,
)
_conform_node = node(conform, name="conform")
_verify_node = node(verify, name="verify")
_report_node = node(report, name="report")


def build_workflow() -> Workflow:
    """Return the assembled ADK workflow graph for the AD pipeline."""
    return Workflow(
        name="audio_description",
        state_schema=ADState,
        edges=[
            (START, _measure_node),
            (_measure_node, _survey_node),
            (_survey_node, _coverage_planner_node),
            (_coverage_planner_node, _conform_node),
            (_conform_node, _verify_node),
            (_verify_node, _report_node),
        ],
    )
