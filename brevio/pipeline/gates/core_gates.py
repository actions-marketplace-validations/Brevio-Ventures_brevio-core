"""The portable pre-PR flight check gate: conflict markers, test custody, test imports, complexity and rules.

Standard library plus exported package modules only. Read-only git subcommands only.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from typing import Any

from brevio.pipeline import core_cli
from brevio.pipeline.rule_host import GIT

HELP = "run the pre-PR flight check (rules, test custody, test imports, conflict markers and complexity)"

TEST_FILE_RE = re.compile(
    r"(?:^|/)(?:test_[^/]+\.py|[^/]+_test\.py|[^/]+\.(?:test|spec)\.[cm]?[jt]sx?)$",
    re.I,
)
_DUP_FILE_RE = re.compile(r"(?:_copy|_new|_v\d+|[_-]?2)\.[A-Za-z0-9]+$", re.I)
_KNOWN_TWO_STEMS = {
    "oauth2", "http2", "jinja2", "argon2", "blake2", "sha2", "select2", "sweetalert2",
    "pm2", "cuid2", "re2", "ec2", "db2", "psycopg2", "urllib2", "bz2", "aria2",
}
_CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx")

_COMMENT_LINE_RE = re.compile(r"^\s*(?:#|//|\*)")
_SQL_COMMENT_RE = re.compile(r"^\s*(?:#|//|\*|--|/\*)")
_TEST_SKIP_FOCUS_RE = re.compile(
    r"(?:\b(?:it|test|describe)\.(?:skip|only)\s*\(|\b(?:xit|xtest|xdescribe)\s*\(|@(?:pytest\.mark\.skip|unittest\.skip)\b)"
)
_TAUTOLOGICAL_ASSERT_RE = re.compile(
    r"(?:\bassert\s+(?:True|1\s*==\s*1)\b"
    r"|\bassert\s+([A-Za-z_]\w*)\s*==\s*\1\b"
    r"|\bexpect\s*\(\s*(?:true|1)\s*\)\s*\.to(?:Be|Equal)\s*\(\s*(?:true|1)\s*\)"
    r"|\bexpect\s*\(\s*([A-Za-z_]\w*)\s*\)\s*\.to(?:Be|Equal|StrictEqual)\s*\(\s*\2\s*\))"
)
_MIGRATION_PATH_RE = re.compile(
    r"(?:^|/)(?:migrations|prisma/migrations|alembic/versions|drizzle)/",
    re.I,
)
_DESTRUCTIVE_DDL_RE = re.compile(
    r"\b(?:DROP\s+(?:TABLE|COLUMN)|TRUNCATE\s+TABLE)\b",
    re.I,
)
_RELATIVE_IMPORT_RE = re.compile(
    r"""(?:(?:from|import)\s+['"](\.{1,2}/[^'"]+)['"]|require\(\s*['"](\.{1,2}/[^'"]+)['"]\s*\))"""
)
_IMPORT_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".json")
_INDEX_SUFFIXES = ("/index.ts", "/index.tsx", "/index.mts", "/index.cts", "/index.js", "/index.jsx")
_STRIP_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs")
_TROJAN_AGENT_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069\U000e0000-\U000e007f]")
_AGENT_FILE_NAMES = frozenset({".cursorrules", "CLAUDE.md", "AGENTS.md", "SKILL.md"})
_ENV_KEY_DEF_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_ENV_REF_RE = re.compile(
    r"""(?:process\.env\.([A-Z][A-Z0-9_]{2,})|os\.(?:environ(?:\.get)?\s*[\[\(]\s*['"]|getenv\s*\(\s*['"])([A-Z][A-Z0-9_]{2,}))"""
)
_STD_ENV_VARS = frozenset({
    "NODE_ENV", "CI", "PATH", "HOME", "USER", "SHELL", "PORT", "HOST",
    "TZ", "PWD", "LANG", "TERM", "TMPDIR", "DEBUG", "LOG_LEVEL", "PYTHONPATH",
})
_STD_ENV_PREFIXES = ("GITHUB_", "VERCEL_", "NEXT_RUNTIME", "PYTEST_", "npm_", "NPM_")


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run([*GIT, "-c", "core.quotepath=off", "-C", cwd, *args], capture_output=True, encoding="utf-8", errors="replace", timeout=30)  # a non-ASCII path stays readable (2026-11-12)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _resolve_base(root: str, base: str | None = "HEAD") -> str:
    cands = [b for b in (base, "origin/main", "main", "HEAD") if b]
    for cand in cands:
        if _git(root, "rev-parse", "--verify", "-q", cand).strip():
            return cand
    return base or "HEAD"


def _is_conflict_marker(text: str) -> bool:
    if text.startswith(("<<<<<<< ", ">>>>>>> ", "||||||| ")):
        return True
    if text.startswith("======="):
        rest = text[7:]
        return not rest or rest[0] in (" ", "\t", "\r", "\n")
    return False


def check_conflict_markers(lines: dict[str, list[tuple[int, str]]]) -> list[str]:
    """Check for merge conflict markers in changed lines."""
    findings: list[str] = []
    for rel, numbered in sorted(lines.items()):
        for lno, text in numbered:
            if _is_conflict_marker(text):
                findings.append(f"{rel}:{lno}: [R5.1] Merge conflict marker left in file")
    return findings


def _py_has_cut_import(content: str, path: str, py_ignored: set[str]) -> bool | None:
    try:
        tree = ast.parse(content, path)
    except SyntaxError:
        return None
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                return True
            if node.module:
                imported.append(node.module.split(".")[0])
    return any(m not in py_ignored for m in imported)


def _js_has_cut_import(content: str, test_frameworks: set[str]) -> bool:
    import_matches = re.findall(
        r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\))""",
        content,
    )
    modules = [(m[0] or m[1] or m[2]).split("/")[0] for m in import_matches if m[0] or m[1] or m[2]]
    return any(m not in test_frameworks for m in modules)


def check_test_imports(lines: dict[str, list[tuple[int, str]]], root: str = ".") -> list[str]:
    """Check that test files with assertions import the module under test."""
    findings: list[str] = []
    test_frameworks = {"unittest", "pytest", "vitest", "jest", "@jest", "assert", "node:assert", "chai"}
    py_ignored = test_frameworks | set(getattr(sys, "stdlib_module_names", ()))

    for rel in sorted(lines.keys()):
        if not TEST_FILE_RE.search(rel):
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            continue

        if not re.search(r"(?:\bassert(?:\s+|\()|self\.assert\w*|\bexpect\s*\(|pytest\.raises|assertRaises|assert\.\w+)", content):
            continue

        py_ok = _py_has_cut_import(content, path, py_ignored) if rel.endswith(".py") else None
        has_cut = _js_has_cut_import(content, test_frameworks) if py_ok is None else py_ok
        if not has_cut:
            findings.append(f"{rel}:1: [R6.1] Test file has assertions but imports no module under test")

    return findings


def _count_tests_and_asserts(text: str) -> tuple[int, int]:
    active = "\n".join(
        ln for ln in text.splitlines() if not re.match(r"^\s*(?:#|//|\*)", ln)
    )
    tests = len(re.findall(r"(?:def\s+test_\w+|(?:\b|^)(?:it|test)\s*\()", active))
    asserts = len(re.findall(r"(?:(?:\bassert(?:\s+|\())|(?:self\.assert\w*)|(?:\bexpect\s*\()|pytest\.raises|assertRaises|assert\.\w+)", active))
    return tests, asserts


def check_test_tampering(lines: dict[str, list[tuple[int, str]]]) -> list[str]:
    """Check for added test skips, focus markers, or tautological assertions."""
    findings: list[str] = []
    for rel, numbered in sorted(lines.items()):
        if not TEST_FILE_RE.search(rel):
            continue
        for lno, text in numbered:
            if _COMMENT_LINE_RE.match(text):
                continue
            if _TEST_SKIP_FOCUS_RE.search(text):
                findings.append(
                    f"{rel}:{lno}: [R20.3] Test skip or focus marker added; fix the failing test instead of silencing it"
                )
            if _TAUTOLOGICAL_ASSERT_RE.search(text):
                findings.append(
                    f"{rel}:{lno}: [R20.4] Tautological test assertion; assert real output or state"
                )
    return findings


_EMPTY_JS_TEST_RE = re.compile(
    r"""(?:it|test)\s*\(\s*['"][^'"]*['"]\s*,\s*(?:\([^)]*\)|function\s*\([^)]*\))\s*=>?\s*\{\s*\}\s*\)"""
)


def check_empty_test_blocks(
    lines: dict[str, list[tuple[int, str]]],
    root: str = ".",
) -> list[str]:
    """Check for empty test function or block definitions (R20.5)."""
    findings: list[str] = []
    for rel, numbered in sorted(lines.items()):
        if not TEST_FILE_RE.search(rel):
            continue
        path = os.path.join(root, rel)
        if rel.endswith(".py"):
            content = None
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                except OSError:
                    pass
            if content is not None:
                try:
                    tree = ast.parse(content, rel)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if node.name.startswith("test_"):
                                real_stmts = [
                                    s for s in node.body
                                    if not (
                                        isinstance(s, ast.Pass)
                                        or (
                                            isinstance(s, ast.Expr)
                                            and isinstance(s.value, ast.Constant)
                                        )
                                    )
                                ]
                                if not real_stmts:
                                    func_lines = set(range(node.lineno, getattr(node, "end_lineno", node.lineno) + 1))
                                    added_lnos = {lno for lno, _ in numbered}
                                    if (func_lines & added_lnos) or not numbered:
                                        findings.append(
                                            f"{rel}:{node.lineno}: [R20.5] Empty test block `{node.name}`; test must contain assertions"
                                        )
                except SyntaxError:
                    pass
        for lno, text in numbered:
            if _COMMENT_LINE_RE.match(text):
                continue
            if _EMPTY_JS_TEST_RE.search(text):
                findings.append(
                    f"{rel}:{lno}: [R20.5] Empty test block; test must contain assertions"
                )
            elif re.search(r"def\s+test_\w+\s*\([^)]*\)\s*:\s*(?:pass|\.\.\.)\s*$", text):
                findings.append(
                    f"{rel}:{lno}: [R20.5] Empty test block; test must contain assertions"
                )
    seen: set[str] = set()
    deduped: list[str] = []
    for f in findings:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    return deduped


_AI_SLOP_MARKERS_RE = re.compile(
    r"""(?i)(?:\bas an ai language model\b|certainly!\s*here (?:is|'s) the\b|certainly,\s*here (?:is|'s) the\b|let me know if you(?:'d|\s+would) like me to\b|here (?:is|'s) the updated (?:code|implementation)\b|i hope this helps\b)"""
)


def check_ai_slop_markers(lines: dict[str, list[tuple[int, str]]]) -> list[str]:
    """Check for conversational AI residue markers in added comments or strings."""
    findings: list[str] = []
    for rel, numbered in sorted(lines.items()):
        for lno, text in numbered:
            m = _AI_SLOP_MARKERS_RE.search(text)
            if m:
                findings.append(
                    f"{rel}:{lno}: [R19.3] Conversational AI marker detected in added code: `{m.group(0)}`"
                )
    return findings


def _check_weakened_test(root: str, rel: str, old_content: str, resolved: str) -> str | None:
    path = os.path.join(root, rel)
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            new_content = fh.read()
    except OSError:
        return None
    return weakened_test(rel, old_content, new_content, resolved)


def weakened_test(rel: str, old_content: str, new_content: str, resolved: str) -> str | None:
    """A finding when ``new_content`` of a test file holds fewer tests or assertions than ``old_content`` (``resolved``)."""
    old_tests, old_asserts = _count_tests_and_asserts(old_content)
    new_tests, new_asserts = _count_tests_and_asserts(new_content)
    if new_tests < old_tests or new_asserts < old_asserts:
        return (
            f"{rel}:1: [R20.2] Existing test assertions were removed or weakened against {resolved} "
            f"({old_asserts} -> {new_asserts} assertions)"
        )
    return None


def check_test_custody(
    root: str = ".",
    base: str = "HEAD",
    lines: dict[str, list[tuple[int, str]]] | None = None,
) -> list[str]:
    """Check that existing tests and assertions are preserved against base."""
    findings: list[str] = []
    resolved = _resolve_base(root, base)
    diff_output = _git(root, "diff", "--name-status", resolved)
    for line in diff_output.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            old_rel = parts[1].replace(os.sep, "/")
            new_rel = parts[2].replace(os.sep, "/")
            if not TEST_FILE_RE.search(old_rel):
                continue
            if not TEST_FILE_RE.search(new_rel):
                findings.append(
                    f"{old_rel}:1: [R20.1] Existing test file was renamed to non-test `{new_rel}` against {resolved}; restore it or get human review"
                )
                continue
            old_content = _git(root, "show", f"{resolved}:{old_rel}")
            weakened = _check_weakened_test(root, new_rel, old_content, resolved)
            if weakened:
                findings.append(weakened)
            continue

        rel = parts[-1].replace(os.sep, "/")
        if not TEST_FILE_RE.search(rel):
            continue
        if status.startswith("D"):
            findings.append(
                f"{rel}:1: [R20.1] Existing test file was deleted against {resolved}; restore it or get human review"
            )
        elif status.startswith("M"):
            old_content = _git(root, "show", f"{resolved}:{rel}")
            weakened = _check_weakened_test(root, rel, old_content, resolved)
            if weakened:
                findings.append(weakened)

    if lines is not None:
        findings.extend(check_test_tampering(lines))
    return findings


def check_migration_safety(
    lines: dict[str, list[tuple[int, str]]] | str = ".",
    root: str = ".",
    base: str = "HEAD",
    paths: list[str] | None = None,
) -> list[str]:
    """Check that committed migrations are not modified/deleted and no destructive DDL is added."""
    if isinstance(lines, str):
        root = lines
        lines = collect_diff_lines(root, base, paths=paths)
    findings: list[str] = []
    resolved = _resolve_base(root, base)
    diff_output = _git(root, "diff", "--name-status", resolved)
    for raw in diff_output.splitlines():
        parts = raw.strip().split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        if status.startswith(("M", "D")):
            rel = parts[-1].replace(os.sep, "/")
            if _MIGRATION_PATH_RE.search(rel):
                findings.append(
                    f"{rel}:1: [R4.6] Committed migration file was modified or deleted against {resolved}; create a new migration file instead"
                )

    for rel, numbered in sorted(lines.items()):
        if not (_MIGRATION_PATH_RE.search(rel) or rel.endswith(".sql")):
            continue
        for lno, text in numbered:
            if _SQL_COMMENT_RE.match(text) or "brevio: allow-drop" in text:
                continue
            if _DESTRUCTIVE_DDL_RE.search(text):
                findings.append(
                    f"{rel}:{lno}: [R4.6] Destructive DDL detected; use additive expand-and-contract migrations"
                )
    return findings


def _candidate_import_paths(dirpath: str, spec: str) -> list[str]:
    target = os.path.normpath(os.path.join(dirpath, spec))
    candidates = [target]
    for ext in _IMPORT_EXTENSIONS:
        candidates.append(target + ext)
    for idx in _INDEX_SUFFIXES:
        candidates.append(target + idx)
    for suf in _STRIP_SUFFIXES:
        if target.endswith(suf):
            stem = target[:-len(suf)]
            candidates.append(stem)
            for ext in _IMPORT_EXTENSIONS:
                candidates.append(stem + ext)
            for idx in _INDEX_SUFFIXES:
                candidates.append(stem + idx)
    return candidates


def _import_exists(dirpath: str, spec: str) -> bool:
    return any(os.path.isfile(c) for c in _candidate_import_paths(dirpath, spec))


_resolve_relative_ts_import = _import_exists


def _check_line_imports(
    rel: str,
    lno: int,
    text: str,
    dirpath: str,
    has_pkg: bool,
) -> list[str]:
    if _COMMENT_LINE_RE.match(text):
        return []
    has_directive = "brevio-check-imports" in text
    if not has_pkg and not has_directive:
        return []
    findings: list[str] = []
    for m in _RELATIVE_IMPORT_RE.finditer(text):
        spec = m.group(1) or m.group(2)
        if spec and not _import_exists(dirpath, spec):
            findings.append(
                f"{rel}:{lno}: [R9.4] Unresolved relative import `{spec}`; target module does not exist on disk"
            )
    return findings


def check_resolved_imports(
    lines: dict[str, list[tuple[int, str]]] | str = ".",
    root: str = ".",
    base: str = "HEAD",
    paths: list[str] | None = None,
) -> list[str]:
    """Check that relative imports in JS/TS files resolve to existing files on disk."""
    if isinstance(lines, str):
        root = lines
        lines = collect_diff_lines(root, base, paths=paths)
    if not os.path.isdir(os.path.join(root, ".git")):
        return []

    has_pkg = (
        os.path.isfile(os.path.join(root, "package.json"))
        or os.path.isfile(os.path.join(root, "tsconfig.json"))
    )

    findings: list[str] = []
    for rel, numbered in sorted(lines.items()):
        if not rel.endswith((".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")) or TEST_FILE_RE.search(rel):
            continue
        full_path = os.path.join(root, rel)
        if not os.path.isfile(full_path):
            continue

        dirpath = os.path.dirname(full_path)
        for lno, text in numbered:
            findings.extend(_check_line_imports(rel, lno, text, dirpath, has_pkg))
    return findings


def _is_agent_instruction_file(rel: str, name: str) -> bool:
    if name in _AGENT_FILE_NAMES or name.endswith(".mdc"):
        return True
    return name.endswith(".md") and (".cursor/rules/" in rel or ".agents/rules/" in rel)


def check_agent_files(root: str = ".", paths: list[str] | None = None) -> list[str]:
    """Scan agent instruction files for hidden Unicode bidirectional or tag prompt-injection characters."""
    findings: list[str] = []
    if not os.path.isdir(root):
        return findings
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in core_cli.prune_dirs(dirpath, dirs) if not d.startswith(".") or d in (".cursor", ".agents", ".hermes")]
        for n in sorted(names):
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if not _is_agent_instruction_file(rel, n) or not _matches_filter(rel, paths):
                continue
            try:
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    file_lines = fh.read().splitlines()
            except OSError:
                continue
            for lno, text in enumerate(file_lines, 1):
                if _TROJAN_AGENT_RE.search(text):
                    findings.append(
                        f"{rel}:{lno}: [R2.8] Hidden Unicode bidirectional or tag characters in agent instruction file; "
                        "remove invisible prompt-injection characters"
                    )
    return findings


def _parse_env_example(env_path: str) -> set[str]:
    defined: set[str] = set()
    try:
        with open(env_path, encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                ln = raw.strip()
                if not ln or ln.startswith("#"):
                    continue
                m = _ENV_KEY_DEF_RE.match(ln)
                if m:
                    defined.add(m.group(1))
    except OSError:
        pass
    return defined


def check_env_parity(
    root: str | dict[str, list[tuple[int, str]]] = ".",
    base: str = "HEAD",
    paths: list[str] | None = None,
    lines: dict[str, list[tuple[int, str]]] | None = None,
) -> list[str]:
    """Check that environment variables referenced in added non-test code lines are documented in .env.example."""
    if isinstance(root, dict):
        lines = root
        root = "."
    env_path = os.path.join(root, ".env.example")
    if not os.path.isfile(env_path):
        return []
    defined = _parse_env_example(env_path)
    if lines is None:
        lines = collect_diff_lines(root, base, paths=paths)
        if not lines and not os.path.isdir(os.path.join(root, ".git")):
            lines = _collect_non_git_code_lines(root, paths)
    findings: list[str] = []
    for rel, numbered in sorted(lines.items()):
        if not rel.endswith(_CODE_SUFFIXES) or TEST_FILE_RE.search(rel) or not _matches_filter(rel, paths):
            continue
        for lno, text in numbered:
            if _COMMENT_LINE_RE.match(text):
                continue
            for m in _ENV_REF_RE.finditer(text):
                var = m.group(1) or m.group(2)
                if var and var not in defined and var not in _STD_ENV_VARS and not var.startswith(_STD_ENV_PREFIXES):
                    findings.append(
                        f"{rel}:{lno}: [R16.3] Environment variable `{var}` is not documented in .env.example"
                    )
    return findings


def _collect_non_git_code_lines(root: str, paths: list[str] | None) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = core_cli.prune_dirs(dirpath, dirs)
        for n in sorted(names):
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel.endswith(_CODE_SUFFIXES) and not core_cli.skipped(rel) and _matches_filter(rel, paths):
                try:
                    with open(full, encoding="utf-8", errors="ignore") as fh:
                        out[rel] = list(enumerate(fh.read().splitlines(), 1))
                except OSError:
                    pass
    return out


def _function_complexity(fn_node: ast.AST) -> int:
    """Compute McCabe cyclomatic complexity of a function without descending into nested definitions."""
    cc = 1
    stack = list(getattr(fn_node, "body", []))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(
            n,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ExceptHandler,
                ast.With,
                ast.AsyncWith,
                ast.Assert,
                ast.IfExp,
            ),
        ):
            cc += 1
        elif isinstance(n, ast.BoolOp) and len(n.values) > 1:
            cc += len(n.values) - 1
        stack.extend(ast.iter_child_nodes(n))
    return cc


def check_ast_complexity(lines: dict[str, list[tuple[int, str]]], root: str = ".") -> list[str]:
    """Check length and cyclomatic complexity of functions overlapping changed lines."""
    findings: list[str] = []
    for rel, numbered in sorted(lines.items()):
        if not rel.endswith(".py") or TEST_FILE_RE.search(rel):
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                src = fh.read()
            tree = ast.parse(src, path)
        except (OSError, SyntaxError):
            continue

        changed_lnos = {lno for lno, _ in numbered}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", node.lineno)
                if not any(start <= lno <= end for lno in changed_lnos):
                    continue
                length = end - start + 1
                if length > 100:
                    findings.append(
                        f"{rel}:{node.lineno}: [R19.1] Function `{node.name}` is {length} lines (limit 100); extract helper functions"
                    )
                cc = _function_complexity(node)
                if cc > 20:
                    findings.append(
                        f"{rel}:{node.lineno}: [R19.2] Function `{node.name}` has cyclomatic complexity {cc} (limit 20)"
                    )
    return findings


def _is_duplicate_code_file(root: str, rel: str) -> bool:
    if not rel.endswith(_CODE_SUFFIXES) or not _DUP_FILE_RE.search(rel):
        return False
    stem, ext = os.path.splitext(os.path.basename(rel))
    if stem.lower() in _KNOWN_TWO_STEMS:
        parent = os.path.dirname(rel)
        base_stem = stem[:-1].rstrip("_-")
        cand = os.path.join(parent, base_stem + ext) if parent else base_stem + ext
        return bool(base_stem and os.path.isfile(os.path.join(root, cand)))
    return True


def check_orphan_files(root: str = ".") -> list[str]:
    """Check for untracked numbered copies or duplicate code files."""
    findings: list[str] = []
    out = _git(root, "ls-files", "--others", "--exclude-standard")
    for raw in out.splitlines():
        rel = raw.strip().replace(os.sep, "/")
        if _is_duplicate_code_file(root, rel):
            findings.append(
                f"{rel}:1: [R7.3] Probable duplicate file `{os.path.basename(rel)}`; edit the canonical module in place"
            )
    return sorted(findings)


def _matches_filter(rel: str, paths: list[str] | None) -> bool:
    if not paths:
        return True
    norm = rel.replace(os.sep, "/")
    for p in paths:
        clean = p.replace(os.sep, "/").rstrip("/")
        if not clean or clean == "." or norm == clean or norm.startswith(clean + "/"):
            return True
    return False


def _collect_untracked_lines(
    root: str,
    paths: list[str] | None,
) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    for raw in untracked.splitlines():
        rel = raw.strip().replace(os.sep, "/")
        is_code = rel.endswith(_CODE_SUFFIXES) or rel.endswith(".sql")
        if is_code and not core_cli.skipped(rel) and _matches_filter(rel, paths):
            full = os.path.join(root, rel)
            if core_cli.bundled(full):
                continue
            try:
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    out[rel] = list(enumerate(fh.read().splitlines(), 1))
            except OSError:
                pass
    return out


def collect_diff_lines(
    root: str = ".",
    base: str = "HEAD",
    paths: list[str] | None = None,
) -> dict[str, list[tuple[int, str]]]:
    """Collect modified and untracked code file lines against base."""
    out: dict[str, list[tuple[int, str]]] = {}
    resolved = _resolve_base(root, base)
    diff_text = _git(root, "diff", "-U0", "--no-color", "--diff-filter=ACMR", resolved)
    path = ""
    cur_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            cand = raw[6:]
            full_cand = os.path.join(root, cand)
            if core_cli.skipped(cand) or core_cli.bundled(full_cand) or not _matches_filter(cand, paths):
                path = ""
            else:
                path = cand
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            cur_line = int(m.group(1)) if m else 0
        elif path and raw.startswith("+") and not raw.startswith("+++"):
            out.setdefault(path, []).append((cur_line, raw[1:]))
            cur_line += 1
    out.update(_collect_untracked_lines(root, paths))
    return out


def run_check(
    root: str = ".",
    base: str = "HEAD",
    lines: dict[str, list[tuple[int, str]]] | None = None,
    paths: list[str] | None = None,
    diff: str | None = None,
) -> dict[str, Any]:
    """Run all flight checks: rules scan, conflict markers, test imports, test custody, complexity, and orphan files."""
    if lines is None:
        lines = collect_diff_lines(root, base, paths=paths)

    agent_findings = check_agent_files(root=root, paths=paths)
    env_findings = check_env_parity(root=root, base=base, paths=paths, lines=lines)
    rule_findings = (
        core_cli.scan_surface(lines, root=root)
        + check_migration_safety(lines, root=root, base=base)
        + check_resolved_imports(lines, root=root)
        + check_empty_test_blocks(lines, root=root)
        + check_ai_slop_markers(lines)
        + agent_findings
        + env_findings
    )
    rule_findings, _, _ = core_cli.triage(rule_findings, root=root, whole=sorted(lines))      # as ``scan --diff`` does (2026-11-13)

    conflict_findings = check_conflict_markers(lines)
    test_import_findings = check_test_imports(lines, root=root)
    custody_findings = check_test_custody(root=root, base=base, lines=lines)
    complexity_findings = check_ast_complexity(lines, root=root)
    orphan_findings = check_orphan_files(root=root)

    all_findings = sorted(
        set(
            rule_findings
            + conflict_findings
            + test_import_findings
            + custody_findings
            + complexity_findings
            + orphan_findings
        )
    )
    passed = len(all_findings) == 0

    return {
        "ok": passed,
        "passed": passed,
        "base": _resolve_base(root, base),
        "findings": all_findings,
        "checks": {
            "rules": rule_findings,
            "conflict_markers": conflict_findings,
            "test_imports": test_import_findings,
            "test_custody": custody_findings,
            "complexity": complexity_findings,
            "orphan_files": orphan_findings,
            "agent_files": agent_findings,
            "env_parity": env_findings,
        },
        "counts": {
            "total": len(all_findings),
            "rules": len(rule_findings),
            "conflict_markers": len(conflict_findings),
            "test_imports": len(test_import_findings),
            "test_custody": len(custody_findings),
            "complexity": len(complexity_findings),
            "orphan_files": len(orphan_findings),
            "agent_files": len(agent_findings),
            "env_parity": len(env_findings),
        },
    }


def _render_markdown_receipt(result: dict[str, Any]) -> str:
    passed = result["passed"]
    findings = result["findings"]
    counts = result.get("counts", {})
    status_badge = "**PASSED**" if passed else f"**FAILED** ({len(findings)} findings)"
    lines = [
        "### Pre-PR Flight Check Receipt",
        f"**Status**: {status_badge}",
        f"**Base**: {result.get('base', 'HEAD')}",
        "",
        "| Check | Status | Count |",
        "| :--- | :--- | :--- |",
        f"| Conflict Markers (R5.1) | {'PASS' if not counts.get('conflict_markers') else 'FAIL'} | {counts.get('conflict_markers', 0)} |",
        f"| Test Imports (R6.1) | {'PASS' if not counts.get('test_imports') else 'FAIL'} | {counts.get('test_imports', 0)} |",
        f"| Test Custody (R20) | {'PASS' if not counts.get('test_custody') else 'FAIL'} | {counts.get('test_custody', 0)} |",
        f"| Orphan Files (R7.3) | {'PASS' if not counts.get('orphan_files') else 'FAIL'} | {counts.get('orphan_files', 0)} |",
        f"| AST Complexity (R19) | {'PASS' if not counts.get('complexity') else 'FAIL'} | {counts.get('complexity', 0)} |",
        f"| Rule Violations | {'PASS' if not counts.get('rules') else 'FAIL'} | {counts.get('rules', 0)} |",
        "",
    ]
    if findings:
        lines += ["**Findings**:", "```"] + findings + ["```", ""]
    lines.append("Resolve findings before submitting pull request." if not passed else "Ready for pull request.")
    return "\n".join(lines) + "\n"


def _render_text_receipt(result: dict[str, Any]) -> str:
    passed = result["passed"]
    findings = result["findings"]
    counts = result.get("counts", {})
    status_str = "PASSED" if passed else f"FAILED ({len(findings)} findings)"
    lines = [
        "=== Pre-PR Flight Check Receipt ===",
        f"Status: {status_str}",
        f"Base: {result.get('base', 'HEAD')}",
        "Checks:",
        f"  [{'OK' if not counts.get('conflict_markers') else 'FAIL'}] R5.1 Conflict markers: {counts.get('conflict_markers', 0)} findings",
        f"  [{'OK' if not counts.get('test_imports') else 'FAIL'}] R6.1 Test imports: {counts.get('test_imports', 0)} findings",
        f"  [{'OK' if not counts.get('test_custody') else 'FAIL'}] R20 Test custody: {counts.get('test_custody', 0)} findings",
        f"  [{'OK' if not counts.get('orphan_files') else 'FAIL'}] R7.3 Orphan files: {counts.get('orphan_files', 0)} findings",
        f"  [{'OK' if not counts.get('complexity') else 'FAIL'}] R19 Complexity: {counts.get('complexity', 0)} findings",
        f"  [{'OK' if not counts.get('rules') else 'FAIL'}] Rules scan: {counts.get('rules', 0)} findings",
    ]
    if findings:
        lines += ["", "Findings:"] + [f"  {f}" for f in findings]
        lines += ["", "Result: Flight checks failed. Resolve findings before submitting PR."]
    else:
        lines += ["", "Result: 0 findings. All flight checks passed."]
    return "\n".join(lines) + "\n"


def render_receipt(result: dict[str, Any], fmt: str = "text") -> str:
    """Render the Pre-PR Flight Check Receipt in text or markdown format."""
    if fmt == "markdown":
        return _render_markdown_receipt(result)
    return _render_text_receipt(result)


def main(argv: list[str] | None = None, prog: str = "brevio-core check") -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog=prog, description=HELP)
    parser.add_argument("--base", default="", help="git base ref to compare against (default: the base of scan --diff: origin/main, main or HEAD)")
    parser.add_argument("--json", action="store_true", help="output JSON receipt")
    parser.add_argument("--format", choices=["text", "markdown", "json"], default="text", help="receipt format")
    parser.add_argument("paths", nargs="*", help="optional paths to check")
    args = parser.parse_args(argv)
    fmt = "json" if args.json else args.format
    if args.base and not _git(".", "rev-parse", "--verify", "-q", args.base).strip():     # a mistyped base is compared with the next one, and says so
        print(f"brevio-core: warning: base '{args.base}' is not in this checkout; compared with {_resolve_base('.', args.base)}", file=sys.stderr)
    result = run_check(".", base=args.base, paths=args.paths or None)
    if fmt == "json":
        print(json.dumps(result, indent=2))
    else:
        sys.stdout.write(render_receipt(result, fmt=fmt))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
