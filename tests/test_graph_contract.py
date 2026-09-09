"""The contract between the graph's nodes, exercised without a model or a film.

Two real defects motivated this file. Both were invisible to every other test and
both only surfaced at the end of a run that had already spent its Gemini and TTS
calls:

- `report` rebuilt an `ad.conform.ConformedCue` from the cue dicts that `draft`
  writes into state. A field was added to the dataclass and not to the dict, so
  the last node of the graph raised TypeError after every model call in the run
  had already been paid for.
- the CLI progress callback read `steps[-1]['step']` while `_step` writes `node`.
  The KeyError was swallowed by the callback's own except clause, so a run printed
  nothing and looked hung.

The nodes here are driven with a hand-built context, so these run in
milliseconds and in CI with no credentials.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json

import pytest

import ad.conform as _conform_mod
import agent.graph as graph
import agent.pipeline as pipeline


class Ctx:
    """The slice of ADK's node context the deterministic nodes actually touch."""

    def __init__(self, **state) -> None:
        self.state: dict = {"steps": [], "run_id": "", **state}


def _cue(
    gap_index: int = 0,
    *,
    verdict: str = "FIT",
    margin_ms: int = 500,
    attempts: int = 1,
    chars: int = 40,
    rendered_duration_s: float = 3.5,
    audio_path: str = "",
) -> dict:
    return {
        "gap_index": gap_index,
        "gap_start_s": 100.0,
        "gap_end_s": 104.25,
        "gap_duration_s": 4.25,
        "char_budget": 34,
        "chars_per_second": 8.6,
        "text": "A man in a sailor suit looks at a statue.",
        "chars": chars,
        "audio_path": audio_path,
        "clip_path": f"out/x/gap{gap_index:04d}.clip.mp4",
        "rendered_duration_s": rendered_duration_s,
        "margin_ms": margin_ms,
        "verdict": verdict,
        "attempts": attempts,
        "attempt_log": [
            {
                "attempt": 1,
                "char_budget": 34,
                "chars": chars,
                "rendered_duration_s": rendered_duration_s,
                "margin_ms": margin_ms,
                "verdict": verdict,
            }
        ],
    }


def test_cue_dicts_carry_every_field_conformed_cue_requires() -> None:
    """The graph's cue dict and ad.conform.ConformedCue must not drift apart.

    Asserted from the dataclass's own field list, so adding a field to
    ConformedCue without adding it to the dict fails here rather than in the last
    node of a live run.
    """
    required = {
        name
        for name, spec in _conform_mod.ConformedCue.__dataclass_fields__.items()
        if spec.default is dataclasses.MISSING
        and spec.default_factory is dataclasses.MISSING
    }
    assert len(required) >= 14, (
        "ConformedCue has no required fields, so this test would assert nothing"
    )
    take = _conform_mod.Take(
        gap_index=3,
        attempt=1,
        char_budget=34,
        text="A man stands in a room.",
        chars=23,
        audio_path="out/x/gap0003.take1.wav",
        clip_path="out/x/gap0003.clip.mp4",
        rendered_duration_s=2.4,
        margin_ms=1600,
        verdict="FIT",
    )
    produced = graph._cue_from_take(
        take,
        {"index": 3, "start_s": 10.0, "end_s": 14.25, "duration_s": 4.25},
        8.6,
    )
    missing = required - set(produced)
    assert not missing, f"cue dict is missing {sorted(missing)}"
    _conform_mod.ConformedCue(**{k: produced[k] for k in required})


def test_report_node_rebuilds_the_cues_it_was_given(tmp_path) -> None:
    """The last node of the graph must not raise on its own upstream output.

    Uses a real short media file so probe_duration has something to read, because
    `ad.conform.report` measures the film's duration rather than trusting state.
    """
    import subprocess

    media = tmp_path / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:a", "aac", str(media),
        ],
        capture_output=True,
        check=True,
    )

    ctx = Ctx(
        cues=[_cue(0), _cue(1, verdict="OVERFLOW", margin_ms=-320, attempts=3)],
        skipped=[],
        verified={"checked": 2, "agreed": 2, "disagreed": [], "table": [], "rounds": 1},
        verdict={"disposition": "escalate", "reason": "gap 1 overran"},
        escalation={"hand_back": [1]},
        plan={"selected": [0, 1]},
    )
    asyncio.run(graph.report(ctx, str(media), -26.0, 4.0, 250))

    result = ctx.state["report"]
    assert result["totals"]["fit"] == 1
    assert result["totals"]["overflow"] == 1
    assert result["verdict"]["disposition"] == "escalate"
    assert result["escalation"]["hand_back"] == [1]
    assert ctx.state["steps"][-1]["node"] == "report"


def test_progress_callback_receives_the_key_the_cli_reads() -> None:
    """The CLI's progress line must not depend on a key _step does not write.

    The CLI's callback is invoked inside _step behind a bare except, so a KeyError
    there is silent. This drives the real callable the CLI builds.
    """
    seen: list[str] = []

    def cli_progress(steps: list[dict]) -> None:
        seen.append(
            f"[{steps[-1]['node']}] {'ok' if steps[-1]['ok'] else 'FAILED'}: "
            f"{steps[-1]['summary']}"
        )

    graph._PROGRESS["test-run"] = cli_progress
    try:
        ctx = Ctx(run_id="test-run")
        graph._step(ctx, "measure", True, "found 9 gaps", {"gap_count": 9})
    finally:
        graph._PROGRESS.pop("test-run", None)

    assert seen == ["[measure] ok: found 9 gaps"]

    source = inspect.getsource(pipeline.main)
    assert "steps[-1]['node']" in source
    assert "steps[-1]['step']" not in source


def test_initial_state_carries_every_parameter_the_nodes_ask_for() -> None:
    """ADK fills node parameters from session state, not from the state schema.

    A key missing from the starting state raises inside the node that wanted it,
    which on this graph is after the whole silencedetect pass has run. This asserts
    the starting state covers every non-ctx parameter of every deterministic node,
    read off the function signatures rather than listed here, so a node that grows a
    parameter fails here instead of mid-run. It is how a real failure was found:
    `survey` asked for `headroom_ms` from a state that did not have it.
    """
    state = pipeline.initial_state("film.mp4", run_id="r")
    nodes = (
        graph.measure,
        graph.survey,
        graph.draft,
        graph.retake,
        graph.verify,
        graph.dispatch,
        graph.escalate,
        graph.report,
    )
    for func in nodes:
        wanted = [
            name
            for name, spec in inspect.signature(func).parameters.items()
            if name != "ctx" and spec.default is inspect.Parameter.empty
        ]
        missing = [name for name in wanted if name not in state]
        assert not missing, f"{func.__name__} asks for {missing}, absent from initial_state"

    assert state["chars_per_second"] == _conform_mod.default_chars_per_second()
    assert state["headroom_ms"] == 250
    assert state["max_attempts"] == 3


def test_draft_discards_a_gap_index_the_planner_invented() -> None:
    """A selected index that is not in the measurement must never be described.

    Driven through the node so the discard record and the route are both checked.
    Nothing is described here because index 99 is the only selection, so no Gemini
    or TTS call is reachable.
    """
    ctx = Ctx(
        plan={"selected": [99], "reasons": {"99": "invented"}, "note": ""},
        gaps=[{"index": 0, "start_s": 1.0, "end_s": 6.0, "duration_s": 5.0}],
    )
    events = asyncio.run(_collect(graph.draft(
        ctx, "/nonexistent/film.mp4", "/tmp/sightread-never", "p", "us-central1", 250, 8.6, 3
    )))
    assert ctx.state["cues"] == []
    assert ctx.state["selected"] == []
    assert ctx.state["skipped"] == [
        {"gap_index": 99, "reason": "index not present in measured gaps"}
    ]
    assert [e.actions.route for e in events] == ["settled"]


async def _collect(generator) -> list:
    return [event async for event in generator]


def test_draft_routes_to_shorten_only_when_something_overran() -> None:
    """The branch out of draft is the measurement, not a model field."""
    settled = Ctx(cues=[], plan={"selected": []}, gaps=[])
    events = asyncio.run(_collect(graph.draft(
        settled, "/nonexistent/film.mp4", "/tmp/sightread-never", "p", "us-central1", 250, 8.6, 3
    )))
    assert [e.actions.route for e in events] == ["settled"]

    live, payload = graph._overruns_payload(
        [_cue(0, verdict="OVERFLOW", margin_ms=-1200, chars=60, rendered_duration_s=5.2)], 3
    )
    assert len(live) == 1
    assert live[0]["overran_by_ms"] == 1200
    assert live[0]["max_chars"] < 60, "the ceiling must be below the length that overran"
    assert json.loads(payload) == live


def test_a_cue_out_of_attempts_is_not_offered_to_the_shortener() -> None:
    """Rewriting a line the loop will not render is a wasted model call."""
    exhausted = _cue(0, verdict="OVERFLOW", margin_ms=-90, attempts=3, chars=30)
    live, _ = graph._overruns_payload([exhausted], 3)
    assert live == []

    still_going = _cue(0, verdict="OVERFLOW", margin_ms=-90, attempts=2, chars=30)
    live, _ = graph._overruns_payload([still_going], 3)
    assert len(live) == 1


def test_dispatch_overrides_a_publish_verdict_on_an_overflow_row() -> None:
    """A model cannot publish a run holding a line ffprobe says does not fit."""
    ctx = Ctx(
        verdict={"disposition": "publish", "reason": "looks fine", "hand_back": []},
        verified={
            "checked": 2,
            "agreed": 2,
            "disagreed": [],
            "table": [
                {"gap_index": 0, "gap_s": 4.25, "line": "a", "chars": 1,
                 "spoken_s": 1.0, "margin_ms": 3000, "verdict": "FIT", "attempts": 1},
                {"gap_index": 1, "gap_s": 2.0, "line": "b", "chars": 1,
                 "spoken_s": 3.0, "margin_ms": -1250, "verdict": "OVERFLOW", "attempts": 3},
            ],
        },
    )
    events = asyncio.run(_collect(graph.dispatch(ctx)))
    assert [e.actions.route for e in events] == ["escalate"]
    assert ctx.state["verdict"]["disposition"] == "escalate"
    assert "OVERFLOW" in ctx.state["verdict"]["override"]
    assert ctx.state["steps"][-1]["ok"] is False


def test_dispatch_overrides_publish_when_re_measurement_disagreed() -> None:
    """A disagreement is a defect in this tool. No verdict waves it through."""
    ctx = Ctx(
        verdict={"disposition": "publish", "reason": "all fit", "hand_back": []},
        verified={
            "checked": 1,
            "agreed": 0,
            "disagreed": [{"gap_index": 0, "conform_verdict": "FIT",
                           "verify_verdict": "OVERFLOW"}],
            "table": [
                {"gap_index": 0, "gap_s": 4.25, "line": "a", "chars": 1,
                 "spoken_s": 1.0, "margin_ms": 3000, "verdict": "FIT", "attempts": 1},
            ],
        },
    )
    events = asyncio.run(_collect(graph.dispatch(ctx)))
    assert [e.actions.route for e in events] == ["escalate"]
    assert "disagreement" in ctx.state["verdict"]["override"]


def test_dispatch_lets_a_clean_run_publish() -> None:
    """Without this the override could reject everything and still look correct."""
    ctx = Ctx(
        verdict={"disposition": "publish", "reason": "9 of 9 fit", "hand_back": []},
        verified={
            "checked": 1,
            "agreed": 1,
            "disagreed": [],
            "table": [
                {"gap_index": 0, "gap_s": 4.93, "line": "A man kisses a woman.",
                 "chars": 21, "spoken_s": 2.531, "margin_ms": 2151,
                 "verdict": "FIT", "attempts": 1},
            ],
        },
    )
    events = asyncio.run(_collect(graph.dispatch(ctx)))
    assert [e.actions.route for e in events] == ["publish"]
    assert "override" not in ctx.state["verdict"]
    assert ctx.state["steps"][-1]["ok"] is True


@pytest.mark.parametrize("garbage", ["", "PUBLISH_MAYBE", "yes", "ship it"])
def test_dispatch_refuses_a_disposition_it_does_not_recognise(garbage: str) -> None:
    """An unparseable verdict routes to a person, not to publish."""
    ctx = Ctx(
        verdict={"disposition": garbage},
        verified={"checked": 0, "agreed": 0, "disagreed": [], "table": []},
    )
    events = asyncio.run(_collect(graph.dispatch(ctx)))
    assert [e.actions.route for e in events] == ["escalate"]
    assert "unrecognised" in ctx.state["verdict"]["override"]


def test_escalate_adds_every_overflow_the_adjudicator_left_out() -> None:
    """The model can widen the hand-back set. It cannot narrow it."""
    ctx = Ctx(
        verdict={"hand_back": [0], "reason": "gap 0 is tight", "residual_work": "review"},
        verified={
            "table": [
                {"gap_index": 0, "gap_s": 4.6, "line": "a", "chars": 32,
                 "spoken_s": 4.13, "margin_ms": 185, "verdict": "FIT", "attempts": 2},
                {"gap_index": 1, "gap_s": 2.0, "line": "b", "chars": 45,
                 "spoken_s": 6.13, "margin_ms": -4197, "verdict": "OVERFLOW", "attempts": 3},
            ],
        },
    )
    asyncio.run(graph.escalate(ctx))
    assert ctx.state["escalation"]["hand_back"] == [0, 1]
    assert ctx.state["escalation"]["added_by_measurement"] == [1]


def test_escalate_drops_a_gap_index_that_was_never_measured() -> None:
    """Same rule as the planner: an index not in the measurement does not exist."""
    ctx = Ctx(
        verdict={"hand_back": [0, 77], "reason": "", "residual_work": "none"},
        verified={
            "table": [
                {"gap_index": 0, "gap_s": 4.6, "line": "a", "chars": 32,
                 "spoken_s": 4.13, "margin_ms": 185, "verdict": "FIT", "attempts": 2},
            ],
        },
    )
    asyncio.run(graph.escalate(ctx))
    assert ctx.state["escalation"]["hand_back"] == [0]
    assert ctx.state["escalation"]["dropped_indices"] == [77]


def test_shrink_budget_comes_down_from_the_measured_overrun() -> None:
    """The ceiling is derived from the overrun, and never rises."""
    ceiling = _conform_mod.shrink_budget(
        chars=111, rendered_duration_s=11.251, margin_ms=-7328, ceiling=111
    )
    assert 0 < ceiling < 111
    observed_cps = 111 / 11.251
    assert ceiling <= 111 - (7.328 * observed_cps)

    assert _conform_mod.shrink_budget(30, 3.0, -50, ceiling=12) == 12

    with pytest.raises(ValueError):
        _conform_mod.shrink_budget(30, 3.0, 250, ceiling=30)


def test_verify_records_a_disagreement_rather_than_dropping_it(tmp_path) -> None:
    """A cue whose reported duration is wrong must show up in verified.disagreed."""
    import subprocess

    wav = tmp_path / "take.wav"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            str(wav),
        ],
        capture_output=True,
        check=True,
    )
    honest = _cue(0, audio_path=str(wav), rendered_duration_s=2.0, margin_ms=2000)
    honest["gap_duration_s"] = 4.25
    lying = _cue(1, audio_path=str(wav), rendered_duration_s=0.5, margin_ms=3500)
    lying["gap_duration_s"] = 4.25

    ctx = Ctx(cues=[honest, lying], rounds=1)
    asyncio.run(graph.verify(ctx, 250))

    verified = ctx.state["verified"]
    assert verified["checked"] == 2
    assert verified["agreed"] == 1
    assert [d["gap_index"] for d in verified["disagreed"]] == [1]
    assert verified["disagreed"][0]["verify_duration_s"] == pytest.approx(2.0, abs=0.05)
    assert ctx.state["steps"][-1]["ok"] is False
    assert json.loads(ctx.state["verified_json"])["checked"] == 2
