"""The thin host adapter of the portable rule engines (ROADMAP 8.2 item 2, Stage 1A, 2026-11-03).

``dev_rules`` needs three things from its host: a hardened ``git`` command line, the lines a file adds or
loses against main, and the raw-palette test. They live here, on the standard library alone, so
``dev_rules``, ``code_rules`` and the AEO rubric export with no private import and no model call
(the harness's core exporter). The harness keeps its own copies of the git and palette helpers for its gates;
``tests/pipeline/test_rule_host.py`` pins this copy to them so the two cannot drift.
"""
from __future__ import annotations

import os
import re
import subprocess

# Same hardening as the harness's own git helper: a hook or fsmonitor in an agent-writable checkout never runs.
_GIT_BIN = "git"   # a name, so the bare-git scan sees no literal that skips the flags
GIT = (_GIT_BIN, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.ext.allow=never")
PALETTE = re.compile(r"\b(?:bg|text|border|ring|from|to|via)-(?:zinc|slate|gray|neutral|stone)-\d{2,3}\b")
_TEST = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$|(^|/)(__tests__|tests?|e2e)/")


def ui_source(path: str) -> bool:
    """A file the palette check covers: not a test and not under ``docs/``."""
    return not _TEST.search(path) and not path.startswith("docs/")


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run([*GIT, "-C", cwd, *args],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _base(cwd: str) -> str:
    for ref in ("origin/main", "main"):
        if _git(cwd, "rev-parse", "--verify", "-q", ref).strip():
            return ref
    return "HEAD"


_HUNK_HEAD = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def diff_added_rows(diff: str) -> list[tuple[int, str]]:
    """``(new line number, text)`` for each added row of a diff. A hunk is read by the counts on its ``@@`` row, so an
    added ``++i;`` (shown as ``+++i;``) is a line and the ``+++ b/file`` header is not (2026-11-15, the family of
    pipeline-gates-1-F4)."""
    out: list[tuple[int, str]] = []
    old_left = new_left = new_no = 0
    for raw in diff.splitlines():
        if old_left or new_left:
            if raw.startswith("+"):
                out.append((new_no, raw[1:]))
                new_no += 1
                new_left = max(0, new_left - 1)
                continue
            if raw.startswith("-"):
                old_left = max(0, old_left - 1)
                continue
            if raw.startswith("\\"):
                continue
            if raw.startswith(" "):
                old_left, new_left, new_no = max(0, old_left - 1), max(0, new_left - 1), new_no + 1
                continue
            old_left = new_left = 0
        head = _HUNK_HEAD.match(raw)
        if head:
            old_left = int(head.group(1) if head.group(1) is not None else 1)
            new_no = int(head.group(2))
            new_left = int(head.group(3) if head.group(3) is not None else 1)
    return out


def base_ref(cwd: str) -> str:
    """The ref added lines are measured against: ``origin/main``, ``main`` or, with neither, ``HEAD``."""
    return _base(cwd)


def added_lines_numbered(cwd: str, files: list[str]) -> dict[str, list[tuple[int, str]]]:
    """``(line number, text)`` of the lines each file gains against main (committed or not). A file absent from main is
    all added; a file that main holds and the change only shortens has no added line (2026-11-15, pipeline-2-F7)."""
    out: dict[str, list[tuple[int, str]]] = {}
    base = _base(cwd)
    for f in files:
        diff = _git(cwd, "diff", "-U0", base, "--", f)
        rows = diff_added_rows(diff)
        if not rows and not _git(cwd, "ls-tree", base, "--", f).strip():
            try:
                with open(os.path.join(cwd, f), encoding="utf-8", errors="ignore") as fh:
                    rows = list(enumerate(fh.read().splitlines(), 1))
            except OSError:
                rows = []
        out[f] = rows
    return out


def added_lines(cwd: str, files: list[str]) -> dict[str, list[str]]:
    """The lines each file gains against main (committed or not); a new untracked file is all added."""
    return {f: [text for _, text in rows] for f, rows in added_lines_numbered(cwd, files).items()}


def removed_lines(cwd: str, files: list[str]) -> dict[str, list[str]]:
    """The lines each file loses against main (committed or not)."""
    base = _base(cwd)
    out: dict[str, list[str]] = {}
    for f in files:
        lines, in_hunk = [], False
        for ln in _git(cwd, "diff", "-U0", base, "--", f).splitlines():
            if ln.startswith("diff --git "):
                in_hunk = False
            elif ln.startswith("@@"):
                in_hunk = True
            elif in_hunk and ln.startswith("-"):
                lines.append(ln[1:])
        out[f] = lines
    return out


def head_added_lines(workspace_dir: str, rel_path: str) -> list[tuple[int, str]]:
    """``(line_no, text)`` for the lines ``rel_path`` adds against HEAD; empty when git has no diff for it."""
    try:
        res = subprocess.run([*GIT, "-C", workspace_dir, "diff", "-U0", "HEAD", "--", rel_path],
                             capture_output=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if res.returncode != 0 or not res.stdout.strip():
        return []
    return diff_added_rows(res.stdout)
