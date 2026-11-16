"""Which files are agent instruction files, and where a scan finds them (the agent-file check of the pre-PR gate).

Git lists a repository's files for the check; a folder without git is walked. Both skip the same folders and find the
same files, in the same order, so the findings do not depend on whether git ran (2026-11-15).
"""

from __future__ import annotations

import os

from brevio.pipeline import core_cli
from brevio.pipeline.core_diff import SKIP_DIRS, VENDORED_DIRS
from brevio.pipeline.gates import core_gate_lines

_AGENT_FILE_NAMES = frozenset({".cursorrules", "CLAUDE.md", "AGENTS.md", "SKILL.md"})


def _is_agent_instruction_file(rel: str, name: str) -> bool:
    if name in _AGENT_FILE_NAMES or name.endswith(".mdc"):
        return True
    return name.endswith(".md") and (".cursor/rules/" in rel or ".agents/rules/" in rel)


_AGENT_HIDDEN_DIRS = (".cursor", ".agents", ".hermes")


def _agent_key(rel: str) -> tuple[tuple[str, ...], str]:
    """The order a walk meets agent files in: each folder's files by name, then its subfolders (2026-11-15)."""
    parts = rel.split("/")
    return tuple(parts[:-1]), parts[-1]


def _walk_agent_files(root: str) -> list[str]:
    """The agent instruction files under ``root`` (posix paths from ``root``), found by walking every folder not skipped."""
    found: list[str] = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in core_cli.prune_dirs(dirpath, dirs) if not d.startswith(".") or d in _AGENT_HIDDEN_DIRS]
        for n in names:
            rel = os.path.relpath(os.path.join(dirpath, n), root).replace(os.sep, "/")
            if _is_agent_instruction_file(rel, n):
                found.append(rel)
    return found


def _git_agent_files(root: str) -> list[str] | None:
    """The files ``_walk_agent_files`` finds, listed by git for a repository root (2026-11-15). Git tracked and untracked
    files, ignored ones included as the walk includes them, and it does not enter the skipped folders (a pathspec each),
    so a stop no longer walks node_modules. None when git cannot list the root: the caller walks."""
    skipped = sorted(SKIP_DIRS | VENDORED_DIRS)
    out = core_gate_lines.git_out(root, "ls-files", "-co", "-z", "--", ".", *(f":(exclude,glob)**/{d}/**" for d in skipped))
    if out is None:
        return None
    found: list[str] = []
    for rel in out.split("\0"):
        if not rel or rel.endswith("/"):           # a nested checkout is listed as one folder: the walk skips it too
            continue
        parts = rel.split("/")
        hidden = [d for d in parts[:-1] if d.startswith(".") and d not in _AGENT_HIDDEN_DIRS]
        if hidden or any(d in SKIP_DIRS or d in VENDORED_DIRS for d in parts[:-1]):
            continue
        if _is_agent_instruction_file(rel, parts[-1]):
            found.append(rel)
    return found


def agent_files(root: str) -> list[str]:
    """Agent instruction files under ``root`` in walk order: git's listing for a repository root, else a walk."""
    listed = _git_agent_files(root) if core_gate_lines.is_git_tree(root) else None
    return sorted(listed if listed is not None else _walk_agent_files(root), key=_agent_key)
