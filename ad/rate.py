"""Measures observed speaking rates from conform report data so the
chars_per_second default in ad.fit can be checked against real renders rather
than trusted as a fixed constant. Feed it one or more JSON report files produced
by ad.conform.report to get a current distribution. It does not re-render
anything and it does not call any model.
"""

from __future__ import annotations

import json
import statistics
import sys


def observed_rates(report: dict) -> list[float]:
    """Return chars/rendered_duration_s for every attempt in every cue.

    Attempts whose rendered_duration_s is zero are skipped; dividing by zero
    would mean the TTS engine returned silence, which is not a speaking rate.
    """
    rates: list[float] = []
    for cue in report.get("cues", []):
        for entry in cue.get("attempt_log", []):
            duration = entry.get("rendered_duration_s", 0.0)
            chars = entry.get("chars", 0)
            if duration > 0:
                rates.append(chars / duration)
    return rates


def summarise(rates: list[float]) -> dict:
    """Return summary statistics for a list of speaking rates.

    Returns {"n": 0, "min": None, "median": None, "max": None, "mean": None}
    for an empty list. All floats are rounded to 2 decimal places.
    """
    if not rates:
        return {"n": 0, "min": None, "median": None, "max": None, "mean": None}
    return {
        "n": len(rates),
        "min": round(min(rates), 2),
        "median": round(statistics.median(rates), 2),
        "max": round(max(rates), 2),
        "mean": round(statistics.mean(rates), 2),
    }


def main() -> int:
    """Pool observed rates from one or more report JSON paths and print a summary.

    Usage: python -m ad.rate report1.json [report2.json ...]

    This is the procedure for re-deriving the chars_per_second default in
    ad.fit.target_chars when new render data is available.
    """
    if len(sys.argv) < 2:
        print("usage: python -m ad.rate report.json [report2.json ...]", file=sys.stderr)
        return 2

    all_rates: list[float] = []
    for path in sys.argv[1:]:
        with open(path) as fh:
            report = json.load(fh)
        all_rates.extend(observed_rates(report))

    summary = summarise(all_rates)
    print(json.dumps(summary, indent=2))

    sorted_rates = sorted(all_rates)
    print("rates:", sorted_rates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
