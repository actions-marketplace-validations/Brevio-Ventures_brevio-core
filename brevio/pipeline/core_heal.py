"""Autonomous self-healing loop: deterministic auto-fixes, gate checks, defect guards, and heal packets (ROADMAP E42).

Standard library and existing brevio pipeline modules only. No model calls, no network, no vendor SDKs.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from collections.abc import Generator
from typing import Any

from brevio.pipeline import core_cli, core_memory
from brevio.pipeline.code_rules import details, fixers, hints
from brevio.pipeline.gates import core_gates, defect_compiler

HELP = "scan, apply the deterministic fixes, and print what still needs a person (exit 1 while anything remains)"
EXIT_OK = 0
EXIT_BLOCK = 1
MAX_HEAL_PACKET_CHARS = 6000
_FINDING_LINE_RE = re.compile(r"^([^:\n]+?):(\d+):\s*(?:\[(R\d+\.\d+)\])?\s*(.*)")
_STATE_ERRORS = (OSError, ValueError, RuntimeError)   # unreadable, unwritable or corrupt state files (a corrupt store is a RuntimeError)


@contextlib.contextmanager
def _in_directory(target_dir: str) -> Generator[str, None, None]:
    """Change working directory temporarily within context manager."""
    orig = os.getcwd()
    abs_target = os.path.abspath(target_dir)
    try:
        os.chdir(abs_target)
        yield abs_target
    finally:
        os.chdir(orig)


_OWN_DIRS = (".brevio", ".agents")
_VERIFY_OFF_RE = re.compile(r"verify\s*=\s*False")
_COMMENT_START = ("#", "//", "/*", "<!--")


def _inside(root: str, rel_path: str) -> str:
    """The real path of ``rel_path`` when it is a file inside ``root``; "" for a path that leaves it, or is not a file."""
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, rel_path))
    if os.path.commonpath([base, full]) != base or not os.path.isfile(full):
        return ""
    return full


def _read_line(full_path: str, lno: int) -> str:
    """Line ``lno`` of the file without its line end; "" when the file or the line is missing."""
    try:
        with open(full_path, encoding="utf-8", errors="replace") as fh:
            for cur_idx, line in enumerate(fh, 1):
                if cur_idx == lno:
                    return line.rstrip("\r\n")
    except OSError:
        return ""
    return ""


def _read_numbered(full_path: str) -> list[tuple[int, str]]:
    """The numbered lines of the file; empty when it cannot be read."""
    try:
        with open(full_path, encoding="utf-8", errors="replace") as fh:
            return [(n, line.rstrip("\r\n")) for n, line in enumerate(fh, 1)]
    except OSError:
        return []


def _verified_line(rel_path: str, rule_id: str, target: str, numbered: list[tuple[int, str]] | None = None,
                   lno: int = 1) -> str:
    """The one-line rewrite of ``target`` that fixes ``rule_id``, or "" when no rewrite passes the checks.

    A rewrite is kept only when the rule's own fixer made it, the scan no longer reports the rule on it, it adds no other
    finding, it is still one line, it is not a comment, and it does not leave certificate checks off (2026-11-13). A bare
    ``except:`` whose handler only passes gets no rewrite: the handler would still be a finding (2026-11-13)."""
    ext = os.path.splitext(rel_path)[1].lower()
    fixer = fixers.FIXERS.get(rule_id)
    if fixer is None or ext not in fixer.suffixes or not target.strip():
        return ""
    if rule_id == "R5.4" and rule_id not in fixers.fix_line(rel_path, target, numbered, lno)[1]:
        return ""
    cand = fixer(target)
    if cand == target or "\n" in cand or "\r" in cand or not cand.strip():
        return ""
    if cand.lstrip().startswith(_COMMENT_START) or _VERIFY_OFF_RE.search(cand):
        return ""
    before = fixers.rules_on(rel_path, target)
    after = fixers.rules_on(rel_path, cand)
    return cand if rule_id not in after and after <= before else ""


def _surgical_edit_for_finding(finding: str, root: str = ".") -> str:
    """A <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE block for one finding, or "" when no verified fix exists.

    Only a finding with a real rule id, a file inside the project and a fix the scan confirms gets a block. Before
    2026-11-13 each finding got one, and some replaced the code with comments or with an example in another language."""
    m = _FINDING_LINE_RE.match(finding)
    if not m or not m.group(3):
        return ""
    rel_path, lno, rule_id = m.group(1).strip(), int(m.group(2)), m.group(3)
    full_path = _inside(root, rel_path)
    target = _read_line(full_path, lno) if full_path else ""
    replacement = _verified_line(rel_path, rule_id, target, _read_numbered(full_path), lno) if target else ""
    if not replacement:
        return ""
    return (
        f"#### File: {rel_path}:{lno} [{rule_id}]\n"
        f"<<<<<<< SEARCH\n"
        f"{target}\n"
        f"=======\n"
        f"{replacement}\n"
        f">>>>>>> REPLACE\n"
    )


def _rationale_lines(remaining_findings: list[str]) -> list[str]:
    """One line of why per rule found, and a good example for each kind of finding and language it was found in.

    The example comes from ``hints.example`` (rule, path, message), the one source the hook and ``explain`` use, so a Python
    finding never gets a TypeScript example (2026-11-13, merge of the hint and heal work). The bad example is shown only for
    a rule with one kind of finding, and only in the language of the files it was found in."""
    found: dict[str, list[tuple[str, str]]] = {}
    for f in remaining_findings:
        m = _FINDING_LINE_RE.match(f)
        if m and m.group(3):
            found.setdefault(m.group(3), []).append((m.group(1).strip(), m.group(4)))
    out: list[str] = []
    for rid in sorted(found):
        detail = details.DETAILS.get(rid)
        if not detail:
            continue
        out.append(f"- [{rid}]: {detail.why}")
        goods = list(dict.fromkeys(hints.fix_text(rid, path, msg) for path, msg in found[rid]))
        goods = [g for g in goods if g]
        one_kind = not any(v.rule == rid for v in hints.VARIANTS)
        if detail.bad and goods and one_kind:
            out.append(f"  Bad: {detail.bad.splitlines()[0]}")
        out += [f"  Good: {g}" for g in goods]
    return out


def _fit(head: list[str], optional: list[list[str]], tail: list[str], max_chars: int) -> str:
    """Join ``head``, as many whole ``optional`` blocks as fit in ``max_chars``, and ``tail``: a block is kept whole or left out."""
    kept = list(optional)
    while True:
        text = "\n".join(head + [line for blk in kept for line in blk] + tail)
        if len(text) <= max_chars or not kept:
            return text
        kept.pop()


def _synthesize_heal_packet(
    remaining_findings: list[str],
    gate_failures: list[str],
    implicated_files: list[str],
    brief: dict[str, Any],
    max_chars: int = MAX_HEAL_PACKET_CHARS,
    root: str = ".",
    applied: list[str] | None = None,
) -> str:
    """Synthesize compact, self-contained <brevio_heal_packet>.

    It holds the findings, the gate failures, what was fixed, the why of each rule, and a SEARCH/REPLACE block only for a
    fix the scan confirms. Blocks that do not fit ``max_chars`` are left out whole."""
    edits = [b for b in (_surgical_edit_for_finding(f, root=root) for f in remaining_findings) if b]
    instruction = ("Apply the verified edits below directly, then repair the other findings by hand."
                   if edits else "No automatic edit is verified for these findings: repair each one by hand.")
    head: list[str] = [
        "<brevio_heal_packet>",
        "### Brevio Autonomous Self-Healing Packet",
        f"Target: resolve {len(remaining_findings)} finding(s) and {len(gate_failures)} gate failure(s).",
        f"Instructions: {instruction} Do NOT run noisy full-repo test suites.",
        "",
    ]
    tail = ["</brevio_heal_packet>"]
    shown = len(remaining_findings)
    while True:
        cur = list(head)
        if applied:
            cur += [f"#### Fixes already applied ({len(applied)}):"] + [f"- {a}" for a in applied] + [""]
        if remaining_findings:
            cur += ["#### Remaining Findings:"] + [f"- {f}" for f in remaining_findings[:shown]]
            if shown < len(remaining_findings):
                cur.append(f"- ... and {len(remaining_findings) - shown} more: run `brevio-core scan` for the list")
            cur.append("")
        if gate_failures:
            cur += ["#### Gate Failures:"] + [f"- {gf}" for gf in gate_failures] + [""]
        if shown == 0 or len("\n".join(cur + tail)) <= max_chars:
            break
        shown -= 1
    head = cur

    context = ["#### 1-Hop Architectural Context:",
               f"- Implicated files: {', '.join(implicated_files) if implicated_files else 'none'}"]
    neighbors = brief.get("neighbors", [])
    if neighbors:
        context.append(f"- 1-Hop neighbors: {', '.join(neighbors[:6])}")
    episodes = brief.get("episodes", [])
    if episodes:
        context.append("- Recalled traps & fixes:")
        context += [f"  • {ep.get('rule_id')}: {ep.get('trap')} -> {ep.get('fix')}" for ep in episodes[:4]]
    guards = brief.get("guards", [])
    if guards:
        context.append(f"- Active zero-token guards: {len(guards)} registered")
    context.append("")

    rationale = _rationale_lines(remaining_findings)
    optional: list[list[str]] = [context]
    if rationale:
        optional.append(["#### Rule Rationales:"] + rationale + [""])
    if edits:
        optional.append(["#### Surgical Edits:", edits[0]])
        optional += [[b] for b in edits[1:]]
    return _fit(head, optional, tail, max_chars)


def _compile_guards(remaining_findings: list[str], diff_base: str | None) -> list[str]:
    """Compile the diff into a guard and register one per remaining rule; return what could not be done, in words.

    A corrupt or unwritable guards file used to vanish into a bare ``except`` (2026-11-11): now it is reported."""
    notes: list[str] = []
    try:
        data = defect_compiler.load_guards(cwd=".")
    except _STATE_ERRORS as exc:
        return [f"guards skipped: could not read the guards file ({type(exc).__name__}: {exc})"]
    if not isinstance(data, dict) or not isinstance(data.get("guards", {}), dict):
        return ["guards skipped: the guards file is not in the expected shape; fix or remove .brevio/guards.json"]
    # The tool's own outputs are not a fix to learn from: a plan or an agent file would become a guard (2026-11-13).
    diff_text = core_gates._git(".", "diff", "-U1", diff_base or "HEAD", "--", ".", *(f":(exclude){d}" for d in _OWN_DIRS))
    try:
        if diff_text:
            defect_compiler.compile_guard_from_diff(diff_text, cwd=".")
        data = defect_compiler.load_guards(cwd=".")
        guards = data.setdefault("guards", {})
        for finding in remaining_findings:
            m = re.search(r"\[(R\d+\.\d+)\]\s*(.*)", finding)
            gid = f"guard-{m.group(1).lower().replace('.', '-')}" if m else ""
            if m and gid not in guards:
                guards[gid] = {
                    "id": gid,
                    "title": f"Prevent recurrence of {m.group(1)}",
                    "rule_id": m.group(1),
                    "description": m.group(2).strip()[:60],
                    "status": "active",
                    "created_at": defect_compiler._now_iso(),
                }
        defect_compiler.save_guards(data, cwd=".")
    except _STATE_ERRORS as exc:
        notes.append(f"guards not written: {type(exc).__name__}: {exc}")
    return notes


def _applied_fixes(changes: dict[str, tuple[str, str, list[str]]]) -> list[str]:
    """One ``file:line RULE`` entry for each line a deterministic fix rewrote, in file and line order."""
    out: list[str] = []
    for rel, (old, new, _rules) in sorted(changes.items()):
        for lno, (before, after) in enumerate(zip(old.splitlines(), new.splitlines(), strict=True), 1):
            if before != after:
                out.append(f"{rel}:{lno} {', '.join(fixers.fix_line(rel, before)[1])}")
    return out


def _scan_and_check(diff_base: str | None) -> tuple[list[str], list[str]]:
    """The rule findings and the gate failures of the selected lines (the uncommitted changes, or all files with no base)."""
    lines = core_cli.select_lines([], diff_base)
    gate_res = core_gates.run_check(root=".", base=diff_base or "HEAD", lines=lines)
    checks = gate_res.get("checks", {})
    rule_findings = list(checks.get("rules", []))
    try:
        triaged_rules, _, _ = core_cli.triage(rule_findings, root=".")
    except TypeError:
        triaged_rules, _, _ = core_cli.triage(rule_findings)
    gate_failures: list[str] = []
    for k in ("conflict_markers", "test_custody", "test_imports", "complexity", "orphan_files", "agent_files", "env_parity"):
        gate_failures.extend(checks.get(k, []))
    return sorted(set(triaged_rules)), sorted(set(gate_failures))


def run_heal(
    root: str = ".",
    diff_base: str | None = None,
    apply_fixes: bool = True,
    compile_guards: bool = True,
    max_chars: int = MAX_HEAL_PACKET_CHARS,
    findings: list[str] | None = None,
) -> dict[str, Any]:
    """Run autonomous self-healing loop: auto-fixes, scan/gate checks, guards compilation, and packet synthesis.

    ``findings`` is the selection of the caller. The stop hook passes the findings it reports, so the packet covers those
    and no older ones in the other files (2026-11-13: the hook said 1 finding and the packet 5)."""
    with _in_directory(root):
        auto_fixed_files: list[str] = []
        auto_fixed_count = 0
        applied: list[str] = []

        # Step 1 (Deterministic Auto-Fix)
        if apply_fixes:
            lines = core_cli.select_lines([], diff_base)
            changes = core_cli.plan_fixes(lines)
            if changes:
                applied = _applied_fixes(changes)
                core_cli.apply_fixes(changes)
                auto_fixed_files = sorted(changes.keys())
                auto_fixed_count = sum(len(r) for _o, _n, r in changes.values())

        # Step 2 (Scan & Pre-PR Gate Check); with ``findings`` the caller has made the selection (the stop hook) and it stands
        if findings is None:
            remaining_findings, gate_failures = _scan_and_check(diff_base)
        else:
            remaining_findings, gate_failures = list(findings), []

        # Step 3 (Defect Compilation & Guard Synthesis)
        notes: list[str] = []
        if compile_guards and (remaining_findings or gate_failures):
            notes += _compile_guards(remaining_findings, diff_base)

        # Step 4 (1-Hop Context & Surgical <brevio_heal_packet> Synthesis)
        implicated_set: set[str] = set()
        for item in remaining_findings + gate_failures:
            m = re.match(r"^([^:\n]+?):(?:\d+):", item)
            if m:
                cand = m.group(1).strip()
                if os.path.exists(cand):
                    implicated_set.add(cand)
            else:
                parts = item.split(":", 1)
                if parts and os.path.exists(parts[0].strip()):
                    implicated_set.add(parts[0].strip())
        implicated_files = sorted(implicated_set)

        brief: dict[str, Any] = {}
        try:
            brief = core_memory.brief_for_files(implicated_files, root=".")
        except _STATE_ERRORS as exc:
            notes.append(f"memory brief skipped: {type(exc).__name__}: {exc}")

        ok = len(remaining_findings) == 0 and len(gate_failures) == 0
        status = "clean" if ok else "needs_repair"

        heal_packet_str = ""
        if not ok:
            heal_packet_str = _synthesize_heal_packet(
                remaining_findings=remaining_findings,
                gate_failures=gate_failures,
                implicated_files=implicated_files,
                brief=brief,
                max_chars=max_chars,
                root=".",
                applied=applied,
            )

        return {
            "ok": ok,
            "verified": True,       # each edit in the packet was re-checked by the scan before it was put there; the stop hook reads this
            "status": status,
            "auto_fixed_files": auto_fixed_files,
            "auto_fixed_count": auto_fixed_count,
            "auto_fixed": applied,
            "remaining_findings": remaining_findings,
            "gate_failures": gate_failures,
            "implicated_files": implicated_files,
            "heal_packet": heal_packet_str,
            "warnings": notes,
        }


def format_heal_text(report: dict[str, Any]) -> str:
    """Format self-heal report for terminal output; a line first says which files were rewritten, and where."""
    notes = "".join(f"\nnote: {w}" for w in report.get("warnings", []))
    applied = report.get("auto_fixed", [])
    files = report.get("auto_fixed_files", [])
    said = ""
    if applied:
        said = f"Applied {len(applied)} deterministic fix(es) in {len(files)} file(s): " + "; ".join(applied) + "\n"
    if report.get("ok"):
        count = report.get("auto_fixed_count", 0)
        if count > 0:
            return said + f"✅ Self-heal complete: 0 remaining findings ({count} auto-fixed in {len(files)} file(s))." + notes
        return "✅ Self-heal complete: 0 remaining findings." + notes
    return said + str(report.get("heal_packet", "")) + notes


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for brevio-core heal."""
    parser = argparse.ArgumentParser(prog="brevio-core heal", description="Autonomous self-healing loop: rewrites files with deterministic fixes (--no-fix only reports), then prints a repair packet")
    parser.add_argument("--root", default=".", help="project root directory")
    parser.add_argument("--diff", default=None, help="diff base commit or branch")
    parser.add_argument("--no-fix", action="store_true", help="only report: without it heal rewrites files with the rules' deterministic fixes and lists the lines it changed")
    parser.add_argument("--no-compile-guards", action="store_true", help="skip compiling defect guards")
    parser.add_argument("--json", action="store_true", help="output report as JSON")
    parser.add_argument("--format", choices=("text", "json"), default=None, help="output format")
    parser.add_argument("--max-chars", type=int, default=MAX_HEAL_PACKET_CHARS, help="max chars for heal packet")
    args = parser.parse_args(argv)

    report = run_heal(
        root=args.root,
        diff_base=args.diff,
        apply_fixes=not args.no_fix,
        compile_guards=not args.no_compile_guards,
        max_chars=args.max_chars,
    )

    fmt = "json" if (args.json or args.format == "json") else "text"
    if fmt == "json":
        print(json.dumps(report, indent=2))
    else:
        print(format_heal_text(report))

    return EXIT_OK if report.get("ok") else EXIT_BLOCK


if __name__ == "__main__":
    sys.exit(main())
