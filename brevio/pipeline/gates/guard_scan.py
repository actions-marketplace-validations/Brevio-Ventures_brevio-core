"""Run the learned guards over changed files: zero tokens, no model call.

`brevio-core learn` compiles a fixed defect into `.brevio/guards.json`. This module
is the other half of the loop: it matches those guards against added lines, bumps
each guard's run counters and writes one
ledger event per caught defect, which `gain` then prints (ROADMAP §8.2 item 8/9,
2026-11-03).
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any

from brevio.pipeline.gates import defect_compiler, dopamine_ledger

_LIVE = defect_compiler.LIVE
MATCH_ALL = "all"
# A guard file comes with a pull request, and ``re`` has no time limit and holds the interpreter while it matches, so the
# scan runs a guard's regular expression only when it is short-lined and not of the shapes that take exponential time
# (a repeated group that repeats, a repeated alternative) or too many unbounded repeats (2026-11-12, improvement plan).
REGEX_MAX_LINE = 200
MAX_UNBOUNDED = 3
_BRACES = re.compile(r"\{\d*(,\d*)?\}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _glob_match(path: str, glob: str) -> bool:
    path = path.replace("\\", "/")
    return fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob.replace("**/", ""))


_REGEX_META_RE = re.compile(r"\.\*|\\[sSwWdDbB()]|\(\?[!:<=]")


def _class_end(expr: str, start: int) -> int:
    """The index after the ``[...]`` that opens at ``start``."""
    i = start + 1
    i += 1 if expr[i:i + 1] == "^" else 0
    i += 1 if expr[i:i + 1] == "]" else 0
    while i < len(expr) and expr[i] != "]":
        i += 2 if expr[i] == "\\" else 1
    return i + 1


def risky(expr: str) -> str:
    """Why the regular expression ``expr`` is not run, or "" when it is: a repeated group that holds a repeat or an
    alternative, or more than ``MAX_UNBOUNDED`` unbounded repeats."""
    groups, unbounded, i = [False], 0, 0      # groups: whether each open group holds a repeat or an alternative
    while i < len(expr):
        c = expr[i]
        if c == "\\":
            i += 2
        elif c == "[":
            i = _class_end(expr, i)
        else:
            i += 2 if c == "(" and expr.startswith("(?", i) else 1
            if c == "(":
                groups.append(False)
            elif c == ")":
                inner = groups.pop() if len(groups) > 1 else False
                groups[-1] = groups[-1] or inner
                if inner and expr[i:i + 1] in ("*", "+", "{"):
                    return "a repeated group holds a repeat or an alternative"
            elif c in "*+?|":
                groups[-1] = True
                unbounded += c in "*+"
            elif c == "{" and (m := _BRACES.match(expr, i - 1)):
                groups[-1] = True
                unbounded += m.group(1) is not None and m.group(0).endswith(",}")
                i = m.end()
    return f"more than {MAX_UNBOUNDED} unbounded repeats" if unbounded > MAX_UNBOUNDED else ""


def regex_problem(raw_pat: str) -> str:
    """Why a pattern that reads as a regular expression is not run as one, or "" (a literal, or a safe expression)."""
    if not (raw_pat.startswith("re:") or _REGEX_META_RE.search(raw_pat)):
        return ""
    return risky(raw_pat[3:] if raw_pat.startswith("re:") else raw_pat)


def _compile_pattern(raw_pat: str) -> tuple[str, re.Pattern[str] | None, str]:
    is_rx = (raw_pat.startswith("re:") or bool(_REGEX_META_RE.search(raw_pat))) and not regex_problem(raw_pat)
    expr = raw_pat[3:] if raw_pat.startswith("re:") else raw_pat
    if is_rx:
        try:
            return raw_pat, re.compile(expr), expr
        except re.error:
            pass
    lit = expr.replace("\\\\", "\\")
    return lit, None, lit


def _line_matches(compiled: tuple[str, re.Pattern[str] | None, str], ln: str) -> bool:
    _label, rx, lit = compiled
    if rx is not None:
        return len(ln) <= REGEX_MAX_LINE and bool(rx.search(ln))
    return lit in ln


def watches(guard: dict[str, Any], path: str) -> bool:
    """The guard's globs name ``path``."""
    return any(_glob_match(path, g) for g in guard.get("file_patterns", []))


def file_ok(guard: dict[str, Any], lines: list[str]) -> bool:
    """The file passes the guard's optional ``file_contains``: a literal checked once against the file's lines before the
    line patterns run (a guard of the 0.9.1 file has none, so nothing changes for it). A line match cannot see a
    ``'use client'`` that sits on another line; this can."""
    need = guard.get("file_contains")
    return not (isinstance(need, str) and need) or any(need in ln for ln in lines)


def compile_patterns(guard: dict[str, Any]) -> list[tuple[str, re.Pattern[str] | None, str]]:
    """The guard's patterns ready to match, once per scan."""
    return [_compile_pattern(p) for p in guard.get("patterns", []) if isinstance(p, str)]


def needs_all(guard: dict[str, Any]) -> bool:
    """The guard's ``match`` field says every pattern must be in the lines scanned (``"all"``); a guard without the field,
    like every guard written before it existed, hits on any one pattern."""
    return guard.get("match") == MATCH_ALL


def matching_rows(pats: list[tuple[str, re.Pattern[str] | None, str]], rows: list[tuple[int, str]],
                  every: bool = False) -> list[tuple[int, str]]:
    """``(line number, pattern)`` for each numbered line that holds a pattern (the first one that does); the text is
    whitespace-folded by ``fold`` already. With ``every`` nothing is returned unless each pattern is held by some line of
    ``rows`` (a guard whose ``match`` is ``"all"``: the lines of one file, or one commit's added lines)."""
    if every and not all(any(_line_matches(pat, text) for _n, text in rows) for pat in pats):
        return []
    out = []
    for lno, text in rows:
        label = next((pat[0] for pat in pats if _line_matches(pat, text)), None)
        if label is not None:
            out.append((lno, label))
    return out


def fold(text: str) -> str:
    """A line as the patterns are matched against it: whitespace folded and trimmed."""
    return _norm(text)


def guard_hits(guard: dict[str, Any], files: dict[str, list[str]]) -> list[tuple[str, str]]:
    """(file, pattern) pairs where an added line holds one of the guard's patterns (all of them, for a guard that needs
    all)."""
    hits: list[tuple[str, str]] = []
    pats = [_compile_pattern(p) for p in guard.get("patterns", [])]
    every = needs_all(guard)
    for path, lines in files.items():
        if not watches(guard, path) or not file_ok(guard, lines):
            continue
        normed = [_norm(ln) for ln in lines]
        found = [pat for pat in pats if any(_line_matches(pat, ln) for ln in normed)]
        hits.extend((path, pat[0]) for pat in found if not every or len(found) == len(pats))
    return hits


def line_hits(guard: dict[str, Any], files: dict[str, list[str]]) -> int:
    """How many lines of ``files`` ({path: lines}) hold one of the guard's patterns, in the files it watches."""
    return len(matching_lines(guard, files))


def matching_lines(guard: dict[str, Any], files: dict[str, list[str]]) -> list[tuple[str, int]]:
    """``(path, line number)`` of the lines of ``files`` that hold a pattern of the guard, in the files it watches."""
    pats = compile_patterns(guard)
    out: list[tuple[str, int]] = []
    for path, lines in files.items():
        if watches(guard, path) and file_ok(guard, lines):
            rows = [(n, _norm(ln)) for n, ln in enumerate(lines, 1)]
            out += [(path, n) for n, _label in matching_rows(pats, rows, needs_all(guard))]
    return out


def scan_files(
    files: dict[str, list[str]], cwd: str | None = None, record: bool = True
) -> list[dict[str, Any]]:
    """Check every live guard against `files` ({path: added lines}); return the hits.

    With `record`, each live guard counts one evaluated run and each hit adds a
    ledger interception (a trial guard is promoted by a person, not by these runs).
    """
    guards = defect_compiler.load_guards(cwd).get("guards", {})
    found: list[dict[str, Any]] = []
    for gid, guard in guards.items():
        if guard.get("status") not in _LIVE:
            continue
        if not any(_glob_match(path, g) for path in files for g in guard.get("file_patterns", [])):
            continue  # nothing this guard watches was scanned: not a trial run
        hits = guard_hits(guard, files)
        for path, pattern in hits:
            found.append({"guard": gid, "title": guard.get("title", gid), "file": path, "pattern": pattern})
        if record:
            defect_compiler.evaluate_guard_trial(gid, caught=bool(hits), cwd=cwd)
            for path, _ in hits:
                dopamine_ledger.record_interception(gid, path, cwd=cwd)
    return found
