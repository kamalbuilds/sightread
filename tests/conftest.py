"""The gates that decide what a run of this suite actually exercised.

pytest counts a skip as a non-failure, so a suite that reports "47 passed" while
every test touching Gemini, ffmpeg or the rendered takes skipped still reads as
success at a glance. That tally is worth nothing: the integrations this project
rests on would be entirely unexercised and nothing in the output would say so.

Two rules here, and they are the whole point of the file.

**A run that names an integration is asserting it works.** With
GOOGLE_CLOUD_PROJECT set, a Vertex call that cannot be made is a broken
configuration and goes red. Only a bare clone with nothing configured skips
quietly, and the skip names what to set.

**The end of the run says which integrations were exercised.** `pytest_terminal_summary`
prints one line per integration, so green is never ambiguous. A run that skipped
the model path says so next to the tally.

Every test that needs a real dependency takes one of these fixtures rather than
probing for it inline, so there is exactly one place that knows what "not
configured" looks like versus "configured and broken".
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.credentials import CREDENTIAL_VARS, model_available  # noqa: E402

#: One entry per integration the suite can exercise, filled in as gates run.
#: `False` means a gate turned tests away, so the summary can say what a green
#: tally did not cover.
_EXERCISED: dict[str, bool] = {}
_TURNED_AWAY: list[str] = []

_HOW_TO_GET_A_MODEL = (
    "Set GOOGLE_CLOUD_PROJECT and run `gcloud auth application-default login` for "
    "Vertex AI, or set GOOGLE_API_KEY for the Gemini API. The model path is three "
    "LlmAgent nodes and two direct Gemini calls, and none of them has a "
    "deterministic stand-in, so a run without credentials proves nothing about it."
)

_HOW_TO_GET_THE_TAKES = (
    "The rendered takes ship in out/. If they are absent, reproduce them with "
    "`python run_conform.py <film.mp4> --min-gap-s 4.0 --out-dir out/nighttide-long "
    "--report out/nighttide-long/report.json`."
)


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def _turn_away(integration: str, reason: str, *, configured: bool) -> None:
    """Skip on a bare clone, fail when the run claimed the dependency was there."""
    _EXERCISED.setdefault(integration, False)
    entry = f"{integration}: {reason}"
    if entry not in _TURNED_AWAY:
        _TURNED_AWAY.append(entry)
    if configured:
        pytest.fail(
            f"this run was configured to exercise {integration} and could not: "
            f"{reason} That is a broken configuration rather than an absent one, so "
            "it is red rather than skipped."
        )
    pytest.skip(reason)


@pytest.fixture(scope="session")
def ffmpeg_tools() -> tuple[str, str]:
    """Paths to a working ffmpeg and ffprobe, or a gate that says why not.

    Two stages, because `which ffmpeg` succeeding only proves a file exists on
    PATH. The version is actually run, because a truncated or wrong-architecture
    binary passes the first check and then fails inside every measurement.

    Never a quiet skip when SIGHTREAD_REQUIRE_FFMPEG is set, which is what the
    container image does: there, a missing ffmpeg means the image is broken.
    """
    required = _truthy("SIGHTREAD_REQUIRE_FFMPEG")
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        _turn_away(
            "ffmpeg measurement",
            f"{' and '.join(missing)} not on PATH. Every number this project reports "
            "comes from silencedetect, scdet or ffprobe.",
            configured=required,
        )
    for name in ("ffmpeg", "ffprobe"):
        try:
            result = subprocess.run(
                [name, "-version"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _turn_away(
                "ffmpeg measurement",
                f"{name} is on PATH but would not run: {exc}",
                configured=required,
            )
        if result.returncode != 0:
            _turn_away(
                "ffmpeg measurement",
                f"{name} -version exited {result.returncode}: "
                f"{result.stderr.strip()[:200]}",
                configured=required,
            )
    _EXERCISED["ffmpeg measurement"] = True
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


@pytest.fixture(scope="session")
def vertex_project(ffmpeg_tools) -> str:
    """A Vertex AI project this process can reach, or a gate that says why not.

    Two stages again. Credentials being present in the environment only proves a
    string is set; the token is minted here so an expired or wrong-project ADC
    fails at the gate with a message about credentials rather than three minutes
    later inside an LlmAgent.

    Depends on ffmpeg_tools because every live model test measures what came back,
    and a model test whose measurement is unavailable proves nothing either.
    """
    if not model_available():
        _turn_away(
            "gemini on vertex",
            f"none of {', '.join(CREDENTIAL_VARS)} is set. {_HOW_TO_GET_A_MODEL}",
            configured=False,
        )

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        _turn_away(
            "gemini on vertex",
            "GOOGLE_API_KEY is set but these tests exercise the Vertex path, which "
            "needs GOOGLE_CLOUD_PROJECT.",
            configured=True,
        )

    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")

    credentials = None
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
    except Exception as exc:
        _turn_away(
            "gemini on vertex",
            f"GOOGLE_CLOUD_PROJECT names {project} but no usable credential could be "
            f"minted for it: {type(exc).__name__}: {exc}",
            configured=True,
        )
    if not getattr(credentials, "token", None):
        _turn_away(
            "gemini on vertex",
            f"credentials for {project} refreshed without producing a token.",
            configured=True,
        )

    _EXERCISED["gemini on vertex"] = True
    return project


@pytest.fixture(scope="session")
def shipped_takes() -> pathlib.Path:
    """The directory of rendered takes the fit tests read, or a gate that says why not.

    A take whose WAV is absent cannot be re-measured, and a fit test that skips is
    indistinguishable from one that has nothing to check. SIGHTREAD_REQUIRE_TAKES
    turns that into a failure, which is what a container image built from this repo
    asserts: the takes are the evidence it serves.
    """
    required = _truthy("SIGHTREAD_REQUIRE_TAKES")
    directory = ROOT / "out" / "nighttide"
    takes = sorted(directory.glob("gap*.take*.wav")) if directory.is_dir() else []
    if not takes:
        _turn_away(
            "rendered takes on disk",
            f"no rendered takes under {directory}. {_HOW_TO_GET_THE_TAKES}",
            configured=required,
        )
    _EXERCISED["rendered takes on disk"] = True
    return directory


def pytest_terminal_summary(terminalreporter) -> None:
    """State which integrations this run exercised, so green is never ambiguous."""
    for name in sorted(_EXERCISED):
        if _EXERCISED[name]:
            terminalreporter.write_line(f"exercised: {name}", green=True)
    if not _TURNED_AWAY:
        return
    terminalreporter.write_sep("=", "integrations NOT exercised", yellow=True)
    for line in _TURNED_AWAY:
        terminalreporter.write_line(f"  {line}", yellow=True)
    terminalreporter.write_line(
        "This tally counts those as non-failures. It is not evidence about the "
        "integrations above. Configure them and re-run before treating the suite "
        "as green.",
        yellow=True,
    )
