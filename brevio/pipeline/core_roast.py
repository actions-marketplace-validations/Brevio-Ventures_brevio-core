"""``brevio-core roast``: the same counts as ``score``, under the name an earlier build gave it (2026-11-12, improvement plan).

It prints no verdict line and no grade, only what ``score`` prints. It is a module of its own so that a trimmed release that leaves the
name out leaves out this file and nothing else.
"""
from __future__ import annotations

from brevio.pipeline import core_score

HELP = "preview: the same counts as score, under its earlier name"


def main(argv: list[str], prog: str = "brevio-core roast") -> int:
    return core_score.main(argv, prog=prog)
