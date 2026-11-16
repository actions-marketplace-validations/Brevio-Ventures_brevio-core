"""The R7.3 check of the flight check: an untracked file that looks like a copy of one that exists.

Split out of ``core_gates`` on 2026-11-15 so that file stays under 800 lines. Standard library plus exported package modules
only. Read-only git subcommands only. ``core_gates`` still offers ``check_orphan_files``, because the commands and the tests
call it there.
"""
from __future__ import annotations

import os
import re

from brevio.pipeline.gates.core_gate_lines import CODE_SUFFIXES, git

_DUP_FILE_RE = re.compile(r"(?:_copy|_new|_v\d+|[_-]?2)\.[A-Za-z0-9]+$", re.I)
# The numbered forms are also real names (``v2.ts``, ``Vector2.ts``, ``handler_v2.ts``): they count as a copy only
# when the file without the number sits beside them, which also covers ``oauth2.py`` and ``http2.ts`` (2026-11-15,
# pipeline-gates-1-F15). This replaces the list of known names.
_NUMBERED_RE = re.compile(r"(?:_v\d+|[_-]?2)$", re.I)


def is_duplicate_code_file(root: str, rel: str) -> bool:
    """``rel`` ends in a copy marker; a numbered name also needs its original in the same folder (tracked or not)."""
    if not rel.endswith(CODE_SUFFIXES) or not _DUP_FILE_RE.search(rel):
        return False
    stem, ext = os.path.splitext(os.path.basename(rel))
    if not _NUMBERED_RE.search(stem):
        return True
    base_stem = _NUMBERED_RE.sub("", stem).rstrip("_-")
    return bool(base_stem) and _has_original(root, rel, base_stem, ext)


def _has_original(root: str, rel: str, base_stem: str, ext: str) -> bool:
    parent = os.path.dirname(rel)
    return os.path.isfile(os.path.join(root, parent, base_stem + ext))


def check_orphan_files(root: str = ".") -> list[str]:
    """Check for untracked numbered copies or duplicate code files."""
    findings: list[str] = []
    out = git(root, "ls-files", "--others", "--exclude-standard")
    for raw in out.splitlines():
        rel = raw.strip().replace(os.sep, "/")
        if is_duplicate_code_file(root, rel):
            findings.append(
                f"{rel}:1: [R7.3] Probable duplicate file `{os.path.basename(rel)}`; edit the canonical module in place"
            )
    return sorted(findings)
