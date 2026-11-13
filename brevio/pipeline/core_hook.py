"""``brevio-core hook run``: the agent hook, answering a coding client in its own format (ROADMAP E34, 2026-11-05).

The 0.4 set. A coding client runs it after a file edit (``--event post-edit``) and when the agent is about to finish
(``--event stop``), with the event as JSON on stdin. After an edit it scans the lines that file now adds against HEAD
(``git diff -U0``; the whole file outside git or before the first commit). At stop it scans every uncommitted change.
An ignore comment never counts here and baselined findings stay hidden. The findings go back as the compact agent
lines (``core_cli.agent_lines``) through the client's own channel:

- Claude Code: ``{"decision": "block", "reason": ...}`` on stdout. After an edit the reason is shown to the agent beside
  the tool result; at stop it keeps the agent working, once: a stop that is already a continuation
  (``stop_hook_active``) is let through.
- Cursor: ``additional_context`` after a tool and ``followup_message`` at stop, only while ``loop_count`` is 0 and the
  turn completed. Cursor also runs Claude Code's hooks; that copy stays quiet when Cursor's own brevio-core hook is
  installed, so the agent hears each finding once.

The learned guards (``core_guards``) run in that scan since 2026-11-12 (improvement plan, build 1a). A hit shows after an
edit. At stop, a hit of a guard on trial, or of one that is a warning, does not keep the agent working; a guard raised
to an error in ``brevio-core.toml`` does. A guard that the uncommitted changes remove, or switch off without the record
``learn dismiss`` leaves, is reported at stop and after an edit of ``.brevio/guards.json``.

After a shell command (``--event post-command``: Claude Code's Bash, Cursor's Shell) it scans nothing. When the command
was a ``git commit`` whose message looks like a fix (fix, bug, revert or regression, any case), it answers with one
line of additional context suggesting ``brevio-core learn --dry-run`` and then ``learn``, so the fix can become a guard
(ROADMAP E40, 2026-11-05). It never runs ``learn`` and never blocks: Claude Code gets ``hookSpecificOutput``
``additionalContext``, Cursor ``additional_context``.

At stop the scan also reports what the uncommitted changes add to the baseline, to the config and as ignore comments
(``core_cli.new_suppressions``), so an agent cannot silence the gate by running ``baseline`` (2026-11-13). After an
edit of a learned guard on trial, or one that is a warning, the reply is a note that does not block.

A clean run prints nothing. The hook fails open: an internal error or a scan longer than the budget (``--budget``,
default 2 s) exits 0 with one line on stderr, so a broken hook never stops an agent. A stop that blocks exits 2 and
writes the reason to stderr as well as stdout, where Claude Code reads it. Standard library only, no network, no model
call.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, TextIO

from brevio.pipeline import core_cli, core_platform
from brevio.pipeline.gates import agent_hooks
from brevio.pipeline.rule_host import GIT

EXIT_OK = 0
EXIT_BLOCK = 2

DEFAULT_BUDGET = 2.0    # seconds of scanning an agent waits for after an edit; past it the hook lets the agent go on
EVENTS = (agent_hooks.POST_EDIT, agent_hooks.POST_COMMAND, agent_hooks.STOP)
CLIENTS = (agent_hooks.CLAUDE_CODE, agent_hooks.CURSOR)
# Claude Code names the edited file ``tool_input.file_path``; Cursor documents no input for its Write tool, so the
# names its other file events use are read too.
PATH_KEYS = ("file_path", "path", "target_file")
FAIL_OPEN = "brevio-core hook: skipped"
# ``git commit``, also after ``-C dir`` or ``-c key=value`` options; the message is read from what follows it, which
# holds ``-m`` and a heredoc alike. A command that only mentions a fix elsewhere (``./fix.sh && git commit``) is not one.
_COMMIT = re.compile(r"\bgit(?:\s+-[cC]\s+\S+|\s+--[\w-]+(?:=\S+)?)*\s+commit\b")
# Fix-like words (ROADMAP E40): fix and its forms (bugfix, hotfix, fixes; not fixture), bug, revert, regression.
_FIX_LIKE = re.compile(r"(?i)\b(?:hot|bug)?fix(?!tur)|\bbugs?\b|\brevert|\bregression")
TRIAL_NOTE = "brevio-core: a learned guard on trial matched; it is reported only, nothing to fix unless you agree:\n"
LEARN_HINT = ("brevio-core: this commit looks like a fix. To have a scan report the line if it comes back, run "
              "`brevio-core learn --dry-run` to see the guard it would make from this commit and what it would have "
              "flagged in recent commits, then `brevio-core learn` to keep it.")


@dataclass(frozen=True)
class Event:
    """One hook call: which client sent it, what happened, the file edited, whether this stop already continued, and
    the shell command a ``post-command`` event ran."""

    client: str
    kind: str
    path: str | None
    root: str
    again: bool
    command: str = ""


def _input_of(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("tool_input")
    if isinstance(raw, str):        # Cursor passes some tool inputs as a JSON string
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def edited_path(payload: dict[str, Any]) -> str | None:
    """The file an edit event names, from its tool input or (Cursor's ``afterFileEdit``) the event itself."""
    for source in (_input_of(payload), payload):
        for key in PATH_KEYS:
            if isinstance(source.get(key), str) and source[key]:
                return source[key]
    return None


def parse(payload: Any, kind: str, installed: str) -> Event:
    """The event a client sent; the client is read from the payload (Cursor stamps ``cursor_version``)."""
    if not isinstance(payload, dict):
        raise ValueError("the hook input is not a JSON object")
    client = agent_hooks.CURSOR if "cursor_version" in payload or installed == agent_hooks.CURSOR else agent_hooks.CLAUDE_CODE
    roots = payload.get("workspace_roots")
    root = payload.get("cwd") or (roots[0] if isinstance(roots, list) and roots else "") or os.getcwd()
    if client == agent_hooks.CURSOR:
        again = int(payload.get("loop_count") or 0) > 0 or payload.get("status", "completed") != "completed"
    else:
        again = bool(payload.get("stop_hook_active"))
    path = edited_path(payload) if kind == agent_hooks.POST_EDIT else None
    command = shell_command(payload) if kind == agent_hooks.POST_COMMAND else ""
    return Event(client, kind, os.path.join(str(root), path) if path else None, str(root), again, command)


def shell_command(payload: dict[str, Any]) -> str:
    """The command a shell tool ran: ``tool_input.command`` (Claude Code's Bash, Cursor's Shell), or the event's own
    ``command`` (Cursor's shell events); "" when the event names none."""
    for source in (_input_of(payload), payload):
        if isinstance(source.get("command"), str):
            return source["command"]
    return ""


def fix_commit(command: str) -> bool:
    """``command`` makes a commit whose message (what follows ``git commit``) holds a fix-like word."""
    m = _COMMIT.search(command)
    return bool(m and _FIX_LIKE.search(command[m.end():]))


def _toplevel(folder: str) -> str | None:
    try:
        r = subprocess.run([*GIT, "-C", folder, "rev-parse", "--show-toplevel"],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def project_root(ev: Event) -> str:
    """The repository holding the edited file (or the event's folder), else that folder itself."""
    folder = os.path.dirname(ev.path) if ev.path else ev.root
    return _toplevel(folder if os.path.isdir(folder) else ev.root) or ev.root


def changed_lines(ev: Event) -> core_cli.Numbered:
    """What to scan, read from the current folder (the project root): the edit's added lines, or every uncommitted one."""
    if ev.kind == agent_hooks.STOP:
        return core_cli.select_lines([], "HEAD" if core_cli.has_head() else None)
    if not ev.path:
        return {}
    rel = core_platform.shown(os.path.realpath(ev.path), os.path.realpath(os.getcwd()))
    if rel.startswith("../") or os.path.isabs(rel) or not os.path.isfile(rel) or core_cli.skipped(rel) or core_cli.bundled(rel):
        return {}       # outside the project, gone, in a folder the scan never reads, minified or bundled
    if core_cli.has_head():
        return core_cli.diff_lines("HEAD", [rel])
    return core_cli.collect_files([rel])


def _cursor_hook_installed(root: str) -> bool:
    return agent_hooks.installed(os.path.join(root, agent_hooks.CURSOR_HOOKS_FILE))


def message(ev: Event, shown: list[str]) -> str:
    where = "the lines you just changed" if ev.kind == agent_hooks.POST_EDIT else "your uncommitted changes"
    then = "Fix them in the code" if ev.kind == agent_hooks.POST_EDIT else "Fix them before you finish"
    return (f"brevio-core: {len(shown)} finding(s) in {where}. {then}; an ignore comment does not count for an agent, "
            "and `brevio-core mcp` explains each rule.\n" + "\n".join(core_cli.agent_lines(shown)))


def _report_only(finding: str, proj: Any) -> bool:
    """A learned guard's finding that does not fail a scan (it is on trial, or a warning): shown, never held against the agent."""
    fields = core_cli.finding_fields(finding) or {}
    return str(fields.get("rule", "")).startswith(core_cli.GUARD_PREFIX) and not core_cli.blocking([finding], "new", proj)


def _guards_edited(ev: Event) -> bool:
    return bool(ev.path) and (ev.path or "").replace(os.sep, "/").endswith("/.brevio/guards.json")


def _suppressions_edited(ev: Event) -> bool:
    """The edit is of the baseline file or a ``brevio-core.toml``: the files a change can silence a rule in."""
    path = (ev.path or "").replace(os.sep, "/")
    tools, cfg = core_cli.baseline_tools(), core_cli.config_tools()
    return bool(path) and ((tools is not None and path.endswith("/" + tools.BASELINE_PATH))
                           or (cfg is not None and cfg.is_config(path)))


def _more_findings(ev: Event, lines: core_cli.Numbered) -> list[str]:
    """What the gate checks add to the scan: conflict markers after an edit; at stop also tests, imports, migrations and
    agent files; and a guard the uncommitted changes remove or weaken (2026-11-12, improvement plan)."""
    gates = core_cli.set_module("pipeline.gates", "core_gates")
    found: list[str] = []
    if gates is not None and ev.kind == agent_hooks.POST_EDIT:
        found += gates.check_conflict_markers(lines)
    if ev.kind == agent_hooks.STOP and core_cli.has_head() and gates is not None:
        found += (gates.check_conflict_markers(lines)
                  + gates.check_test_custody(root=".", base="HEAD", lines=lines)
                  + gates.check_resolved_imports(lines, root=".")
                  + gates.check_migration_safety(lines, root=".", base="HEAD")
                  + gates.check_agent_files(root="."))
    if not core_cli.has_head():
        return found
    if (ev.kind == agent_hooks.STOP or _suppressions_edited(ev)) and core_cli.baseline_tools() is not None:
        found += core_cli.new_suppressions(lines, "HEAD")       # holds the guard changes too
    elif ev.kind == agent_hooks.STOP or _guards_edited(ev):
        found += core_cli.guard_changes("HEAD")
    return found


def checked_packet(packet: str) -> str:
    """``packet`` without an edit block whose rule id is empty or whose file is outside the project; "" when no edit
    is left, so the agent is never handed a block it could apply to the wrong place."""
    closing = "</brevio_heal_packet>"
    body = packet.rstrip()
    ended = body.endswith(closing)       # the closing tag sits after the last block, so it must outlive that block
    head, *blocks = re.split(r"(?m)^(?=#### (?:File|Gate Issue): )", body[:-len(closing)] if ended else body)
    kept = [b for b in blocks if _edit_block_ok(b)]
    if blocks and not kept:
        return ""
    return head + "".join(kept) + (closing if ended else "")


def _edit_block_ok(block: str) -> bool:
    m = re.match(r"#### (File|Gate Issue): (.+?):\d+(?: \[([^\]]*)\])?\s*$", block.split("\n", 1)[0])
    if m is None:
        return False
    path = m.group(2).strip().replace("\\", "/")
    outside = (not path or path.startswith(("/", "../")) or "/../" in path or re.match(r"^[A-Za-z]:", path) is not None)
    return not outside and (m.group(1) == "Gate Issue" or bool((m.group(3) or "").strip()))


def reply(ev: Event, text: str, block: bool = True) -> str:
    """``text`` as the JSON the client reads from a hook's standard output; a ``post-command`` note or a note with
    ``block`` false never blocks."""
    if ev.client == agent_hooks.CURSOR:
        return json.dumps({"followup_message" if ev.kind == agent_hooks.STOP else "additional_context": text})
    if ev.kind == agent_hooks.POST_COMMAND or not block:
        return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}})
    return json.dumps({"decision": "block", "reason": text})


def answer(raw: str, kind: str, installed: str) -> str:
    """The hook's whole standard output for one event: a JSON reply on findings, "" when there is nothing to say."""
    ev = parse(json.loads(raw) if raw.strip() else {}, kind, installed)
    if ev.kind == agent_hooks.STOP and ev.again:
        return ""       # the agent already went on once because of a stop hook: let it finish
    if ev.kind == agent_hooks.POST_COMMAND and not fix_commit(ev.command):
        return ""       # every other shell command: answered before any git call, so the agent waits for nothing
    root = project_root(ev)
    if installed == agent_hooks.CLAUDE_CODE and ev.client == agent_hooks.CURSOR and _cursor_hook_installed(root):
        return ""       # Cursor runs Claude Code's hooks as well; its own brevio-core hook answers
    if ev.kind == agent_hooks.POST_COMMAND:
        tip = ""
        if re.search(r"\b(?:pytest|vitest|jest|cargo\s+test|go\s+test)\b", ev.command) and not re.search(r"\b-q\b|\brtk\b", ev.command):
            tip = " (Tip: add -q or rtk to quiet verbose test runs.)"
        return reply(ev, LEARN_HINT + tip)
    os.chdir(root)      # the scan and the baseline read paths from the repository root
    lines = changed_lines(ev)
    proj = core_cli.project(lines)
    raw_findings = core_cli.scan(lines, proj) + _more_findings(ev, lines)
    shown = core_cli.triage(raw_findings, "new", honour_ignores=False)[0]
    trial = [f for f in shown if _report_only(f, proj)]
    shown = [f for f in shown if f not in trial]
    if not shown:
        if not trial or ev.kind == agent_hooks.STOP:
            return ""   # at stop a guard on trial, or a warning, is not a reason to keep the agent working
        return reply(ev, TRIAL_NOTE + "\n".join(core_cli.agent_lines(trial)), block=False)
    text = message(ev, shown)
    if trial:
        text += "\n\n" + TRIAL_NOTE + "\n".join(core_cli.agent_lines(trial))
    if ev.kind == agent_hooks.STOP:
        heal_mod = core_cli.set_module("pipeline", "core_heal")
        heal_packet = ""
        if heal_mod is not None:
            try:
                rep = heal_mod.run_heal(root=".", apply_fixes=False, compile_guards=False, max_chars=2000, findings=shown)
            except (OSError, ValueError, KeyError, TypeError):      # the hook fails open; no packet is no packet
                rep = {}
            if rep.get("verified") is not False:        # the packet's owner marks one it could not verify
                heal_packet = checked_packet(str(rep.get("heal_packet", "")))
        if heal_packet:
            text += "\n\nSelf-heal packet:\n" + heal_packet
        elif heal_mod is not None:      # a trimmed release has no ``heal``, so it does not tell the agent to run it (2026-11-12)
            text += "\n\nRun `brevio-core heal` to auto-fix and generate surgical repair packets."
    return reply(ev, text)


def _reason(out: str) -> str:
    """The text of a hook reply (its ``reason`` or ``followup_message``), for stderr."""
    data = json.loads(out)
    if not isinstance(data, dict):
        return out
    return next((data[k] for k in ("reason", "followup_message") if isinstance(data.get(k), str)), out)


def run(kind: str, installed: str, budget: float, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    """Answer one event within ``budget`` seconds. Exit 0, except 2 at stop when the reply blocks; a hook that fails
    never stops the agent."""
    box: dict[str, Any] = {}
    started = time.monotonic()

    def work() -> None:
        box["out"] = answer(stdin.read(), kind, installed)
        box["took"] = time.monotonic() - started

    # The fail-open boundary: whatever the worker raises is reported in one line, never as a block (ROADMAP E34).
    prior = threading.excepthook
    threading.excepthook = lambda hook: box.setdefault("error", hook.exc_value)
    worker = threading.Thread(target=work, name="brevio-core-hook", daemon=True)
    try:
        worker.start()
        worker.join(budget)
    finally:
        threading.excepthook = prior
    # The worker's own clock decides, not whether it is still running when this thread wakes: under load this thread
    # can wake late and find a scan done that overran the budget, and a late answer is the one the budget forbids
    # (a budget-0 run answered with findings 2 times in 30 under CPU load, 2026-11-05).
    if worker.is_alive() or box.get("took", 0.0) > budget:
        print(f"{FAIL_OPEN}, the scan took longer than {budget:g} s", file=stderr)
    elif "error" in box:
        err = box["error"]
        print(f"{FAIL_OPEN} after an internal error ({type(err).__name__}: {str(err).splitlines()[0] if str(err) else ''})",
              file=stderr)
    elif box.get("out"):
        stdout.write(box["out"] + "\n")
        stdout.flush()
        if kind == agent_hooks.STOP:
            print(_reason(box["out"]), file=stderr)     # a client that blocks on exit 2 reads stderr, not stdout
            return EXIT_BLOCK
    return EXIT_OK


def main(argv: list[str], prog: str = "brevio-core hook run") -> int:
    ap = argparse.ArgumentParser(prog=prog, description="The hook a coding client calls: reads its event JSON on stdin "
                                 "and answers with the findings in that client's format. Exits 0, "
                                 "and 2 at stop when it keeps the agent working (the reason is on stderr too).")
    ap.add_argument("--event", choices=EVENTS, required=True, help="post-edit: after a file edit; post-command: after a shell command (a fix commit gets a "
                         "`learn` suggestion); stop: before the agent finishes")
    ap.add_argument("--client", choices=CLIENTS, default=agent_hooks.CLAUDE_CODE,
                    help="the client whose config holds this hook (the reply follows the client that sent the event)")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET, metavar="SECONDS",
                    help=f"give up quietly after this long (default {DEFAULT_BUDGET:g})")
    args = ap.parse_args(argv)
    return run(args.event, args.client, args.budget, sys.stdin, sys.stdout, sys.stderr)
