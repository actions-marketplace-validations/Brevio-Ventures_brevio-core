"""Portable single-repo PageRank and surface-briefing module: rules and trap memory.

Standard library plus exported package modules only. Read-only git subcommands only.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from typing import Any

from brevio.pipeline import agent_host, dev_rules
from brevio.pipeline.rule_host import GIT

MEMORY_DIR = ".brevio"
MEMORY_FILE = "file_memory.json"
MEMORY_JSONL = "memory.jsonl"
MAX_EPISODES = 50
HELP = "print the zero-token rule & trap briefing for the files you are about to edit"

_UNSAFE_PATTERNS = [
    # Shell pipe / execution payloads
    r"(?:curl|wget)\s+[^\n|]+\|\s*(?:ba)?sh\b",
    r"rm\s+-(?:r[fF]|fr)\s+/",
    r"\beval\s*\(",
    r"`[^`\n]+`",
    r"\$\([^)\n]+\)",
    # Secret / token literals
    r"\bsk-[A-Za-z0-9]{16,}\b",
    r"\bghp_[A-Za-z0-9]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    # Prompt-injection overrides
    r"ignore\s+(?:all\s+)?previous\s+instructions",
    r"disregard\s+(?:the\s+)?system\s+prompt",
    r"you\s+are\s+now\s+in\s+developer\s+mode",
]
_UNSAFE_MEMORY_RE = re.compile("|".join(f"(?:{p})" for p in _UNSAFE_PATTERNS), re.IGNORECASE)


def sanitize_memory_text(text: str) -> tuple[bool, str]:
    """OpenClaw-grade memory hygiene filter.

    Rejects or strips unsafe patterns in learned lessons/notes:
    - Shell pipe / execution payloads (curl ... | sh, wget ... | bash, rm -rf /, eval(, `...`)
    - Secret / token literals (sk-..., ghp_..., AKIA...)
    - Prompt-injection overrides (ignore all previous instructions, disregard system prompt, developer mode)

    Returns (is_safe: bool, cleaned_text: str).
    """
    if not isinstance(text, str):
        return True, ""
    match = _UNSAFE_MEMORY_RE.search(text)
    if match:
        cleaned = _UNSAFE_MEMORY_RE.sub("", text)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        return False, cleaned
    return True, text.strip()


class _DocstringRemover(ast.NodeTransformer):
    """Strip docstrings so docstring and comment edits do not alter AST hash."""

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        return node

    def visit_Module(self, node: ast.Module) -> ast.AST:
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        return node


def _normalize_non_comment_lines(raw: str) -> str:
    """Normalize lines by stripping comments, empty lines, and excessive whitespace."""
    clean_lines = []
    for line in raw.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        if trimmed.startswith(("#", "//", "/*", "*")):
            continue
        clean_lines.append(re.sub(r"\s+", " ", trimmed))
    return "\n".join(clean_lines)


def compute_ast_hash(file_path: str) -> str:
    """Compute a 12-hex-char SHA-256 structural digest of file_path.

    For .py files: parses with ast.parse, strips docstrings, and dumps ast without
    attributes so comments, whitespace, and docstrings do not affect the hash, while
    structural AST changes do. Falls back to normalized non-comment lines on SyntaxError.
    For other files: hashes normalized non-comment, non-empty signature/code lines.
    Returns '' if the file does not exist.
    """
    if not file_path or not os.path.isfile(file_path):
        return ""
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except OSError:
        return ""

    if file_path.endswith(".py"):
        try:
            tree = ast.parse(content, filename=file_path)
            _DocstringRemover().visit(tree)
            dumped = ast.dump(tree, include_attributes=False)
            return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:12]
        except (SyntaxError, ValueError):
            pass

    normalized = _normalize_non_comment_lines(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def is_lesson_stale(entry: dict[str, Any], root: str = ".") -> bool:
    """Return True if entry has non-empty ast_hash and file, the file exists, and its AST hash has changed."""
    ast_hash = entry.get("ast_hash")
    file_rel = entry.get("file")
    if not ast_hash or not file_rel:
        return False
    full_path = os.path.join(root, file_rel)
    if not os.path.isfile(full_path):
        return False
    current_hash = compute_ast_hash(full_path)
    return bool(current_hash and current_hash != ast_hash)


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run([*GIT, "-C", cwd, *args],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _memory_path(cwd: str | None = None) -> str:
    root = cwd if cwd is not None else agent_host.config.WORKSPACE_DIR
    return os.path.join(root, MEMORY_DIR, MEMORY_FILE)


def _memory_jsonl_path(cwd: str | None = None) -> str:
    root = cwd if cwd is not None else agent_host.config.WORKSPACE_DIR
    return os.path.join(root, MEMORY_DIR, MEMORY_JSONL)


def _append_jsonl_memory(entry: dict[str, Any], cwd: str | None = None) -> None:
    path = _memory_jsonl_path(cwd)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def load_episodes(cwd: str | None = None) -> list[dict[str, Any]]:
    """Load stored episodes from the workspace memory file."""
    path = _memory_path(cwd)
    data = agent_host.jsonstore.read_or(path, {"version": 1, "episodes": []})
    if isinstance(data, dict):
        return list(data.get("episodes", []))
    if isinstance(data, list):
        return list(data)
    return []


def save_episodes(episodes: list[dict[str, Any]], cwd: str | None = None) -> str:
    """Save episodes to the workspace memory file atomically."""
    path = _memory_path(cwd)
    data = {"version": 1, "episodes": episodes}
    agent_host.jsonstore.write(path, data)
    return path


def record_episode(
    file_path: str,
    rule_id: str,
    trap: str,
    fix: str,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Record a trap-to-fix episode for file_path, deduplicating and keeping the newest 50."""
    safe_trap, clean_trap = sanitize_memory_text(trap)
    safe_fix, clean_fix = sanitize_memory_text(fix)
    if not safe_trap or not safe_fix:
        raise ValueError(f"Unsafe memory pattern detected in episode: trap={safe_trap}, fix={safe_fix}")

    root = cwd if cwd is not None else agent_host.config.WORKSPACE_DIR
    norm_path = file_path.replace(os.sep, "/")
    full_path = os.path.join(root, norm_path) if not os.path.isabs(norm_path) else norm_path
    ast_hash = compute_ast_hash(full_path)

    ep: dict[str, Any] = {
        "file": norm_path,
        "rule_id": rule_id,
        "trap": clean_trap,
        "fix": clean_fix,
        "ast_hash": ast_hash,
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    existing = load_episodes(cwd=cwd)
    deduped = [e for e in existing if (e.get("file"), e.get("rule_id")) != (norm_path, rule_id)]
    new_list = ([ep] + deduped)[:MAX_EPISODES]
    save_episodes(new_list, cwd=cwd)
    _append_jsonl_memory(ep, cwd=cwd)
    return ep


def record(
    *args: Any,
    text: str | None = None,
    file: str | None = None,
    rule_id: str | None = None,
    cwd: str | None = None,
    root: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Record a memory entry or episode, enforcing hygiene and storing AST hashes."""
    actual_cwd = cwd or root
    if len(args) == 4 and all(isinstance(a, str) for a in args):
        file_path, r_id, trap, fix = args
        return record_episode(file_path, r_id, trap, fix, cwd=actual_cwd)

    lesson_text = text if text is not None else (args[0] if args else "")
    file_path = file or kwargs.get("file_path")
    rule = rule_id or kwargs.get("rule")

    is_safe, cleaned = sanitize_memory_text(lesson_text)
    if not is_safe:
        if kwargs.get("raise_on_unsafe", True):
            raise ValueError(f"Unsafe memory pattern detected: {lesson_text}")
        return {"ok": False, "error": "unsafe_memory"}

    workspace = actual_cwd if actual_cwd is not None else agent_host.config.WORKSPACE_DIR
    ast_hash = ""
    norm_file = ""
    if file_path:
        norm_file = file_path.replace(os.sep, "/")
        full_path = os.path.join(workspace, norm_file) if not os.path.isabs(norm_file) else norm_file
        ast_hash = compute_ast_hash(full_path)

    entry: dict[str, Any] = {
        "text": cleaned,
        "file": norm_file,
        "rule_id": rule or "",
        "ast_hash": ast_hash,
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **kwargs,
    }
    _append_jsonl_memory(entry, cwd=workspace)
    return {"ok": True, "entry": entry, **entry}


learn = record


def _py_import_candidates(root: str, full_path: str, tree: ast.AST) -> list[str]:
    cands: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_rel = alias.name.replace(".", "/")
                cands.extend((os.path.join(root, mod_rel + ".py"), os.path.join(root, mod_rel, "__init__.py")))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_dir = os.path.dirname(full_path)
                for _ in range(node.level - 1):
                    base_dir = os.path.dirname(base_dir)
                target_dir = os.path.join(base_dir, node.module.replace(".", "/")) if node.module else base_dir
            elif node.module:
                target_dir = os.path.join(root, node.module.replace(".", "/"))
            else:
                continue
            cands.extend((target_dir + ".py", os.path.join(target_dir, "__init__.py")))
            cands.extend(os.path.join(target_dir, alias.name + ".py") for alias in node.names)
    return cands


_JS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:(?:[\w*\s{},]*)\s+from\s+)?['"](\.[^'"]+)['"]|require\(['"](\.[^'"]+)['"]\)"""
)
_JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _js_import_candidates(root: str, full_path: str, text: str) -> list[str]:
    cands: list[str] = []
    base_dir = os.path.dirname(full_path)
    for m in _JS_IMPORT_RE.finditer(text):
        rel_spec = m.group(1) or m.group(2)
        if not rel_spec:
            continue
        target_base = os.path.normpath(os.path.join(base_dir, rel_spec))
        if target_base.endswith(_JS_SUFFIXES):
            cands.append(target_base)
            if target_base.endswith(".js"):
                cands.extend([target_base[:-3] + ".ts", target_base[:-3] + ".tsx"])
        else:
            for sfx in _JS_SUFFIXES:
                cands.append(target_base + sfx)
                cands.append(os.path.join(target_base, "index" + sfx))
    return cands


def _matches_any_glob(rel_path: str, globs: list[str] | tuple[str, ...]) -> bool:
    """Match a normalized relative path against a list of glob patterns, supporting root-level ``**/``."""
    norm = rel_path.replace(os.sep, "/").lstrip("./")
    for pat in globs:
        if fnmatch.fnmatch(norm, pat) or (pat.startswith("**/") and fnmatch.fnmatch(norm, pat[3:])):
            return True
    return False


def _normalize_target(tf: str, real_root: str) -> str | None:
    if os.path.isabs(tf):
        try:
            real_tf = os.path.realpath(tf)
            if os.path.commonpath([real_root, real_tf]) != real_root:
                return None
            rel = os.path.relpath(real_tf, real_root).replace(os.sep, "/")
            return None if rel.startswith("..") else rel
        except ValueError:
            return None
    return tf.replace(os.sep, "/")


def one_hop_neighbors(root: str, target_files: list[str], max_files: int = 25) -> set[str]:
    """Return bounded 1-hop neighborhood for target_files via imports and commit co-changes."""
    ast_neighbors: set[str] = set()
    cochange_neighbors: set[str] = set()
    real_root = os.path.realpath(root)
    norm_targets = [r for tf in target_files if (r := _normalize_target(tf, real_root)) is not None]
    target_set = set(norm_targets)

    def _add_cand(cand: str) -> None:
        if not os.path.isfile(cand):
            return
        try:
            real_cand = os.path.realpath(cand)
            if os.path.commonpath([real_root, real_cand]) != real_root:
                return
        except ValueError:
            return
        rel = os.path.relpath(real_cand, real_root).replace(os.sep, "/")
        if not rel.startswith("..") and rel not in target_set:
            ast_neighbors.add(rel)

    for rel_tf in norm_targets:
        full_path = os.path.join(root, rel_tf)
        if not os.path.isfile(full_path):
            continue
        if rel_tf.endswith(".py"):
            try:
                with open(full_path, encoding="utf-8", errors="ignore") as fh:
                    tree = ast.parse(fh.read(), full_path)
            except (OSError, SyntaxError):
                continue
            for cand in _py_import_candidates(root, full_path, tree):
                _add_cand(cand)
        elif rel_tf.endswith(_JS_SUFFIXES):
            try:
                with open(full_path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            for cand in _js_import_candidates(root, full_path, text):
                _add_cand(cand)

    for ref in ("HEAD", "HEAD~1", "HEAD~2", "HEAD~3", "HEAD~4"):
        commit_files_raw = _git(root, "show", "--name-only", "--format=", ref)
        if not commit_files_raw:
            continue
        commit_files = {ln.strip().replace(os.sep, "/") for ln in commit_files_raw.splitlines() if ln.strip()}
        if len(commit_files) <= 30 and target_set.intersection(commit_files):
            cochange_neighbors.update(commit_files - target_set)

    ordered = sorted(ast_neighbors) + sorted(cochange_neighbors - ast_neighbors)
    return set(ordered[:max_files])


def _load_active_guards(root: str, target_files: Iterable[str]) -> list[dict[str, Any]]:
    guards_path = os.path.join(root, ".brevio", "guards.json")
    if not os.path.isfile(guards_path):
        return []
    try:
        with open(guards_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    raw = data.get("guards", {}) if isinstance(data, dict) else {}     # a guards file that is a list is no guards, not a crash
    items = raw if isinstance(raw, list) else (list(raw.values()) if isinstance(raw, dict) else [])
    norm_targets = {t.replace(os.sep, "/").lstrip("./") for t in target_files}
    matched = []
    for g in items:
        if not isinstance(g, dict):
            continue
        if g.get("status") not in ("active", "trial"):
            continue
        sf = str(g.get("source_file", "")).replace(os.sep, "/").lstrip("./")
        if sf in norm_targets or any(fnmatch.fnmatch(t, sf) for t in norm_targets):
            matched.append(g)
    return matched


_SIG_RE = re.compile(r"^\s*(?:export\s+(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|const)|(?:async\s+)?def\s+|class\s+)")


def _extract_signatures(root: str, files: Iterable[str], limit: int = 20) -> list[str]:
    sigs: list[str] = []
    for rel in files:
        full_path = os.path.join(root, rel)
        if not os.path.isfile(full_path):
            continue
        try:
            with open(full_path, encoding="utf-8", errors="ignore") as fh:
                for idx, line in enumerate(fh, 1):
                    if idx > 200:
                        break
                    sline = line.strip()
                    if _SIG_RE.match(sline):
                        clean_sig = sline.rstrip("{:").strip()
                        sigs.append(f"{rel}:{idx}: {clean_sig}")
                        if len(sigs) >= limit:
                            return sigs
        except OSError:
            continue
    return sigs


def brief_for_files(files: list[str], cwd: str | None = None, root: str | None = None) -> dict[str, Any]:
    """Combine micro rules, recalled episodes, and 1-hop neighbors for files."""
    target_root = root if root is not None else (cwd if cwd is not None else agent_host.config.WORKSPACE_DIR)
    target_set = {f.replace(os.sep, "/") for f in files}
    micro_rules = dev_rules.slice_micro_rules(files)
    neighbors = one_hop_neighbors(target_root, files)

    episodes = load_episodes(cwd=target_root)
    recalled: list[dict[str, Any]] = []
    for ep in episodes:
        ep_file = str(ep.get("file", "")).replace(os.sep, "/")
        score = 0.0
        if ep_file in target_set:
            score = 1.0
        elif ep_file in neighbors:
            score = 0.5
        if score > 0.0:
            stale = is_lesson_stale(ep, root=target_root)
            recalled.append(dict(ep, score=score, stale=stale))

    recalled.sort(key=lambda e: (e.get("score", 0.0), e.get("at", "")), reverse=True)
    guards = _load_active_guards(target_root, files)
    signatures = _extract_signatures(target_root, [*files, *sorted(neighbors)])
    return {
        "files": files,
        "micro_rules": micro_rules,
        "surface_rules": micro_rules,
        "episodes": recalled,
        "neighbors": sorted(neighbors),
        "guards": guards,
        "signatures": signatures,
    }


build_brief = brief_for_files


def recall(files: list[str], cwd: str | None = None, include_stale: bool = True) -> list[dict[str, Any]]:
    """Recall past trap episodes / lessons for files, optionally filtering stale ones."""
    brief = brief_for_files(files, cwd=cwd)
    episodes = brief.get("episodes", [])
    if not include_stale:
        return [e for e in episodes if not e.get("stale", False)]
    return episodes


def main(argv: list[str] | None = None, prog: str = "brevio-core brief", cwd: str | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog=prog, description=HELP)
    parser.add_argument("files", nargs="*", help="files to generate briefing for")
    parser.add_argument("--json", action="store_true", help="output briefing as JSON")
    parser.add_argument(
        "--record",
        nargs=3,
        metavar=("RULE", "TRAP", "FIX"),
        help="record a Trap -> Fix episode for the first named file",
    )
    args = parser.parse_args(argv)

    root = cwd if cwd is not None else os.getcwd()
    if args.record:
        if not args.files:
            sys.stderr.write("error: a file must be specified with --record\n")
            return 2
        rule_id, trap, fix = args.record
        target_file = args.files[0]
        ep = record_episode(target_file, rule_id, trap, fix, cwd=root)
        if args.json:
            print(
                json.dumps(
                    {
                        "recorded": True,
                        "rule_id": rule_id,
                        "file": target_file,
                        "path": f"{MEMORY_DIR}/{MEMORY_FILE}",
                        "episode": ep,
                    },
                    indent=2,
                )
            )
        else:
            sys.stdout.write(f"Recorded episode for {target_file}: [{rule_id}] {trap} -> {fix}\n")
        return 0

    brief = brief_for_files(args.files, cwd=root)
    if args.json:
        print(json.dumps(brief, indent=2))
    else:
        out: list[str] = []
        if brief["micro_rules"]:
            out.append(brief["micro_rules"].strip())
        if brief["episodes"]:
            out.append("\nRECALLED TRAPS & FIXES:")
            for ep in brief["episodes"]:
                rel = ep.get("score", 1.0)
                out.append(
                    f"  [{ep.get('rule_id', '')}] {ep.get('file', '')} (score: {rel}): {ep.get('trap', '')} -> {ep.get('fix', '')}"
                )
        if brief["neighbors"]:
            out.append(f"\n1-HOP NEIGHBORS: {', '.join(brief['neighbors'][:10])}")
        if not out:
            out.append("No active rules or past traps found for the specified files.")
        sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
