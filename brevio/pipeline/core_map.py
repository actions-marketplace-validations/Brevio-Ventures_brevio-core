"""Zero-dependency Personalized PageRank repository symbol map and indexer."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from typing import Any

HELP = "build a zero-token Personalized PageRank repository symbol map and index"

_AGENTS_DIR = "." + "agents"      # the same spelling as core_cli.SKIP_DIRS (public pull request #11)
IGNORED_DIRS = frozenset({
    _AGENTS_DIR,
    ".brevio",
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
})

VALID_EXTENSIONS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
)

_PY_KEYWORDS = frozenset({
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
    "self", "cls",
})

_JS_KEYWORDS = frozenset({
    "abstract", "any", "as", "async", "await", "boolean", "break", "case",
    "catch", "class", "const", "constructor", "continue", "debugger", "declare",
    "default", "delete", "do", "else", "enum", "export", "extends", "false",
    "finally", "for", "from", "function", "get", "if", "implements", "import",
    "in", "instanceof", "interface", "is", "keyof", "let", "module",
    "namespace", "never", "new", "null", "number", "object", "package",
    "private", "protected", "public", "readonly", "require", "return", "set",
    "static", "string", "super", "switch", "symbol", "this", "throw", "true",
    "try", "type", "typeof", "undefined", "unknown", "var", "void", "while",
    "with", "yield",
})

_GO_KEYWORDS = frozenset({
    "break", "case", "chan", "const", "continue", "default", "defer", "else",
    "fallthrough", "for", "func", "go", "goto", "if", "import", "interface",
    "map", "package", "range", "return", "select", "struct", "switch", "type",
    "var", "string", "int", "bool", "byte", "error", "nil", "true", "false",
})

_RUST_KEYWORDS = frozenset({
    "as", "break", "const", "continue", "crate", "else", "enum", "extern",
    "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
    "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct",
    "super", "trait", "true", "type", "unsafe", "use", "where", "while",
    "async", "await", "dyn", "String", "str", "Option", "Result", "Vec",
})

_IDENT_RE = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]{2,}\b")
_TS_IMPORT_RE = re.compile(
    r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]|require\(['"]([^'"]+)['"]\)|export\s+.*?\s+from\s+['"]([^'"]+)['"])""",
    re.M | re.S,
)


def _gather_multiline_sig(lines: list[str], start_idx: int) -> str:
    """Gather signature tokens across multiple lines until balanced parentheses and terminating token."""
    accum = []
    parens = 0
    for j in range(start_idx, min(len(lines), start_idx + 15)):
        line = lines[j]
        parens += line.count("(") - line.count(")")
        accum.append(line.strip())
        if parens <= 0 and ("{" in line or ";" in line or "=" in line):
            break
    joined = " ".join(accum)
    joined = re.sub(r"\s+", " ", joined)
    joined = re.sub(r"\(\s+", "(", joined)
    joined = re.sub(r"\s+\)", ")", joined)
    joined = re.sub(r"\s*\{\s*$", "", joined)
    joined = re.sub(r"\s*;\s*$", "", joined)
    joined = joined.strip()
    if len(joined) > 200:
        joined = joined[:197] + "..."
    return joined


def _format_python_signature(node: ast.AST) -> str:
    """Build a normalized single-line signature for a class or function."""
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases]
        for kw in node.keywords:
            if kw.arg:
                bases.append(f"{kw.arg}={ast.unparse(kw.value)}")
        base_str = f"({', '.join(bases)})" if bases else ""
        sig = f"class {node.name}{base_str}:"
        return re.sub(r"\s+", " ", sig).strip()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        args_str = ast.unparse(node.args)
        args_str = re.sub(r"=(\S)", r" = \1", args_str)
        sig = f"{prefix}{node.name}({args_str}){ret}:"
        return re.sub(r"\s+", " ", sig).strip()
    return ""


def _extract_python_symbols(
    content: str, rel_path: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    defs: list[dict[str, Any]] = []
    imports: list[str] = []
    refs_set: set[str] = set()

    try:
        tree = ast.parse(content, filename=rel_path)
    except (SyntaxError, ValueError, UnicodeDecodeError):
        return defs, [], []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = _format_python_signature(node)
            defs.append({
                "name": node.name,
                "kind": "function",
                "line": node.lineno,
                "signature": sig,
            })
        elif isinstance(node, ast.ClassDef):
            sig = _format_python_signature(node)
            defs.append({
                "name": node.name,
                "kind": "class",
                "line": node.lineno,
                "signature": sig,
            })
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_sig = _format_python_signature(m)
                    defs.append({
                        "name": m.name,
                        "kind": "function",
                        "line": m.lineno,
                        "signature": m_sig,
                    })
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    val_str = (
                        f" = {ast.unparse(node.value)}"
                        if getattr(node, "value", None)
                        else ""
                    )
                    sig = f"{t.id}{val_str}"
                    if len(sig) > 100:
                        sig = sig[:97] + "..."
                    defs.append({
                        "name": t.id,
                        "kind": "const",
                        "line": node.lineno,
                        "signature": sig,
                    })

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level and node.level > 0:
                dots = "." * node.level
                rel_mod = f"{dots}{mod}" if mod else dots
                imports.append(rel_mod)
                file_dir = os.path.dirname(rel_path)
                parts = file_dir.split("/") if file_dir else []
                steps_up = node.level - 1
                if steps_up <= len(parts):
                    base_parts = parts[: len(parts) - steps_up] if steps_up > 0 else parts
                    mod_parts = mod.split(".") if mod else []
                    resolved_parts = [p for p in base_parts + mod_parts if p]
                    if resolved_parts:
                        resolved_path = "/".join(resolved_parts)
                        imports.append(resolved_path)
                        imports.append(resolved_path.replace("/", "."))
            else:
                if mod:
                    imports.append(mod)
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            refs_set.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs_set.add(node.attr)

    refs = sorted(list(refs_set - _PY_KEYWORDS))
    unique_imports = list(dict.fromkeys(imports))
    return defs, refs, unique_imports


def _extract_ts_js_symbols(
    content: str, rel_path: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    defs: list[dict[str, Any]] = []
    lines = content.splitlines()

    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            continue

        m_fn = re.search(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function(?:\s*\*|\s+)([A-Za-z0-9_$]+)", line)
        if m_fn:
            name = m_fn.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "function", "line": idx, "signature": sig})
            continue

        m_cls = re.search(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_$]+)", line)
        if m_cls:
            name = m_cls.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "class", "line": idx, "signature": sig})
            continue

        m_iface = re.search(r"^(?:export\s+)?interface\s+([A-Za-z0-9_$]+)", line)
        if m_iface:
            name = m_iface.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "interface", "line": idx, "signature": sig})
            continue

        m_type = re.search(r"^(?:export\s+)?type\s+([A-Za-z0-9_$]+)\s*=", line)
        if m_type:
            name = m_type.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "type", "line": idx, "signature": sig})
            continue

        m_enum = re.search(r"^(?:export\s+)?enum\s+([A-Za-z0-9_$]+)", line)
        if m_enum:
            name = m_enum.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "type", "line": idx, "signature": sig})
            continue

        m_arrow = re.search(
            r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>",
            line,
        )
        if m_arrow:
            name = m_arrow.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "function", "line": idx, "signature": sig})
            continue

        m_const = re.search(r"^(?:export\s+)?const\s+([A-Za-z0-9_$]+)\s*=", line)
        if m_const:
            name = m_const.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "const", "line": idx, "signature": sig})
            continue

    imports: list[str] = []
    for m in _TS_IMPORT_RE.finditer(content):
        imp = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        if imp:
            imports.append(imp)
            if imp.startswith("."):
                file_dir = os.path.dirname(rel_path)
                resolved = os.path.normpath(os.path.join(file_dir, imp)).replace("\\", "/")
                imports.append(resolved)

    refs_set = set(_IDENT_RE.findall(content)) - _JS_KEYWORDS
    refs = sorted(list(refs_set))
    unique_imports = list(dict.fromkeys(imports))
    return defs, refs, unique_imports


def _extract_go_symbols(
    content: str, rel_path: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    defs: list[dict[str, Any]] = []
    lines = content.splitlines()

    for idx, line in enumerate(lines, 1):
        m_type = re.search(r"^type\s+([A-Za-z0-9_]+)\s+(struct|interface)", line)
        if m_type:
            name = m_type.group(1)
            kind = "interface" if m_type.group(2) == "interface" else "type"
            sig = f"type {name} {m_type.group(2)}"
            defs.append({"name": name, "kind": kind, "line": idx, "signature": sig})
            continue

        m_func = re.search(r"^func\s+(?:\([^)]+\)\s+)?([A-Za-z0-9_]+)\s*\(", line)
        if m_func:
            name = m_func.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "function", "line": idx, "signature": sig})
            continue

        m_const = re.search(r"^const\s+([A-Za-z0-9_]+)", line)
        if m_const:
            name = m_const.group(1)
            sig = line.strip().rstrip("{;").strip()
            defs.append({"name": name, "kind": "const", "line": idx, "signature": sig})
            continue

    imports: list[str] = []
    for m in re.finditer(r'"([A-Za-z0-9_./\-]+)"', content):
        imports.append(m.group(1))

    refs_set = set(_IDENT_RE.findall(content)) - _GO_KEYWORDS
    refs = sorted(list(refs_set))
    unique_imports = list(dict.fromkeys(imports))
    return defs, refs, unique_imports


def _extract_rust_symbols(
    content: str, rel_path: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    defs: list[dict[str, Any]] = []
    lines = content.splitlines()

    for idx, line in enumerate(lines, 1):
        m_type = re.search(r"^(?:pub\s+)?(struct|enum)\s+([A-Za-z0-9_]+)", line)
        if m_type:
            name = m_type.group(2)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "type", "line": idx, "signature": sig})
            continue

        m_trait = re.search(r"^(?:pub\s+)?trait\s+([A-Za-z0-9_]+)", line)
        if m_trait:
            name = m_trait.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "interface", "line": idx, "signature": sig})
            continue

        m_fn = re.search(r"^(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)\s*\(", line)
        if m_fn:
            name = m_fn.group(1)
            sig = _gather_multiline_sig(lines, idx - 1)
            defs.append({"name": name, "kind": "function", "line": idx, "signature": sig})
            continue

        m_const = re.search(r"^(?:pub\s+)?const\s+([A-Za-z0-9_]+)", line)
        if m_const:
            name = m_const.group(1)
            sig = line.strip().rstrip(";").strip()
            defs.append({"name": name, "kind": "const", "line": idx, "signature": sig})
            continue

    imports: list[str] = []
    for m in re.finditer(r"use\s+([^;]+);", content):
        imports.append(m.group(1).strip())

    refs_set = set(_IDENT_RE.findall(content)) - _RUST_KEYWORDS
    refs = sorted(list(refs_set))
    unique_imports = list(dict.fromkeys(imports))
    return defs, refs, unique_imports


def extract_file_symbols(file_path: str, root: str = ".") -> dict[str, Any]:
    """Extract symbol definitions, references, and imports from a source file."""
    abs_root = os.path.abspath(root)
    abs_file = os.path.abspath(file_path if os.path.isabs(file_path) else os.path.join(root, file_path))
    rel_path = os.path.relpath(abs_file, abs_root).replace("\\", "/")

    empty_res = {"file": rel_path, "defs": [], "refs": [], "imports": []}

    parts = rel_path.split("/")
    if any(p in IGNORED_DIRS for p in parts[:-1]):
        return empty_res

    if os.path.islink(abs_file):
        return empty_res

    if not os.path.isfile(abs_file):
        return empty_res

    try:
        with open(abs_file, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return empty_res

    ext = os.path.splitext(rel_path)[1].lower()

    try:
        if ext == ".py":
            defs, refs, imports = _extract_python_symbols(content, rel_path)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            defs, refs, imports = _extract_ts_js_symbols(content, rel_path)
        elif ext == ".go":
            defs, refs, imports = _extract_go_symbols(content, rel_path)
        elif ext == ".rs":
            defs, refs, imports = _extract_rust_symbols(content, rel_path)
        else:
            return empty_res
    except (SyntaxError, UnicodeDecodeError, ValueError, Exception):
        return empty_res

    return {
        "file": rel_path,
        "defs": defs,
        "refs": refs,
        "imports": imports,
    }


def personalized_pagerank(
    files_data: dict[str, dict[str, Any]],
    seed_files: list[str] | None = None,
    alpha: float = 0.85,
    max_iter: int = 30,
) -> list[tuple[str, float]]:
    """Build directed dependency graph and compute Personalized PageRank."""
    nodes = sorted(list(files_data.keys()))
    num_nodes = len(nodes)
    if num_nodes == 0:
        return []
    if num_nodes == 1:
        return [(nodes[0], 1.0)]

    symbol_to_files: dict[str, set[str]] = {}
    for f, data in files_data.items():
        for d in data.get("defs", []):
            name = d.get("name")
            if name:
                symbol_to_files.setdefault(name, set()).add(f)

    file_lookup: dict[str, set[str]] = {}
    for f in nodes:
        file_lookup.setdefault(f, set()).add(f)
        stem = os.path.splitext(f)[0]
        file_lookup.setdefault(stem, set()).add(f)
        file_lookup.setdefault(stem.replace("/", "."), set()).add(f)
        base_stem = os.path.splitext(os.path.basename(f))[0]
        file_lookup.setdefault(base_stem, set()).add(f)
        base_name = os.path.basename(f)
        file_lookup.setdefault(base_name, set()).add(f)

    adj: dict[str, dict[str, float]] = {f: {} for f in nodes}

    for file_a in nodes:
        data_a = files_data[file_a]
        for ref in data_a.get("refs", []):
            if ref in symbol_to_files:
                for target_f in symbol_to_files[ref]:
                    if target_f != file_a:
                        adj[file_a][target_f] = adj[file_a].get(target_f, 0.0) + 1.0

        for imp in data_a.get("imports", []):
            clean_imp = imp.lstrip("./").strip()
            matched_targets: set[str] = set()

            if imp in file_lookup:
                matched_targets.update(file_lookup[imp])
            if clean_imp in file_lookup:
                matched_targets.update(file_lookup[clean_imp])
            dot_to_slash = clean_imp.replace(".", "/")
            if dot_to_slash in file_lookup:
                matched_targets.update(file_lookup[dot_to_slash])

            for target_cand in nodes:
                if (
                    target_cand.endswith("/" + clean_imp)
                    or target_cand.endswith("/" + clean_imp + ".py")
                    or target_cand.endswith("/" + clean_imp + ".ts")
                    or target_cand.endswith("/" + clean_imp + ".js")
                ):
                    matched_targets.add(target_cand)

            for target_f in matched_targets:
                if target_f != file_a:
                    adj[file_a][target_f] = adj[file_a].get(target_f, 0.0) + 1.5

    if seed_files:
        norm_seeds: set[str] = set()
        for s in seed_files:
            clean_s = os.path.normpath(str(s).replace("\\", "/")).replace("\\", "/")
            if clean_s.startswith("./"):
                clean_s = clean_s[2:]
            clean_s = clean_s.strip()
            matched = False
            for n in nodes:
                if (
                    n == clean_s
                    or n.endswith("/" + clean_s)
                    or clean_s.endswith("/" + n)
                    or os.path.splitext(n)[0] == clean_s
                    or os.path.splitext(os.path.basename(n))[0] == clean_s
                    or (clean_s.endswith(n) and (len(clean_s) == len(n) or clean_s[-len(n) - 1] == "/"))
                ):
                    norm_seeds.add(n)
                    matched = True
            if not matched and clean_s in files_data:
                norm_seeds.add(clean_s)

        if norm_seeds:
            neighbors = set(norm_seeds)
            for s in norm_seeds:
                for out_v in adj.get(s, {}):
                    neighbors.add(out_v)
                for u in nodes:
                    if s in adj.get(u, {}):
                        neighbors.add(u)

            h_count = len(neighbors)
            seed_weight = 0.8 / h_count if h_count > 0 else 0.0
            uniform_weight = 0.2 / num_nodes
            p0 = {k: uniform_weight + (seed_weight if k in neighbors else 0.0) for k in nodes}
        else:
            p0 = {k: 1.0 / num_nodes for k in nodes}
    else:
        p0 = {k: 1.0 / num_nodes for k in nodes}

    r = dict(p0)
    for _ in range(max_iter):
        next_r = {k: (1.0 - alpha) * p0[k] for k in nodes}
        dangling_sum = sum(r[u] for u in nodes if not adj.get(u))

        for u in nodes:
            out_edges = adj.get(u, {})
            out_total = sum(out_edges.values())
            if out_total > 0.0:
                for v, w in out_edges.items():
                    if v in next_r:
                        next_r[v] += alpha * r[u] * (w / out_total)

        if dangling_sum > 0.0:
            for v in nodes:
                next_r[v] += alpha * dangling_sum * p0[v]

        r = next_r

    total_r = sum(r.values())
    if total_r > 0.0:
        r = {k: v / total_r for k, v in r.items()}

    return sorted(r.items(), key=lambda item: (-item[1], item[0]))


def build_repo_map(
    root: str = ".",
    seed_files: list[str] | None = None,
    token_budget: int = 4000,
    as_json: bool = False,
) -> str | dict[str, Any]:
    """Walk root, extract symbols, rank via Personalized PageRank, and build summary."""
    abs_root = os.path.abspath(root)
    collected_files: list[str] = []

    for dirpath, dirnames, filenames in os.walk(abs_root):
        dirnames[:] = sorted([
            d
            for d in dirnames
            if d not in IGNORED_DIRS
            and not os.path.islink(os.path.join(dirpath, d))
            and not os.path.exists(os.path.join(dirpath, d, ".git"))     # a nested checkout is another project (2026-11-12)
        ])
        for fn in sorted(filenames):
            if fn.endswith(VALID_EXTENSIONS):
                fpath = os.path.join(dirpath, fn)
                if os.path.islink(fpath):
                    continue
                rel = os.path.relpath(fpath, abs_root).replace("\\", "/")
                collected_files.append(rel)
                if len(collected_files) >= 500:
                    break
        if len(collected_files) >= 500:
            break

    files_data: dict[str, dict[str, Any]] = {}
    for rel in collected_files:
        abs_f = os.path.join(abs_root, rel)
        files_data[rel] = extract_file_symbols(abs_f, root=abs_root)

    norm_seeds: list[str] | None = None
    if seed_files:
        norm_seeds = []
        for sf in seed_files:
            sf_str = str(sf).replace("\\", "/")
            if os.path.isabs(sf_str):
                try:
                    rel_sf = os.path.relpath(sf_str, abs_root).replace("\\", "/")
                    if not rel_sf.startswith("../") and rel_sf != "..":
                        norm_seeds.append(rel_sf)
                    else:
                        norm_seeds.append(sf_str)
                except ValueError:
                    norm_seeds.append(sf_str)
            else:
                norm_p = os.path.normpath(sf_str).replace("\\", "/")
                if norm_p.startswith("./"):
                    norm_p = norm_p[2:]
                norm_seeds.append(norm_p)

    ranked = personalized_pagerank(files_data, seed_files=norm_seeds)
    char_budget = token_budget * 4

    if as_json:
        ranked_files: list[dict[str, Any]] = []
        result: dict[str, Any] = {
            "root": abs_root,
            "total_files": len(files_data),
            "seed_files": norm_seeds if norm_seeds is not None else [],
            "ranked_files": ranked_files,
        }
        for f, sc in ranked:
            fdata = files_data.get(f, {})
            entry = {
                "file": f,
                "score": round(sc, 6),
                "defs": fdata.get("defs", []),
                "imports": fdata.get("imports", []),
            }
            ranked_files.append(entry)
            if len(json.dumps(result)) > char_budget and len(ranked_files) > 1:
                ranked_files.pop()
                break
        return result

    header = "# Repository Symbol Map (PageRank Ranked)\n"
    lines = [header]
    current_len = len(header)

    for f, sc in ranked:
        fdata = files_data.get(f, {})
        defs = fdata.get("defs", [])
        imps = fdata.get("imports", [])

        file_lines = [f"## {f} (score: {round(sc, 6):.6f})"]
        if imps:
            file_lines.append(f"Imports: {', '.join(imps[:6])}")
        if defs:
            file_lines.append("Definitions:")
            for d in defs[:10]:
                file_lines.append(f"  - {d.get('signature', d.get('name', ''))}")
        file_lines.append("")

        block = "\n".join(file_lines) + "\n"
        if current_len + len(block) > char_budget and len(lines) > 1:
            trunc_msg = "\n[...truncated to token budget]"
            if current_len + len(trunc_msg) <= char_budget:
                lines.append(trunc_msg)
            break
        lines.append(block)
        current_len += len(block)

    return "\n".join(lines).strip()


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    """CLI entry point for repository map generation."""
    parser = argparse.ArgumentParser(
        prog=prog or "brevio-core map",
        description="Zero-Dependency Personalized PageRank Repo Map",
    )
    parser.add_argument(
        "positional_files",
        nargs="*",
        default=[],
        help="Seed files for Personalized PageRank (positional)",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory of the repository (default: '.')",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="Seed files for Personalized PageRank (comma-separated or multiple)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=4000,
        help="Token budget for output map (default: 4000)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_flag",
        help="Output map as JSON",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default=None,
        help="Output format (json or text)",
    )

    args = parser.parse_args(argv)

    seed_files = []
    raw_files = list(args.positional_files or [])
    if args.files:
        raw_files.extend(args.files)
    for item in raw_files:
        for part in item.split(","):
            part = part.strip()
            if part:
                seed_files.append(part)

    as_json = bool(args.json_flag or args.format == "json")

    res = build_repo_map(
        root=args.root,
        seed_files=seed_files if seed_files else None,
        token_budget=args.budget,
        as_json=as_json,
    )

    if as_json:
        print(json.dumps(res, indent=2))
    else:
        print(res)

    return 0


if __name__ == "__main__":
    sys.exit(main())
