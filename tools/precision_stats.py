"""The statistics and the reviewer checks of the precision run: standard library only, shipped with ``precision_run.py``.

The interval beside each count is the Wilson score interval for a share, at 95% (z = 1.96), on the hits judged for the rule.
It is named wherever it is printed, so a reader can recompute it from the two counts the page also prints.
"""
from __future__ import annotations

import math
import re

Z = 1.96
INTERVAL_NAME = "Wilson score interval, 95%"
VERDICTS = ("tp", "fp")


def wilson(real: int, judged: int) -> tuple[float, float] | None:
    """The Wilson score interval ``(low, high)`` of ``real`` out of ``judged``; ``None`` when nothing was judged."""
    if judged <= 0:
        return None
    p, z2 = real / judged, Z * Z
    denom = 1 + z2 / judged
    centre = (p + z2 / (2 * judged)) / denom
    half = Z * math.sqrt(p * (1 - p) / judged + z2 / (4 * judged * judged)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def interval_text(real: int, judged: int) -> str:
    """The interval as two shares with two decimals ("0.84 to 1.00"), or an empty string when nothing was judged. A share
    is not printed as a percentage: a bare 100% reads as a promise."""
    got = wilson(real, judged)
    return "" if got is None else f"{got[0]:.2f} to {got[1]:.2f}"


MAX_NAME = 80
_NAME_BAD = re.compile(r"[\x00-\x1f\x7f|<>\[\]`\\]")      # a name is printed in a page and a table: no line break, no markup


def named(entry: dict) -> str:
    """The reviewer a row names, or an empty string when it names none or the name could not be printed safely (a line break,
    markup, or more than ``MAX_NAME`` characters). A sample file is edited by hand, and its names reach the pages."""
    name = str(entry.get("reviewer") or "").strip()
    return "" if len(name) > MAX_NAME or _NAME_BAD.search(name) else name


def valid_name(name: str) -> bool:
    """Whether ``name`` may be given with ``--reviewer``: not empty, and one ``named`` accepts."""
    return bool(name.strip()) and bool(named({"reviewer": name}))


def reviewers(sample: list[dict]) -> list[str]:
    """The distinct, non-empty reviewer names the judged rows of ``sample`` carry, sorted."""
    return sorted({named(e) for e in sample if e.get("verdict") in VERDICTS and named(e)})


def stamp(sample: list[dict], reviewer: str) -> int:
    """Set ``reviewer`` on each judged row that names none; returns how many rows changed. A row without a verdict is left."""
    name, changed = reviewer.strip(), 0
    for e in sample:
        if valid_name(name) and e.get("verdict") in VERDICTS and not named(e):
            e["reviewer"] = name
            changed += 1
    return changed


def publish_problems(sample: list[dict]) -> list[str]:
    """Why a figure drawn from ``sample`` may not be published: a sampled hit with no verdict, a verdict that is not ``tp`` or
    ``fp``, or a verdict with no reviewer."""
    unknown = sum(bool(e.get("verdict")) and e.get("verdict") not in VERDICTS for e in sample)
    unjudged = sum(e.get("verdict") not in VERDICTS for e in sample) - unknown
    unnamed = sum(e.get("verdict") in VERDICTS and not named(e) for e in sample)
    out = []
    if not sample:
        out.append("the sample is empty: there is nothing to publish")
    if unjudged:
        out.append(f"{unjudged} sampled hit(s) have no verdict")
    if unknown:
        out.append(f"{unknown} hit(s) have a verdict that is not tp or fp (write it in lower case)")
    if unnamed:
        out.append(f"{unnamed} judged hit(s) name no reviewer, or a name with a line break, markup or over {MAX_NAME} characters "
                   "(fill `reviewer` in the sample file, or pass --reviewer NAME)")
    return out


def size_problems(sample: list[dict], hits_by_rule: dict[str, int], per_rule: int) -> list[str]:
    """Why ``sample`` is not the sample the run drew: a rule with fewer or more rows than ``min(per_rule, hits)``, a row of a rule
    the run did not scan, or a repeated ``id``. A figure from a sample with rows taken out says nothing about the hits."""
    out = []
    counts: dict[str, int] = {}
    for e in sample:
        counts[e["rule"]] = counts.get(e["rule"], 0) + 1
    for rule in sorted(set(counts) | set(hits_by_rule)):
        want = min(per_rule, hits_by_rule.get(rule, 0))
        if counts.get(rule, 0) != want:
            out.append(f"{rule} has {counts.get(rule, 0)} sampled row(s), the run drew {want}: do not add or remove rows")
    if len({e["id"] for e in sample}) != len(sample):
        out.append("a sampled hit appears twice")
    return out
