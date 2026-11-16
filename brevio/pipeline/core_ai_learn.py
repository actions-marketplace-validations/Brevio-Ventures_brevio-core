"""``brevio-core ai learn``: a model's patterns for a guard, kept only if the checks of ``learn`` pass (preview; the 0.7 set,
improvement plan, 2026-11-12).

``brevio-core learn`` turns the removed lines of a fix into a guard with no model. This command asks the model you connected
to generalise those lines into up to three patterns, and keeps one only when it passes what a hand-made guard passes:

- the regex subset check (``core_ai_regex``): no backreference, no nested repeat, a length cap;
- the control run: it hits the removed side of the fix and does not hit the fixed file, and it does not look like a credential;
- the replay over recent history, which is printed (what it would have flagged in the last commits).

The guard then enters the same trial as a hand-made one (it reports and does not fail a run until a person promotes it), with
its origin recorded as ``model-proposed``. One call. It takes no ``--yes``, needs a terminal, writes nothing without a second
yes, and is not reachable from the agent hook or the MCP server. It sends the removed lines with runs of spaces folded to one, and the file's
extension: no file name. Standard library and exported modules only; it reaches the network only through the transport that
``core_ai`` loads.
"""
from __future__ import annotations

import argparse
import posixpath
from collections.abc import Callable, Mapping
from typing import Any

from brevio.pipeline import core_ai as flow
from brevio.pipeline import core_ai_regex, core_patch
from brevio.pipeline import core_ai_verify as verify
from brevio.pipeline.gates import defect_compiler, guard_proof, guard_scan

MAX_LINES, MAX_CHARS = 40, guard_scan.REGEX_MAX_LINE    # the removed lines that are sent, and how long one may be
MAX_PATTERNS = 3
ORIGIN = "model-proposed"
GUARDS_FILE = f"{defect_compiler.GUARDS_DIR}/{defect_compiler.GUARDS_FILE}"     # as ``learn`` names it (a job does not import the operator layer)
COMMIT_LINE = "Commit {files} so your team and their agents get the change."
SYSTEM = ("You write patterns that find one kind of mistake in source code lines. Reply with the patterns only, one per line, "
          "in one fenced code block.")


def numbers() -> tuple[int, int]:
    """The two numbers the first-use text quotes: how many lines are sent at most, and how long one may be."""
    return MAX_LINES, MAX_CHARS


def _spec(args: argparse.Namespace) -> str:
    """The ``git diff`` argument for the fix."""
    return "--cached" if args.staged else "HEAD" if args.worktree else args.diff


def _fixed_at(args: argparse.Namespace) -> str | None:
    """Where the fixed file is read from: the index (``""``), a revision, or the working tree (``None``)."""
    if args.staged:
        return ""
    if not args.worktree and ".." in args.diff:
        return args.diff.rsplit("..", 1)[-1].lstrip(".") or "HEAD"
    return None


def build_prompt(ext: str, lines: list[str]) -> str:
    """The request: the removed lines (runs of spaces folded to one, as patterns are matched) and what to answer."""
    fence = flow.fence_for(lines)
    return (f"A fix to a {ext} file removed the lines below, which held a mistake. Each run of spaces is folded to one space.\n"
            f"Reply with up to {MAX_PATTERNS} regular expressions (Python re syntax), one per line, in one fenced code block "
            "and nothing else of substance. Each should match the mistake in these lines and also a similar line elsewhere, "
            "and be as specific as the lines allow. Rules: no backreference, no lookahead or lookbehind, no named group, no "
            f"flag; a group is not repeated if it holds a repeat or an alternative; at most {core_ai_regex.MAX_UNBOUNDED} "
            f"repeats without an upper end; {core_ai_regex.MIN_LITERALS} or more plain characters; at most "
            f"{core_ai_regex.MAX_LENGTH} characters. A pattern that breaks a rule is dropped.\n"
            f"{fence}\n" + "\n".join(lines) + f"\n{fence}\n")


class Plan:
    """What the fix gives to ask about: the planned guard (its id, globs and file), the diff and the lines to send."""

    def __init__(self, guard: dict[str, Any], diff_text: str, lines: list[str]) -> None:
        self.guard, self.diff_text, self.lines = guard, diff_text, lines


def _plan(args: argparse.Namespace, root: str, policy: Any) -> Plan:
    if _spec(args).startswith("-"):     # ``--diff=--output=FILE`` would have git write a file (review, 2026-11-12)
        raise flow.Stop("a name that starts with a hyphen is not a commit or a range: git would read it as an option",
                        flow.EXIT_USAGE)
    diff_text = defect_compiler.get_git_diff_text(_spec(args), root)
    if not diff_text:
        raise flow.Stop(f"no changes were found for {_spec(args)}: nothing to learn from", flow.EXIT_FAILED)
    guard = defect_compiler.plan_guard(diff_text, cwd=root)
    if not guard:
        raise flow.Stop("the fix removes no line of 8 or more characters that this can learn from", flow.EXIT_FAILED)
    have = defect_compiler.guard_table(defect_compiler.load_guards(root)).get(guard["id"])
    if isinstance(have, dict):
        raise flow.Stop(f"{guard['id']} is in {GUARDS_FILE} already ({have.get('status', 'unknown')}); learn does "
                        "not overwrite a guard", flow.EXIT_FAILED)
    source = guard["source_file"]
    if core_patch.secret_name(source) or policy.denies(source):
        raise flow.Stop("not sent: the file of the fix is one that holds secrets, or the repository's brevio-core.toml denies "
                        "its path")
    folded = [guard_scan.fold(x) for x in defect_compiler.removed_lines(diff_text).get(source, [])]
    lines = [x for x in folded if len(x) <= MAX_CHARS][:MAX_LINES]
    if not lines:
        raise flow.Stop(f"the removed lines are over {MAX_CHARS} characters: a pattern could not be checked against them",
                        flow.EXIT_FAILED)
    return Plan(guard, diff_text, lines)


def _refuse_secret(lines: list[str]) -> None:
    hit = verify.secret_lines(list(enumerate(lines, 1)))
    if hit:
        raise flow.Stop(f"not sent: removed line{'s' if len(hit) > 1 else ''} {', '.join(map(str, hit))} look like a secret (the "
                        "stricter check ai makes); the line is not shown here. Remove it from the fix, or keep this file out "
                        "of ai")


def _candidates(text: str) -> tuple[list[str], str]:
    """``(patterns, "")`` from the reply's one fenced block, or ``([], why not)``."""
    block, why = flow.extract_block(text)
    if block is None:
        return [], why
    found = list(dict.fromkeys(x.strip() for x in block if x.strip()))
    if not found or len(found) > MAX_PATTERNS:
        return [], f"the reply holds {len(found)} pattern(s); between 1 and {MAX_PATTERNS} are read"
    return found, ""


def check_patterns(plan: Plan, found: list[str], fixed: list[str] | None) -> tuple[list[str], list[str]]:
    """``(accepted, reasons for the rest)``: each pattern alone passes the regex subset check and the control run."""
    accepted: list[str] = []
    refused: list[str] = []
    for n, pattern in enumerate(found, 1):
        why = core_ai_regex.problem(pattern)
        if not why:
            reasons = guard_proof.control_run({**plan.guard, "patterns": [f"re:{pattern}"]}, plan.diff_text, fixed)
            why = "; ".join(r for r in reasons if "credential" in r) or "; ".join(reasons)     # the other reasons quote the pattern
        if why:
            refused.append(f"pattern {n}: {why}")
        else:
            accepted.append(pattern)
    return accepted, refused


def _guard_of(plan: Plan, accepted: list[str]) -> dict[str, Any]:
    guard = dict(plan.guard)
    guard.update(patterns=[f"re:{p}" for p in accepted], origin=ORIGIN, title=f"{guard['title']} ({ORIGIN})")
    return guard


def keep(guard: dict[str, Any], literal: list[str], replay: dict[str, Any], root: str) -> None:
    """Write the guard file entry and its agent rule. The rule lists the removed lines: what an agent is told to avoid."""
    data = defect_compiler.load_guards(root)
    guard["replay"] = guard_proof.stored(replay, guard_proof.REPLAY_COMMITS)
    data.setdefault("guards", {})[guard["id"]] = guard
    defect_compiler._write_agent_rule(guard["id"], guard["title"], guard["category"], guard["file_patterns"], literal, cwd=root)
    defect_compiler.save_guards(data, root)


def _report(p: flow.Prepared, accepted: list[str], refused: list[str], replay: dict[str, Any]) -> None:
    print(flow.labelled(p, "Proposed patterns", "\n".join(accepted) if accepted else "(none was accepted)"))
    print(f"accepted {len(accepted)}, refused {len(refused)}:")
    print("".join(f"  - {flow.safe(r)}\n" for r in refused), end="")
    if accepted:
        print("  " + "\n  ".join(guard_proof.describe(replay)))


def run(args: argparse.Namespace, env: Mapping[str, str], ask: Callable[[str], str] | None) -> int:
    cfg, connector, root, policy = flow.session("learn", env)
    if ask is None and not args.dry_run:
        raise flow.Stop("`ai learn` needs a person at a terminal: it takes no --yes, and it writes a guard only after a yes")
    plan = _plan(args, root, policy)
    _refuse_secret(plan.lines)
    if guard_proof.credential_in(plan.guard):
        raise flow.Stop("not sent: a removed line looks like a credential, and guards are committed")
    flow.check_connector(connector, env)
    ext = posixpath.splitext(plan.guard["source_file"])[1] or "no extension"
    p = flow.Prepared(cfg, connector, root, None, build_prompt(ext, plan.lines), SYSTEM)
    flow.refuse_limits(p)
    reply = flow.exchange("learn", p, env, ask, args.dry_run, False)
    if reply is None:
        return flow.EXIT_OK
    found, why = _candidates(reply)
    if not found:
        print(f"refused: {why}; nothing was written")
        return flow.EXIT_FAILED
    accepted, refused = check_patterns(plan, found, guard_proof.fixed_side(plan.guard["source_file"], _fixed_at(args), root))
    guard = _guard_of(plan, accepted)
    replay = guard_proof.replay(guard, root) if accepted else {}
    _report(p, accepted, refused, replay)
    if not accepted:
        return flow.EXIT_FAILED
    if ask is None or not flow.ask_yes(ask, "Keep this guard on trial? It reports and does not fail a run until you promote it. [y/N] "):
        print("not written")
        return flow.EXIT_NOT_SENT
    keep(guard, plan.guard["patterns"], replay, root)
    print(f"kept {guard['id']} on trial, origin {ORIGIN}. " + COMMIT_LINE.format(
        files=f"{GUARDS_FILE} and {guard['rule_file']}"))
    return flow.EXIT_OK
