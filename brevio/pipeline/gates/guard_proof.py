"""Prove a guard before it is kept: the control run and the replay (improvement plan, build 1c, 2026-11-12).

A guard compiled from a fix is lines of text. Two checks say what it would do before a person keeps it:

- The control run. The guard must hit the lines the fix removed, and must not hit the file the fix produced. A guard
  that fails either is not written, and the reasons are printed. A pattern that looks like a credential also fails:
  the guard file is committed.
- The replay. The guard is run over the lines the last ``REPLAY_COMMITS`` commits added (a flag changes the number),
  and what it would have flagged is listed by commit and file. The first hit is often the change that brought the
  defect in; the others are what a person judges before keeping the guard.

Standard library and git only. Nothing is written here and nothing is sent anywhere.
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
import threading
from typing import Any

from brevio.pipeline.agent_host import gitsafe
from brevio.pipeline.code_rules import scan_lines
from brevio.pipeline.gates import defect_compiler, guard_scan

REPLAY_COMMITS = 50         # the commits a replay looks at, newest first, unless ``--replay`` says otherwise
GIT_TIMEOUT = 60            # seconds one git call may take
MAX_LOG_CHARS = 20_000_000  # the history text a replay reads at most; past it the replay says it stopped
CREDENTIAL_RULE = "R13.1"
_RECORD = "\x1e"
_NUL = "\x00"
# What R13.1 does not read as a credential but a fix often removes: a quoted value bound to a secret-named variable, a URL
# with a password in it, and a bearer token. A guard keeps the removed line as text in a committed file, so these are kept
# out as well (found by the review of the learn stage, 2026-11-12).
_CREDENTIAL_SHAPES = re.compile(
    r"""(?ix)
    (?:pass(?:word|wd)?|secret|token|api[_-]?key|auth\w*)\w*["']?\s*[:=]\s*["'][^"'\s]{6,}["']
    | [a-z][a-z0-9+.-]*://[^/\s:@"']+:[^/\s@"']+@
    | \bbearer\s+[A-Za-z0-9._~+/=-]{12,}""")


def git(cwd: str, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run([*gitsafe.GIT, "-c", "core.quotepath=off", *args], cwd=cwd, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout or ""


def capped_git(cwd: str, cap: int, *args: str) -> tuple[int, str, bool]:
    """``(exit code, output, cut)`` of a git call whose output is read up to ``cap`` characters and then dropped: the
    history of a repository holding a huge text file would otherwise be read into memory whole (2026-11-12, improvement
    plan). A call past ``GIT_TIMEOUT`` seconds is stopped. ``cut`` says the output was longer than ``cap``."""
    try:
        proc = subprocess.Popen([*gitsafe.GIT, "-c", "core.quotepath=off", *args], cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return 1, "", False
    timer = threading.Timer(GIT_TIMEOUT, proc.kill)
    timer.start()
    try:
        data = proc.stdout.read(cap * 4 + 1) if proc.stdout is not None else b""     # up to four bytes a character
    finally:
        timer.cancel()
    cut = len(data) > cap * 4
    if cut:
        proc.kill()
    if proc.stdout is not None:
        proc.stdout.close()
    code = proc.wait()
    return (0 if cut else code), data.decode("utf-8", "replace")[:cap], cut


def fixed_side(path: str, at: str | None, cwd: str) -> list[str] | None:
    """The lines of ``path`` as the fix leaves it: the working tree (``at`` None), the index (``at`` ""), or the
    revision ``at``; ``None`` when that is not readable."""
    if at is None:
        code, top = git(cwd, "rev-parse", "--show-toplevel")
        try:
            with open(os.path.join(top.strip() if code == 0 and top.strip() else cwd, path), encoding="utf-8", errors="ignore") as fh:
                return fh.read().splitlines()
        except OSError:
            return None
    code, out = git(cwd, "show", f":{path}" if at == "" else f"{at}:{path}")
    return out.splitlines() if code == 0 else None


def credential_in(guard: dict[str, Any]) -> bool:
    """A pattern of the guard looks like a credential: it is not kept, and what is printed about it is hidden."""
    source = str(guard.get("source_file", ""))
    patterns = [p for p in guard.get("patterns", []) if isinstance(p, str)]
    return (any(f"[{CREDENTIAL_RULE}]" in f for p in patterns for f in scan_lines(source, [(1, p)]))
            or any(_CREDENTIAL_SHAPES.search(p) for p in patterns))


def masked(guard: dict[str, Any]) -> dict[str, Any]:
    """``guard`` as it may be shown: the text taken from the fix is hidden when a pattern looks like a credential."""
    if not credential_in(guard):
        return guard
    hidden = "(hidden: it looks like a credential)"
    return {**guard, "id": hidden, "title": hidden, "patterns": [hidden], "rule_file": hidden}


def _fixed_rows(guard: dict[str, Any], diff_text: str, fixed: list[str] | None) -> list[tuple[int, str]]:
    """The numbered, folded lines of the fixed file, or of the lines the fix added when the file is not readable."""
    source = str(guard.get("source_file", ""))
    after = fixed if fixed is not None else defect_compiler.added_lines(diff_text).get(source, [])
    return [(n, guard_scan.fold(t)) for n, t in enumerate(after, 1)]


def without_incidental(guard: dict[str, Any], diff_text: str, fixed: list[str] | None,
                       name: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """``(guard, dropped)``: ``guard`` without the patterns that alone match a line of the fixed file (a line the fix moved,
    like an import), and those patterns. The id, title and rule file are made again from the first pattern left. When no
    pattern would be left the guard is returned as it is, and the control run refuses it (2026-11-13, I32)."""
    rows = _fixed_rows(guard, diff_text, fixed)
    patterns = [p for p in guard.get("patterns", []) if isinstance(p, str)]
    kept = [p for p in patterns if not guard_scan.matching_rows(guard_scan.compile_patterns({"patterns": [p]}), rows)]
    if not kept or len(kept) == len(patterns):
        return guard, []
    trimmed = {**guard, "patterns": kept}
    if len(kept) < 2:
        trimmed.pop("match", None)
    return defect_compiler.retitle(trimmed, name), [p for p in patterns if p not in kept]


def control_run(guard: dict[str, Any], diff_text: str, fixed: list[str] | None) -> list[str]:
    """The reasons ``guard`` is not kept; an empty list when it passes.

    ``fixed`` is the fixed file's lines; ``None`` checks the lines the fix added instead."""
    source = str(guard.get("source_file", ""))
    removed = defect_compiler.removed_lines(diff_text).get(source, [])
    pats = [p for p in guard.get("patterns", []) if isinstance(p, str)]
    reasons = ["a pattern looks like a credential, and guards are committed"] if credential_in(guard) else []
    missed = [p for p in pats if not guard_scan.guard_hits({**guard, "patterns": [p]}, {source: removed})]
    reasons += [f"`{p}` does not match the lines the fix removed" for p in missed]
    still = guard_scan.matching_rows(guard_scan.compile_patterns(guard), _fixed_rows(guard, diff_text, fixed),
                                     guard_scan.needs_all(guard))
    if still:
        reasons.append(f"it also matches {len(still)} line(s) of the fixed file, the first at line {still[0][0]}"
                       + ("" if fixed is not None else " of the lines the fix added"))
    return reasons


def _added(body: str) -> dict[str, list[str]]:
    """The added lines per file of one commit's ``-U0`` patch."""
    files: dict[str, list[str]] = {}
    current = ""
    for line in body.splitlines():
        if line.startswith("+++ "):
            name = line[4:].split("\t")[0]
            current = name[2:] if name.startswith("b/") else ""
        elif line.startswith("+") and current:
            files.setdefault(current, []).append(line[1:])
    return files


def _file_ok(guard: dict[str, Any], sha: str, path: str, cwd: str) -> bool:
    """The file at commit ``sha`` passes the guard's ``file_contains`` (a patch holds only the added lines, so the file is
    read once a line has matched); a guard with none passes without a git call."""
    if not guard.get("file_contains"):
        return True
    code, text = git(cwd, "show", f"{sha}:{path}")
    return code == 0 and guard_scan.file_ok(guard, text.splitlines())


def replay(guard: dict[str, Any], cwd: str, commits: int = REPLAY_COMMITS) -> dict[str, Any]:
    """What ``guard`` would have flagged in the added lines of the last ``commits`` commits.

    ``{"checked": commits looked at, "hits": [{"commit", "subject", "date", "file"}], "stopped": the history was cut,
    "failed": git could not give the history}``."""
    # ``show --do-walk`` walks the history like ``log -p`` does; ``show`` is among the read-only git subcommands the
    # security review lists, ``log`` is not.
    code, out, stopped = capped_git(cwd, MAX_LOG_CHARS, "show", "--do-walk", f"--max-count={max(1, commits)}", "--no-merges",
                                    "--no-renames", "-U0", "--date=short", "--format=%x1e%H%x00%s%x00%cd", "HEAD")
    if code != 0:
        return {"checked": 0, "hits": [], "stopped": False, "failed": True}
    pats, every = guard_scan.compile_patterns(guard), guard_scan.needs_all(guard)
    hits: list[dict[str, str]] = []
    records = out.split(_RECORD)[1:]
    for record in records:
        head, _, body = record.partition("\n")
        sha, _, rest = head.partition(_NUL)
        subject, _, date = rest.rpartition(_NUL)
        for path, lines in _added(body).items():
            rows = [(n, guard_scan.fold(t)) for n, t in enumerate(lines, 1)]
            if guard_scan.watches(guard, path) and guard_scan.matching_rows(pats, rows, every) and _file_ok(guard, sha, path, cwd):
                hits.append({"commit": sha[:12], "subject": subject, "date": date, "file": path})
    return {"checked": len(records), "hits": hits, "stopped": stopped}


def introduced_by(guard: dict[str, Any], result: dict[str, Any]) -> dict[str, str] | None:
    """The oldest change of a replay that holds the guard's lines, preferring the file of the fix: the change that most
    likely brought the line in. ``None`` when the replay found none (2026-11-13, I36)."""
    hits = result.get("hits") or []
    own = [h for h in hits if h["file"] == guard.get("source_file")]
    return (own or hits)[-1] if (own or hits) else None      # the replay lists the newest first


def broadened(guard: dict[str, Any], diff_text: str, fixed: list[str] | None, cwd: str,
              commits: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """``(guard, replay)`` for the call-shaped variant of ``guard`` (``yaml.load(`` for ``return yaml.load(f)``) when it
    passes the control run and its replay can be read; ``None`` when there is no variant or it fails (2026-11-13, I6)."""
    wide = defect_compiler.broad_variant(guard)
    if wide is None or control_run(wide, diff_text, fixed):
        return None
    result = replay(wide, cwd, commits)
    return None if result.get("failed") else (wide, result)


def stored(result: dict[str, Any], limit: int) -> dict[str, Any]:
    """What a kept guard records of its replay: how many changes were looked at, how many flagged, and which."""
    return {"checked": result["checked"], "limit": limit, "flagged": len({h["commit"] for h in result["hits"]}),
            "accepted": [f"{h['commit']}:{h['file']}" for h in result["hits"]],
            "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")}


def unexplained(guard: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """The ``commit:file`` hits of a replay that the guard's stored replay did not hold when it was kept."""
    held = guard.get("replay")
    known = set(held.get("accepted", [])) if isinstance(held, dict) else set()
    return [key for key in (f"{h['commit']}:{h['file']}" for h in result["hits"]) if key not in known]


def describe(result: dict[str, Any], shown: int = 10) -> list[str]:
    """The replay as the lines a person reads."""
    if result.get("failed"):
        return ["replay: the history could not be read, so nothing is known about past changes"]
    commits = len({h["commit"] for h in result["hits"]})
    lines = [f"replay: would have flagged {commits} of {result['checked']} past change(s)"
             + (" (history cut short)" if result.get("stopped") else "")]
    lines += [f"    {h['commit'][:7]} {h['file']}  {subject_shown(h['subject'])}" for h in result["hits"][:shown]]
    if len(result["hits"]) > shown:
        lines.append(f"    and {len(result['hits']) - shown} more; `git show <commit>` shows the line")
    return lines


def printable(text: str, limit: int = 80) -> str:
    """One printable line: a commit subject, like a guard's name in the file, is text from the repository."""
    return "".join(c if c.isprintable() else " " for c in str(text))[:limit]


def subject_shown(subject: str, limit: int = 80) -> str:
    """A commit subject as ``printable``, or a note when it looks like a credential: it is printed, and a title made of it
    is committed, so it is held to the rule a pattern is held to (2026-11-13, review of I36)."""
    if credential_in({"source_file": "subject.txt", "patterns": [str(subject)]}):
        return "(hidden: it looks like a credential)"
    return printable(subject, limit)
