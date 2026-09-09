"""The speaking rate is declared once, and the whole repo is the scope of that.

The character budget was wrong once: the first version assumed 14 characters per
second, roughly subtitle reading speed, and the rendered audio disagreed. Three
real silences overran by 474 ms, 988 ms and 4197 ms before a second measurement
made it visible. The measured median is 8.6.

A wrong constant is cheap to fix in one place and expensive to fix in four,
because the fix silently leaves the wrong value behind wherever it was copied.
This file makes the copy fail rather than the value.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import ad.conform as _conform_mod
import ad.fit as _fit_mod
import agent.graph as graph
import agent.pipeline as pipeline

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The one file allowed to write the number down, and the docstring in it that
#: carries the sample the number came from.
DECLARING_FILE = ROOT / "ad" / "fit.py"

SEARCHED_DIRS = ("ad", "agent")


def _float_literals(path: pathlib.Path) -> list[float]:
    """Every float literal in *path*, excluding docstrings and comments."""
    tree = ast.parse(path.read_text())
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]


def test_the_measured_rate_is_a_literal_in_exactly_one_file() -> None:
    """8.6 may appear as a number only in ad/fit.py.

    Walks the AST rather than grepping, so the sample distribution in the
    target_chars docstring does not count and a copy written as 8.60 does.
    """
    kwdefaults = _fit_mod.target_chars.__kwdefaults__ or {}
    expected = inspect.signature(_fit_mod.target_chars).parameters[
        "chars_per_second"
    ].default
    assert expected == 8.6, f"the declared rate moved to {expected}; update this test"

    offenders: list[str] = []
    for directory in SEARCHED_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if path == DECLARING_FILE:
                continue
            if expected in _float_literals(path):
                offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, f"the speaking rate is copied into {offenders}"
    assert expected in _float_literals(DECLARING_FILE), (
        "ad/fit.py must still be the file that declares it"
    )
    assert kwdefaults.get("chars_per_second") == expected, (
        "the rate must stay a keyword default on target_chars, not move into the body"
    )


def test_every_caller_resolves_the_rate_rather_than_naming_it() -> None:
    """The graph state, the entry point and the conform loop all read one place."""
    assert _conform_mod.default_chars_per_second() == 8.6

    state = graph.ADState(media_path="x.mp4")
    assert state.chars_per_second == 8.6

    assert (
        inspect.signature(pipeline.run_pipeline)
        .parameters["chars_per_second"]
        .default
        is None
    )
    assert (
        inspect.signature(_conform_mod.conform_gap)
        .parameters["chars_per_second"]
        .default
        is None
    )


def test_moving_the_declared_rate_moves_every_caller() -> None:
    """Patch the one declaration and every reader must follow.

    Without this, all the assertions above would still pass if each caller had its
    own hardcoded 8.6, because the value they hold would coincidentally match.
    """
    original = _fit_mod.target_chars

    def slower(gap_duration_s, *, headroom_ms=250, chars_per_second=4.3):
        return original(
            gap_duration_s,
            headroom_ms=headroom_ms,
            chars_per_second=chars_per_second,
        )

    _fit_mod.target_chars = slower
    try:
        assert _conform_mod.default_chars_per_second() == 4.3
        assert graph.ADState(media_path="x.mp4").chars_per_second == 4.3
    finally:
        _fit_mod.target_chars = original

    assert _conform_mod.default_chars_per_second() == 8.6
