"""The defect-to-guard compiler: `cli.py learn` in the harness, `brevio-core learn` in the package.

``--staged`` and ``--worktree`` compile a fix that is not committed yet (the index, or the working tree against HEAD),
and ``--dry-run`` prints the guard and how many lines of the repository it matches today, writing nothing, so an agent
can check a guard before it keeps it (ROADMAP E34, 2026-11-05).

Since 2026-11-12 (improvement plan, build 1c) a guard is checked before it is kept: a control run (it must hit the lines
the fix removed and not the fixed file) and a replay over the last commits (what it would have flagged). A guard that
fails the control run is not written and the reasons are printed. ``learn list``, ``promote``, ``wrong`` and ``dismiss``
are the guard's life afterwards (``learn_manage``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from brevio.operator import learn_manage
from brevio.pipeline.agent_host import config
from brevio.pipeline.gates import defect_compiler, guard_proof, guard_scan


def _in_repository(path: str, root: str) -> str:
    """``path`` as the guards' globs read it: relative to the repository with "/", when it lies inside. A guard's globs name
    the folder of the fix, so an absolute path would not be watched (2026-11-13, D45)."""
    full = os.path.realpath(path)
    try:
        inside = os.path.commonpath([root, full]) == root
    except ValueError:      # another drive: nothing in common
        inside = False
    return os.path.relpath(full, root).replace(os.sep, "/") if inside else path


def _check(paths: list[str], as_json: bool) -> int:
    """Match the learned guards against whole files; record trials and interceptions."""
    files: dict[str, list[str]] = {}
    root = os.path.realpath(config.WORKSPACE_DIR)
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                files[_in_repository(path, root)] = f.read().splitlines()
        except OSError:
            print(f"cannot read {path}", file=sys.stderr)
    hits = guard_scan.scan_files(files)
    if as_json:
        print(json.dumps(hits, indent=2))
    for h in hits:
        print(f"guard {h['guard']}: {h['file']} holds `{h['pattern']}` ({h['title']})")
    return 1 if hits else 0


def _diff_spec(args: argparse.Namespace) -> tuple[str, str]:
    """The ``git diff`` argument for the fix, and how to name it in a message."""
    if args.staged:
        return "--cached", "the staged changes"
    if args.worktree:
        return "HEAD", "the uncommitted changes"
    return args.diff, f"reference '{args.diff}'"


def _fixed_at(args: argparse.Namespace) -> str | None:
    """Where the fixed file is read from: the index (``""``), a revision, or the working tree (``None``)."""
    if args.staged:
        return ""
    if not args.worktree and ".." in args.diff:
        return args.diff.rsplit("..", 1)[-1].lstrip(".") or "HEAD"
    return None


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _flagged(result: dict[str, Any]) -> int:
    return len({h["commit"] for h in result["hits"]})


def _broader_note(literal: dict[str, Any], literal_replay: dict[str, Any], wide: dict[str, Any], wide_replay: dict[str, Any],
                  today: tuple[int, int]) -> str:
    """The line that says a call-shaped pattern replaced the removed line, with both sets of counts (2026-11-13, I6)."""
    shown = ", ".join(f"`{p}`" for p in wide["patterns"])
    return (f"broader pattern kept: {shown} instead of the removed line: replay flagged {_flagged(wide_replay)} of "
            f"{wide_replay['checked']} past change(s) (the line alone: {_flagged(literal_replay)}), matches {today[1]} line(s) in this "
            f"repository today (the line alone: {today[0]})")


def _print_dry_run(guard: dict[str, Any], hits: int, reasons: list[str], replay: dict[str, Any], as_json: bool,
                   notes: list[str]) -> None:
    if as_json:
        control = {"passed": not reasons, "reasons": reasons}
        print(json.dumps({**guard, "repo_hits": hits, "written": False, "control": control, "replay": replay,
                          "notes": notes}, indent=2))
        return
    print(f"guard {guard['id']}: {guard['title']}")
    print(f"  watches:  {', '.join(guard['file_patterns'])}")
    print(f"  patterns: {', '.join(guard['patterns'])}" + ("  (a hit needs all of them)" if guard_scan.needs_all(guard) else ""))
    print("".join(f"  {n}\n" for n in notes), end="")
    print("  control run: " + ("passed: it hits the lines the fix removed and none of the fixed file's" if not reasons
                               else "FAILED, so this guard would not be kept:"))
    print("".join(f"    - {r}\n" for r in reasons), end="")
    print("  " + "\n  ".join(guard_proof.describe(replay)))
    print(f"  matches {hits} line(s) in this repository today")
    print("  dry run: nothing written" + (" (run again without --dry-run to keep it)" if not reasons else ""))


def _brought_in(guard: dict[str, Any], replay: dict[str, Any], limit: int) -> str:
    """The second line of what ``learn`` prints: the change that brought the line in and where the guard reports. A guard
    reports; it does not prevent (2026-11-13, I36)."""
    where = f"it will report in {', '.join(guard['file_patterns'])}"
    found = guard_proof.introduced_by(guard, replay)
    if found is None:
        return f"Brought in by: not found in the last {replay['checked'] or limit} change(s); {where}"
    return f"Brought in by: {found['commit'][:7]} {guard_proof.subject_shown(found['subject'])} ({found.get('date', '')}); {where}"


def _print_kept(guard: dict[str, Any], replay: dict[str, Any], notes: list[str], limit: int) -> None:
    print("brevio-core learn: guard kept")
    print("  " + _brought_in(guard, replay, limit))
    print(f"  Guard ID:     {guard['id']}")
    print(f"  Title:        {guard['title']}")
    print(f"  Category:     {guard['category']}")
    print(f"  File Globs:   {', '.join(guard['file_patterns'])}")
    print(f"  Patterns:     {', '.join(guard['patterns'])}" + ("  (a hit needs all of them)" if guard_scan.needs_all(guard) else ""))
    print("".join(f"  {n}\n" for n in notes), end="")
    print("  Control run:  passed")
    print("  " + "\n  ".join(guard_proof.describe(replay)))
    print(f"  Status:       trial: its hits are reported and do not fail a run; `learn promote` is possible from {guard['trial_ends']}")
    print(f"  A hit that is wrong: `brevio-core learn wrong {guard['id']} path:line`; switch it off: `brevio-core learn dismiss {guard['id']}`")
    print(learn_manage.COMMIT_LINE.format(files=f".brevio/guards.json and {guard['rule_file']}"))


def _diff_text(args: argparse.Namespace) -> str:
    if args.file and os.path.isfile(args.file):
        try:
            with open(args.file, encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            return ""
    return defect_compiler.get_git_diff_text(_diff_spec(args)[0])


def _checked(args: argparse.Namespace, guard: dict[str, Any], diff_text: str, root: str) -> tuple[dict[str, Any], list[str], dict[str, Any], list[str], int]:
    """``(guard, reasons, replay, notes, literal today count)``: the guard after the incidental patterns are dropped and a
    call-shaped pattern is tried (2026-11-13, I32 and I6), the control run's reasons, its replay, and the lines that say
    what changed."""
    fixed = guard_proof.fixed_side(guard["source_file"], _fixed_at(args), root)
    guard, dropped = guard_proof.without_incidental(guard, diff_text, fixed, args.name)
    notes = [f"dropped: {guard_proof.masked({**guard, 'patterns': [p]})['patterns'][0]} (also in the fixed file)" for p in dropped]
    reasons = guard_proof.control_run(guard, diff_text, fixed)
    replay = guard_proof.replay(guard, root, args.replay)
    today = 0
    wide = None if reasons else guard_proof.broadened(guard, diff_text, fixed, root, args.replay)
    if wide is not None:
        files = learn_manage.repo_files(root)
        today = guard_scan.line_hits(guard, files)
        notes.append(_broader_note(guard, replay, wide[0], wide[1], (today, guard_scan.line_hits(wide[0], files))))
        guard, replay = wide
    found = guard_proof.introduced_by(guard, replay)
    if found is not None and not args.name and not guard_proof.credential_in({**guard, "patterns": [found["subject"]]}):
        guard = {**guard, "title": defect_compiler.short_title(guard_proof.printable(found["subject"], 60).strip(), 60) or guard["title"]}
    return guard, reasons, replay, notes, today


def _compile(args: argparse.Namespace) -> int:
    diff_text = _diff_text(args)
    if not diff_text:
        print(f"No git diff changes detected for {_diff_spec(args)[1]}.", file=sys.stderr)
        return 1
    guard = defect_compiler.plan_guard(diff_text, name=args.name, category=args.category)
    if not guard:
        print("Could not extract a deterministic fault pattern from the diff.", file=sys.stderr)
        print(defect_compiler.explain_no_guard(diff_text), file=sys.stderr)
        return 1
    root = config.WORKSPACE_DIR
    guard, reasons, replay, notes, _today = _checked(args, guard, diff_text, root)
    have = defect_compiler.guard_table(defect_compiler.load_guards(root)).get(guard["id"])
    if isinstance(have, dict):
        # A second run would reset the trial, the wrong marks and a person's switch-off or promotion (2026-11-12, improvement plan).
        print(f"brevio-core: not kept: {guard['id']} is in {learn_manage.GUARDS_FILE} already ({have.get('status', 'unknown')}); "
              "`learn` does not overwrite a guard. Give this one another name with --name, or change the file by hand", file=sys.stderr)
        return 1
    if args.dry_run:
        _print_dry_run(guard_proof.masked(guard), guard_scan.line_hits(guard, learn_manage.repo_files(root)), reasons, replay,
                       args.json, notes)
        return 1 if reasons else 0
    if reasons:
        print(f"brevio-core: not kept: the guard from {guard['source_file']} fails the control run:\n"
              + "\n".join(f"  - {r}" for r in reasons), file=sys.stderr)
        return 1
    kept = defect_compiler.compile_guard_from_diff(diff_text, name=args.name, category=args.category,
                                                   replay=guard_proof.stored(replay, args.replay), guard=guard)
    if not kept:
        return 1
    if args.json:
        print(json.dumps(kept, indent=2))
        print("".join(f"{n}\n" for n in notes), end="", file=sys.stderr)
    else:
        _print_kept(kept, replay, notes, args.replay)
    return 0


def main(argv: list[str] | None = None, prog: str = "cli.py learn") -> int:
    """Compile a fixed defect into a guard; ``prog`` is how the user types the command."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Compile fixed defects into guards and progressive agent rules. A guard is checked before it is kept "
                    "(a control run, and a replay over recent commits) and starts on trial: it reports and does not fail "
                    "a run until a person promotes it. ACTION (preview): list shows the guards; show prints one in full; "
                    "promote makes a trial guard active; wrong marks one hit wrong; dismiss switches a guard off (wrong "
                    "and dismiss run only at a terminal); import FILE brings the guards of a guards file on this machine, "
                    "on trial, after a replay on this repository and a typed yes.",
    )
    parser.add_argument("action", nargs="?", choices=learn_manage.ACTIONS, metavar="ACTION",
                        help="list | show GUARD_ID | promote GUARD_ID [--accept] | wrong GUARD_ID [PATH:LINE] | "
                             "dismiss GUARD_ID [--reason TEXT] | import FILE (preview); leave out to compile a guard")
    parser.add_argument("guard_id", nargs="?", metavar="GUARD_ID", help="the guard an ACTION acts on, or the file `import` reads")
    parser.add_argument("hit", nargs="?", default="", metavar="PATH:LINE",
                        help="with wrong (preview): the hit that is wrong; a named hit is counted once; a call that names none adds one mark")
    parser.add_argument("--accept", action="store_true",
                        help="with promote (preview): take in the replay hits that were not there when the guard was kept, then promote "
                             "(at a terminal only)")
    parser.add_argument("--reason", default="", help="with dismiss (preview): why the guard is switched off, kept in the file")
    parser.add_argument("--diff", default="HEAD~1", help="Git diff commit or range (default: HEAD~1)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--staged", action="store_true", help="compile the staged fix (git diff --cached) instead of --diff")
    source.add_argument("--worktree", action="store_true", help="compile the uncommitted fix (the working tree against HEAD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the guard, its control run, what it would have flagged in recent commits and how many "
                             "lines of the repository it matches; write nothing")
    parser.add_argument("--replay", type=_positive, default=guard_proof.REPLAY_COMMITS, metavar="N",
                        help=f"how many recent commits the replay looks at (preview; default: {guard_proof.REPLAY_COMMITS})")
    parser.add_argument("--file", help="Specific file to inspect or extract diff from")
    parser.add_argument("--name", help="Human-readable title for the synthesized guard")
    parser.add_argument("--category", default="robustness", choices=["security", "robustness", "functional", "compliance", "finops"])
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    parser.add_argument("--check", nargs="+", metavar="FILE", help="Run the learned guards over these files instead of compiling one")

    args = parser.parse_args(argv)
    if args.accept and args.action != "promote":
        parser.error("--accept goes with `learn promote`")
    if args.hit and args.action != "wrong":
        parser.error("a second name goes with `learn wrong GUARD_ID PATH:LINE`")
    if args.action:
        if args.dry_run or args.staged or args.worktree or args.check or args.name or args.file:
            # ``learn dismiss ID --dry-run`` used to dismiss the guard: a flag that reads as "change nothing" was ignored
            parser.error(f"`learn {args.action}` acts at once and takes none of --dry-run, --staged, --worktree, --check, "
                         "--name or --file")
        return learn_manage.run(args.action, args.guard_id, args.reason, args.replay, args.json, args.hit, args.accept)
    if args.check:
        return _check(args.check, as_json=args.json)
    return _compile(args)


if __name__ == "__main__":
    sys.exit(main())
