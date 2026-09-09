"""The Sightread agent graph: a google.adk.workflow.Workflow over one film.

Seven deterministic nodes and three LlmAgent nodes, with three routed branches.
One of those branches closes a cycle, so the number of model turns in a run is
decided at runtime by ffprobe rather than written into the topology.

    START -> measure -> survey -> coverage_planner -> draft
    draft          -> shorten     when a take overran
                   -> verify      when every take fit
    shorten        -> retake
    retake         -> shorten     when a take still overran and rounds remain
                   -> verify      otherwise
    verify         -> adjudicate -> dispatch
    dispatch       -> escalate    when the run needs a human describer
                   -> report      when it is deliverable as measured
    escalate       -> report

What each model node decides, and what it cannot:

    coverage_planner  which measured silences deserve a listener's attention.
                      It cannot invent a gap index; `draft` drops any index that
                      was not in the measurement and records the discard.
    shorten           which words to cut from a line whose real spoken length is
                      now known. It is given a character ceiling computed from
                      the overrun that ffprobe measured, and `retake` renders and
                      re-measures whatever it returns. Coming in under the
                      ceiling does not make a line accepted, and coming in over
                      it does not make one rejected. The WAV decides.
    adjudicate        whether the run as measured is deliverable to a mixer, and
                      which gaps a human describer must take over. It reads the
                      verify block and cannot alter a verdict in it.

`verify` re-measures every rendered WAV from disk with no knowledge of what the
conforming path reported, and records any disagreement in `verified.disagreed`
rather than discarding it. There is no route around it: every path from `draft`
reaches `verify`, and `report` is only reachable through it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from google.adk.events.event import Event
from google.adk.workflow import RetryConfig, Workflow, node, START
from pydantic import BaseModel, Field

import ad.conform as _conform_mod
import ad.fit as _fit_mod
import ad.gaps as _gaps_mod
import ad.visual as _visual_mod
from agent.credentials import require_model, resolve_model


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
    overruns_json: str = "[]"
    shorten: dict = Field(default_factory=dict)
    rounds: int = 0
    verified: dict = Field(default_factory=dict)
    verified_json: str = "{}"
    verdict: dict = Field(default_factory=dict)
    escalation: dict = Field(default_factory=dict)
    report: dict = Field(default_factory=dict)
    steps: list[dict] = Field(default_factory=list)


class CoveragePlan(BaseModel):
    selected: list[int]
    reasons: dict[str, str]
    note: str


class ShortLine(BaseModel):
    gap_index: int
    line: str
    dropped: str


class ShortenedLines(BaseModel):
    lines: list[ShortLine]


class RunVerdict(BaseModel):
    disposition: str
    reason: str
    hand_back: list[int]
    residual_work: str


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


# --- deterministic nodes --------------------------------------------------


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
                "start_s": round(gap["start_s"], 3),
                "end_s": round(gap["end_s"], 3),
                "duration_s": round(gap["duration_s"], 3),
                "char_budget": budget,
                "peak": activity["peak"],
                "mean": activity["mean"],
            }
        )

    ctx.state["gaps_json"] = json.dumps(gap_objects, separators=(",", ":"))
    _step(ctx, "survey", True, f"surveyed {len(gaps)} gaps", {"surveyed": len(gaps)})


def _gap_object(entry: dict) -> _gaps_mod.Gap:
    return _gaps_mod.Gap(
        index=entry["index"],
        start_s=entry["start_s"],
        end_s=entry["end_s"],
        duration_s=entry["duration_s"],
        source_lines=(),
    )


def _cue_from_take(take: _conform_mod.Take, gap: dict, cps: float) -> dict:
    return {
        "gap_index": take.gap_index,
        "gap_start_s": gap["start_s"],
        "gap_end_s": gap["end_s"],
        "gap_duration_s": gap["duration_s"],
        "char_budget": take.char_budget,
        "chars_per_second": cps,
        "text": take.text,
        "chars": take.chars,
        "audio_path": take.audio_path,
        "clip_path": take.clip_path,
        "rendered_duration_s": take.rendered_duration_s,
        "margin_ms": take.margin_ms,
        "verdict": take.verdict,
        "attempts": take.attempt,
        "attempt_log": [
            {
                "attempt": take.attempt,
                "char_budget": take.char_budget,
                "chars": take.chars,
                "rendered_duration_s": take.rendered_duration_s,
                "margin_ms": take.margin_ms,
                "verdict": take.verdict,
            }
        ],
    }


def _overruns_payload(cues: list[dict], max_attempts: int) -> tuple[list[dict], str]:
    """The cues that still overran, with the ceiling each rewrite has to hit.

    The ceiling is `ad.conform.shrink_budget`, sized from the measured overrun.
    Cues that have already used every attempt are not offered again: a fourth
    rewrite of a line the loop is not going to render is a wasted model call.
    """
    live: list[dict] = []
    for cue in cues:
        if cue["verdict"] != "OVERFLOW" or cue["attempts"] >= max_attempts:
            continue
        ceiling = _conform_mod.shrink_budget(
            cue["chars"],
            cue["rendered_duration_s"],
            cue["margin_ms"],
            cue["char_budget"],
        )
        live.append(
            {
                "gap_index": cue["gap_index"],
                "line": cue["text"],
                "chars": cue["chars"],
                "spoken_s": round(cue["rendered_duration_s"], 3),
                "gap_s": round(cue["gap_duration_s"], 3),
                "overran_by_ms": -cue["margin_ms"],
                "max_chars": ceiling,
            }
        )
    return live, json.dumps(live, separators=(",", ":"))


async def draft(
    ctx: Any,
    media_path: str,
    out_dir: str,
    project: str,
    location: str,
    headroom_ms: int,
    chars_per_second: float,
    max_attempts: int,
):
    """First take per selected gap: describe the picture, speak it, measure it.

    Planner indices that were not in the measurement are discarded here and
    recorded, so a model that invented a gap cannot cause one to be described.
    """
    plan: dict = dict(ctx.state.get("plan") or {})
    gaps: list[dict] = list(ctx.state.get("gaps") or [])
    gaps_by_index = {g["index"]: g for g in gaps}

    accepted: list[int] = []
    skipped: list[dict] = []
    for idx in list(plan.get("selected") or []):
        if idx in gaps_by_index:
            accepted.append(idx)
        else:
            skipped.append(
                {"gap_index": idx, "reason": "index not present in measured gaps"}
            )

    cues: list[dict] = []
    for idx in accepted:
        entry = gaps_by_index[idx]
        try:
            take = await asyncio.to_thread(
                _conform_mod.draft_take,
                media_path,
                _gap_object(entry),
                out_dir,
                project=project,
                location=location,
                headroom_ms=headroom_ms,
                chars_per_second=chars_per_second,
            )
        except _conform_mod.ConformError as exc:
            skipped.append({"gap_index": idx, "reason": str(exc)})
            continue
        cues.append(_cue_from_take(take, entry, chars_per_second))

    ctx.state["cues"] = cues
    ctx.state["selected"] = accepted
    ctx.state["skipped"] = skipped
    ctx.state["rounds"] = 0

    live, payload = _overruns_payload(cues, max_attempts)
    ctx.state["overruns_json"] = payload
    route = "shorten" if live else "settled"

    fit = sum(1 for c in cues if c["verdict"] == "FIT")
    _step(
        ctx,
        "draft",
        True,
        f"{fit} of {len(cues)} first takes fit, {len(live)} to shorten",
        {
            "drafted": len(cues),
            "fit_first_take": fit,
            "to_shorten": len(live),
            "discarded_by_planner": len([s for s in skipped if "not present" in s["reason"]]),
            "route": route,
        },
    )
    yield Event(output={"route": route, "to_shorten": len(live)}, route=route)


async def retake(
    ctx: Any,
    out_dir: str,
    project: str,
    location: str,
    headroom_ms: int,
    max_attempts: int,
):
    """Speak each shortened line, measure it, and decide whether to go round again.

    The route out of here is the attempt cap and the ffprobe reading, in that
    order. The shortener cannot extend the loop and cannot end it.
    """
    cues: list[dict] = list(ctx.state.get("cues") or [])
    shorten: dict = dict(ctx.state.get("shorten") or {})
    cues_by_index = {c["gap_index"]: c for c in cues}
    rounds = int(ctx.state.get("rounds") or 0) + 1

    rendered = 0
    improved = 0
    ignored: list[dict] = []
    for item in list(shorten.get("lines") or []):
        idx = item.get("gap_index")
        line = (item.get("line") or "").strip()
        cue = cues_by_index.get(idx)
        if cue is None or not line:
            ignored.append({"gap_index": idx, "reason": "no overrun cue for this index"})
            continue
        if cue["verdict"] == "FIT" or cue["attempts"] >= max_attempts:
            ignored.append({"gap_index": idx, "reason": "cue already settled"})
            continue

        ceiling = _conform_mod.shrink_budget(
            cue["chars"],
            cue["rendered_duration_s"],
            cue["margin_ms"],
            cue["char_budget"],
        )
        attempt = cue["attempts"] + 1
        gap = {
            "index": idx,
            "start_s": cue["gap_start_s"],
            "end_s": cue["gap_end_s"],
            "duration_s": cue["gap_duration_s"],
        }
        take = await asyncio.to_thread(
            _conform_mod.speak_take,
            _gap_object(gap),
            line,
            out_dir,
            attempt,
            char_budget=ceiling,
            clip_path=cue["clip_path"],
            project=project,
            location=location,
            headroom_ms=headroom_ms,
        )
        rendered += 1
        log = list(cue["attempt_log"]) + [
            {
                "attempt": attempt,
                "char_budget": ceiling,
                "chars": take.chars,
                "rendered_duration_s": take.rendered_duration_s,
                "margin_ms": take.margin_ms,
                "verdict": take.verdict,
                "dropped": item.get("dropped") or "",
            }
        ]
        if take.verdict == "FIT":
            improved += 1
        cue.update(
            {
                "char_budget": ceiling,
                "text": take.text,
                "chars": take.chars,
                "audio_path": take.audio_path,
                "rendered_duration_s": take.rendered_duration_s,
                "margin_ms": take.margin_ms,
                "verdict": take.verdict,
                "attempts": attempt,
                "attempt_log": log,
            }
        )

    ctx.state["cues"] = list(cues)
    ctx.state["rounds"] = rounds
    if ignored:
        ctx.state["skipped"] = list(ctx.state.get("skipped") or []) + ignored

    live, payload = _overruns_payload(cues, max_attempts)
    ctx.state["overruns_json"] = payload
    route = "shorten" if live else "settled"

    _step(
        ctx,
        "retake",
        True,
        f"round {rounds}: rendered {rendered}, {improved} now fit, {len(live)} still over",
        {
            "round": rounds,
            "rendered": rendered,
            "now_fit": improved,
            "still_over": len(live),
            "ignored": len(ignored),
            "route": route,
        },
    )
    yield Event(output={"route": route, "round": rounds}, route=route)


async def verify(ctx: Any, headroom_ms: int) -> None:
    """Re-measure every rendered WAV from disk and recompute its verdict.

    This node is told nothing about what the conforming path reported until after
    it has its own number. A disagreement is recorded, never dropped.
    """
    cues: list[dict] = list(ctx.state.get("cues") or [])

    checked = 0
    agreed = 0
    disagreed: list[dict] = []
    table: list[dict] = []

    for cue in cues:
        audio_path: str = cue.get("audio_path", "")
        if not audio_path:
            continue
        fit = await asyncio.to_thread(
            _fit_mod.check_fit,
            cue.get("gap_index", -1),
            cue.get("gap_duration_s", 0.0),
            audio_path,
            headroom_ms=headroom_ms,
        )
        checked += 1
        reported_verdict: str = cue.get("verdict", "")
        reported_duration: float = cue.get("rendered_duration_s", -1.0)
        if (
            fit.verdict == reported_verdict
            and abs(fit.rendered_duration_s - reported_duration) < 0.005
        ):
            agreed += 1
        else:
            disagreed.append(
                {
                    "gap_index": cue.get("gap_index", -1),
                    "audio_path": audio_path,
                    "conform_verdict": reported_verdict,
                    "conform_duration_s": reported_duration,
                    "verify_verdict": fit.verdict,
                    "verify_duration_s": fit.rendered_duration_s,
                }
            )
        table.append(
            {
                "gap_index": cue.get("gap_index", -1),
                "gap_s": round(cue.get("gap_duration_s", 0.0), 3),
                "line": cue.get("text", ""),
                "chars": cue.get("chars", 0),
                "spoken_s": round(fit.rendered_duration_s, 3),
                "margin_ms": fit.margin_ms,
                "verdict": fit.verdict,
                "attempts": cue.get("attempts", 0),
            }
        )

    verified = {
        "checked": checked,
        "agreed": agreed,
        "disagreed": disagreed,
        "table": table,
        "rounds": int(ctx.state.get("rounds") or 0),
    }
    ctx.state["verified"] = verified
    ctx.state["verified_json"] = json.dumps(verified, separators=(",", ":"))
    _step(
        ctx,
        "verify",
        len(disagreed) == 0,
        f"checked {checked}, agreed {agreed}, disagreed {len(disagreed)}",
        {"checked": checked, "agreed": agreed, "disagreements": len(disagreed)},
    )


DISPOSITIONS = ("publish", "escalate")


async def dispatch(ctx: Any):
    """Route on the adjudicator's disposition, and refuse anything else.

    The branch is taken on one structured field so the shape of a run is
    reproducible while the judgement inside it is not. A disposition this node
    does not recognise routes to `escalate`, because the safe reading of an
    unparseable verdict is that a person should look at the run, and it is
    recorded so the malformed output is visible rather than absorbed.

    A run whose verify block holds a disagreement escalates whatever the
    adjudicator said. A disagreement means this tool reported a duration it could
    not reproduce from the file, and no model gets to wave that through.
    """
    verdict: dict = dict(ctx.state.get("verdict") or {})
    verified: dict = dict(ctx.state.get("verified") or {})
    asked = str(verdict.get("disposition") or "").strip().lower()

    overran = [
        row["gap_index"]
        for row in verified.get("table") or []
        if row.get("verdict") != "FIT"
    ]
    disagreed = list(verified.get("disagreed") or [])

    if asked not in DISPOSITIONS:
        route, override = "escalate", f"unrecognised disposition {asked!r}"
    elif disagreed:
        route, override = "escalate", (
            f"{len(disagreed)} re-measurement disagreements override the verdict"
        )
    elif asked == "publish" and overran:
        route, override = "escalate", (
            f"gaps {overran} are OVERFLOW on re-measurement and cannot be published"
        )
    else:
        route, override = asked, ""

    if override:
        ctx.state["verdict"] = {**verdict, "disposition": route, "override": override}

    _step(
        ctx,
        "dispatch",
        not override,
        f"{asked or 'no disposition'} -> {route}" + (f" ({override})" if override else ""),
        {"asked": asked, "route": route, "override": override},
    )
    yield Event(output={"route": route, "override": override}, route=route)


async def escalate(ctx: Any) -> None:
    """Record the hand-back the adjudicator asked for, restricted to real gaps.

    An index the adjudicator named that is not in the verify table is dropped
    here and the drop is recorded, for the same reason invented gap indices are
    dropped in `draft`.
    """
    verdict: dict = dict(ctx.state.get("verdict") or {})
    verified: dict = dict(ctx.state.get("verified") or {})
    known = {row["gap_index"] for row in verified.get("table") or []}

    asked = [int(i) for i in (verdict.get("hand_back") or [])]
    dropped = [i for i in asked if i not in known]

    # Every row that does not fit is handed back whether the adjudicator listed it
    # or not. The model can widen this set and cannot narrow it: a line that
    # ffprobe says overruns its silence is a line a person has to deal with.
    rows = {row["gap_index"]: row for row in verified.get("table") or []}
    forced = [i for i, row in rows.items() if row.get("verdict") != "FIT"]
    kept = sorted({i for i in asked if i in known} | set(forced))
    added = sorted(set(forced) - set(asked))

    ctx.state["escalation"] = {
        "hand_back": kept,
        "dropped_indices": dropped,
        "added_by_measurement": added,
        "reason": verdict.get("reason", ""),
        "residual_work": verdict.get("residual_work", ""),
        "gaps": [
            {
                "gap_index": i,
                "gap_s": rows[i]["gap_s"],
                "line": rows[i]["line"],
                "margin_ms": rows[i]["margin_ms"],
                "verdict": rows[i]["verdict"],
                "attempts": rows[i]["attempts"],
            }
            for i in kept
        ],
    }
    _step(
        ctx,
        "escalate",
        True,
        f"{len(kept)} gaps handed back to a describer",
        {
            "hand_back": len(kept),
            "dropped_indices": len(dropped),
            "added_by_measurement": len(added),
        },
    )


async def report(
    ctx: Any,
    media_path: str,
    noise_db: float,
    min_gap_s: float,
    headroom_ms: int,
) -> None:
    """Assemble the conform report and attach verify, the verdict and any hand-back."""
    cues_as_dicts: list[dict] = list(ctx.state.get("cues") or [])
    conformed = [
        _conform_mod.ConformedCue(
            gap_index=c["gap_index"],
            gap_start_s=c["gap_start_s"],
            gap_end_s=c["gap_end_s"],
            gap_duration_s=c["gap_duration_s"],
            char_budget=c["char_budget"],
            chars_per_second=c["chars_per_second"],
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
        conformed,
        noise_db=noise_db,
        min_gap_s=min_gap_s,
        headroom_ms=headroom_ms,
        skipped=list(ctx.state.get("skipped") or []),
    )
    result["verified"] = dict(ctx.state.get("verified") or {})
    result["verdict"] = dict(ctx.state.get("verdict") or {})
    result["escalation"] = dict(ctx.state.get("escalation") or {})
    result["plan"] = dict(ctx.state.get("plan") or {})

    ctx.state["report"] = result
    _step(
        ctx,
        "report",
        True,
        f"report assembled, {result['totals']['fit']} cues deliverable",
        {"fit": result["totals"]["fit"], "overflow": result["totals"]["overflow"]},
    )


# --- model nodes ----------------------------------------------------------

_PLANNER_INSTRUCTION = """\
You are an audio description coverage planner. Select which measured silences in
a film are worth describing for a blind or partially sighted viewer.

Each object in the array below is one silence that ffmpeg measured:
  index         integer gap identifier
  start_s       gap start in seconds
  end_s         gap end in seconds
  duration_s    gap length in seconds
  char_budget   characters a description may use at the measured speaking rate
  peak          strongest frame-to-frame scene-change score inside the gap
  mean          mean scene-change score across every frame in the gap

Choose the gaps where a spoken line earns the listener's attention. A high peak
means something changed on screen: a cut, an entrance, a movement. A low peak
over a long duration means the frame is close to still, and narrating a still
frame spends attention on nothing. A char_budget under 20 will not hold a
sentence worth hearing.

You must return only indices present in the array. An index that is not in it
will be discarded and recorded as invented. Give one line of reason per index.

Gaps:
{gaps_json}
"""

_SHORTEN_INSTRUCTION = """\
You are cutting audio description lines that have already been spoken aloud and
measured. Each line below was rendered to speech and the rendered audio was
longer than the silence it has to sit in.

  gap_index      which silence this line belongs to
  line           the line as written
  chars          its length in characters
  gap_s          the measured length of the silence, in seconds
  spoken_s       how long the rendered speech actually ran, in seconds
  overran_by_ms  how far past the usable window it ran
  max_chars      the hard ceiling for your rewrite, in characters

Return one rewritten line per gap_index, at or under max_chars. The ceiling came
from the measured overrun at the measured speaking rate, so it is not a style
preference and there is no margin in it.

Cut, do not rewrite from scratch. Keep the single most load-bearing visual fact
and drop the rest: a subject doing something beats a subject plus its
surroundings, and a subject plus its surroundings beats an inventory of the
frame. Drop adjectives before nouns, drop the second sentence before the first,
drop set dressing before people. Never invent something not in the original
line, because you cannot see the picture and the original writer could.

Keep present tense. No character names. No mention of sound or music. No
phrasing like "we see" or "the camera". No em dashes.

In `dropped`, name what you removed in a few words, for the mixer's record.

Your line will be rendered to speech and measured again. Coming in under
max_chars does not make it accepted.

Lines that overran:
{overruns_json}
"""

_ADJUDICATE_INSTRUCTION = """\
You are deciding whether a conformed audio description pass is deliverable to a
re-recording mixer, or has to go back to a human describer first.

Every number below was measured by ffprobe on the rendered audio, after the
retake loop finished. You cannot change any of them, and a verdict of OVERFLOW
is final: that line does not fit and no reasoning makes it fit.

  checked      accepted takes re-measured independently from disk
  agreed       of those, how many matched what the conforming path reported
  disagreed    the ones that did not, which is a defect in this tool
  rounds       how many shortening rounds the loop needed
  table        one row per gap: gap_s, line, chars, spoken_s, margin_ms,
               verdict, attempts

At least one row will be present. A verify block with an empty table means the
run measured nothing, which is itself a reason to escalate.

Set `disposition` to exactly one of:
  publish    every row is FIT, nothing disagreed, and the surviving margins are
             wide enough that a mixer can lay these in as they are.
  escalate   at least one row is OVERFLOW, or something disagreed, or a margin
             is tight enough that a describer should look at the line before it
             reaches a mix.

Put in `hand_back` the gap indices a human describer has to take over. Every
OVERFLOW row belongs there. A FIT row belongs there only if you can say why from
the numbers, for instance a line that took three attempts and survives on a
handful of milliseconds. An index not in the table will be dropped.

`reason` cites the figures that decided it. `residual_work` says what a person
still has to do, or the single word none. No em dashes.

Verify block:
{verified_json}
"""


# --- assembly -------------------------------------------------------------


def build_workflow() -> Workflow:
    """Return the assembled ADK workflow graph for the AD pipeline.

    Raises GeminiRequired before constructing a single node when this process has
    no way to reach a model. Building the graph first and discovering that inside
    the coverage planner would spend the whole silencedetect pass over an 87
    minute film before failing.
    """
    require_model()
    model = resolve_model()

    from google.adk.agents import LlmAgent

    measure_node = node(measure, name="measure", timeout=3600.0)
    survey_node = node(survey, name="survey", timeout=1800.0)
    draft_node = node(draft, name="draft", timeout=3600.0)
    retake_node = node(retake, name="retake", timeout=1800.0)
    verify_node = node(verify, name="verify", timeout=600.0)
    dispatch_node = node(dispatch, name="dispatch", timeout=120.0)
    escalate_node = node(escalate, name="escalate", timeout=120.0)
    report_node = node(report, name="report", timeout=600.0)

    coverage_planner = LlmAgent(
        name="coverage_planner",
        model=model,
        description="Chooses which measured silences deserve a described line.",
        instruction=_PLANNER_INSTRUCTION,
        output_schema=CoveragePlan,
        output_key="plan",
    )
    shorten = LlmAgent(
        name="shorten",
        model=model,
        description="Cuts a line that overran to the ceiling the measurement set.",
        instruction=_SHORTEN_INSTRUCTION,
        output_schema=ShortenedLines,
        output_key="shorten",
    )
    adjudicate = LlmAgent(
        name="adjudicate",
        model=model,
        description="Decides whether the measured pass ships or goes to a describer.",
        instruction=_ADJUDICATE_INSTRUCTION,
        output_schema=RunVerdict,
        output_key="verdict",
    )

    retry_twice = RetryConfig(max_attempts=2)
    planner_node = node(coverage_planner, retry_config=retry_twice, timeout=180.0)
    shorten_node = node(shorten, retry_config=retry_twice, timeout=180.0)
    adjudicate_node = node(adjudicate, retry_config=retry_twice, timeout=180.0)

    return Workflow(
        name="audio_description",
        description=(
            "Measures the silences in a film, writes and speaks a description line "
            "for each one worth describing, and re-measures the rendered audio."
        ),
        state_schema=ADState,
        edges=[
            (START, measure_node, survey_node, planner_node, draft_node),
            (draft_node, {"shorten": shorten_node, "settled": verify_node}),
            (shorten_node, retake_node),
            (retake_node, {"shorten": shorten_node, "settled": verify_node}),
            (verify_node, adjudicate_node, dispatch_node),
            (dispatch_node, {"escalate": escalate_node, "publish": report_node}),
            (escalate_node, report_node),
        ],
    )
