"""Sightread must stop when it cannot reach Gemini, not degrade into a stub.

Three model nodes decide things no deterministic function in this repo can
produce: which silences deserve a described line, what to cut from a line whose
real spoken length overran, and whether the pass is deliverable. If any of those
acquired a non-model fallback, the model would become decorative and these tests
would be the only thing that noticed.

Every test here removes the credentials from the process environment rather than
patching a flag, because a missing credential is the failure a hosted instance
actually has.
"""

from __future__ import annotations

import inspect

import pytest

import agent.credentials as creds
import agent.graph as graph
import agent.pipeline as pipeline


def _strip_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in creds.CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)


def test_no_credentials_stops_the_workflow_from_being_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_workflow must raise before it constructs a single node.

    Not merely before the first model call: before the graph exists at all. An
    87 minute silencedetect pass runs in `measure`, and discovering the missing
    credential inside the coverage planner would spend that first.
    """
    _strip_credentials(monkeypatch)
    with pytest.raises(creds.GeminiRequired) as caught:
        graph.build_workflow()
    message = str(caught.value)
    assert "GOOGLE_CLOUD_PROJECT" in message
    assert "GOOGLE_API_KEY" in message


def test_no_credentials_stops_run_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry point raises on a path that never touches ffmpeg or the network.

    The media path given here does not exist. If run_pipeline reached the measure
    node it would fail with a GapMeasurementError instead, so this test also pins
    that the credential check happens first.
    """
    _strip_credentials(monkeypatch)
    with pytest.raises(creds.GeminiRequired):
        pipeline.run_pipeline("/nonexistent/film.mp4", out_dir="/tmp/sightread-never")


def test_model_available_is_false_with_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The web surface reads this to decide whether to offer a live run."""
    _strip_credentials(monkeypatch)
    assert creds.model_available() is False


@pytest.mark.parametrize("name", creds.CREDENTIAL_VARS)
def test_each_credential_alone_is_enough(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Any one of the three is a working configuration.

    Without this the previous tests would still pass if model_available always
    returned False, which would block every real run.
    """
    _strip_credentials(monkeypatch)
    monkeypatch.setenv(name, "set-for-this-test")
    assert creds.model_available() is True
    assert graph.build_workflow().name == "audio_description"


def test_blank_model_id_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty SIGHTREAD_MODEL must raise rather than silently pick a default."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "set-for-this-test")
    monkeypatch.setenv("SIGHTREAD_MODEL", "   ")
    with pytest.raises(creds.GeminiRequired):
        graph.build_workflow()


def test_every_model_node_carries_a_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three LlmAgent nodes must be on a real model, with no shared mutable default."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "set-for-this-test")
    monkeypatch.setenv("SIGHTREAD_MODEL", "gemini-2.5-flash")
    workflow = graph.build_workflow()
    llm_nodes = {
        n.name: n for n in workflow.graph.nodes if type(n).__name__ == "LlmAgent"
    }
    assert set(llm_nodes) == {"coverage_planner", "shorten", "adjudicate"}
    for name, agent in llm_nodes.items():
        assert agent.model == "gemini-2.5-flash", name
        assert agent.output_schema is not None, name
        assert agent.instruction.strip(), name


def test_no_module_offers_a_non_gemini_description_path() -> None:
    """ad.describe and ad.render must have exactly one client construction each.

    A second client, a `try: ... except: return canned` around the call, or a
    module-level TEXT constant would all be ways for a run to keep going without
    a model. This asserts on the source because the failure mode is a code path
    that only appears when credentials are absent, which no green run reaches.
    """
    import ad.describe as describe
    import ad.render as render

    for module in (describe, render):
        source = inspect.getsource(module)
        assert source.count("genai.Client(") == 1, module.__name__
        assert "except Exception" not in source, module.__name__
        for banned in ("fallback", "FALLBACK", "placeholder", "TODO", "stub"):
            assert banned not in source, f"{module.__name__} mentions {banned}"
