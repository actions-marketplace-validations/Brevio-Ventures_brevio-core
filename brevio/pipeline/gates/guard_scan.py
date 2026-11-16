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
MAX_WAYS = REGEX_MAX_LINE ** 2     # two repeats that run to the end of a line, the most the unbounded limit lets through
_BRACES = re.compile(r"\{\d*(,\d*)?\}")
# The optional list of globs a guard does not watch (2026-11-13, guard exclude list). It is read from a file that comes with
# a pull request or from another repository, so a glob is held to bounds before it is matched.
EXCLUDE_KEY = "exclude_file_patterns"
MAX_GLOBS = 20
MAX_GLOB_CHARS = 200
MAX_WILDCARDS = 8


# A guard may name ``join_lines``: how many lines (at most ``MAX_JOIN``) of a call that opens a bracket and does not close
# it are read as one line. A call written over several lines escaped a pattern made for its one-line form (ROADMAP L611,
# 2026-11-15). A guard without the key reads one line at a time, as every guard did before. The joined text may be longer
# than a line, so such a guard's expression runs on up to ``JOINED_MAX_LINE`` characters; the expression's own limits
# (``risky``) still hold.
JOIN_KEY = "join_lines"
MAX_JOIN = 10
JOINED_MAX_LINE = REGEX_MAX_LINE * 3


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


def _brace_ways(text: str) -> int:
    """How many lengths a bounded ``{m}`` or ``{m,n}`` repeat can take; 1 for one without an upper end (counted as unbounded)."""
    low, comma, high = text[1:-1].partition(",")
    return int(high) - int(low or 0) + 1 if comma and high else 1


def risky(expr: str) -> str:
    """Why the regular expression ``expr`` is not run, or "" when it is: a repeated group that holds a repeat or an
    alternative, more than ``MAX_UNBOUNDED`` unbounded repeats, or bounded repeats and alternatives that multiply into more
    than ``MAX_WAYS`` ways to match (2026-11-14, C7: ``x?`` sixteen times took more than twenty seconds on one line)."""
    groups, unbounded, i = [False], 0, 0      # groups: whether each open group holds a repeat or an alternative
    alts, ways = [0], 1                        # alternatives per open group; the product of what the bounded repeats can take
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
                alts.append(0)
            elif c == ")":
                inner = groups.pop() if len(groups) > 1 else False
                ways *= (alts.pop() if len(alts) > 1 else 0) + 1
                groups[-1] = groups[-1] or inner
                if inner and expr[i:i + 1] in ("*", "+", "{"):
                    return "a repeated group holds a repeat or an alternative"
            elif c in "*+?|":
                groups[-1] = True
                unbounded += c in "*+"
                ways *= 2 if c == "?" else 1
                alts[-1] += c == "|"
            elif c == "{" and (m := _BRACES.match(expr, i - 1)):
                groups[-1] = True
                unbounded += m.group(1) is not None and m.group(0).endswith(",}")
                ways *= _brace_ways(m.group(0))
                i = m.end()
    if unbounded > MAX_UNBOUNDED:
        return f"more than {MAX_UNBOUNDED} unbounded repeats"
    return "its bounded repeats and alternatives can be tried in too many ways on one line" if ways > MAX_WAYS else ""


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


def _line_matches(compiled: tuple[str, re.Pattern[str] | None, str], ln: str, limit: int = REGEX_MAX_LINE) -> bool:
    _label, rx, lit = compiled
    if rx is not None:
        return len(ln) <= limit and bool(rx.search(ln))
    return lit in ln


def join_limit(guard: dict[str, Any]) -> int:
    """How many lines the guard reads as one for a call left open, or 0 (a guard that reads one line at a time)."""
    n = guard.get(JOIN_KEY)
    return min(n, MAX_JOIN) if isinstance(n, int) and not isinstance(n, bool) and n > 1 else 0


def joined_rows(guard: dict[str, Any], rows: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """``rows`` with each line that opens a bracket it does not close followed by the next lines (consecutive numbers,
    at most ``join_limit``) up to where the brackets balance, as one folded text under the first line's number.
    A guard with no ``join_lines`` gets ``rows`` back."""
    cap = join_limit(guard)
    if not cap:
        return rows
    out: list[tuple[int, str]] = []
    for i, (lno, text) in enumerate(rows):
        depth = sum(text.count(c) for c in "([{") - sum(text.count(c) for c in ")]}")
        end = i + 1
        while depth > 0 and end < len(rows) and end - i < cap and rows[end][0] == rows[end - 1][0] + 1:
            more = rows[end][1]
            depth += sum(more.count(c) for c in "([{") - sum(more.count(c) for c in ")]}")
            text = f"{text} {more}"
            end += 1
        out.append((lno, text))
    return out


def row_limit(guard: dict[str, Any]) -> int:
    """The longest text one of the guard's expressions runs on."""
    return JOINED_MAX_LINE if join_limit(guard) else REGEX_MAX_LINE


def glob_problem(glob: Any) -> str:
    """Why ``glob`` cannot be an entry of a guard's exclude list, or "" when it can: not short single-line text, a path that
    is absolute or leaves the repository (a match is always against a path inside it), or too many wildcards."""
    if not isinstance(glob, str) or not 0 < len(glob) <= MAX_GLOB_CHARS or not glob.isprintable():
        return f"a glob must be text of 1 to {MAX_GLOB_CHARS} printable characters on one line"
    if glob.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", glob):
        return "a glob is an absolute path"
    if ".." in glob.replace("\\", "/").split("/"):
        return "a glob leaves the repository with `..`"
    if sum(glob.count(c) for c in "*?[") > MAX_WILDCARDS:
        return f"a glob holds more than {MAX_WILDCARDS} wildcards"
    if not re.sub(r"\[[^\]]*\]?", "", glob).strip("*?/\\"):
        return "a glob of wildcards only names every file"      # ``*`` or ``**/*`` would switch the guard off (2026-11-14, review)
    return ""


_SAMPLE_NAMES = ("a", "src", "lib", "zz9")


def _samples(globs: list[Any]) -> list[str]:
    """Paths the guard's own globs name, made by putting a plain name where each glob has a wildcard, with and without the
    folder a leading ``**/`` stands for. They stand in for the tree, which a guard file cannot be tested against."""
    out: list[str] = []
    for glob in globs:
        if not isinstance(glob, str):
            continue
        for name in _SAMPLE_NAMES:
            plain = re.sub(r"\[[^\]]*\]", name[0], glob.replace("\\", "/"))
            for lead in (name + "/", ""):
                path = plain.replace("**/", lead).replace("**", name).replace("*", name).replace("?", name[0])
                if _glob_match(path, glob):
                    out.append(path)
    return out


def swallows(guard: dict[str, Any], entries: list[str]) -> bool:
    """The exclude ``entries`` together name every path the guard's watched globs name (as far as sample paths show), so the
    guard would watch nothing and say nothing. A guard file from another repository could do that to switch a guard off."""
    watched = _samples(guard.get("file_patterns", []))
    return bool(entries) and bool(watched) and all(any(_excluded(path, g) for g in entries) for path in watched)


def excludes_of(guard: dict[str, Any]) -> list[str]:
    """The entries of the guard's exclude list that are usable: text that passes ``glob_problem``, at most ``MAX_GLOBS``. A
    guard without the key, like every guard written before it existed, has none."""
    raw = guard.get(EXCLUDE_KEY)
    kept = [g for g in raw[:MAX_GLOBS] if not glob_problem(g)] if isinstance(raw, list) else []
    return [] if swallows(guard, kept) else kept        # a list that leaves nothing to watch is not used: the guard watches more


def exclude_problem(guard: dict[str, Any]) -> str:
    """Why the guard's exclude list is not used whole, or "" (no list, or every entry fine). An unusable entry is left out,
    so the guard watches more, not less."""
    raw = guard.get(EXCLUDE_KEY)
    if raw is None:
        return ""
    if not isinstance(raw, list):
        return "the exclude list is not a list"
    if len(raw) > MAX_GLOBS:
        return f"the exclude list holds more than {MAX_GLOBS} entries; the rest are not used"
    bad = next(((g, why) for g in raw if (why := glob_problem(g))), None)
    if bad is None:
        return "the exclude list names every path the guard watches, so it is not used" if swallows(guard, raw) else ""
    shown = f": `{bad[0][:40]}`" if isinstance(bad[0], str) and bad[0].isprintable() else ""     # never raw text from the file
    return bad[1] + shown


def _excluded(path: str, glob: str) -> bool:
    """Like ``_glob_match`` but the same on every system: ``fnmatch.fnmatch`` folds case on Windows only. A backslash in
    the glob reads as ``/``, as it does in the path, so ``tests\\*`` names the same files on Linux as on Windows."""
    path, glob = path.replace("\\", "/"), glob.replace("\\", "/")
    return fnmatch.fnmatchcase(path, glob) or fnmatch.fnmatchcase(path, glob.replace("**/", ""))


def excluded(guard: dict[str, Any], path: str) -> bool:
    """The guard's exclude list names ``path``."""
    return any(_excluded(path, g) for g in excludes_of(guard))


def watches(guard: dict[str, Any], path: str) -> bool:
    """The guard's globs name ``path`` and its exclude list does not. Every route to a hit (``scan``, the hook, the MCP
    server, the Action, ``learn --check``, the control run and the replay) asks this."""
    return any(_glob_match(path, g) for g in guard.get("file_patterns", [])) and not excluded(guard, path)


def reads_whole_file(guard: dict[str, Any]) -> bool:
    """The guard names ``file_contains`` or ``file_not_contains``, so the file itself must be read, not only a diff's lines."""
    return bool(guard.get("file_contains") or guard.get("file_not_contains"))


def file_ok(guard: dict[str, Any], lines: list[str]) -> bool:
    """The file passes the guard's optional ``file_contains``: a literal checked once against the file's lines before the
    line patterns run (a guard of the 0.9.1 file has none, so nothing changes for it). A line match cannot see a
    ``'use client'`` that sits on another line; this can. The optional ``file_not_contains`` is the same test turned round:
    a file that holds the literal anywhere passes nothing (2026-11-15, the EU AI Act pack's multi-line response)."""
    need = guard.get("file_contains")
    if isinstance(need, str) and need and not any(need in ln for ln in lines):
        return False
    barred = guard.get("file_not_contains")
    return not (isinstance(barred, str) and barred and any(barred in ln for ln in lines))


def compile_patterns(guard: dict[str, Any]) -> list[tuple[str, re.Pattern[str] | None, str]]:
    """The guard's patterns ready to match, once per scan."""
    return [_compile_pattern(p) for p in guard.get("patterns", []) if isinstance(p, str)]


def needs_all(guard: dict[str, Any]) -> bool:
    """The guard's ``match`` field says every pattern must be in the lines scanned (``"all"``); a guard without the field,
    like every guard written before it existed, hits on any one pattern."""
    return guard.get("match") == MATCH_ALL


def matching_rows(pats: list[tuple[str, re.Pattern[str] | None, str]], rows: list[tuple[int, str]],
                  every: bool = False, limit: int = REGEX_MAX_LINE) -> list[tuple[int, str]]:
    """``(line number, pattern)`` for each numbered line that holds a pattern (the first one that does); the text is
    whitespace-folded by ``fold`` already. With ``every`` nothing is returned unless each pattern is held by some line of
    ``rows`` (a guard whose ``match`` is ``"all"``: the lines of one file, or one commit's added lines)."""
    if every and not all(any(_line_matches(pat, text, limit) for _n, text in rows) for pat in pats):
        return []
    out = []
    for lno, text in rows:
        label = next((pat[0] for pat in pats if _line_matches(pat, text, limit)), None)
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
        normed = [t for _n, t in joined_rows(guard, [(n, _norm(ln)) for n, ln in enumerate(lines, 1)])]
        limit = row_limit(guard)
        found = [pat for pat in pats if any(_line_matches(pat, ln, limit) for ln in normed)]
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
            rows = joined_rows(guard, [(n, _norm(ln)) for n, ln in enumerate(lines, 1)])
            out += [(path, n) for n, _label in matching_rows(pats, rows, needs_all(guard), row_limit(guard))]
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
    recorded: list[tuple[str, str]] = []
    for gid, guard in guards.items():
        if guard.get("status") not in _LIVE:
            continue
        if not any(watches(guard, path) for path in files):
            continue  # nothing this guard watches was scanned: not a trial run
        hits = guard_hits(guard, files)
        for path, pattern in hits:
            found.append({"guard": gid, "title": guard.get("title", gid), "file": path, "pattern": pattern})
        if record:
            defect_compiler.evaluate_guard_trial(gid, caught=bool(hits), cwd=cwd)
            recorded.extend((gid, path) for path, _ in hits)
    dopamine_ledger.record_interceptions(recorded, cwd=cwd)     # one write for the whole scan
    return found
