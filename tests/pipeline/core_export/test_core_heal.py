"""Unit tests for brevio-core heal (Pillar 2: Autonomous Self-Healing Loop).

Standard library and exported brevio modules only. Zero forbidden words, no dynamic network code.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout

from brevio.pipeline import core_heal, core_memory

AWS_KEY_SAMPLE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
REQUESTS_SAMPLE = "import requests\nresp = requests.get(url)\n"
CLEAN_SAMPLE = "export const add = (a: number, b: number): number => a + b;\n"
CONFLICT_SAMPLE = "<<<<<<< HEAD\nconst a = 1;\n=======\nconst a = 2;\n>>>>>>> main\n"


class TestCoreHeal(unittest.TestCase):
    def test_run_heal_autofixes_deterministic_rule_r101(self):
        with tempfile.TemporaryDirectory() as d:
            src_file = os.path.join(d, "fetcher.py")
            with open(src_file, "w", encoding="utf-8") as fh:
                fh.write(REQUESTS_SAMPLE)

            res = core_heal.run_heal(root=d, apply_fixes=True)
            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "clean")
            self.assertEqual(res["auto_fixed_count"], 1)
            self.assertIn("fetcher.py", res["auto_fixed_files"])
            self.assertEqual(res["remaining_findings"], [])
            self.assertEqual(res["gate_failures"], [])
            self.assertEqual(res["heal_packet"], "")

            with open(src_file, encoding="utf-8") as fh:
                fixed_content = fh.read()
            self.assertIn("timeout=30", fixed_content)

            text_output = core_heal.format_heal_text(res)
            self.assertIn("✅ Self-heal complete: 0 remaining findings", text_output)
            self.assertIn("1 auto-fixed", text_output)

    def test_run_heal_unfixable_rule_violation_returns_needs_repair(self):
        """Changed on purpose (D3): the packet used to hold a SEARCH/REPLACE block for a secret; it holds none now."""
        with tempfile.TemporaryDirectory() as d:
            secret_file = os.path.join(d, "secret.ts")
            with open(secret_file, "w", encoding="utf-8") as fh:
                fh.write(AWS_KEY_SAMPLE)

            res = core_heal.run_heal(root=d, apply_fixes=True)
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "needs_repair")
            self.assertEqual(res["auto_fixed_count"], 0)
            self.assertTrue(any("[R13.1]" in f for f in res["remaining_findings"]))
            self.assertIn("secret.ts", res["implicated_files"])

            packet = res["heal_packet"]
            self.assertIn("<brevio_heal_packet>", packet)
            self.assertNotIn("<<<<<<< SEARCH", packet)     # a secret has no verified fix: no edit is invented (D3, 2026-11-13)
            self.assertIn("</brevio_heal_packet>", packet)

            formatted = core_heal.format_heal_text(res)
            self.assertEqual(formatted, packet)

    def test_run_heal_apply_fixes_false_keeps_deterministic_finding(self):
        with tempfile.TemporaryDirectory() as d:
            src_file = os.path.join(d, "client.py")
            with open(src_file, "w", encoding="utf-8") as fh:
                fh.write(REQUESTS_SAMPLE)

            res = core_heal.run_heal(root=d, apply_fixes=False)
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "needs_repair")
            self.assertEqual(res["auto_fixed_count"], 0)
            self.assertTrue(any("[R10.1]" in f for f in res["remaining_findings"]))

            with open(src_file, encoding="utf-8") as fh:
                self.assertNotIn("timeout=30", fh.read())

    def test_run_heal_gate_failure_conflict_marker(self):
        """Changed on purpose (D3): the packet lists the conflict markers and no longer replaces them with comments."""
        with tempfile.TemporaryDirectory() as d:
            src_file = os.path.join(d, "merge.ts")
            with open(src_file, "w", encoding="utf-8") as fh:
                fh.write(CONFLICT_SAMPLE)

            res = core_heal.run_heal(root=d, apply_fixes=True)
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "needs_repair")
            self.assertTrue(len(res["gate_failures"]) > 0 or len(res["remaining_findings"]) > 0)
            self.assertIn("<brevio_heal_packet>", res["heal_packet"])
            self.assertNotIn("<<<<<<< SEARCH", res["heal_packet"])    # no comment-only replacement for a gate failure (D3)

    def test_main_json_clean_exits_0(self):
        with tempfile.TemporaryDirectory() as d:
            src_file = os.path.join(d, "clean.ts")
            with open(src_file, "w", encoding="utf-8") as fh:
                fh.write(CLEAN_SAMPLE)

            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                exit_code = core_heal.main(["--root", d, "--json"])

            self.assertEqual(exit_code, 0)
            data = json.loads(out.getvalue())
            self.assertTrue(data["ok"])
            self.assertEqual(data["status"], "clean")

    def test_main_json_needs_repair_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            src_file = os.path.join(d, "secret.ts")
            with open(src_file, "w", encoding="utf-8") as fh:
                fh.write(AWS_KEY_SAMPLE)

            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                exit_code = core_heal.main(["--root", d, "--json"])

            self.assertEqual(exit_code, 1)
            data = json.loads(out.getvalue())
            self.assertFalse(data["ok"])
            self.assertEqual(data["status"], "needs_repair")
            self.assertIn("<brevio_heal_packet>", data["heal_packet"])

    def test_main_format_text_output(self):
        with tempfile.TemporaryDirectory() as d:
            src_file = os.path.join(d, "req.py")
            with open(src_file, "w", encoding="utf-8") as fh:
                fh.write(REQUESTS_SAMPLE)

            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                exit_code = core_heal.main(["--root", d, "--format", "text"])

            self.assertEqual(exit_code, 0)
            self.assertIn("✅ Self-heal complete", out.getvalue())

    def test_constants_contract(self):
        self.assertEqual(core_heal.EXIT_OK, 0)
        self.assertEqual(core_heal.EXIT_BLOCK, 1)
        self.assertEqual(core_heal.MAX_HEAL_PACKET_CHARS, 6000)


class TestHealReportsWhatItCouldNotDo(unittest.TestCase):
    """A failure of the guards or the memory brief used to vanish into a bare ``except`` (2026-11-11): it is reported now."""

    def plant(self, d):
        with open(os.path.join(d, "secret.ts"), "w", encoding="utf-8") as fh:
            fh.write(AWS_KEY_SAMPLE)

    def test_a_clean_run_has_no_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            self.plant(d)
            res = core_heal.run_heal(root=d)
        self.assertEqual(res["warnings"], [])

    def test_a_guards_file_in_the_wrong_shape_is_reported_and_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            self.plant(d)
            os.makedirs(os.path.join(d, ".brevio"))
            path = os.path.join(d, ".brevio", "guards.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[1, 2]")
            res = core_heal.run_heal(root=d)
            with open(path, encoding="utf-8") as fh:
                kept = fh.read()
        self.assertEqual(kept, "[1, 2]")
        self.assertTrue(any("not in the expected shape" in w for w in res["warnings"]), res["warnings"])
        self.assertTrue(any("[R13.1]" in f for f in res["remaining_findings"]))      # the repair itself still went on
        self.assertIn("note: guards skipped", core_heal.format_heal_text(res))

    def test_a_guards_file_that_cannot_be_read_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            self.plant(d)
            os.makedirs(os.path.join(d, ".brevio", "guards.json"))      # a folder where the file should be
            res = core_heal.run_heal(root=d)
        self.assertTrue(any(w.startswith("guards ") for w in res["warnings"]), res["warnings"])

    def test_a_memory_brief_that_fails_to_read_is_reported_and_the_packet_is_still_made(self):
        with tempfile.TemporaryDirectory() as d:
            self.plant(d)
            with unittest.mock.patch.object(core_memory, "brief_for_files", side_effect=OSError("disk gone")):
                res = core_heal.run_heal(root=d)
        self.assertEqual(res["warnings"], ["memory brief skipped: OSError: disk gone"])
        self.assertIn("<brevio_heal_packet>", res["heal_packet"])

    def test_a_bug_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as d:
            self.plant(d)
            with unittest.mock.patch.object(core_memory, "brief_for_files", side_effect=TypeError("a bug")):
                with self.assertRaises(TypeError):
                    core_heal.run_heal(root=d)


PY_BAD = (
    "import subprocess\nimport requests\n"
    "def f(u, q, c):\n"
    "    try:\n"
    "        c.execute(f\"select * from t where id = {q}\")\n"
    "        subprocess.run(q, shell=True)\n"
    "        requests.get(u, verify=False)\n"
    "    except:\n"
    "        u = None\n"
    "KEY = \"" + "sk_live_" + "abcdefghijklmnopqrstuvwx12345678\"\n"
)


def _blocks(packet: str) -> list[str]:
    return packet.split("<<<<<<< SEARCH\n")[1:]


class TestPacketHoldsOnlyVerifiedRepairs(unittest.TestCase):
    """D3 and I30 (2026-11-13): a repair block is a fix the scan confirms; everything else is a finding and its why."""

    def packet(self, max_chars=6000):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "app.py"), "w", encoding="utf-8") as fh:
                fh.write(PY_BAD)
            return core_heal.run_heal(root=d, apply_fixes=False, compile_guards=False, max_chars=max_chars)["heal_packet"]

    def test_no_code_line_is_replaced_by_comments_or_by_another_language(self):
        packet = self.packet()
        self.assertNotIn("# Fix [", packet)
        self.assertNotIn("# Ref:", packet)
        self.assertNotIn("Resolved:", packet)
        self.assertNotIn("BaseException", packet)
        self.assertNotIn("const ", packet)          # the examples of R13.1 and R2.8 are TypeScript; the file is Python
        self.assertNotIn("JSON.parse", packet)
        self.assertNotIn("[]", packet)

    def test_the_one_edit_is_the_bare_except_and_it_keeps_the_line_count(self):
        packet = self.packet()
        blocks = _blocks(packet)
        self.assertEqual(len(blocks), 1, packet)
        search, rest = blocks[0].split("=======\n")
        self.assertEqual(search, "    except:\n")
        self.assertTrue(rest.startswith("    except Exception:\n>>>>>>> REPLACE"), rest)

    def test_an_example_in_the_language_of_the_file_is_kept(self):
        packet = self.packet()
        self.assertIn("Good: subprocess.run(cmd, timeout=60, check=True)", packet)

    def test_the_example_of_a_rule_with_several_kinds_of_finding_comes_from_the_hints_in_the_language_of_the_file(self):
        """The hook and the heal work met here: a Python SQL finding gets the Python example of ``hints.example``."""
        packet = self.packet()
        self.assertIn("[R2.8]", packet)
        self.assertIn('Good: cur.execute("SELECT * FROM users WHERE name = %s", (name,))', packet)
        self.assertNotIn("db.query", packet)
        self.assertNotIn("dangerouslySetInnerHTML", packet.split("Rule Rationales:")[-1])
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.ts"), "w", encoding="utf-8") as fh:
                fh.write("const q = `SELECT * FROM users WHERE name = '${name}'`;\ndb.query(q);\n")
            ts = core_heal.run_heal(root=d, apply_fixes=False, compile_guards=False)["heal_packet"]
        self.assertNotIn("cur.execute", ts)

    def test_the_report_says_it_is_verified_and_the_stop_hook_reads_that_flag(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "app.py"), "w", encoding="utf-8") as fh:
                fh.write(PY_BAD)
            rep = core_heal.run_heal(root=d, apply_fixes=False, compile_guards=False)
        self.assertIs(rep["verified"], True)
        self.assertEqual(len(_blocks(rep["heal_packet"])), 1)

    def test_a_requests_edit_that_would_keep_verify_off_is_not_offered(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
                fh.write("import requests\nrequests.get(u, verify=False)\n")
            res = core_heal.run_heal(root=d, apply_fixes=False, compile_guards=False)
        self.assertFalse(_blocks(res["heal_packet"]))
        self.assertIn("[R10.1]", res["heal_packet"])

    def test_a_finding_without_a_rule_id_or_outside_the_project_has_no_edit(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            with open(os.path.join(other, "o.py"), "w", encoding="utf-8") as fh:
                fh.write("try:\n    pass\nexcept:\n    pass\n")
            with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
                fh.write("except:\n")
            outside = os.path.relpath(os.path.join(other, "o.py"), d)
            self.assertEqual(core_heal._surgical_edit_for_finding("a.py:1: [] some guard", root=d), "")
            self.assertEqual(core_heal._surgical_edit_for_finding("a.py:1: no rule here", root=d), "")
            self.assertEqual(core_heal._surgical_edit_for_finding(f"{outside}:3: [R5.4] bare", root=d), "")
            self.assertNotEqual(core_heal._surgical_edit_for_finding("a.py:1: [R5.4] bare", root=d), "")

    def test_a_short_packet_is_cut_between_blocks_never_inside_one(self):
        for limit in (700, 900, 1100, 1300, 1600):
            packet = self.packet(max_chars=limit)
            self.assertLessEqual(len(packet), limit, limit)
            self.assertTrue(packet.endswith("</brevio_heal_packet>"), packet)
            self.assertEqual(packet.count("<<<<<<< SEARCH"), packet.count(">>>>>>> REPLACE"), packet)
            self.assertNotIn("\n...\n", packet)

    def test_a_long_list_of_findings_is_shortened_by_whole_lines(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
                fh.write("import requests\n" + "requests.get(u)\n" * 80)
            packet = core_heal.run_heal(root=d, apply_fixes=False, compile_guards=False, max_chars=1500)["heal_packet"]
        self.assertLessEqual(len(packet), 1500)
        self.assertIn("more: run `brevio-core scan`", packet)
        self.assertTrue(packet.endswith("</brevio_heal_packet>"))


class TestHealSaysWhatItWrote(unittest.TestCase):
    """D2: heal keeps its 0.9.1 behaviour of fixing files, and now says which lines it rewrote."""

    def test_the_text_names_each_rewritten_line_before_the_packet(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
                fh.write("import requests\ntry:\n    pass\nexcept:\n    u = None\nrequests.get(u)\nKEY = \"" + "sk_live_" + "abcdefghijklmnopqrstuvwx12345678\"\n")
            res = core_heal.run_heal(root=d)
            with open(os.path.join(d, "a.py"), encoding="utf-8") as fh:
                after = fh.read()
        self.assertIn("except Exception:", after)
        self.assertEqual(res["auto_fixed"], ["a.py:4 R5.4", "a.py:6 R10.1"])
        text = core_heal.format_heal_text(res)
        self.assertTrue(text.startswith("Applied 2 deterministic fix(es) in 1 file(s): a.py:4 R5.4; a.py:6 R10.1\n"), text)
        self.assertIn("Fixes already applied (2):", res["heal_packet"])

    def test_a_bare_except_that_only_passes_is_not_rewritten_by_heal_and_stays_reported(self):
        """``except Exception:`` + ``pass`` is still a finding of R5.4, so neither the fix nor the edit is offered (2026-11-13)."""
        text = "try:\n    run()\nexcept:\n    pass\n"
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
                fh.write(text)
            res = core_heal.run_heal(root=d, compile_guards=False)
            with open(os.path.join(d, "a.py"), encoding="utf-8") as fh:
                after = fh.read()
            only = core_heal.run_heal(root=d, apply_fixes=False, compile_guards=False)
        self.assertEqual(after, text)
        self.assertEqual(res["auto_fixed"], [])
        self.assertIn("a.py:3: [R5.4]", res["heal_packet"])
        self.assertFalse(_blocks(only["heal_packet"]))
        self.assertIn("[R5.4]", only["heal_packet"])

    def test_the_help_says_that_files_are_rewritten(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            core_heal.main(["--help"])
        flat = " ".join(out.getvalue().split())
        self.assertIn("rewrites files", flat)
        self.assertIn("only report", flat)


if __name__ == "__main__":
    unittest.main()
