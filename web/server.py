"""Sightread HTTP host.

Serves the evidence page, the conform reports behind it, and the rendered takes
themselves. The takes are served because a margin figure is a claim until you can
hear the line that produced it: a judge who plays a rejected take is looking at a
post-condition, not a status update.
"""

from __future__ import annotations

import json
import mimetypes
import sys
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.pipeline import GeminiRequired, model_available, run_pipeline  # noqa: E402

app = FastAPI(title="Sightread", version="1.0")

WEB = Path(__file__).parent
OUT = ROOT / "out"

#: The runs already on disk, newest interesting first. Each is a directory under
#: out/ holding report.json plus every take as a WAV.
SHIPPED = [
    {
        "slug": "nighttide-long",
        "label": "Night Tide (1961), gaps of 4s and over",
        "note": "Nine silences across the whole 87 minute film. Room for real sentences.",
    },
    {
        "slug": "nighttide",
        "label": "Night Tide (1961), a tight 2s window",
        "note": "Three silences at the short end, where a line has 11 to 17 characters.",
    },
]

#: The source these runs were measured from. A hosted instance does not carry the
#: 354MB film, so the live conform path is unavailable there by design. It says so
#: with the URL and the command rather than spinning, because a control that looks
#: alive and does nothing is worse than one that is honestly switched off.
SOURCE = {
    "title": "Night Tide (1961)",
    "url": "https://archive.org/download/NightTide16x9CorrectedAudio/NightTide_512kb.mp4",
    "reproduce": (
        "curl -L -o NightTide.mp4 "
        "https://archive.org/download/NightTide16x9CorrectedAudio/NightTide_512kb.mp4 && "
        "python run_conform.py NightTide.mp4 --min-gap-s 4.0 "
        "--out-dir out/nighttide-long --report out/nighttide-long/report.json"
    ),
}


def local_media() -> str | None:
    """The film on this host, or None when only the completed runs are present."""
    for candidate in (
        Path("/tmp/sightread-media/nighttide.mp4"),
        ROOT / "media" / "nighttide.mp4",
    ):
        if candidate.is_file():
            return str(candidate)
    return None

_runs: dict[str, dict] = {}
_lock = threading.Lock()


def _report(slug: str) -> dict:
    path = OUT / slug / "report.json"
    if not path.is_file():
        raise HTTPException(404, f"no report for {slug}")
    return json.loads(path.read_text())


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB / "index.html").read_text())


@app.get("/api/runs")
def runs() -> JSONResponse:
    """Every shipped run with its report, so the page needs no second request."""
    out = []
    for entry in SHIPPED:
        path = OUT / entry["slug"] / "report.json"
        if not path.is_file():
            continue
        out.append({**entry, "report": json.loads(path.read_text())})
    if not out:
        raise HTTPException(
            503,
            "No conform reports on disk. Run: python run_conform.py <film> "
            "--min-gap-s 4.0 --report out/<slug>/report.json",
        )
    media = local_media()
    return JSONResponse(
        {
            "runs": out,
            "model_available": model_available(),
            "live": {
                "available": bool(media) and model_available(),
                "reason": _live_reason(media),
                "source": SOURCE,
            },
        }
    )


def _live_reason(media: str | None) -> str:
    if not media:
        return (
            "This instance serves completed runs only. It does not carry the 354MB "
            "source film, so there is nothing here to measure live. Every figure on "
            "this page came from the runs below, and the command opposite reproduces "
            "them from the same public file."
        )
    if not model_available():
        return (
            "The film is present but no Gemini configuration is. Set "
            "GOOGLE_CLOUD_PROJECT for Vertex AI, or GOOGLE_API_KEY for the Gemini API."
        )
    return "Ready. A live run measures the film, plans coverage, conforms and re-verifies."


@app.get("/api/rate")
def rate() -> JSONResponse:
    """The measured speaking rate distribution behind the character budget."""
    from ad.rate import observed_rates, summarise

    rates: list[float] = []
    for entry in SHIPPED:
        path = OUT / entry["slug"] / "report.json"
        if path.is_file():
            rates.extend(observed_rates(json.loads(path.read_text())))
    return JSONResponse({"summary": summarise(rates), "rates": sorted(rates)})


#: The graph runs on disk. The tight one is first because it is the one where the
#: shortening cycle earns its place: at a 2 second threshold the silences are short
#: enough that ten of twenty five first takes overran, so the run goes round the
#: cycle twice and nine silences are refused outright as too short to hold a line.
#: The 4 second run is the clean pass, and a page that only ever showed a clean
#: pass would be showing the easy half of the product.
ADK_RUNS = ("adk-nighttide-tight", "adk-nighttide")


@app.get("/api/adk")
def adk(run: str | None = None) -> JSONResponse:
    """One full ADK graph run: the path it took, and what verify found at the end.

    The model's selections matter only next to the verify block. A model that
    picked well and a model that picked badly both produce a list of indices; only
    an independent re-measurement of the rendered files says whether what came out
    the other end holds up. The steps are returned verbatim, including the route
    each branch took, so the graph the page draws is the graph that ran.
    """
    available = [slug for slug in ADK_RUNS if (OUT / slug / "run.json").is_file()]
    if not available:
        raise HTTPException(404, "no ADK run on disk; run: python agent/pipeline.py <film>")
    slug = run or available[0]
    if slug not in available:
        raise HTTPException(404, f"no ADK run {slug}; have {available}")

    doc = json.loads((OUT / slug / "run.json").read_text())
    report = doc.get("report") or {}
    return JSONResponse(
        {
            "slug": slug,
            "available": available,
            "run_id": doc.get("run_id"),
            "model": doc.get("model", ""),
            "steps": doc.get("steps", []),
            "plan": doc.get("plan", {}),
            "rounds": doc.get("rounds", 0),
            "verdict": doc.get("verdict", {}),
            "escalation": doc.get("escalation", {}),
            "verified": report.get("verified", {}),
            "totals": report.get("totals", {}),
            "settings": report.get("settings", {}),
            "cues": [
                {k: c[k] for k in ("gap_index", "gap_duration_s", "text", "chars",
                                   "rendered_duration_s", "margin_ms", "verdict",
                                   "attempts", "attempt_log")}
                for c in report.get("cues", [])
            ],
        }
    )


@app.get("/api/bob")
def bob() -> JSONResponse:
    """What IBM Bob authored, read back out of the ACP transcripts on disk."""
    sessions = []
    for path in sorted((ROOT / "bob" / "transcripts").glob("*.json")):
        doc = json.loads(path.read_text())
        wrote: list[str] = []
        for event in doc.get("events", []):
            update = event.get("params", {}).get("update", {})
            if update.get("sessionUpdate") != "tool_call":
                continue
            raw = update.get("rawInput") or {}
            target = raw.get("path") or raw.get("file_path") or ""
            title = update.get("title") or ""
            if target and target not in wrote and ("Writ" in title or "diff" in title):
                wrote.append(target)
            elif not target and ("Writ" in title or "diff" in title):
                wrote.append(title)
        sessions.append(
            {
                "task": path.stem,
                "transcript": path.name,
                "agent": doc.get("agentInfo", {}),
                "sessionId": doc.get("sessionId"),
                "toolCalls": doc.get("toolCalls", []),
                "wrote": wrote,
                "events": len(doc.get("events", [])),
            }
        )
    return JSONResponse({"sessions": sessions})


@app.get("/audio/{slug}/{name}")
def audio(slug: str, name: str) -> FileResponse:
    """One rendered take, so the margin can be listened to rather than believed."""
    if "/" in name or ".." in name or "/" in slug or ".." in slug:
        raise HTTPException(400, "bad path")
    path = OUT / slug / name
    if not path.is_file():
        raise HTTPException(404, f"no take at {slug}/{name}")
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=kind)


@app.post("/api/conform")
def conform(payload: dict) -> JSONResponse:
    """Start a live run over a film already on disk. Returns a job id to poll."""
    media = (payload or {}).get("media_path") or local_media()
    if not media or not Path(media).is_file():
        raise HTTPException(
            503,
            {
                "error": "source_media_unavailable",
                "detail": _live_reason(None),
                "title": SOURCE["title"],
                "source_url": SOURCE["url"],
                "reproduce": SOURCE["reproduce"],
            },
        )
    if not model_available():
        raise HTTPException(
            503,
            {"error": "model_unavailable", "detail": _live_reason(media)},
        )
    job = str(uuid.uuid4())
    slug = f"live-{job[:8]}"

    def progress(steps: list[dict]) -> None:
        with _lock:
            _runs[job] = {**_runs.get(job, {}), "steps": steps, "state": "running"}

    def work() -> None:
        try:
            run = run_pipeline(
                media,
                out_dir=str(OUT / slug),
                min_gap_s=float(payload.get("min_gap_s", 4.0)),
                noise_db=float(payload.get("noise_db", -26.0)),
                on_step=progress,
            )
            (OUT / slug).mkdir(parents=True, exist_ok=True)
            (OUT / slug / "report.json").write_text(json.dumps(run.report, indent=1))
            with _lock:
                _runs[job] = {
                    "state": "done",
                    "slug": slug,
                    "steps": run.steps,
                    "plan": run.plan,
                    "verified": run.verified,
                    "report": run.report,
                    "trace": run.trace,
                }
        except GeminiRequired as exc:
            with _lock:
                _runs[job] = {"state": "failed", "error": str(exc)}
        except Exception as exc:
            with _lock:
                _runs[job] = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}

    with _lock:
        _runs[job] = {"state": "running", "steps": []}
    threading.Thread(target=work, daemon=True).start()
    return JSONResponse({"job": job})


@app.get("/api/conform/{job}")
def conform_status(job: str) -> JSONResponse:
    with _lock:
        state = _runs.get(job)
    if state is None:
        raise HTTPException(404, f"no job {job}")
    return JSONResponse(state)
