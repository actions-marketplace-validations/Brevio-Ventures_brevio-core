"""The life of a guard after ``learn`` kept it: ``learn list``, ``show``, ``promote``, ``wrong`` and ``dismiss`` (preview).

Improvement plan, builds 1b and 1d, 2026-11-12. A new guard is on trial: it reports and does not fail a run, until the end
date it was given. A person then promotes it, which needs a trial that has ended, no hit marked wrong, and a replay of
recent history that flags nothing the guard did not flag when it was kept. A person can mark a hit wrong
(``wrong``); the guard goes off after ``WRONG_LIMIT`` of them and says so. A person can switch a guard off whenever they choose
(``dismiss``). A command is a handler in ``HANDLERS``; ``register`` adds one, which is how another module adds a
subcommand to ``learn`` without editing ``run``. ``import`` (``learn_import``) brings the guards of a local guards
file on trial and is registered the same way.

``dismiss`` and ``wrong`` weaken the gate, so they run only where a person is typing: they are refused when standard input
or output is not a terminal. The agent hook runs no ``learn`` command and the MCP server has no tool for either. An agent that
drives a real terminal is not stopped by this check, so the scan also reports a guard that a change removes or switches off
without the record these commands leave (``core_guards.weakened``).
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

from brevio.operator import learn_import
from brevio.pipeline import core_cli, core_guards, core_platform
from brevio.pipeline.agent_host import config
from brevio.pipeline.gates import defect_compiler, guard_proof, guard_scan

COMMIT_LINE = "Commit {files} so your team and their agents get the change."
GUARDS_FILE = f"{defect_compiler.GUARDS_DIR}/{defect_compiler.GUARDS_FILE}"
OFF_FILES = f"{GUARDS_FILE} and the deleted agent rule under .agents/rules"
SHOWN_LINES = 10    # the lines `learn show` lists that a guard matches today


@dataclass
class Call:
    """What the command line gave a ``learn`` action."""

    guard_id: str | None = None
    reason: str = ""
    commits: int = guard_proof.REPLAY_COMMITS
    as_json: bool = False
    hit: str = ""           # ``path:line`` of the hit ``wrong`` names
    accept: bool = False    # ``promote --accept``: take in the replay hits a person has read


Handler = Callable[[Call], int]
HANDLERS: dict[str, Handler] = {}
ACTIONS = HANDLERS          # the names the parser offers; ``register`` adds to it
NEEDS_PERSON: set[str] = set()


def register(name: str, handler: Handler, needs_person: bool = False) -> None:
    """Add the ``learn`` action ``name``. ``needs_person`` makes it run only at a terminal, like ``wrong`` and ``dismiss``."""
    HANDLERS[name] = handler
    if needs_person:
        NEEDS_PERSON.add(name)


def _refuse(what: str) -> int:
    print(f"brevio-core: `learn {what}` changes what the gate reports, so it runs only at a terminal; it is refused "
          "when input or output is a pipe or a file", file=sys.stderr)
    return 2


def _guard(guard_id: str | None) -> tuple[dict | None, str]:
    guards = defect_compiler.guard_table(defect_compiler.load_guards(config.WORKSPACE_DIR))
    if not guard_id:
        return None, "name a guard: `brevio-core learn list` shows them"
    found = guards.get(guard_id)
    return (found, "") if isinstance(found, dict) else (None, f"no guard {guard_id} in {defect_compiler.GUARDS_DIR}/{defect_compiler.GUARDS_FILE}")


def _shown(text: str) -> str:
    return guard_proof.printable(text, 200)


def repo_files(root: str) -> dict[str, list[str]]:
    """Every code file under ``root`` the scan would read, as ``{path relative to root: lines}``."""
    return {os.path.relpath(p, root).replace(os.sep, "/"): [text for _n, text in lines]
            for p, lines in core_cli.collect_files([root]).items()}


def _list(call: Call) -> int:
    guards = defect_compiler.guard_table(defect_compiler.load_guards(config.WORKSPACE_DIR))
    if call.as_json:
        print(json.dumps(guards, indent=2))
        return 0
    for gid, g in guards.items():
        if not isinstance(g, dict):
            continue
        status = g.get("status", "unknown")
        when = f", trial ends {g['trial_ends']}" if status == defect_compiler.STATUS_TRIAL and g.get("trial_ends") else ""
        replay = g["replay"] if isinstance(g.get("replay"), dict) else {}
        seen = f", replay when kept: flagged {replay['flagged']} of {replay.get('checked')}" if "flagged" in replay else ""
        found = f", {g['intercepted']} hit(s) found by `learn --check`" if g.get("intercepted") else ""
        wrong = f", {g['false_fails']} hit(s) marked wrong" if g.get("false_fails") else ""
        print(_shown(f"{gid}: {status}{when}{seen}{found}{wrong}"))     # the file is part of a change under review: no control characters reach a terminal
        if g.get("status_note"):
            print(_shown(f"  {g['status_note']}"))
    if not guards:
        print("no guards yet: `brevio-core learn` makes one from a fix")
    return 0


def _show(call: Call) -> int:
    """One guard in full: what it watches and looks for, where its trial stands, what its replay flags now (by commit, with
    the subject) and the lines it matches today (2026-11-13, I33). It writes nothing."""
    guard, why = _guard(call.guard_id)
    if guard is None:
        print(f"brevio-core: {why}", file=sys.stderr)
        return 2
    root = config.WORKSPACE_DIR
    held = guard["replay"] if isinstance(guard.get("replay"), dict) else {}
    limit = held.get("limit") if isinstance(held.get("limit"), int) else call.commits
    now = guard_proof.replay(guard, root, max(call.commits, limit))
    today = guard_scan.matching_lines(guard, repo_files(root))
    if call.as_json:
        print(json.dumps({**guard, "replay_now": now, "matches_today": [f"{p}:{n}" for p, n in today]}, indent=2))
        return 0
    status = guard.get("status", "unknown")
    trial = f" (trial ends {guard['trial_ends']})" if status == defect_compiler.STATUS_TRIAL and guard.get("trial_ends") else ""
    lines = [f"{call.guard_id}: {guard.get('title', '')}", f"  status:    {status}{trial}",
             f"  watches:   {', '.join(map(str, guard.get('file_patterns', [])))}",
             f"  patterns:  {', '.join(map(str, guard.get('patterns', [])))}"
             + ("  (a hit needs all of them)" if guard_scan.needs_all(guard) else ""),
             f"  found by `learn --check`: {int(guard.get('intercepted', 0))}; marked wrong: {int(guard.get('false_fails', 0))}"]
    lines += ["  " + ln for ln in guard_proof.describe(now)]
    lines.append(f"  matches {len(today)} line(s) in this repository today")
    lines += [f"    {path}:{n}" for path, n in today[:SHOWN_LINES]]
    if len(today) > SHOWN_LINES:
        lines.append(f"    and {len(today) - SHOWN_LINES} more")
    print("\n".join(_shown(ln) for ln in lines))
    return 0


def _promote(call: Call) -> int:
    guard, why = _guard(call.guard_id)
    if guard is None or not call.guard_id:
        print(f"brevio-core: {why}", file=sys.stderr)
        return 2
    if learn_import.imported(guard) and not (core_platform.is_terminal(sys.stdin) and core_platform.is_terminal(sys.stdout)):
        # An agent rule is an instruction an agent reads, and this one is written from text of another repository.
        print("brevio-core: promoting an imported guard writes its agent rule, so it runs only at a terminal; it is refused "
              "when input or output is a pipe or a file", file=sys.stderr)
        return 2
    held = guard["replay"].get("limit") if isinstance(guard.get("replay"), dict) else None
    # A narrower window than the one stored at keep time would skip the history that held the hits (2026-11-12, improvement plan).
    now = guard_proof.replay(guard, config.WORKSPACE_DIR, max(call.commits, held if isinstance(held, int) else 0))
    new = guard_proof.unexplained(guard, now)
    kept, why = defect_compiler.promote_guard(call.guard_id, new, cwd=config.WORKSPACE_DIR, accept=call.accept)
    if kept is None:
        print(f"brevio-core: not promoted: {why}", file=sys.stderr)
        if new and not call.accept and why.startswith("the replay flags"):
            print("  read the hits with `git show <commit>`; `learn promote --accept` takes them in at a terminal", file=sys.stderr)
        return 1
    rule = learn_import.write_rule(call.guard_id)
    if new:
        print(f"accepted {len(new)} replay hit(s): " + ", ".join(_shown(x) for x in new[:5]) + (" and more" if len(new) > 5 else ""))
    print(f"{call.guard_id} is active: it is now a warning, and brevio-core.toml can raise it to an error")
    print(COMMIT_LINE.format(files=f"{GUARDS_FILE} and {rule}" if rule else GUARDS_FILE))
    return 0


def _hit_fingerprint(guard_id: str, spot: str) -> tuple[str, str]:
    """``(fingerprint, "")`` of the line ``path:line`` names, or ``("", why not)``: the line must be one the guard hits."""
    path, _, number = spot.rpartition(":")
    root = os.path.realpath(config.WORKSPACE_DIR)
    full = os.path.realpath(os.path.join(root, path))
    try:
        inside = bool(path) and number.isdigit() and os.path.commonpath([root, full]) == root
    except ValueError:      # another drive on Windows: nothing in common
        inside = False
    if not inside:
        return "", f"`{_shown(spot)}` is not path:line inside this repository"
    try:
        with open(full, encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return "", f"cannot read {_shown(path)}"
    guard = _guard(guard_id)[0] or {}
    path = os.path.relpath(full, root).replace(os.sep, "/")
    if (path, int(number)) not in guard_scan.matching_lines(guard, {path: lines}):
        return "", f"line {number} of {_shown(path)} is not a hit of {guard_id}"
    return core_guards.fingerprint(path, core_guards.rule_of(guard_id), lines[int(number) - 1]), ""


def _wrong(call: Call) -> int:
    guard, why = _guard(call.guard_id)
    if guard is None or not call.guard_id:
        print(f"brevio-core: {why}", file=sys.stderr)
        return 2
    guard_id, print_id = call.guard_id, ""
    if call.hit:
        print_id, why = _hit_fingerprint(guard_id, call.hit)
        if not print_id:
            print(f"brevio-core: {why}", file=sys.stderr)
            return 2
        if print_id in (guard.get("wrong_hits") or []):
            print(f"{guard_id}: that hit is marked wrong already; a hit counts once")
            return 0
    after = defect_compiler.evaluate_guard_trial(guard_id, false_fail=True, cwd=config.WORKSPACE_DIR, hit=print_id)
    if after is None:
        return 2
    marks = int(after.get("false_fails", 0))
    if after.get("status") == defect_compiler.STATUS_OFF:
        print(f"{guard_id} is off: {marks} hits were marked wrong (the limit is {defect_compiler.WRONG_LIMIT}); it stays in the file, "
              "and its agent rule is deleted")
    else:
        print(f"{guard_id}: {marks} hit(s) marked wrong; it goes off at {defect_compiler.WRONG_LIMIT}")
        if not call.hit:
            print("  no hit was named, so this counted as one more mark. `learn wrong <id> path:line` names the hit, and it counts once")
    print(COMMIT_LINE.format(files=OFF_FILES if after.get("status") == defect_compiler.STATUS_OFF else GUARDS_FILE))
    return 0


def _dismiss(call: Call) -> int:
    guard, why = _guard(call.guard_id)
    if guard is None or not call.guard_id:
        print(f"brevio-core: {why}", file=sys.stderr)
        return 2
    defect_compiler.dismiss_guard(call.guard_id, call.reason, cwd=config.WORKSPACE_DIR)
    print(f"{call.guard_id} is off; it stays in the file with a record of how and when it was switched off, and its agent rule is deleted")
    print(COMMIT_LINE.format(files=OFF_FILES))
    return 0


def _import(call: Call) -> int:
    """``learn import FILE``: it asks for its own terminal and typed yes (``learn_import.run``)."""
    return learn_import.run(call.guard_id, call.commits)


register("list", _list)
register("show", _show)
register("promote", _promote)
register("wrong", _wrong, needs_person=True)
register("dismiss", _dismiss, needs_person=True)
register("import", _import)


def run(action: str, guard_id: str | None, reason: str, commits: int, as_json: bool, hit: str = "", accept: bool = False) -> int:
    """Run one of ``ACTIONS``; the exit code is 0 on success, 1 when a promotion is not allowed, 2 on a usage error.

    ``promote --accept`` changes what the gate reports like ``wrong`` does, so it runs only at a terminal too."""
    if (action in NEEDS_PERSON or accept) and not (core_platform.is_terminal(sys.stdin) and core_platform.is_terminal(sys.stdout)):
        return _refuse(f"{action} --accept" if accept else action)
    return HANDLERS[action](Call(guard_id, reason, commits, as_json, hit, accept))
