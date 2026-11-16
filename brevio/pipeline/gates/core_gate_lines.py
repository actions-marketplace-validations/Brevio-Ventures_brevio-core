"""The git calls of the flight check and the lines it reads: the changed lines of tracked files, and untracked files read when asked.

Split out of ``core_gates`` on 2026-11-14 so that file stays under 800 lines, and so ``check`` and ``heal`` read one file at a
time, as ``scan`` does (``core_diff.FileLines``). Standard library plus exported package modules only. Read-only git
subcommands only. ``core_gates`` still offers each name, because the commands and the tests call them there.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator, Mapping

from brevio.pipeline import core_cli
from brevio.pipeline.core_diff import FileLines
from brevio.pipeline.rule_host import GIT

Numbered = Mapping[str, list[tuple[int, str]]]
CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx")


def is_scanned_name(rel: str, sql: bool = False) -> bool:
    """A file ``check`` reads: a code file, the ``package.json`` manifest (R16.2, as ``core_cli`` and ``core_diff`` read it),
    and a ``.sql`` file when ``sql`` is set (2026-11-15, core-as-user-F3)."""
    return rel.endswith(CODE_SUFFIXES) or rel.rpartition("/")[2] == "package.json" or (sql and rel.endswith(".sql"))


def git_out(cwd: str, *args: str) -> str | None:
    """A git subcommand's output, or None when git could not run or failed (a timeout, no repository)."""
    try:
        r = subprocess.run([*GIT, "-c", "core.quotepath=off", "-C", cwd, *args], capture_output=True, encoding="utf-8", errors="replace", timeout=30)  # a non-ASCII path stays readable (2026-11-12)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def git(cwd: str, *args: str) -> str:
    """Like ``git_out``, with an empty string where it gives None."""
    return git_out(cwd, *args) or ""


def is_git_tree(root: str) -> bool:
    """``root`` holds a git checkout. ``.git`` is a file in a linked worktree and in a submodule, so exists, not isdir."""
    return os.path.exists(os.path.join(root, ".git"))


def resolve_base(root: str, base: str | None = "HEAD") -> str:
    """``base`` when git knows it, else the first of ``origin/main``, ``main`` and ``HEAD`` it knows."""
    cands = [b for b in (base, "origin/main", "main", "HEAD") if b]
    for cand in cands:
        if git(root, "rev-parse", "--verify", "-q", cand).strip():
            return cand
    return base or "HEAD"


class DiffLines(Mapping[str, list[tuple[int, str]]]):
    """The lines a flight check looks at: the added lines of tracked files, and untracked files read when asked and not kept.

    The added lines of a tracked file are the few lines of its change, so they are held. An untracked file counts whole, and a
    new repository can be all untracked files, so those are read one at a time (``FileLines``), and a pass over the files holds
    one file's lines at a time (2026-11-14, bounded memory for ``check``). It answers as the dict ``collect_diff_lines`` returned:
    same keys in the same order, and a path that is both tracked and untracked reads as the untracked file."""

    def __init__(self, added: Mapping[str, list[tuple[int, str]]], files: FileLines) -> None:
        self._added, self._files = added, files
        self._keys = list(dict.fromkeys([*added, *files]))

    def __getitem__(self, key: str) -> list[tuple[int, str]]:
        return self._files[key] if key in self._files else self._added[key]

    def __contains__(self, key: object) -> bool:
        return key in self._files or key in self._added     # the default would read the file to answer

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


def matches_filter(rel: str, paths: list[str] | None) -> bool:
    """``rel`` is among the paths named on the command line, or inside a folder named there (no paths: every file)."""
    if not paths:
        return True
    norm = rel.replace(os.sep, "/")
    for p in paths:
        clean = p.replace(os.sep, "/").rstrip("/")
        if not clean or clean == "." or norm == clean or norm.startswith(clean + "/"):
            return True
    return False


def collect_non_git_code_lines(root: str, paths: list[str] | None) -> FileLines:
    """Every code file under ``root`` that is not skipped, read when asked (a folder that is no git checkout has no diff)."""
    out: dict[str, str] = {}
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = core_cli.prune_dirs(dirpath, dirs)
        for n in sorted(names):
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if is_scanned_name(rel) and not core_cli.skipped(rel) and matches_filter(rel, paths):
                out[rel] = full
    return FileLines(out, resolved=True)


def collect_untracked_lines(root: str, paths: list[str] | None) -> FileLines:
    """The untracked code, SQL and manifest files, not skipped and not minified, read when asked."""
    out: dict[str, str] = {}
    # Before the first commit there is no HEAD to diff, and a staged file is no longer listed as untracked: read the index
    # too, as ``scan --diff`` does, so a repository with its files only staged is not checked as empty (2026-11-15).
    unborn = is_git_tree(root) and not git(root, "rev-parse", "--verify", "-q", "HEAD").strip()
    named = git(root, "ls-files", *(["--cached"] if unborn else []), "--others", "--exclude-standard").splitlines()
    for raw in dict.fromkeys(named):
        rel = raw.strip().replace(os.sep, "/")
        if is_scanned_name(rel, sql=True) and not core_cli.skipped(rel) and matches_filter(rel, paths):
            full = os.path.join(root, rel)
            if os.path.isfile(full) and not core_cli.bundled(full):
                out[rel] = full
    return FileLines(out, resolved=True)


def oversize_untracked(root: str, paths: list[str] | None) -> list[str]:
    """The untracked code files ``check`` cannot read because each is over ``MAX_BYTES``: named, so the skip is not silent."""
    out = []
    for rel in sorted(collect_untracked_lines(root, paths)):
        try:
            if os.path.getsize(os.path.join(root, rel)) > core_cli.MAX_BYTES:
                out.append(rel)
        except OSError:
            continue
    return out


def collect_diff_lines(root: str = ".", base: str = "HEAD", paths: list[str] | None = None) -> Numbered:
    """The modified lines of tracked code files against ``base`` (with their real line numbers), and the untracked files."""
    added: dict[str, list[tuple[int, str]]] = {}
    diff_text = git(root, "diff", "-U0", "--no-color", "--diff-filter=ACMR", resolve_base(root, base))
    path = ""
    cur_line = 0
    in_hunk = False     # header lines are read only outside a hunk: inside one, an added "++i;" shows as "+++i;"
    for raw in diff_text.split("\n"):       # as git numbers lines: a form feed in an added line is not a break (2026-11-15)
        raw = raw[:-1] if raw.endswith("\r") else raw
        if raw.startswith("diff --git"):
            path, in_hunk = "", False
        elif raw.startswith("@@"):
            in_hunk = True
            m = re.search(r"\+(\d+)", raw)
            cur_line = int(m.group(1)) if m else 0
        elif in_hunk:
            if path and raw.startswith("+"):
                added.setdefault(path, []).append((cur_line, raw[1:]))
                cur_line += 1
        elif raw.startswith("+++ b/"):
            cand = raw[6:].rstrip("\t")        # git ends a name that holds a space with a tab, as core_diff reads it
            if core_cli.skipped(cand) or core_cli.bundled(os.path.join(root, cand)) or not matches_filter(cand, paths):
                path = ""
            else:
                path = cand
    return DiffLines(added, collect_untracked_lines(root, paths))
