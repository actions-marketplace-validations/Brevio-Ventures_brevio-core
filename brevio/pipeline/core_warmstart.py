"""The warm start of ``brevio-core init --wizard``: guards from the last fix commits, shown before anything is written.

Until 2026-11-12 the wizard wrote a trial guard for each recent commit whose subject said fix, bug, revert or hotfix, and
nobody had looked at them. Now (improvement plan, build 1c and decision L5) a candidate goes through the control run
and the replay that ``brevio-core learn`` uses (``gates.guard_proof``). The wizard prints which guards it would keep and
which it drops, and why, and writes the kept ones only after a person answers yes. Without a terminal the answer is
read from standard input, and no answer is a no. Standard library only.
"""
from __future__ import annotations

import re
import sys
from typing import Any, NamedTuple

from brevio.pipeline import core_platform
from brevio.pipeline.gates import defect_compiler, guard_proof

FIX_COMMIT = re.compile(r"\b(?:fix|bug|revert|hotfix)\b", re.I)
WARM_COMMITS = 10       # the recent commits the warm start looks at for fixes
_DIFF_MARK = "---BREVIO_DIFF---"


class Candidate(NamedTuple):
    commit: str
    subject: str
    diff: str
    guard: dict[str, Any] | None
    reasons: list[str]          # why it is dropped; empty: kept
    replay: dict[str, Any]


def _show(root: str, ref: str) -> tuple[str, str, str] | None:
    """``(commit, subject, diff)`` of ``ref``, or ``None`` when there is no such commit."""
    code, out = guard_proof.git(root, "show", f"--format=%H%x00%s%n{_DIFF_MARK}", ref)
    head, sep, diff = out.partition(f"\n{_DIFF_MARK}\n")
    commit, _, subject = head.partition("\x00")
    return (commit.strip(), subject.strip(), diff) if code == 0 and sep else None


def candidates(root: str, limit: int = WARM_COMMITS, commits: int = guard_proof.REPLAY_COMMITS) -> list[Candidate]:
    """What the recent fix commits would make, checked; reads the repository and writes nothing."""
    have = set(defect_compiler.guard_table(defect_compiler.load_guards(root)))
    out: list[Candidate] = []
    for idx in range(max(1, limit)):
        shown = _show(root, "HEAD" if idx == 0 else f"HEAD~{idx}")
        if shown is None:
            break
        commit, subject, diff = shown
        if not FIX_COMMIT.search(subject):
            continue
        guard = defect_compiler.plan_guard(diff, name=subject[:60], cwd=root)
        if guard is None:
            out.append(Candidate(commit, subject, diff, None, ["no line the fix removed can be kept as a pattern"], {}))
            continue
        reasons = guard_proof.control_run(guard, diff, guard_proof.fixed_side(guard["source_file"], commit, root))
        if guard["id"] in have:
            reasons.append(f"a guard {guard['id']} exists already")
        have.add(guard["id"])
        out.append(Candidate(commit, subject, diff, guard, reasons, guard_proof.replay(guard, root, commits)))
    return out


def report(found: list[Candidate]) -> list[str]:
    """The list a person reads: a line per candidate, kept or dropped, and why."""
    lines = []
    for c in found:
        where = f"{c.commit[:7]} {guard_proof.printable(c.subject)}"
        if c.reasons:
            lines.append(f"  drop  {where}: {'; '.join(c.reasons)}")
        elif c.guard is not None:
            summary = guard_proof.describe(c.replay)[0]
            lines.append(f"  keep  {where} -> {c.guard['id']} (control run passed; {summary})")
    return lines


def _ask(question: str) -> bool:
    """A yes from a person: typed at a terminal, or the next line of standard input; anything else, and end of input, is no."""
    try:
        if core_platform.is_terminal(sys.stdin):
            reply = input(question)
        else:
            print(question, flush=True)     # a log of a scripted run shows what the line it read answered
            reply = sys.stdin.readline()
    except EOFError:
        return False
    return reply.strip().lower() in ("y", "yes")


def warmstart(root: str) -> int:
    """Show what the recent fix commits would make, ask, and write the kept guards as trial guards; the number written."""
    found = candidates(root)
    if not found:
        return 0
    print(f"warm-start: {len(found)} recent commit(s) look like fixes. The guards went through the control run and a replay "
          f"over the last {guard_proof.REPLAY_COMMITS} commits:")
    print("\n".join(report(found)))
    keep = [c for c in found if not c.reasons]
    if not keep:
        print("warm-start: no guard passed, nothing to write")
        return 0
    if not _ask(f"Write the {len(keep)} kept guard(s)? They start on trial: they report and do not fail a run. [y/N] "):
        print("warm-start: nothing written; `brevio-core learn --dry-run --diff <commit>~1..<commit>` shows one again, `learn --diff <commit>~1..<commit>` keeps it")
        return 0
    for c in keep:
        defect_compiler.compile_guard_from_diff(c.diff, name=c.subject[:60], cwd=root,
                                                replay=guard_proof.stored(c.replay, guard_proof.REPLAY_COMMITS))
    return len(keep)
