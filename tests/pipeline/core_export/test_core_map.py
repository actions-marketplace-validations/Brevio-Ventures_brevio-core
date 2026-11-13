"""Tests for zero-dependency Personalized PageRank repository symbol map."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

from brevio.pipeline import core_map


class TestCoreMap(unittest.TestCase):
    def test_python_ast_extraction(self):
        """Test Python AST symbol, signature, and import extraction."""
        with tempfile.TemporaryDirectory() as d:
            code = (
                "import os\n"
                "from sys import path\n"
                "from .helpers import util_func\n"
                "\n"
                "MAX_CONNECTIONS = 10\n"
                "TIMEOUT: int = 30\n"
                "\n"
                "class DataProcessor(BaseProcessor):\n"
                "    def __init__(self, name: str) -> None:\n"
                "        self.name = name\n"
                "\n"
                "    def process_item(self, item: dict) -> bool:\n"
                "        return bool(util_func(item))\n"
                "\n"
                "async def fetch_async(url: str, retries: int = 3) -> dict:\n"
                "    return {'url': url}\n"
            )
            fpath = os.path.join(d, "processor.py")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(code)

            res = core_map.extract_file_symbols(fpath, root=d)
            self.assertEqual(res["file"], "processor.py")

            defs_by_name = {d["name"]: d for d in res["defs"]}
            self.assertIn("DataProcessor", defs_by_name)
            self.assertEqual(defs_by_name["DataProcessor"]["kind"], "class")
            self.assertEqual(
                defs_by_name["DataProcessor"]["signature"], "class DataProcessor(BaseProcessor):"
            )

            self.assertIn("process_item", defs_by_name)
            self.assertEqual(defs_by_name["process_item"]["kind"], "function")
            self.assertIn("def process_item(self, item: dict) -> bool:", defs_by_name["process_item"]["signature"])

            self.assertIn("fetch_async", defs_by_name)
            self.assertEqual(defs_by_name["fetch_async"]["kind"], "function")
            self.assertIn("async def fetch_async(url: str, retries: int = 3) -> dict:", defs_by_name["fetch_async"]["signature"])

            self.assertIn("MAX_CONNECTIONS", defs_by_name)
            self.assertEqual(defs_by_name["MAX_CONNECTIONS"]["kind"], "const")

            self.assertIn("os", res["imports"])
            self.assertIn("sys", res["imports"])
            self.assertIn("path", res["imports"])

            self.assertIn("BaseProcessor", res["refs"])
            self.assertIn("util_func", res["refs"])

    def test_typescript_extraction_and_relative_imports(self):
        """Test TypeScript/JavaScript multi-line function/interface extraction and relative import resolution."""
        with tempfile.TemporaryDirectory() as d:
            src_dir = os.path.join(d, "src", "services")
            os.makedirs(src_dir)

            ts_code = (
                "import { dbConnect } from '../db/client';\n"
                "import { Helper } from './helper';\n"
                "\n"
                "export interface UserAccount {\n"
                "    id: string;\n"
                "    email: string;\n"
                "    isActive: boolean;\n"
                "}\n"
                "\n"
                "export type AccountStatus = 'active' | 'suspended';\n"
                "\n"
                "export const DEFAULT_PAGE_SIZE = 50;\n"
                "\n"
                "export async function createAccount(\n"
                "    email: string,\n"
                "    initialBalance: number = 0,\n"
                "    notify: boolean = true\n"
                "): Promise<UserAccount> {\n"
                "    return dbConnect(email);\n"
                "}\n"
            )
            fpath = os.path.join(src_dir, "account.ts")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(ts_code)

            res = core_map.extract_file_symbols(fpath, root=d)
            self.assertEqual(res["file"], "src/services/account.ts")

            defs_by_name = {d["name"]: d for d in res["defs"]}
            self.assertIn("UserAccount", defs_by_name)
            self.assertEqual(defs_by_name["UserAccount"]["kind"], "interface")

            self.assertIn("AccountStatus", defs_by_name)
            self.assertEqual(defs_by_name["AccountStatus"]["kind"], "type")

            self.assertIn("DEFAULT_PAGE_SIZE", defs_by_name)
            self.assertEqual(defs_by_name["DEFAULT_PAGE_SIZE"]["kind"], "const")

            self.assertIn("createAccount", defs_by_name)
            self.assertEqual(defs_by_name["createAccount"]["kind"], "function")
            self.assertIn("createAccount", defs_by_name["createAccount"]["signature"])
            self.assertIn("Promise<UserAccount>", defs_by_name["createAccount"]["signature"])

            self.assertIn("../db/client", res["imports"])
            self.assertIn("src/db/client", res["imports"])
            self.assertIn("./helper", res["imports"])
            self.assertIn("src/services/helper", res["imports"])

    def test_global_unseeded_pagerank(self):
        """Test Global Unseeded PageRank ordering (central module imported by 3 files ranks higher)."""
        files_data = {
            "central.py": {
                "file": "central.py",
                "defs": [{"name": "central_fn", "kind": "function", "line": 1, "signature": "def central_fn():"}],
                "refs": [],
                "imports": [],
            },
            "leaf_a.py": {
                "file": "leaf_a.py",
                "defs": [{"name": "a_fn", "kind": "function", "line": 1, "signature": "def a_fn():"}],
                "refs": ["central_fn"],
                "imports": ["central"],
            },
            "leaf_b.py": {
                "file": "leaf_b.py",
                "defs": [{"name": "b_fn", "kind": "function", "line": 1, "signature": "def b_fn():"}],
                "refs": ["central_fn"],
                "imports": ["central"],
            },
            "leaf_c.py": {
                "file": "leaf_c.py",
                "defs": [{"name": "c_fn", "kind": "function", "line": 1, "signature": "def c_fn():"}],
                "refs": ["central_fn"],
                "imports": ["central"],
            },
        }
        ranked = core_map.personalized_pagerank(files_data, seed_files=None)
        self.assertEqual(len(ranked), 4)

        top_file, top_score = ranked[0]
        self.assertEqual(top_file, "central.py")
        for _file_name, score in ranked[1:]:
            self.assertGreater(top_score, score)

    def test_seeded_personalized_pagerank(self):
        """Test Seeded Personalized PageRank boosts seed file and its direct dependencies to the top."""
        files_data = {
            "src/auth.py": {
                "file": "src/auth.py",
                "defs": [{"name": "login", "kind": "function", "line": 1, "signature": "def login():"}],
                "refs": ["db_query"],
                "imports": ["src/db"],
            },
            "src/db.py": {
                "file": "src/db.py",
                "defs": [{"name": "db_query", "kind": "function", "line": 1, "signature": "def db_query():"}],
                "refs": [],
                "imports": [],
            },
            "src/reports.py": {
                "file": "src/reports.py",
                "defs": [{"name": "gen_report", "kind": "function", "line": 1, "signature": "def gen_report():"}],
                "refs": [],
                "imports": [],
            },
            "src/ui.py": {
                "file": "src/ui.py",
                "defs": [{"name": "render_ui", "kind": "function", "line": 1, "signature": "def render_ui():"}],
                "refs": [],
                "imports": [],
            },
        }
        ranked = core_map.personalized_pagerank(files_data, seed_files=["src/auth.py"])
        top_files = [f for f, _ in ranked[:2]]
        self.assertIn("src/auth.py", top_files)
        self.assertIn("src/db.py", top_files)

        scores = dict(ranked)
        self.assertGreater(scores["src/auth.py"], scores["src/reports.py"])
        self.assertGreater(scores["src/db.py"], scores["src/reports.py"])
        self.assertGreater(scores["src/auth.py"], scores["src/ui.py"])
        self.assertGreater(scores["src/db.py"], scores["src/ui.py"])

    def test_build_repo_map_formats_and_budget(self):
        """Test build_repo_map text and JSON formats and token budget truncation."""
        with tempfile.TemporaryDirectory() as d:
            for i in range(5):
                with open(os.path.join(d, f"mod_{i}.py"), "w", encoding="utf-8") as f:
                    f.write(f"def func_{i}(val: int) -> int:\n    return val + {i}\n")

            text_map = core_map.build_repo_map(root=d, as_json=False, token_budget=1000)
            self.assertIsInstance(text_map, str)
            self.assertTrue(text_map.startswith("# Repository Symbol Map (PageRank Ranked)"))
            self.assertIn("mod_0.py", text_map)
            self.assertIn("def func_0(val: int) -> int:", text_map)

            json_map = core_map.build_repo_map(root=d, as_json=True, token_budget=1000)
            self.assertIsInstance(json_map, dict)
            self.assertIn("root", json_map)
            self.assertEqual(json_map["total_files"], 5)
            self.assertIn("ranked_files", json_map)
            self.assertEqual(len(json_map["ranked_files"]), 5)

            tiny_budget = 40
            truncated_text = core_map.build_repo_map(root=d, as_json=False, token_budget=tiny_budget)
            self.assertLessEqual(len(truncated_text), tiny_budget * 4 + 100)

    def test_symlink_skipping_and_cli(self):
        """Test symlink skipping and main CLI execution."""
        with tempfile.TemporaryDirectory() as d:
            real_file = os.path.join(d, "real.py")
            with open(real_file, "w", encoding="utf-8") as f:
                f.write("def real_function(): pass\n")

            symlink_file = os.path.join(d, "symlink.py")
            try:
                os.symlink(real_file, symlink_file)
                has_symlink = True
            except (OSError, NotImplementedError):
                has_symlink = False

            if has_symlink:
                sym_symbols = core_map.extract_file_symbols(symlink_file, root=d)
                self.assertEqual(sym_symbols["defs"], [])

                repo_map = core_map.build_repo_map(root=d, as_json=True)
                ranked_paths = [rf["file"] for rf in repo_map["ranked_files"]]
                self.assertIn("real.py", ranked_paths)
                self.assertNotIn("symlink.py", ranked_paths)

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = core_map.main(["--json", "--budget", "1000", "--root", d])
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            parsed = json.loads(output)
            self.assertIn("root", parsed)
            self.assertIn("ranked_files", parsed)

    def test_empty_repo_directory(self):
        """Test build_repo_map handling of empty directories without crashing."""
        with tempfile.TemporaryDirectory() as d:
            res_json = core_map.build_repo_map(root=d, as_json=True)
            self.assertEqual(res_json["total_files"], 0)
            self.assertEqual(res_json["ranked_files"], [])
            self.assertEqual(res_json["seed_files"], [])

            res_text = core_map.build_repo_map(root=d, as_json=False)
            self.assertIn("# Repository Symbol Map (PageRank Ranked)", res_text)

    def test_syntax_error_and_unicode_resilience(self):
        """Test graceful handling of SyntaxError and invalid UTF-8 bytes without crashing build_repo_map."""
        with tempfile.TemporaryDirectory() as d:
            valid_path = os.path.join(d, "valid.py")
            with open(valid_path, "w", encoding="utf-8") as f:
                f.write("def calculate(x: int) -> int:\n    return x * 2\n")

            syntax_err_path = os.path.join(d, "syntax_err.py")
            with open(syntax_err_path, "w", encoding="utf-8") as f:
                f.write("def broken_syntax(\n")

            bad_unicode_path = os.path.join(d, "bad_unicode.py")
            with open(bad_unicode_path, "wb") as f:
                f.write(b"\xff\xfe\x00\x80def weird_fn(): pass\n")

            sym_err = core_map.extract_file_symbols(syntax_err_path, root=d)
            self.assertEqual(sym_err["defs"], [])
            self.assertEqual(sym_err["refs"], [])
            self.assertEqual(sym_err["imports"], [])

            sym_uni = core_map.extract_file_symbols(bad_unicode_path, root=d)
            self.assertEqual(sym_uni["defs"], [])

            repo_map = core_map.build_repo_map(root=d, as_json=True)
            self.assertEqual(repo_map["total_files"], 3)
            ranked_files = [rf["file"] for rf in repo_map["ranked_files"]]
            self.assertIn("valid.py", ranked_files)
            self.assertIn("syntax_err.py", ranked_files)
            self.assertIn("bad_unicode.py", ranked_files)

    def test_seed_files_normalization_and_absolute_paths(self):
        """Test seed_files passed with ./ prefix, relative path, or absolute path inside root are normalized properly."""
        with tempfile.TemporaryDirectory() as d:
            src_dir = os.path.join(d, "src")
            os.makedirs(src_dir)

            auth_path = os.path.join(src_dir, "auth.py")
            with open(auth_path, "w", encoding="utf-8") as f:
                f.write("def authenticate(user: str) -> bool:\n    return True\n")

            db_path = os.path.join(src_dir, "db.py")
            with open(db_path, "w", encoding="utf-8") as f:
                f.write("def query(): pass\n")

            other_path = os.path.join(src_dir, "other.py")
            with open(other_path, "w", encoding="utf-8") as f:
                f.write("def other(): pass\n")

            # 1. Test build_repo_map with ./ prefix
            res_dot = core_map.build_repo_map(root=d, seed_files=["./src/auth.py"], as_json=True)
            self.assertEqual(res_dot["seed_files"], ["src/auth.py"])
            self.assertEqual(res_dot["ranked_files"][0]["file"], "src/auth.py")

            # 2. Test build_repo_map with absolute path inside root
            res_abs = core_map.build_repo_map(root=d, seed_files=[auth_path], as_json=True)
            self.assertEqual(res_abs["seed_files"], ["src/auth.py"])
            self.assertEqual(res_abs["ranked_files"][0]["file"], "src/auth.py")

            # 3. Test direct personalized_pagerank with absolute path
            files_data = {
                "src/auth.py": {"file": "src/auth.py", "defs": [{"name": "auth_fn", "kind": "function", "line": 1}], "refs": [], "imports": []},
                "src/db.py": {"file": "src/db.py", "defs": [{"name": "db_fn", "kind": "function", "line": 1}], "refs": [], "imports": []},
                "src/other.py": {"file": "src/other.py", "defs": [{"name": "other_fn", "kind": "function", "line": 1}], "refs": [], "imports": []},
            }
            ranked_abs = core_map.personalized_pagerank(files_data, seed_files=[auth_path])
            self.assertEqual(ranked_abs[0][0], "src/auth.py")
            self.assertGreater(dict(ranked_abs)["src/auth.py"], dict(ranked_abs)["src/other.py"])

            # 4. Test CLI positional seed argument
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = core_map.main(["./src/auth.py", "--json", "--root", d])
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            parsed = json.loads(output)
            self.assertEqual(parsed["seed_files"], ["src/auth.py"])
            self.assertEqual(parsed["ranked_files"][0]["file"], "src/auth.py")


    def test_the_repo_map_skips_nested_checkouts_and_vendored_and_virtualenv_folders(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            def put(name: str, text: str) -> None:
                path = os.path.join(d, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
            clean = "def add(a: int, b: int) -> int:\n    return a + b\n"
            put("app.py", clean)
            put(os.path.join("src", "mod.py"), clean)
            put(os.path.join(".claude", "worktrees", "w1", ".git"), "gitdir: elsewhere\n")   # a worktree holds a .git file
            put(os.path.join(".claude", "worktrees", "w1", "copy.py"), clean)
            put(os.path.join("clone", ".git", "HEAD"), "ref: refs/heads/main\n")             # a clone holds a .git folder
            put(os.path.join("clone", "copy.py"), clean)
            put(os.path.join("vendor", "lib.py"), clean)
            put(os.path.join(".venv", "lib", "site.py"), clean)
            put(os.path.join("node_modules", "pkg", "index.js"), "module.exports = 1;\n")
            mapped = core_map.build_repo_map(root=d, as_json=True)
        self.assertNotIn("copy.py", json.dumps(mapped))

    def test_the_repo_map_skips_the_agents_and_brevio_artifact_folders(self) -> None:
        """Public pull request #11 meant the map to skip these; its change reached only the scan's skip list, and the map
        still ranked the files of ``.agents`` and ``.brevio``."""
        with tempfile.TemporaryDirectory() as d:
            for name in (os.path.join("." + "agents", "skill.py"), os.path.join(".brevio", "gen.py"), "app.py"):
                path = os.path.join(d, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("def add(a: int, b: int) -> int:\n    return a + b\n")
            mapped = json.dumps(core_map.build_repo_map(root=d, as_json=True))
        self.assertIn("app.py", mapped)
        self.assertNotIn("skill.py", mapped)
        self.assertNotIn("gen.py", mapped)


if __name__ == "__main__":
    unittest.main()
