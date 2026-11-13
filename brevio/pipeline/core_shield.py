"""PR Maintainer Shield: deterministic, zero-token conversational PR verification bot.

Standard library only: handles `@brevio fix`, `@brevio explain`, `@brevio roast`,
`@brevio triage`, and `@brevio heal` GitHub PR comments.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Any

from brevio.pipeline import core_cli, core_score
from brevio.pipeline.code_rules import PR_RULES_122
from brevio.pipeline.code_rules import docs as rule_docs

HELP = "answer a maintainer's pull-request comment (fix, explain, roast, triage, heal) from the command line"
_SHELL_META_RX = re.compile(r"[;|`&]|\$\(")
_CMD_RX = re.compile(r"@brevio\s+([A-Za-z0-9_-]+)(?:\s+([^\r\n]+))?", re.IGNORECASE)


def parse_shield_command(comment_body: str) -> dict[str, str] | None:
    """Parse `@brevio <cmd> [args]` from a PR comment with shell injection defense."""
    if not comment_body or not isinstance(comment_body, str):
        return None
    if _SHELL_META_RX.search(comment_body):
        return None
    match = _CMD_RX.search(comment_body)
    if not match:
        return None
    cmd = match.group(1).lower()
    raw_args = (match.group(2) or "").strip()
    args = "" if cmd in ("fix", "roast", "heal") else raw_args
    return {"command": cmd, "args": args}


def execute_shield_command(comment_body: str, root: str = ".") -> dict[str, Any]:
    """Execute a parsed `@brevio` maintainer shield command."""
    parsed = parse_shield_command(comment_body)
    if parsed is None:
        return {"matched": False, "command": None, "ok": True, "markdown_reply": ""}

    cmd = parsed["command"]
    args = parsed["args"]

    if cmd == "fix":
        try:
            lines = core_cli.select_lines([root], None)
            proj = core_cli.project(lines)
            changes = core_cli.plan_fixes(lines)
            fixed_count = sum(len(r) for _o, _n, r in changes.values())
            if fixed_count > 0:
                core_cli.apply_fixes(changes)
            new_lines = core_cli.select_lines([root], None)
            remaining = core_cli.triage(core_cli.scan(new_lines, proj))[0]

            lines_out = [
                "### 🛡️ Brevio Maintainer Shield — Remediation Report",
                "",
                f"- **Fixes applied:** {fixed_count} across {len(changes)} file(s)",
                f"- **Remaining findings:** {len(remaining)}",
            ]
            if changes:
                lines_out.append("\n#### Modified Files:")
                for rel, (_o, _n, r_ids) in sorted(changes.items()):
                    lines_out.append(f"- `{rel}` (fixed {', '.join(sorted(set(r_ids)))})")
            if remaining:
                lines_out.append("\n#### Remaining Violations:")
                for f in remaining[:10]:
                    lines_out.append(f"- `{f}`")
                if len(remaining) > 10:
                    lines_out.append(f"- *...and {len(remaining) - 10} more finding(s)*")
                lines_out.append("\n<brevio_heal_packet>")
                packet_data = {
                    "fixed_count": fixed_count,
                    "remaining_count": len(remaining),
                    "remaining_findings": remaining,
                }
                lines_out.append(json.dumps(packet_data, indent=2))
                lines_out.append("</brevio_heal_packet>")
            reply = "\n".join(lines_out)
            return {
                "matched": True,
                "command": "fix",
                "ok": True,
                "markdown_reply": reply,
                "fixed_count": fixed_count,
                "remaining_count": len(remaining),
            }
        except Exception as exc:
            return {
                "matched": True,
                "command": "fix",
                "ok": False,
                "markdown_reply": f"### ⚠️ Brevio Maintainer Shield — Fix Error\n\nFailed to apply fixes: {exc}",
                "error": str(exc),
            }

    if cmd == "explain":
        rid = args.upper().strip().lstrip("[").rstrip("]")
        if not rid:
            return {
                "matched": True,
                "command": "explain",
                "ok": False,
                "markdown_reply": "### ⚠️ Missing Rule ID\n\nPlease specify a rule ID to explain, e.g. `@brevio explain R13.1`.",
            }
        if rid in PR_RULES_122:
            card_content = rule_docs.render(rid)
            reply = f"### 🛡️ Brevio Rule Explanation: `{rid}`\n\n{card_content}"
            return {
                "matched": True,
                "command": "explain",
                "ok": True,
                "markdown_reply": reply,
                "rule": rid,
            }
        return {
            "matched": True,
            "command": "explain",
            "ok": False,
            "markdown_reply": f"### ❓ Unknown Rule: `{args}`\n\nRule `{args}` was not found in the documented catalogue.",
            "rule": rid,
        }

    if cmd == "roast":
        try:
            if not hasattr(core_score, "score_repository"):
                def _score_repo(r: str = ".") -> dict[str, Any]:
                    return core_score.compute_scorecard(root=r)
                core_score.score_repository = _score_repo
            res = core_score.score_repository(root)
            reply = core_score.render_markdown(res)
            return {
                "matched": True,
                "command": "roast",
                "ok": True,
                "markdown_reply": reply,
                "scorecard": res,
            }
        except Exception as exc:
            return {
                "matched": True,
                "command": "roast",
                "ok": False,
                "markdown_reply": f"### ⚠️ Brevio Maintainer Shield — Count Error\n\nFailed to count the findings: {exc}",
                "error": str(exc),
            }

    if cmd == "triage":
        rule_or_finding = args.strip()
        baseline_file = os.path.join(root, ".brevio", "baseline.json")
        try:
            lines = core_cli.select_lines([root], None)
            proj = core_cli.project(lines)
            shown, _, _ = core_cli.triage(core_cli.scan(lines, proj), "all")
            target_findings = [f for f in shown if rule_or_finding.upper() in f.upper()] if rule_or_finding else shown

            tools = core_cli.baseline_tools()
            if tools is not None:
                os.makedirs(os.path.join(root, ".brevio"), exist_ok=True)
                new_entries = tools.counts(target_findings, core_cli.finding_fields, core_cli.line_reader())
                old_entries = core_cli._load_baseline(tools) if os.path.exists(baseline_file) else Counter()
                merged = tools.merge(old_entries, new_entries, lambda _p: False)
                with open(baseline_file, "w", encoding="utf-8") as fh:
                    fh.write(tools.render(merged))
                reply = (
                    f"### 🛡️ Brevio Maintainer Shield — Triage Baseline Updated\n\n"
                    f"Recorded {len(target_findings)} finding(s)"
                    + (f" matching `{rule_or_finding}`" if rule_or_finding else "")
                    + f" into `{baseline_file}`. Future PR checks will treat these as baselined debt."
                )
            else:
                reply = (
                    f"### 🛡️ Brevio Maintainer Shield — Triage Guidance\n\n"
                    f"To baseline `{rule_or_finding or 'current findings'}` locally, run:\n"
                    f"```bash\nbrevio-core baseline\ngit commit -am 'chore: update brevio baseline'\n```"
                )
            return {
                "matched": True,
                "command": "triage",
                "ok": True,
                "markdown_reply": reply,
                "triaged": rule_or_finding,
            }
        except Exception as exc:
            return {
                "matched": True,
                "command": "triage",
                "ok": False,
                "markdown_reply": f"### ⚠️ Brevio Maintainer Shield — Triage Error\n\nFailed to triage: {exc}",
                "error": str(exc),
            }

    if cmd == "heal":
        try:
            from brevio.pipeline import core_heal
            heal_res = core_heal.run_heal(root=root)
            if isinstance(heal_res, dict):
                reply = heal_res.get("markdown") or f"### 🛡️ Brevio Maintainer Shield — Autonomous Heal Loop\n\n```json\n{json.dumps(heal_res, indent=2)}\n```"
            else:
                reply = str(heal_res)
            return {
                "matched": True,
                "command": "heal",
                "ok": True,
                "markdown_reply": reply,
                "heal_result": heal_res,
            }
        except Exception as exc:
            reply = f"### 🛡️ Brevio Maintainer Shield — Autonomous Heal\n\nHeal loop encountered an error: {exc}"
            return {
                "matched": True,
                "command": "heal",
                "ok": False,
                "markdown_reply": reply,
                "error": str(exc),
            }

    return {
        "matched": True,
        "command": cmd,
        "ok": False,
        "markdown_reply": (
            f"### ❓ Unrecognized Command: `@{cmd}`\n\n"
            "Supported Maintainer Shield commands:\n"
            "- `@brevio fix`: apply deterministic AST fixes\n"
            "- `@brevio explain <rule_id>`: architectural rationale for a rule\n"
            "- `@brevio roast`: count the findings by rule (preview)\n"
            "- `@brevio triage <rule_id>`: record findings in baseline\n"
            "- `@brevio heal`: run autonomous repair loop"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for brevio-core shield."""
    ap = argparse.ArgumentParser(prog="brevio-core shield", description=HELP)
    ap.add_argument("--comment", default="", help="PR comment body")
    ap.add_argument("--root", default=".", help="project root directory")
    ap.add_argument("--json", action="store_true", help="output result as JSON")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if not args.comment.strip() and not args.json:     # a blank comment matches no command; say what is needed (2026-11-13)
        print('brevio-core shield: needs --comment, for example: brevio-core shield --comment "@brevio explain R9.2"')
        return 0
    res = execute_shield_command(args.comment, root=args.root)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(res.get("markdown_reply", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
