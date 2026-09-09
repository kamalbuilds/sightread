"""Sightread CLI: conform audio description into the measured gaps of a real film.

Every number this prints was measured. Gap boundaries come from ffmpeg
silencedetect on the actual audio; rendered line durations come from ffprobe on
the actual WAV that Gemini produced. Nothing is accepted because a model said it
would fit.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from ad.conform import conform, report
from ad.gaps import measure_gaps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("media")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--noise-db", type=float, default=-26.0)
    ap.add_argument("--min-gap-s", type=float, default=1.8)
    ap.add_argument("--start-s", type=float, default=0.0)
    ap.add_argument("--duration-s", type=float, default=None)
    ap.add_argument("--headroom-ms", type=int, default=250)
    # Deliberately no default here. Omitting the flag lets ad.fit's measured
    # default apply, which keeps the rate in exactly one place. A default on this
    # line would be a second copy of a number that has already been wrong once.
    ap.add_argument("--chars-per-second", type=float, default=None)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None, help="conform only the first N gaps")
    ap.add_argument("--report", default=None, help="write the JSON report here")
    ap.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    ap.add_argument("--location", default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
    args = ap.parse_args()

    if not args.project:
        print(
            "GOOGLE_CLOUD_PROJECT is not set and --project was not given.",
            file=sys.stderr,
        )
        return 2

    gaps = measure_gaps(
        args.media,
        noise_db=args.noise_db,
        min_gap_s=args.min_gap_s,
        start_s=args.start_s,
        duration_s=args.duration_s,
    )
    print(f"measured {len(gaps)} gaps at {args.noise_db}dB, min {args.min_gap_s}s", flush=True)
    for gap in gaps:
        print(
            f"  gap {gap.index:3d}  {gap.start_s:9.3f} to {gap.end_s:9.3f}  "
            f"{gap.duration_s:6.3f}s",
            flush=True,
        )

    if args.limit is not None:
        gaps = gaps[: args.limit]
    if not gaps:
        print("no gaps to conform", flush=True)
        return 1

    cues, skipped = conform(
        args.media,
        gaps,
        args.out_dir,
        project=args.project,
        location=args.location,
        headroom_ms=args.headroom_ms,
        chars_per_second=args.chars_per_second,
        max_attempts=args.max_attempts,
    )

    print("", flush=True)
    for cue in cues:
        print(
            f"gap {cue.gap_index:3d}  {cue.gap_duration_s:6.3f}s gap  "
            f"budget {cue.char_budget:3d}  {cue.chars:3d} chars  "
            f"rendered {cue.rendered_duration_s:6.3f}s  "
            f"margin {cue.margin_ms:+6d}ms  {cue.verdict}  "
            f"({cue.attempts} attempt{'s' if cue.attempts != 1 else ''})",
            flush=True,
        )
        print(f"        {cue.text}", flush=True)

    doc = report(
        args.media,
        cues,
        noise_db=args.noise_db,
        min_gap_s=args.min_gap_s,
        headroom_ms=args.headroom_ms,
        skipped=skipped,
    )
    print("", flush=True)
    print(json.dumps(doc["totals"], indent=1), flush=True)

    if args.report:
        out = pathlib.Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=1))
        print(f"report written to {out}", flush=True)

    return 0 if doc["totals"]["overflow"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
