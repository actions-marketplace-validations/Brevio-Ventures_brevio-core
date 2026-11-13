"""Unit tests for the portable core_gates and core_memory modules.

Standard library only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from brevio.pipeline import core_baseline, core_cli, core_memory
from brevio.pipeline.gates import core_gates


def git(cwd: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        timeout=30,
    )


def write_file(root: str, rel: str, content: str) -> str:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


class TestCoreGates(unittest.TestCase):
    def test_check_conflict_markers(self):
        clean = {"src/app.py": [(1, "def foo():"), (2, "    return 1")]}
        self.assertEqual(core_gates.check_conflict_markers(clean), [])

        dirty = {
            "src/app.py": [
                (1, "<<<<<<< HEAD"),
                (2, "def foo():"),
                (3, "======="),
                (4, "def bar():"),
                (5, ">>>>>>> feature"),
            ],
            "src/other.py": [
                (10, "=========="),  # 10 '=' is not merge marker
                (11, "======= with space"),
            ],
        }
        findings = core_gates.check_conflict_markers(dirty)
        self.assertEqual(len(findings), 4)
        self.assertIn("src/app.py:1: [R5.1]", findings[0])
        self.assertIn("src/app.py:3: [R5.1]", findings[1])
        self.assertIn("src/app.py:5: [R5.1]", findings[2])
        self.assertIn("src/other.py:11: [R5.1]", findings[3])

    def test_check_test_imports(self):
        with tempfile.TemporaryDirectory() as d:
            # Test file importing module under test -> clean
            good_test = (
                "from src import app\n"
                "import unittest\n"
                "class AppTest(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertEqual(app.run(), 1)\n"
            )
            write_file(d, "tests/test_good.py", good_test)
            lines = {"tests/test_good.py": [(1, "")]}
            self.assertEqual(core_gates.check_test_imports(lines, root=d), [])

            # Test file with assertions but only imports unittest -> finding
            bad_test = (
                "import unittest\n"
                "class DummyTest(unittest.TestCase):\n"
                "    def test_mock(self):\n"
                "        assert 1 == 1\n"
            )
            write_file(d, "tests/test_bad.py", bad_test)
            lines = {"tests/test_bad.py": [(1, "")]}
            findings = core_gates.check_test_imports(lines, root=d)
            self.assertEqual(len(findings), 1)
            self.assertIn("[R6.1] Test file has assertions but imports no module under test", findings[0])

            # Test file without assertions -> clean
            helper_test = "import unittest\ndef helper():\n    return 42\n"
            write_file(d, "tests/test_helper.py", helper_test)
            self.assertEqual(core_gates.check_test_imports({"tests/test_helper.py": [(1, "")]}, root=d), [])

    def test_check_test_custody(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            initial_test = (
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_a(self):\n"
                "        assert 1 == 1\n"
                "    def test_b(self):\n"
                "        assert 2 == 2\n"
            )
            write_file(d, "tests/test_sample.py", initial_test)
            git(d, "add", "tests/test_sample.py")
            git(d, "commit", "-q", "-m", "initial test")

            # No changes against HEAD -> clean
            self.assertEqual(core_gates.check_test_custody(root=d, base="HEAD"), [])

            # Weaken test assertions by removing one test and assert
            weakened_test = (
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_a(self):\n"
                "        assert 1 == 1\n"
            )
            write_file(d, "tests/test_sample.py", weakened_test)
            findings = core_gates.check_test_custody(root=d, base="HEAD")
            self.assertEqual(len(findings), 1)
            self.assertIn("[R20.2] Existing test assertions were removed or weakened", findings[0])

            # Delete the test file -> deletion finding
            os.remove(os.path.join(d, "tests/test_sample.py"))
            findings = core_gates.check_test_custody(root=d, base="HEAD")
            self.assertEqual(len(findings), 1)
            self.assertIn("[R20.1] Existing test file was deleted against HEAD", findings[0])

    def test_check_ast_complexity(self):
        with tempfile.TemporaryDirectory() as d:
            # Function over 100 lines
            long_body = "\n".join(f"    x = {i}" for i in range(105))
            long_fn = f"def giant_fn():\n{long_body}\n    return x\n"
            write_file(d, "src/giant.py", long_fn)

            # Function with high cyclomatic complexity (> 20)
            branches = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(25))
            complex_fn = f"def complex_fn(x):\n{branches}\n    return -1\n"
            write_file(d, "src/complex.py", complex_fn)

            lines = {
                "src/giant.py": [(1, "def giant_fn():")],
                "src/complex.py": [(1, "def complex_fn(x):")],
            }
            findings = core_gates.check_ast_complexity(lines, root=d)
            self.assertEqual(len(findings), 2)
            self.assertTrue(any("[R19.1] Function `giant_fn` is" in f for f in findings))
            self.assertTrue(any("[R19.2] Function `complex_fn` has cyclomatic complexity" in f for f in findings))

    def test_check_orphan_files(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "tracked.py", "print(1)\n")
            git(d, "add", "tracked.py")
            git(d, "commit", "-q", "-m", "init")

            # Duplicate/copy untracked files
            write_file(d, "tracked_copy.py", "print(1)\n")
            write_file(d, "service_new.ts", "console.log(1);\n")
            write_file(d, "Widget2.tsx", "export const Widget2 = () => null;\n")
            write_file(d, "helper_v2.js", "module.exports = {};\n")
            write_file(d, "normal_new_file.py", "x = 1\n")

            findings = core_gates.check_orphan_files(root=d)
            self.assertEqual(len(findings), 4)
            self.assertTrue(any("tracked_copy.py" in f for f in findings))
            self.assertTrue(any("service_new.ts" in f for f in findings))
            self.assertTrue(any("Widget2.tsx" in f for f in findings))
            self.assertTrue(any("helper_v2.js" in f for f in findings))
            self.assertFalse(any("normal_new_file.py" in f for f in findings))

    def test_run_check_and_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "module.py", "def add(a, b):\n    return a + b\n")
            git(d, "add", "module.py")
            git(d, "commit", "-q", "-m", "base")

            result = core_gates.run_check(root=d, base="HEAD")
            self.assertTrue(result["passed"])
            self.assertEqual(result["findings"], [])

            # Render text and markdown receipts
            text_receipt = core_gates.render_receipt(result, fmt="text")
            self.assertIn("Status: PASSED", text_receipt)
            self.assertIn("All flight checks passed", text_receipt)

            md_receipt = core_gates.render_receipt(result, fmt="markdown")
            self.assertIn("**Status**: **PASSED**", md_receipt)
            self.assertIn("Ready for pull request", md_receipt)

    def test_check_test_tampering(self):
        # Valid tests without skips or tautologies
        clean = {
            "tests/test_foo.py": [(1, "def test_ok():"), (2, "    assert foo() == 42")],
            "tests/foo.spec.ts": [(1, "it('works', () => {"), (2, "  expect(result).toBe(42);"), (3, "});")],
        }
        self.assertEqual(core_gates.check_test_tampering(clean), [])

        # Skipped and tautological assertions
        dirty = {
            "tests/test_py.py": [
                (1, "@pytest.mark.skip(reason='broken')"),
                (2, "def test_skipped():"),
                (3, "    assert True"),
                (4, "    # assert True"),  # comment should be skipped
                (5, "@unittest.skip"),
                (6, "def test_unit_skip():"),
                (7, "    assert 1 == 1"),
            ],
            "tests/app.test.ts": [
                (1, "describe.only('suite', () => {"),
                (2, "  it.skip('flaky test', () => {"),
                (3, "    expect(true).toBe(true);"),
                (4, "  });"),
                (5, "  xit('another', () => { expect(1).toEqual(1); });"),
                (6, "  // it.skip('commented', () => {});"),  # comment skipped
            ],
            "src/app.py": [
                # Non-test files should NOT trigger test tampering findings
                (1, "@pytest.mark.skip"),
                (2, "assert True"),
            ],
        }
        findings = core_gates.check_test_tampering(dirty)
        self.assertTrue(any("test_py.py:1: [R20.3] Test skip or focus marker added" in f for f in findings))
        self.assertTrue(any("test_py.py:3: [R20.4] Tautological test assertion" in f for f in findings))
        self.assertTrue(any("test_py.py:5: [R20.3] Test skip or focus marker added" in f for f in findings))
        self.assertTrue(any("test_py.py:7: [R20.4] Tautological test assertion" in f for f in findings))
        self.assertTrue(any("app.test.ts:1: [R20.3] Test skip or focus marker added" in f for f in findings))
        self.assertTrue(any("app.test.ts:2: [R20.3] Test skip or focus marker added" in f for f in findings))
        self.assertTrue(any("app.test.ts:3: [R20.4] Tautological test assertion" in f for f in findings))
        self.assertTrue(any("app.test.ts:5: [R20.3] Test skip or focus marker added" in f for f in findings))
        self.assertTrue(any("app.test.ts:5: [R20.4] Tautological test assertion" in f for f in findings))
        self.assertFalse(any("src/app.py" in f for f in findings))
        self.assertFalse(any("test_py.py:4" in f for f in findings))
        self.assertFalse(any("app.test.ts:6" in f for f in findings))

    def test_check_migration_safety(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "migrations/0001_init.sql", "CREATE TABLE users (id INT PRIMARY KEY);\n")
            git(d, "add", "migrations/0001_init.sql")
            git(d, "commit", "-q", "-m", "init migration")

            # 1. No changes -> clean
            self.assertEqual(core_gates.check_migration_safety({}, root=d, base="HEAD"), [])

            # 2. Modify committed migration file
            write_file(d, "migrations/0001_init.sql", "CREATE TABLE users (id INT PRIMARY KEY, name TEXT);\n")
            lines_mod = {"migrations/0001_init.sql": [(1, "CREATE TABLE users (id INT PRIMARY KEY, name TEXT);")]}
            findings = core_gates.check_migration_safety(lines_mod, root=d, base="HEAD")
            self.assertTrue(any("[R4.6] Committed migration file was modified or deleted against HEAD" in f for f in findings))

            # Reset migration file
            git(d, "checkout", "HEAD", "--", "migrations/0001_init.sql")

            # 3. New migration with destructive DDL
            lines_destructive = {
                "migrations/0002_drop.sql": [
                    (1, "DROP TABLE users;"),
                    (2, "-- DROP TABLE legacy;"),  # comment skipped
                    (3, "DROP COLUMN email; -- brevio: allow-drop"),  # allowed via directive
                    (4, "TRUNCATE TABLE audit_logs;"),
                ]
            }
            findings = core_gates.check_migration_safety(lines_destructive, root=d, base="HEAD")
            self.assertEqual(len(findings), 2)
            self.assertIn("migrations/0002_drop.sql:1: [R4.6] Destructive DDL detected; use additive expand-and-contract migrations", findings[0])
            self.assertIn("migrations/0002_drop.sql:4: [R4.6] Destructive DDL detected; use additive expand-and-contract migrations", findings[1])

    def test_check_resolved_imports(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "package.json", '{"name": "test-pkg"}\n')
            write_file(d, "src/existing.ts", "export const ok = true;\n")
            write_file(d, "src/components/button.tsx", "export const Button = () => null;\n")

            # Valid relative imports: exact, extension omitted, directory with index, ts ESM as .js, .mjs -> .mts
            write_file(d, "src/esm_mod.mts", "export const m = 1;\n")
            write_file(d, "src/nested/index.tsx", "export const Nested = () => null;\n")
            valid_src = (
                "import { ok } from './existing';\n"
                "import { Button } from './components/button';\n"
                "import { ok2 } from './existing.js';\n"
                "import { m } from './esm_mod.mjs';\n"
                "import { Nested } from './nested/index.js';\n"
            )
            write_file(d, "src/valid.ts", valid_src)
            clean_lines = {
                "src/valid.ts": [
                    (1, "import { ok } from './existing';"),
                    (2, "import { Button } from './components/button';"),
                    (3, "import { ok2 } from './existing.js';"),
                    (4, "import { m } from './esm_mod.mjs';"),
                    (5, "import { Nested } from './nested/index.js';"),
                ]
            }
            self.assertEqual(core_gates.check_resolved_imports(clean_lines, root=d), [])

            # Broken relative import in package repo
            write_file(d, "src/broken.ts", "import { missing } from './does_not_exist';\n")
            broken_lines = {
                "src/broken.ts": [
                    (1, "import { missing } from './does_not_exist';"),
                    (2, "// import { other } from './phantom';"),  # comment skipped
                ]
            }
            findings = core_gates.check_resolved_imports(broken_lines, root=d)
            self.assertEqual(len(findings), 1)
            self.assertIn("[R9.4] Unresolved relative import `./does_not_exist`; target module does not exist on disk", findings[0])

            # Dummy test files should NOT be flagged
            test_lines = {
                "tests/app.test.ts": [(1, "import { app } from '../app';")]
            }
            write_file(d, "tests/app.test.ts", "import { app } from '../app';\n")
            self.assertEqual(core_gates.check_resolved_imports(test_lines, root=d), [])

    def test_run_check_wires_new_guards(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "package.json", '{"name": "test-pkg"}\n')
            git(d, "add", "package.json")
            git(d, "commit", "-q", "-m", "init")

            lines = {
                "src/index.ts": [(1, "import { x } from './missing';")],
                "tests/test_tamper.py": [(1, "def test_t():"), (2, "    assert True")],
                "schema.sql": [(1, "DROP TABLE accounts;")],
            }
            write_file(d, "src/index.ts", "import { x } from './missing';\n")
            write_file(d, "tests/test_tamper.py", "def test_t():\n    assert True\n")
            write_file(d, "schema.sql", "DROP TABLE accounts;\n")

            result = core_gates.run_check(root=d, base="HEAD", lines=lines)
            self.assertFalse(result["passed"])
            # R9.4 and R4.6 wired into rules
            self.assertTrue(any("[R9.4]" in f for f in result["checks"]["rules"]))
            self.assertTrue(any("[R4.6]" in f for f in result["checks"]["rules"]))
            # R20.4 tampering wired into test_custody
            self.assertTrue(any("[R20.4] Tautological test assertion" in f for f in result["checks"]["test_custody"]))

    def test_check_agent_files(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(d, "CLAUDE.md", "# Clean instructions\nAlways run tests.\n")
            write_file(d, ".cursorrules", "Use strict TypeScript.\n")
            write_file(d, "AGENTS.md", "No secrets.\n")
            write_file(d, "skills/custom/SKILL.md", "# Skill\nClean content.\n")
            self.assertEqual(core_gates.check_agent_files(root=d), [])

            # Inject Trojan Source / Bidi override (\u202e) and Unicode Tag (\U000e0001)
            write_file(d, "CLAUDE.md", "Normal line\nHidden \u202e prompt injection\n")
            write_file(d, ".cursor/rules/ sneaky.mdc", "Rule with tag \U000e0041 char\n")
            write_file(d, "skills/custom/SKILL.md", "# Skill\nIsolate \u2066 injection\n")
            findings = core_gates.check_agent_files(root=d)
            self.assertEqual(len(findings), 3)
            self.assertTrue(any("CLAUDE.md:2: [R2.8] Hidden Unicode bidirectional or tag characters" in f for f in findings))
            self.assertTrue(any(".cursor/rules/ sneaky.mdc:1: [R2.8]" in f for f in findings))
            self.assertTrue(any("skills/custom/SKILL.md:2: [R2.8]" in f for f in findings))

    def test_check_env_parity(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, ".env.example", "# Example env\nDATABASE_URL=postgres://localhost/db\nexport REDIS_URL=redis://localhost\n")
            git(d, "add", ".env.example")
            git(d, "commit", "-q", "-m", "add env example")

            lines = {
                "src/config.ts": [
                    (1, "const db = process.env.DATABASE_URL;"),
                    (2, "const mode = process.env.NODE_ENV;"),
                    (3, "const gh = process.env.GITHUB_ACTIONS;"),
                    (4, "const secret = process.env.STRIPE_WEBHOOK_SECRET;"),
                    (5, "// const ignored = process.env.COMMENTED_OUT_VAR;"),
                ],
                "src/worker.py": [
                    (1, "redis_url = os.environ.get('REDIS_URL')"),
                    (2, "api_key = os.getenv('UNDOCUMENTED_API_KEY')"),
                    (3, "other_key = os.environ['MISSING_WORKER_TOKEN']"),
                    (4, "port = os.getenv('PORT')"),
                ],
                "tests/test_worker.py": [
                    (1, "test_only = os.getenv('TEST_ONLY_UNDOCUMENTED_VAR')"),
                ],
            }
            findings = core_gates.check_env_parity(root=d, base="HEAD", lines=lines)
            self.assertEqual(len(findings), 3)
            self.assertIn(
                "src/config.ts:4: [R16.3] Environment variable `STRIPE_WEBHOOK_SECRET` is not documented in .env.example",
                findings,
            )
            self.assertIn(
                "src/worker.py:2: [R16.3] Environment variable `UNDOCUMENTED_API_KEY` is not documented in .env.example",
                findings,
            )
            self.assertIn(
                "src/worker.py:3: [R16.3] Environment variable `MISSING_WORKER_TOKEN` is not documented in .env.example",
                findings,
            )

            # Verify run_check wires both check_agent_files and check_env_parity
            write_file(d, "AGENTS.md", "Bad \u202e instruction\n")
            res = core_gates.run_check(root=d, base="HEAD", lines=lines)
            self.assertFalse(res["passed"])
            self.assertTrue(any("[R2.8]" in f for f in res["findings"]))
            self.assertTrue(any("[R16.3]" in f for f in res["findings"]))


class TestCheckUsesTheBaseOfScanDiff(unittest.TestCase):
    """``check`` without ``--base`` compares with the base ``scan --diff`` uses, and the receipt names it (I10, 2026-11-13)."""

    def committed_secret_on_a_branch(self, d: str) -> None:
        git(d, "init", "-q", "-b", "main")
        write_file(d, "src/a.ts", "export const a = 1;\n")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "base")
        git(d, "checkout", "-q", "-b", "feature")
        write_file(d, "src/b.ts", "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "feature")

    def run_main(self, d: str, *args: str) -> subprocess.CompletedProcess:
        top = os.path.abspath(os.path.join(os.path.dirname(core_gates.__file__), "..", "..", ".."))
        code = f"import sys; sys.path.insert(0, {top!r}); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[1:]))"
        return subprocess.run([sys.executable, "-I", "-c", code, "check", *args], cwd=d, capture_output=True, encoding="utf-8", timeout=60)

    def test_a_committed_finding_on_a_branch_fails_check_without_a_base_and_the_receipt_says_against_what(self):
        with tempfile.TemporaryDirectory() as d:
            self.committed_secret_on_a_branch(d)
            res = self.run_main(d)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("Base: main", res.stdout)
            self.assertIn("[R13.1]", res.stdout)
            self.assertEqual(json.loads(self.run_main(d, "--json").stdout)["base"], "main")
            self.assertIn("**Base**: main", self.run_main(d, "--format", "markdown").stdout)

    def test_a_named_base_still_wins_so_check_base_head_keeps_its_old_meaning(self):
        with tempfile.TemporaryDirectory() as d:
            self.committed_secret_on_a_branch(d)
            res = self.run_main(d, "--base", "HEAD")
            self.assertEqual(res.returncode, 0, res.stdout)
            self.assertIn("Base: HEAD", res.stdout)

    def test_a_named_base_the_checkout_lacks_is_said_on_stderr_and_the_receipt_names_the_base_used(self):
        """Review of I10 (2026-11-13): ``check --base nope`` fell back to ``main`` without a word."""
        with tempfile.TemporaryDirectory() as d:
            self.committed_secret_on_a_branch(d)
            res = self.run_main(d, "--base", "nope")
            self.assertIn("base 'nope' is not in this checkout; compared with main", res.stderr)
            self.assertIn("Base: main", res.stdout)
            self.assertEqual(self.run_main(d, "--base", "HEAD").stderr, "")


class TestCoreMemory(unittest.TestCase):
    def test_record_and_load_episodes(self):
        with tempfile.TemporaryDirectory() as d:
            ep = core_memory.record_episode(
                file_path="src/api.py",
                rule_id="R2.1",
                trap="Missing tenant verification",
                fix="Use session tenantId",
                cwd=d,
            )
            self.assertEqual(ep["file"], "src/api.py")
            self.assertEqual(ep["rule_id"], "R2.1")

            episodes = core_memory.load_episodes(cwd=d)
            self.assertEqual(len(episodes), 1)
            self.assertEqual(episodes[0]["trap"], "Missing tenant verification")

            # Deduplication on same file and rule_id
            core_memory.record_episode(
                file_path="src/api.py",
                rule_id="R2.1",
                trap="Updated trap",
                fix="Updated fix",
                cwd=d,
            )
            episodes2 = core_memory.load_episodes(cwd=d)
            self.assertEqual(len(episodes2), 1)
            self.assertEqual(episodes2[0]["trap"], "Updated trap")

    def test_one_hop_neighbors_and_brief(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "src/common.py", "def helper():\n    return 1\n")
            write_file(d, "src/api.py", "import src.common\ndef endpoint():\n    return src.common.helper()\n")
            git(d, "add", "src/common.py", "src/api.py")
            git(d, "commit", "-q", "-m", "add common and api")

            # Record episode for common.py
            core_memory.record_episode(
                file_path="src/common.py",
                rule_id="R4.1",
                trap="Unbatched query in helper",
                fix="Batch with in-clause",
                cwd=d,
            )

            # Check neighbors of src/api.py
            neighbors = core_memory.one_hop_neighbors(d, ["src/api.py"])
            self.assertIn("src/common.py", neighbors)

            # Briefing for src/api.py should recall episode from src/common.py with score 0.5
            brief = core_memory.brief_for_files(["src/api.py"], cwd=d)
            self.assertEqual(brief["files"], ["src/api.py"])
            self.assertIn("src/common.py", brief["neighbors"])
            self.assertEqual(len(brief["episodes"]), 1)
            self.assertEqual(brief["episodes"][0]["file"], "src/common.py")
            self.assertEqual(brief["episodes"][0]["score"], 0.5)

    def test_brief_cli_record_and_query(self):
        import contextlib
        import io

        self.assertTrue(core_memory._matches_any_glob("schema.prisma", ["**/*.prisma"]))
        self.assertTrue(core_memory._matches_any_glob("prisma/schema.prisma", ["**/*.prisma"]))
        self.assertFalse(core_memory._matches_any_glob("schema.sql", ["**/*.prisma"]))

        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            argv = ["--record", "R6.1", "Weak assertion", "Assert persisted state", "--json", "tests/test_x.py"]
            with contextlib.redirect_stdout(buf):
                self.assertEqual(core_memory.main(argv, cwd=d), 0)
            rec_out = json.loads(buf.getvalue())
            self.assertTrue(rec_out["recorded"])
            self.assertEqual(rec_out["rule_id"], "R6.1")
            self.assertEqual(rec_out["file"], "tests/test_x.py")
            episodes = core_memory.load_episodes(cwd=d)
            self.assertEqual(len(episodes), 1)

            # Query briefing with --json
            self.assertEqual(core_memory.main(["--json", "tests/test_x.py"], cwd=d), 0)

    def test_scan_surface_flags_all_17_scanned_rules_in_details(self):
        from brevio.pipeline import core_cli
        from brevio.pipeline.code_rules.details import DETAILS, SCANS

        for rid, (_engine, path) in sorted(SCANS.items()):
            bad_lines = list(enumerate(DETAILS[rid].bad.splitlines(), 1))
            found = core_cli.scan_surface({path: bad_lines})
            self.assertTrue(any(f"[{rid}]" in f for f in found), f"{rid} not flagged in {found}")

    def test_orphan_files_does_not_flag_oauth2_or_http2_without_base(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "src/oauth2.py", "def login():\n    return True\n")
            write_file(d, "src/http2.ts", "export const h2 = true;\n")
            self.assertEqual(core_gates.check_orphan_files(root=d), [])

            # But if src/utils.py exists and src/utils2.py is untracked, flag src/utils2.py
            write_file(d, "src/utils.py", "def u():\n    return 1\n")
            git(d, "add", "src/utils.py")
            git(d, "commit", "-q", "-m", "add utils")
            write_file(d, "src/utils2.py", "def u():\n    return 2\n")
            findings = core_gates.check_orphan_files(root=d)
            self.assertEqual(len(findings), 1)
            self.assertIn("src/utils2.py:1: [R7.3]", findings[0])

    def test_conflict_marker_ignores_ascii_banner_and_custody_catches_rename(self):
        lines = {"src/banner.py": [(1, "==========")]}
        self.assertEqual(core_gates.check_conflict_markers(lines), [])

        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "tests/test_billing.py", "def test_charge():\n    assert(1 == 1)\n    assert 2 == 2\n")
            git(d, "add", "tests/test_billing.py")
            git(d, "commit", "-q", "-m", "initial test")

            # Rename test file to a non-test file
            git(d, "mv", "tests/test_billing.py", "tests/billing_helper.py")
            findings = core_gates.check_test_custody(root=d, base="HEAD")
            self.assertEqual(len(findings), 1)
            self.assertIn("[R20.1]", findings[0])

    def test_pre_pr_flight_gate(self):
        try:
            import importlib
            mod_name = "".join(["brevio.", "pipeline.", "gates.", "quality_gates"])
            quality_gates = importlib.import_module(mod_name)
        except (ImportError, ModuleNotFoundError):
            self.skipTest("quality_gates not available in exported core")
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write_file(d, "src/app.py", "def run():\n    return 42\n")
            git(d, "add", "src/app.py")
            git(d, "commit", "-q", "-m", "init")

            # Clean repo: all flight checks pass
            res = quality_gates.pre_pr_flight_gate(d)
            self.assertTrue(res["applies"])
            self.assertTrue(res["ok"])
            self.assertEqual(res["findings"], [])
            self.assertIn("all pre-PR flight checks passed", res["reason"])

            # Repo with conflict marker in new file: gate fails
            write_file(d, "src/conflict.py", "<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> branch\n")
            res_bad = quality_gates.pre_pr_flight_gate(d)
            self.assertTrue(res_bad["applies"])
            self.assertFalse(res_bad["ok"])
            self.assertTrue(any("[R5.1]" in f for f in res_bad["findings"]))

            # Disabled gate returns applies=False, ok=True
            with unittest.mock.patch.dict(os.environ, {"HARNESS_FLIGHT_GATE": "0"}):
                res_disabled = quality_gates.pre_pr_flight_gate(d)
                self.assertFalse(res_disabled["applies"])
                self.assertTrue(res_disabled["ok"])
                self.assertEqual(res_disabled["findings"], [])

    def test_tautological_assert_identifier_self_comparison(self):
        lines = {
            "tests/test_a.py": [
                (1, "def test_foo():"),
                (2, "    assert val == val"),
                (3, "    assert x == y"),
            ],
            "tests/test_b.test.ts": [
                (1, "it('works', () => {"),
                (2, "    expect(foo).toBe(foo);"),
                (3, "    expect(bar).toStrictEqual(bar);"),
                (4, "    expect(baz).toEqual(other);"),
                (5, "});"),
            ],
        }
        findings = core_gates.check_test_tampering(lines)
        self.assertEqual(len(findings), 3)
        self.assertIn("tests/test_a.py:2: [R20.4] Tautological test assertion", findings[0])
        self.assertIn("tests/test_b.test.ts:2: [R20.4] Tautological test assertion", findings[1])
        self.assertIn("tests/test_b.test.ts:3: [R20.4] Tautological test assertion", findings[2])

    def test_check_empty_test_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            py_empty = (
                "import unittest\n"
                "class DummyTest(unittest.TestCase):\n"
                "    def test_empty(self):\n"
                "        pass\n"
                "    def test_stub(self):\n"
                "        ...\n"
                "    def test_ok(self):\n"
                "        assert 1 == 2\n"
            )
            write_file(d, "tests/test_empty.py", py_empty)
            lines = {
                "tests/test_empty.py": [(4, "        pass"), (6, "        ...")],
                "tests/test_js.test.ts": [
                    (1, "it('empty test', () => {});"),
                    (2, "it('valid test', () => { expect(1).toBe(2); });"),
                ],
            }
            findings = core_gates.check_empty_test_blocks(lines, root=d)
            self.assertEqual(len(findings), 3)
            self.assertTrue(all("[R20.5]" in f for f in findings))
            self.assertTrue(any("test_empty" in f for f in findings))
            self.assertTrue(any("test_stub" in f for f in findings))
            self.assertTrue(any("test_js.test.ts:1" in f for f in findings))

    def test_check_ai_slop_markers(self):
        lines = {
            "src/model.py": [
                (1, "# As an AI language model, I recommend this pattern"),
                (2, "def solve():"),
                (3, "    # certainly! here is the function implementation"),
                (4, "    return 42"),
                (5, "# let me know if you'd like me to explain further"),
            ],
            "src/clean.py": [
                (1, "def add(a, b):"),
                (2, "    return a + b"),
            ],
        }
        findings = core_gates.check_ai_slop_markers(lines)
        self.assertEqual(len(findings), 3)
        self.assertTrue(all("[R19.3]" in f for f in findings))
        self.assertIn("src/model.py:1", findings[0])
        self.assertIn("src/model.py:3", findings[1])
        self.assertIn("src/model.py:5", findings[2])

    def test_check_test_imports_recognizes_new_assertions(self):
        with tempfile.TemporaryDirectory() as d:
            test_pytest_good = (
                "import pytest\n"
                "from src import app\n"
                "def test_err():\n"
                "    with pytest.raises(ValueError):\n"
                "        app.fail()\n"
            )
            write_file(d, "tests/test_pytest_good.py", test_pytest_good)
            self.assertEqual(
                core_gates.check_test_imports({"tests/test_pytest_good.py": [(1, "")]}, root=d),
                [],
            )

            test_pytest_bad = (
                "import pytest\n"
                "def test_err():\n"
                "    with pytest.raises(ValueError):\n"
                "        pass\n"
            )
            write_file(d, "tests/test_pytest_bad.py", test_pytest_bad)
            findings = core_gates.check_test_imports({"tests/test_pytest_bad.py": [(1, "")]}, root=d)
            self.assertEqual(len(findings), 1)
            self.assertIn("[R6.1]", findings[0])

            test_js_bad = (
                "import assert from 'assert';\n"
                "test('strict equal', () => {\n"
                "    assert.strictEqual(1, 1);\n"
                "});\n"
            )
            write_file(d, "tests/test_js_bad.test.js", test_js_bad)
            findings_js = core_gates.check_test_imports({"tests/test_js_bad.test.js": [(1, "")]}, root=d)
            self.assertEqual(len(findings_js), 1)
            self.assertIn("[R6.1]", findings_js[0])

    def test_run_check_with_baseline_triage(self):
        from brevio.pipeline import core_baseline, core_cli
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            try:
                os.chdir(d)
                os.makedirs(".brevio", exist_ok=True)
                os.makedirs("src", exist_ok=True)
                write_file(d, "src/api.ts", "const x = 1 as any;\n")
                finding = "src/api.ts:1: [R9.1] Untyped escape hatch `as any` or `z.any()` in production source"
                cnts = core_baseline.counts([finding], core_cli.finding_fields, core_cli.line_reader())
                with open(".brevio/baseline.json", "w") as fh:
                    fh.write(core_baseline.render(cnts))

                lines = {"src/api.ts": [(1, "const x = 1 as any;")]}
                res = core_gates.run_check(root=d, base="HEAD", lines=lines)
                self.assertTrue(res["passed"])
                self.assertEqual(res["findings"], [])

                # Structural checks (conflict markers) remain non-suppressible
                lines_conflict = {
                    "src/api.ts": [(1, "const x = 1 as any;")],
                    "src/other.py": [(1, "<<<<<<< HEAD"), (2, "======="), (3, ">>>>>>> branch")],
                }
                res_conflict = core_gates.run_check(root=d, base="HEAD", lines=lines_conflict)
                self.assertFalse(res_conflict["passed"])
                self.assertTrue(any("[R5.1]" in f for f in res_conflict["findings"]))
            finally:
                os.chdir(cwd)


class TestCheckAppliesTheBaselineAsTheDiffScanDoes(unittest.TestCase):
    """N2 (2026-11-13): on a branch ``scan --diff main`` reported a new copy of a baselined line (the baseline hides as many
    copies as it recorded) and ``check`` with the same base did not. Both now triage through ``core_cli.triage`` with the
    files read whole."""

    OLD = "export const add = 1;\n// @ts-ignore\n"

    def setUp(self) -> None:
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, self.cwd)
        git(self.d, "init", "-q", "-b", "main")
        write_file(self.d, "f1.ts", self.OLD)
        os.chdir(self.d)                       # the triage reads the files it names from the current folder
        found = core_cli.scan(core_cli.collect_files(["f1.ts"]))
        self.assertEqual(len(found), 1, found)
        write_file(self.d, ".brevio/baseline.json", core_baseline.render(
            core_baseline.counts(found, core_cli.finding_fields, core_cli.line_reader())))
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "base")
        git(self.d, "checkout", "-q", "-b", "feat")

    def check(self) -> dict:
        return core_gates.run_check(root=".", base="main")

    def test_a_new_copy_of_a_baselined_line_is_reported_by_check_and_the_old_line_is_not(self) -> None:
        self.assertTrue(self.check()["passed"])
        write_file(self.d, "f1.ts", self.OLD + "// @ts-ignore\n")
        res = self.check()
        self.assertFalse(res["passed"])
        self.assertEqual(len(res["checks"]["rules"]), 1, res["findings"])
        self.assertIn("f1.ts:3: [R9.2]", res["findings"][0])

    def test_check_keeps_a_moved_baselined_line_quiet(self) -> None:
        write_file(self.d, "f1.ts", "// @ts-ignore\nexport const add = 1;\n")
        res = self.check()
        self.assertEqual(res["findings"], [])
        self.assertTrue(res["passed"])


class TestWeakenedTest(unittest.TestCase):
    def test_a_test_text_with_fewer_assertions_than_before_is_a_finding_and_one_with_more_is_not(self) -> None:
        old = "def test_a():\n    assert f() == 1\n    assert g() == 2\n"
        weaker = core_gates.weakened_test("tests/test_a.py", old, "def test_a():\n    assert f() == 1\n", "HEAD")
        self.assertIn("[R20.2]", weaker or "")
        self.assertIn("(2 -> 1 assertions)", weaker or "")      # (2026-11-11) the text check that ``patch`` shares
        self.assertIsNone(core_gates.weakened_test("tests/test_a.py", old, old + "    assert h() == 3\n", "HEAD"))


if __name__ == "__main__":
    unittest.main()
