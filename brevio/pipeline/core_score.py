"""``brevio-core score`` (preview): the findings of a scan, counted by rule, and what to do next.

Standard library and exported core modules only; it starts no git subprocess of its own.

Until 2026-11-12 this printed a 0-100 score, a letter grade, a verdict line, README badge links and a signed receipt link. The
formula (ten points per error, three per warning) was a constant, not a measure, the badge and receipt pages were not part of
this repository, and the receipt was signed with a key that sits in the source, so the improvement plan (section 8) took them
out. What is left is a count the code can show: findings by rule, then the command that explains the most frequent one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

from brevio.pipeline import core_cli
from brevio.pipeline.code_rules import PR_RULES_122

HELP = "preview: count the findings of a scan by rule and say what to do next"
FORMATS = ("text", "markdown", "json")
GUARD_TITLE = "a learned guard of this repository (`brevio-core learn list`)"    # the guard's id is in the rule column (2026-11-12)


def _triggered_rules(shown: list[str]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for f in shown:
        fields = core_cli.finding_fields(f)
        if fields and fields.get("rule"):
            counts[str(fields["rule"])] += 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"rule": rid, "count": cnt, "title": GUARD_TITLE if rid.startswith("guard:") else PR_RULES_122.get(rid, rid)}
            for rid, cnt in ordered]


def compute_scorecard(paths: list[str] | None = None, diff: str | None = None, root: str = ".") -> dict[str, Any]:
    """Run the deterministic scan and count what it found, by rule. The name is kept for its callers (``qa``, ``shield``, MCP)."""
    named = list(paths or [])
    missing = [p for p in named if not os.path.exists(p)]
    if missing:
        raise core_cli.UsageError(f"no such file or folder: {', '.join(missing)}")
    from_root = not named and diff is None and root != "."     # a scan of ``root`` itself, not of the current folder (2026-11-12)
    files = core_cli.select_lines([root] if from_root else named, diff)
    proj = core_cli.project(files)
    raw = core_cli.scan_surface(files, proj, root="." if from_root else root)    # the file names already start with ``root``
    shown, silenced, baselined = core_cli.triage(raw)
    warnings = sum(1 for f in shown if core_cli.level_of(f, proj) == "warn")
    return {
        "files": len(files),
        "errors": len(shown) - warnings,
        "warnings": warnings,
        "findings": shown,
        "finding_count": len(shown),
        "silenced": len(silenced),
        "baselined": len(baselined),
        "triggered_rules": _triggered_rules(shown),
    }


def score_repository(root: str = ".", diff_base: str | None = None) -> dict[str, Any]:
    """The counts for the repository at ``root`` (``finding_count`` is part of the card)."""
    return compute_scorecard(diff=diff_base, root=root)


def next_step(card: dict[str, Any]) -> str:
    """What to run next: the explanation of the most frequent rule, or a plain statement that nothing was found."""
    rules = card["triggered_rules"]
    if not card["files"]:       # a mistyped --diff base reads no line: "nothing to fix" would be a false reassurance (2026-11-12)
        return "Next: no file was read, so nothing was counted; check the path or the --diff base."
    if not rules:
        return (f"Next: nothing to fix. The {len(core_cli.scanned_rules())} scanned rules found no finding in what was read; "
                "that is not a review of the change.")
    first = str(rules[0]["rule"])
    if not first.startswith("R"):
        return "Next: `brevio-core scan` lists each line; `brevio-core learn list` shows the guard behind a guard hit."
    return (f"Next: `brevio-core explain {first}` says why the most frequent rule exists and how to fix it; "
            "`brevio-core scan` lists each line.")


def _head(card: dict[str, Any]) -> str:
    return (f"{card['finding_count']} finding(s) in {card['files']} file(s): "
            f"{card['errors']} error-level, {card['warnings']} warn-level")


def render_text(card: dict[str, Any], footer: str = "") -> str:
    """The counts as plain text."""
    lines = [f"brevio-core score (preview): {_head(card)}"]
    lines += [f"  [{r['rule']}] {r['count']} finding(s): {r['title']}" for r in card["triggered_rules"]]
    lines.append(next_step(card))
    if footer:
        lines.append(footer)
    return "\n".join(lines) + "\n"


def render_markdown(card: dict[str, Any], footer: str = "") -> str:
    """The counts as Markdown, for a comment or a summary."""
    lines = [f"### brevio-core score (preview): {_head(card)}"]
    if card["triggered_rules"]:
        lines += ["", "| Rule | Findings | What it catches |", "| :--- | :--- | :--- |"]
        lines += [f"| `{r['rule']}` | {r['count']} | {r['title']} |" for r in card["triggered_rules"]]
    lines += ["", next_step(card)]
    if footer:
        lines += ["", footer]
    return "\n".join(lines) + "\n"


def parser(prog: str = "brevio-core score") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog, description=HELP)
    ap.add_argument("paths", nargs="*", help="files or folders to count (default: the current folder)")
    ap.add_argument("--diff", nargs="?", const="", default=None, metavar="BASE",
                    help="count only the lines added against BASE (default origin/main)")
    ap.add_argument("--format", choices=FORMATS, default="text", help="output format (default: text)")
    ap.add_argument("--markdown", action="store_true", help="alias for --format markdown")
    ap.add_argument("--json", action="store_true", help="output the counts as JSON")
    ap.add_argument("--no-upgrade-line", action="store_true", help="leave out the closing Teams line")
    # The flags of 0.9.1, kept hidden so a script that passes them still runs (2026-11-13): see ``_removed_flags_notice``.
    ap.add_argument("--min", type=float, default=None, metavar="N", help=argparse.SUPPRESS)
    ap.add_argument("--badge", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--roast", action="store_true", help=argparse.SUPPRESS)
    return ap


def _removed_flags_notice(args: argparse.Namespace) -> int:
    """Say on standard error that the flags of 0.9.1 no longer do what they did; the exit code ``--min`` asks for.

    No score or badge exists any more, so ``--badge`` and ``--roast`` change nothing. ``--min`` was a threshold on the
    score: it now stops the step (exit 1) when an error-level finding is counted, and the notice names ``scan --fail-on``
    as the gate to move to. The threshold itself is not read."""
    used = [flag for flag, on in (("--min", args.min is not None), ("--badge", args.badge), ("--roast", args.roast)) if on]
    if not used:
        return 0
    gate = ("; --min now stops with exit 1 when an error-level finding is counted, whatever its value"
            if args.min is not None else "")
    verb = "applies" if len(used) == 1 else "apply"
    print(f"brevio-core: {', '.join(used)} no longer {verb}: score prints counts and no score or badge{gate}; "
          "to gate a CI step use `brevio-core scan --fail-on new`", file=sys.stderr)
    return 1 if args.min is not None else 0


def main(argv: list[str] | None = None, prog: str = "brevio-core score") -> int:
    args = parser(prog).parse_args(sys.argv[1:] if argv is None else argv)
    try:
        card = compute_scorecard(args.paths, args.diff)
    except core_cli.UsageError as exc:
        print(f"brevio-core: {exc}", file=sys.stderr)
        return 2
    fmt = "markdown" if args.markdown else ("json" if args.json else args.format)
    gating = _removed_flags_notice(args)
    if fmt == "json":
        print(json.dumps(card, indent=2))
    else:
        footer = core_cli.upgrade_line("score", not args.no_upgrade_line)
        sys.stdout.write(render_markdown(card, footer) if fmt == "markdown" else render_text(card, footer))
    return 1 if gating and card["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
