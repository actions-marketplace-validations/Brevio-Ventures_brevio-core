"""Unit tests for brevio-core PR Maintainer Shield (core_shield).

Standard library only: runs in both the development harness and the exported core package.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from brevio.pipeline import core_shield

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


SWALLOW = "def swallow():\n    try:\n        risky()\n    except:\n        pass\n"
CLEAN = "def add(a: int, b: int) -> int:\n    return a + b\n"


def _write(d: str, name: str, text: str) -> None:
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def _run_cli(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    entry = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from brevio.pipeline import core_cli; "
        "raise SystemExit(core_cli.main(sys.argv[2:]))"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", entry, ROOT, *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )


class TestParseShieldCommand(unittest.TestCase):
    def test_parse_commands(self) -> None:
        self.assertEqual(
            core_shield.parse_shield_command("@brevio fix"),
            {"command": "fix", "args": ""},
        )
        self.assertEqual(
            core_shield.parse_shield_command("@BREVIO FIX"),
            {"command": "fix", "args": ""},
        )
        self.assertEqual(
            core_shield.parse_shield_command("@brevio explain R13.1"),
            {"command": "explain", "args": "R13.1"},
        )
        self.assertEqual(
            core_shield.parse_shield_command("@brevio explain [R5.4]"),
            {"command": "explain", "args": "[R5.4]"},
        )
        self.assertEqual(
            core_shield.parse_shield_command("@brevio roast"),
            {"command": "roast", "args": ""},
        )
        self.assertEqual(
            core_shield.parse_shield_command("@brevio triage R5.4"),
            {"command": "triage", "args": "R5.4"},
        )
        self.assertEqual(
            core_shield.parse_shield_command("@brevio heal"),
            {"command": "heal", "args": ""},
        )

    def test_rejects_shell_metacharacters(self) -> None:
        self.assertIsNone(core_shield.parse_shield_command("@brevio fix; cat /etc/passwd"))
        self.assertIsNone(core_shield.parse_shield_command("@brevio fix | ls"))
        self.assertIsNone(core_shield.parse_shield_command("@brevio fix & echo pwn"))
        self.assertIsNone(core_shield.parse_shield_command("@brevio fix `whoami`"))
        self.assertIsNone(core_shield.parse_shield_command("@brevio fix $(whoami)"))

    def test_returns_none_when_unmatched(self) -> None:
        self.assertIsNone(core_shield.parse_shield_command("LGTM, thanks for the PR!"))
        self.assertIsNone(core_shield.parse_shield_command(""))


class TestExecuteShieldCommand(unittest.TestCase):
    def test_unmatched_comment(self) -> None:
        res = core_shield.execute_shield_command("Looks good to me")
        self.assertFalse(res["matched"])
        self.assertIsNone(res["command"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["markdown_reply"], "")

    def test_explain_valid_and_unknown_rule(self) -> None:
        res = core_shield.execute_shield_command("@brevio explain R13.1")
        self.assertTrue(res["matched"])
        self.assertEqual(res["command"], "explain")
        self.assertTrue(res["ok"])
        self.assertIn("R13.1", res["markdown_reply"])

        bad_res = core_shield.execute_shield_command("@brevio explain R999.999")
        self.assertTrue(bad_res["matched"])
        self.assertFalse(bad_res["ok"])
        self.assertIn("Unknown Rule", bad_res["markdown_reply"])

    def test_roast_command(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            res = core_shield.execute_shield_command("@brevio roast", root=d)
            self.assertTrue(res["matched"])
            self.assertEqual(res["command"], "roast")
            self.assertTrue(res["ok"])
            self.assertIn("score (preview)", res["markdown_reply"])
            self.assertNotIn("Agent Safety Score", res["markdown_reply"])

    def test_fix_command_with_remediation_and_packet(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            py_file = os.path.join(d, "app.py")
            with open(py_file, "w", encoding="utf-8") as fh:
                fh.write("try:\n    pass\nexcept:\n    done = 1\n")
            res = core_shield.execute_shield_command("@brevio fix", root=d)
            self.assertTrue(res["matched"])
            self.assertEqual(res["command"], "fix")
            self.assertTrue(res["ok"])
            self.assertGreaterEqual(res["fixed_count"], 1)

    def test_triage_command(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            res = core_shield.execute_shield_command("@brevio triage R5.4", root=d)
            self.assertTrue(res["matched"])
            self.assertEqual(res["command"], "triage")
            self.assertTrue(res["ok"])
            self.assertIn("Triage", res["markdown_reply"])

    def test_unrecognized_shield_command(self) -> None:
        res = core_shield.execute_shield_command("@brevio deploy")
        self.assertTrue(res["matched"])
        self.assertFalse(res["ok"])
        self.assertIn("Unrecognized Command", res["markdown_reply"])

    def test_the_shield_roast_counts_the_root_it_is_given_and_not_the_current_folder(self) -> None:
        with tempfile.TemporaryDirectory() as here, tempfile.TemporaryDirectory() as there:
            _write(here, "noisy.py", SWALLOW)
            _write(there, "quiet.py", CLEAN)
            old_cwd = os.getcwd()
            os.chdir(here)
            try:
                res = core_shield.execute_shield_command("@brevio roast", root=there)
            finally:
                os.chdir(old_cwd)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["scorecard"]["files"], 1)
        self.assertEqual(res["scorecard"]["finding_count"], 0)


class TestShieldCLI(unittest.TestCase):
    def test_cli_execution_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            r1 = _run_cli(d, "shield", "--comment", "@brevio roast", "--json")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            data = json.loads(r1.stdout)
            self.assertTrue(data["matched"])
            self.assertEqual(data["command"], "roast")

            r2 = _run_cli(d, "shield", "--comment", "@brevio explain R13.1")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("R13.1", r2.stdout)


if __name__ == "__main__":
    unittest.main()
