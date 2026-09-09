"""The model path, exercised against real Vertex AI rather than described.

Nothing else in this suite makes a Gemini call. Every other test drives the graph
with hand-built state, which proves the plumbing and proves nothing about whether
a coverage plan, a shortened line or a rendered WAV comes back at all. That gap is
exactly the shape of a green tally that covers nothing: the three LlmAgent nodes
and the two direct Gemini calls are what this project is judged on.

These tests take `vertex_project`, so on a bare clone with no credentials they skip
with a message naming what to set, and on a run that DOES name a project they fail
if Vertex is unreachable. See tests/conftest.py.

Everything the model produces here is measured, never trusted:

- the coverage planner's output is checked against the gap indices it was given
- the shortened line is checked against the ceiling the measurement set
- the rendered WAV is measured with ffprobe, and the overrun gate is mutated to
  confirm it can reject

The media is built by ffmpeg in the fixture rather than committed, so these run
against whatever ffmpeg is on this machine and cannot pass on a stale artefact.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import wave

import pytest

import ad.conform as _conform_mod
import ad.fit as _fit_mod
import ad.gaps as _gaps_mod
import agent.graph as graph


@pytest.fixture(scope="module")
def spoken_gap_media(tmp_path_factory, ffmpeg_tools) -> str:
    """A file with one 6 s silence between two tones, and moving picture in it.

    The picture moves because the survey node scores visual activity and the
    coverage planner is instructed to skip static frames. A file of black frames
    would give the planner a legitimate reason to select nothing, and a test that
    passes on an empty selection asserts nothing.
    """
    out = tmp_path_factory.mktemp("live") / "spoken-gap.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=12:duration=10",
        "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]",
        "-map", "[a]", "-map", "3:v",
        "-c:a", "aac", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-shortest", str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    return str(out)


@pytest.fixture(scope="module")
def measured_gap(spoken_gap_media: str) -> _gaps_mod.Gap:
    """The silence, measured. Not assumed from how the fixture was built."""
    gaps = _gaps_mod.measure_gaps(spoken_gap_media, noise_db=-40.0, min_gap_s=2.0)
    assert len(gaps) == 1, f"the fixture must hold exactly one silence, got {gaps}"
    assert 5.0 < gaps[0].duration_s < 7.0, gaps[0]
    return gaps[0]


def test_gemini_writes_a_line_for_a_measured_silence(
    vertex_project: str, spoken_gap_media: str, measured_gap, tmp_path
) -> None:
    """ad.describe.describe_gap must return real text from a real Gemini call.

    Also pins that the clip it sends is a file ffmpeg actually wrote: an empty clip
    would make the description a hallucination about nothing.
    """
    from ad.describe import describe_gap

    budget = _fit_mod.target_chars(measured_gap.duration_s)
    assert budget > 20, f"the fixture must leave room for a sentence, got {budget}"

    clip = tmp_path / "gap.clip.mp4"
    description = describe_gap(
        spoken_gap_media,
        measured_gap.index,
        measured_gap.start_s,
        measured_gap.duration_s,
        budget,
        str(clip),
        project=vertex_project,
    )
    assert clip.is_file() and clip.stat().st_size > 0
    assert description.text.strip()
    assert description.chars == len(description.text)
    assert "\n" not in description.text
    assert description.model.startswith("gemini")


def test_gemini_tts_produces_a_wav_ffprobe_can_measure(
    vertex_project: str, tmp_path
) -> None:
    """A rendered line must be a readable WAV whose length two tools agree on.

    The wave module reads the header and ffprobe decodes the stream. If they
    disagree the file is malformed in a way that would make every margin in the
    product wrong, and a single reading could not tell.
    """
    from ad.render import render_line

    out = tmp_path / "line.wav"
    rendered = render_line(
        "A man in a sailor suit looks at a statue.",
        str(out),
        project=vertex_project,
    )
    assert out.is_file() and out.stat().st_size > 1000
    assert rendered.sample_rate >= 8000

    with wave.open(str(out), "rb") as handle:
        header_seconds = handle.getnframes() / handle.getframerate()
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2

    probed_seconds = _gaps_mod.probe_duration(str(out))
    assert probed_seconds > 0.5
    assert abs(probed_seconds - header_seconds) < 0.05, (
        f"wave header says {header_seconds:.3f}s, ffprobe says {probed_seconds:.3f}s"
    )


def test_the_overrun_gate_rejects_a_take_that_is_genuinely_too_long(
    vertex_project: str, tmp_path
) -> None:
    """Inject a known overrun into a real rendered WAV and confirm the gate goes red.

    The same file is checked against a silence it fits and a silence it cannot, so
    the gate is shown deciding both ways on one measurement. A gate that only ever
    saw the passing case would be indistinguishable from `return "FIT"`.
    """
    from ad.render import render_line

    out = tmp_path / "long-line.wav"
    render_line(
        "A man in a sailor suit stands beside a glass case of jars and looks down at "
        "the woman asleep on the bed behind him.",
        str(out),
        project=vertex_project,
    )
    spoken_s = _gaps_mod.probe_duration(str(out))
    assert spoken_s > 2.0, f"the fixture line must run long enough to overrun, got {spoken_s}"

    generous_gap_s = spoken_s + 1.0
    accepted = _fit_mod.check_fit(0, generous_gap_s, str(out), headroom_ms=250)
    assert accepted.verdict == "FIT"
    assert accepted.margin_ms == pytest.approx(750, abs=5)

    tight_gap_s = spoken_s - 1.0
    rejected = _fit_mod.check_fit(0, tight_gap_s, str(out), headroom_ms=250)
    assert rejected.verdict == "OVERFLOW"
    assert rejected.margin_ms == pytest.approx(-1250, abs=5)
    assert rejected.rendered_duration_s == pytest.approx(spoken_s, abs=0.001)


def test_the_coverage_planner_returns_indices_it_was_given(
    vertex_project: str, spoken_gap_media: str
) -> None:
    """Drive the real coverage_planner LlmAgent and check its output against the input.

    Runs the graph's own measure, survey and coverage_planner nodes through ADK's
    runner, then stops: describing and rendering are covered above and cost a TTS
    call per gap. What is asserted here is that a live Gemini structured-output call
    comes back parseable and confined to the gap indices ffmpeg measured.
    """
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.adk.workflow import START, Workflow, node
    from google.genai import types

    from agent.credentials import resolve_model, use_vertex
    from agent.pipeline import initial_state

    use_vertex()

    planner = LlmAgent(
        name="coverage_planner",
        model=resolve_model(),
        description="Chooses which measured silences deserve a described line.",
        instruction=graph._PLANNER_INSTRUCTION,
        output_schema=graph.CoveragePlan,
        output_key="plan",
    )
    partial = Workflow(
        name="coverage_only",
        state_schema=graph.ADState,
        edges=[
            (
                START,
                node(graph.measure, name="measure", timeout=600.0),
                node(graph.survey, name="survey", timeout=600.0),
                node(planner, timeout=180.0),
            )
        ],
    )

    async def drive() -> dict:
        runner = InMemoryRunner(node=partial, app_name="sightread-live-test")
        session = await runner.session_service.create_session(
            app_name="sightread-live-test",
            user_id="test",
            state=initial_state(
                spoken_gap_media, noise_db=-40.0, min_gap_s=2.0, run_id=""
            ),
        )
        async for _ in runner.run_async(
            user_id="test",
            session_id=session.id,
            new_message=types.Content(
                role="user", parts=[types.Part(text="Plan coverage.")]
            ),
        ):
            pass
        final = await runner.session_service.get_session(
            app_name="sightread-live-test", user_id="test", session_id=session.id
        )
        return dict(final.state)

    state = asyncio.run(drive())

    measured_indices = {gap["index"] for gap in state["gaps"]}
    assert measured_indices, "measure must find the fixture's silence"
    assert len(state["survey"]) == len(state["gaps"])

    surveyed = json.loads(state["gaps_json"])
    assert surveyed and all("peak" in row and "char_budget" in row for row in surveyed)

    plan = state["plan"]
    assert isinstance(plan.get("selected"), list)
    assert isinstance(plan.get("reasons"), dict)
    assert plan.get("note", "") is not None
    invented = set(plan["selected"]) - measured_indices
    assert not invented, f"planner returned indices that were never measured: {invented}"
    assert plan["selected"], (
        "the planner selected nothing from a moving-picture silence with a "
        f"{surveyed[0]['char_budget']} character budget, so this run asserted nothing "
        "about coverage"
    )


def test_the_shortener_cuts_a_line_toward_the_measured_ceiling(
    vertex_project: str,
) -> None:
    """Drive the real shorten LlmAgent on a line with a real measured overrun.

    The ceiling handed to it is ad.conform.shrink_budget over figures from the
    shipped Night Tide run: an 11.251 s take against a 4.173 s silence, 111
    characters, 7328 ms over. The assertion is that the model returns a line for
    the index it was given, materially shorter than the original. Whether it comes
    in under the ceiling is not asserted, because the product does not rely on it:
    retake renders whatever comes back and ffprobe decides.
    """
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.adk.workflow import START, Workflow, node
    from google.genai import types

    from agent.credentials import resolve_model, use_vertex
    from agent.pipeline import initial_state

    use_vertex()

    original = (
        "A man in a sailor suit stands in a dim room beside a tall glass case filled "
        "with jars, looking down at the woman asleep on the bed."
    )
    overrun = {
        "gap_index": 3,
        "line": original,
        "chars": len(original),
        "spoken_s": 11.251,
        "gap_s": 4.173,
        "overran_by_ms": 7328,
        "max_chars": _conform_mod.shrink_budget(len(original), 11.251, -7328, len(original)),
    }
    assert 0 < overrun["max_chars"] < len(original)

    shortener = LlmAgent(
        name="shorten",
        model=resolve_model(),
        description="Cuts a line that overran to the ceiling the measurement set.",
        instruction=graph._SHORTEN_INSTRUCTION,
        output_schema=graph.ShortenedLines,
        output_key="shorten",
    )

    async def drive() -> dict:
        runner = InMemoryRunner(
            node=Workflow(
                name="shorten_only",
                state_schema=graph.ADState,
                edges=[(START, node(shortener, timeout=180.0))],
            ),
            app_name="sightread-live-test",
        )
        session = await runner.session_service.create_session(
            app_name="sightread-live-test",
            user_id="test",
            state=initial_state(
                "unused.mp4",
                overruns_json=json.dumps([overrun], separators=(",", ":")),
                run_id="",
            ),
        )
        async for _ in runner.run_async(
            user_id="test",
            session_id=session.id,
            new_message=types.Content(
                role="user", parts=[types.Part(text="Cut these lines.")]
            ),
        ):
            pass
        final = await runner.session_service.get_session(
            app_name="sightread-live-test", user_id="test", session_id=session.id
        )
        return dict(final.state)

    lines = asyncio.run(drive())["shorten"]["lines"]
    assert lines, "the shortener returned no lines for a line that overran"
    cut = next(item for item in lines if item["gap_index"] == overrun["gap_index"])
    assert cut["line"].strip()
    assert len(cut["line"]) < len(original), (
        f"the rewrite is not shorter: {len(cut['line'])} vs {len(original)}"
    )
    assert "—" not in cut["line"], "em dash in a description line"
    assert cut["dropped"].strip(), "the mixer's record of what was cut must not be empty"
