"""Which lines a diff scan reads: the git calls, the base to compare with and the lines the working tree adds (ROADMAP E16).

Split out of ``core_cli`` on 2026-11-13 so the command module does not grow. Standard library only, and it imports no
other module of the core, so ``core_cli`` can import it at the top. ``core_cli`` still offers each name (``_git``, ``_base``,
``_read``, ``diff_lines``, ``skipped``, ``bundled``, ``has_head`` and the constants), because the agent hook, the MCP
server, ``check`` and the other commands call them there.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable

from brevio.pipeline.rule_host import GIT

Numbered = dict[str, list[tuple[int, str]]]
_HUNK = re.compile(r"^@@ -\S+ \+(\d+)")

CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".html", ".sql", ".vue", ".svelte")
_AGENTS_DIR = "." + "agents"
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", _AGENTS_DIR, ".brevio"}
# Code a project keeps but did not write: vendored folders, minified files and bundle chunks. Their findings are the
# upstream's, and most of the false positives of the E35 precision run came from them (docs/benchmarks/precision.md,
# 2026-11-05). Every scan mode skips them; a project excludes more with `exclude` in brevio-core.toml.
VENDORED_DIRS = {"vendor", "third_party", "third-party"}
_BUNDLE_NAME = re.compile(r"\.min\.(?:[cm]?js|css)$|\.(?:bundle|chunk)\.[cm]?js$|^chunk-[\w-]+\.[cm]?js$"
                          r"|[.-](?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\.[cm]?js$")
LONG_LINE = 500         # a file whose first two lines are longer than this is minified or bundled
MAX_BYTES = 1_000_000


def read(path: str) -> list[tuple[int, str]]:
    """The numbered lines of ``path``; a diff scan names files from the git root, so one missing here is read from there."""
    if not os.path.exists(path) and not os.path.isabs(path):
        path = os.path.join(git("rev-parse", "--show-toplevel").strip() or ".", path)
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return []
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return list(enumerate(fh.read().splitlines(), 1))
    except OSError:
        return []


def bundled(path: str) -> bool:
    """One of the first two lines of the file on disk is longer than ``LONG_LINE``: minified or bundled output."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return any(len(fh.readline(LONG_LINE + 2).rstrip("\r\n")) > LONG_LINE for _ in range(2))
    except OSError:
        return False


def git(*args: str) -> str:
    try:
        r = subprocess.run([*GIT, "-c", "core.quotepath=off", *args], capture_output=True, encoding="utf-8", errors="replace", timeout=120)  # a non-ASCII path is printed as it is, not as a quoted escape (2026-11-12)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def has_head() -> bool:
    """The current folder is a git checkout with at least one commit."""
    return bool(git("rev-parse", "--verify", "-q", "HEAD").strip())


def resolve(want: str) -> str:
    """``want`` when git knows it; with no ``want`` the first of ``origin/main``, ``main`` and ``HEAD`` it knows. A named
    base that is unknown is not resolved here: ``scan --diff BASE`` refuses it first (D27, 0.10.0)."""
    for ref in ([want] if want else ["origin/main", "main", "HEAD"]):
        if git("rev-parse", "--verify", "-q", ref).strip():
            return ref
    return "HEAD"


def diff_lines(base: str, only: Iterable[str] = ()) -> Numbered:
    """The lines the working tree adds against ``base`` (with their real line numbers), plus new untracked files.

    ``only`` narrows both to those paths (the agent hook asks for the one file an edit changed)."""
    spec = [f":(literal){p}" for p in only]
    out: Numbered = {}
    path, lno = "", 0
    for ln in git("diff", "-U0", "--no-color", "--diff-filter=ACMR", resolve(base), "--", *spec).splitlines():
        if ln.startswith("+++ "):
            path = ln[6:].rstrip("\t") if ln.startswith("+++ b/") else ""     # git ends the name with a tab when it holds a space
        elif ln.startswith("@@"):
            m = _HUNK.match(ln)
            lno = int(m.group(1)) if m else 0
        elif path and ln.startswith("+"):
            out.setdefault(path, []).append((lno, ln[1:]))
            lno += 1
    here = git("rev-parse", "--show-prefix").strip()     # ls-files names a file from the current folder, diff from the git root
    for new in git("ls-files", "--others", "--exclude-standard", "--", *spec).splitlines():
        if new.endswith(CODE_SUFFIXES) or new.rpartition("/")[2] == "package.json":     # a manifest is scanned too (R16.2)
            out[here + new] = read(new)
    # Build output and dependencies are never the change under review, tracked or not: an install in CI writes
    # build/lib copies of the package, and scanning those flagged the rule docs' own bad examples (2026-11-05).
    # Vendored, minified and bundled files are not the project's own code either (E35, 2026-11-05).
    return {p: v for p, v in out.items() if not skipped(p) and not bundled(p)}


def skipped(path: str) -> bool:
    """``path`` lies in a folder the scan never reads (``SKIP_DIRS``, ``VENDORED_DIRS`` or an ``*.egg-info`` folder),
    or is named as a minified file or a bundle chunk."""
    parts = path.replace("\\", "/").split("/")
    return (any(p in SKIP_DIRS or p in VENDORED_DIRS or p.endswith(".egg-info") for p in parts[:-1])
            or bool(_BUNDLE_NAME.search(parts[-1])))
