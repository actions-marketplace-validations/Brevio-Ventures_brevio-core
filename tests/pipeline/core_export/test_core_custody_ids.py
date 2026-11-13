"""The test-custody findings have rule IDs of their own, and R6.5 keeps the rule it names (2026-11-12, improvement plan 3a).

Before this, `brevio-core check` printed a deleted test, fewer assertions, a skip marker and a tautology as R6.5, the
regression-test rule, and an empty test as R6.1, the rule about tests that import nothing under test. Standard library
only: the exporter copies this file beside the code and it runs unchanged there.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from brevio.pipeline import core_baseline, core_cli
from brevio.pipeline.code_rules import PR_RULES_122, PREVIEW, RULE_NOTES, STABLE, docs, rule_status, stability
from brevio.pipeline.gates import core_gates

CUSTODY = ("R20.1", "R20.2", "R20.3", "R20.4", "R20.5")
TOP = os.path.abspath(os.path.join(os.path.dirname(core_cli.__file__), "..", ".."))      # the folder that holds ``brevio``
CLI = "import sys; sys.path.insert(0, {top!r}); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[1:]))"
OLD = "def test_a():\n    assert f() == 1\n    assert g() == 2\n"


def git(cwd: str, *args: str) -> None:
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
                   check=True, capture_output=True, timeout=30)


def write(root: str, rel: str, text: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def repo_with_a_tampered_test(d: str, new_a: str) -> None:
    git(d, "init", "-q")
    write(d, "tests/test_a.py", OLD)
    write(d, "tests/test_b.py", "def test_b():\n    assert f() == 1\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    os.remove(os.path.join(d, "tests", "test_b.py"))
    write(d, "tests/test_a.py", new_a)


def ids_of(findings: list[str]) -> set[str]:
    return {str(core_cli.finding_fields(f)["rule"]) for f in findings if core_cli.finding_fields(f)}


class TestEachCustodyFindingPrintsItsOwnId(unittest.TestCase):
    NEW_A = "\n".join(["import pytest", "@pytest.mark.skip", "def test_a():", "    assert True", "", "def test_empty():",
                       "    pass", ""])

    def test_a_deleted_test_fewer_assertions_a_skip_a_tautology_and_an_empty_test_each_get_their_own_id(self):
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, self.NEW_A)
            found = core_gates.run_check(d, "HEAD")["findings"]
        self.assertEqual(ids_of(found) & set(CUSTODY), set(CUSTODY), found)
        self.assertEqual([f for f in found if "[R6.5]" in f], [], "R6.5 names the regression-test rule, not these")

    def test_the_empty_test_block_is_no_longer_reported_as_the_test_imports_rule(self):
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, self.NEW_A)
            found = core_gates.run_check(d, "HEAD")["findings"]
        empty = [f for f in found if "Empty test block" in f]
        self.assertEqual(len(empty), 1, found)
        self.assertIn("[R20.5]", empty[0])
        self.assertTrue(all("[R6.1]" in f for f in found if "imports no module under test" in f))

    def test_the_receipt_names_the_group_of_the_custody_ids(self):
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, self.NEW_A)
            text = core_gates.render_receipt(core_gates.run_check(d, "HEAD"))
        self.assertIn("R20 Test custody", text)
        self.assertNotIn("R6.5", text)

    def test_an_ignore_comment_silences_an_empty_test_block_and_not_the_other_custody_findings(self):
        new_a = "\n".join(["import pytest", "# brevio-core: ignore R20.3 -- reason", "@pytest.mark.skip", "def test_a():",
                           "    assert True", "", "# brevio-core: ignore R20.5 -- reason", "def test_empty():", "    pass", ""])
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, new_a)
            res = subprocess.run([sys.executable, "-I", "-c", CLI.format(top=TOP), "check", "--json"], cwd=d,
                                 capture_output=True, encoding="utf-8", timeout=60)      # an ignore comment is read from the working folder
        found = json.loads(res.stdout)["findings"]
        self.assertNotIn("R20.5", ids_of(found))
        self.assertTrue({"R20.1", "R20.2", "R20.3", "R20.4"} <= ids_of(found), found)


class TestWhatSilencesACustodyFinding(unittest.TestCase):
    """The rule pages say what turns a custody rule off; these run what they say (review of the precision stage, 2026-11-12)."""

    NEW_A = "\n".join(["import pytest", "@pytest.mark.skip", "def test_a():", "    assert True", "", "def test_empty():",
                       "    pass", ""])

    def run_cli(self, d: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", "-c", CLI.format(top=TOP), *args], cwd=d, input=stdin,
                              capture_output=True, encoding="utf-8", timeout=60)

    def test_an_ignore_r6_1_comment_written_for_0_9_1_still_silences_an_empty_test_and_scan_says_to_rename_it(self):
        """0.9.1 printed an empty test as R6.1, so ``ignore R6.1`` comments exist; R20.5 reads them for one release (D34)."""
        new_a = "\n".join(["def test_a():", "    assert f() == 1", "", "# brevio-core: ignore R6.1 -- stub", "def test_empty():",
                           "    pass", ""])
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, new_a)
            checked = self.run_cli(d, "check", "--json")
        found = json.loads(checked.stdout)["findings"]
        self.assertNotIn("R20.5", ids_of(found), found)
        self.assertIn("tests/test_a.py:5: the ignore comment names the old ID of R20.5; rename it (R6.1 is now R20.5", checked.stderr)

    def test_an_ignore_r6_1_comment_for_a_real_r6_1_finding_gets_no_rename_notice(self):
        """Review of D34 (2026-11-13): R6.1 is still a rule, so a comment for it must not be told to rename itself."""
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, "\n".join(["def test_a():", "    assert f() == 1", ""]))
            write_text = os.path.join(d, "tests", "test_a.py")
            with open(write_text, "a", encoding="utf-8", newline="") as fh:
                fh.write("# brevio-core: ignore R6.1 -- kept\ndef test_b():\n    assert f() == 2\n")
            scanned = self.run_cli(d, "check", "--json")
        self.assertNotIn("old ID", scanned.stderr)
        self.assertNotIn("one release", scanned.stderr)

    def test_an_ignore_comment_for_r20_5_needs_no_notice(self):
        new_a = "\n".join(["def test_a():", "    assert f() == 1", "", "# brevio-core: ignore R20.5 -- stub", "def test_empty():",
                           "    pass", ""])
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, new_a)
            scanned = self.run_cli(d, "check", "--json")
        self.assertNotIn("old ID", scanned.stderr)

    def test_a_setting_in_the_config_file_turns_off_none_of_the_custody_rules_in_check(self):
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, self.NEW_A)
            write(d, "brevio-core.toml", 'ignore = ["R20"]\nexclude = ["tests/**"]\n')
            found = json.loads(self.run_cli(d, "check", "--json").stdout)["findings"]
        self.assertEqual(ids_of(found) & set(CUSTODY), set(CUSTODY), found)
        for rid in CUSTODY:
            self.assertIn("brevio-core.toml", docs.silence_text(rid), rid)

    def test_the_hook_does_not_say_an_ignore_comment_counts_in_ci_for_a_rule_that_check_never_lets_it_silence(self):
        new_a = "def test_a():\n    assert f() == 1\n    assert True  # brevio-core: ignore R20.4 -- reason\n"
        with tempfile.TemporaryDirectory() as d:
            repo_with_a_tampered_test(d, new_a)
            write(d, "src/app.ts", "const y = (z as any) // brevio-core: ignore R9.1 -- reason\n")
            git(d, "add", "-N", "src/app.ts")
            out = self.run_cli(d, "hook", "run", "--event", "stop", stdin=json.dumps({"cwd": d})).stdout
        lines = json.loads(out)["reason"].split("\n\nSelf-heal")[0].splitlines()
        tautology = [ln for ln in lines if "[R20.4]" in ln]
        control = [ln for ln in lines if "[R9.1]" in ln]
        self.assertEqual(len(tautology), 1, lines)
        self.assertNotIn("counts in CI only", tautology[0])
        self.assertEqual(len(control), 1, lines)
        self.assertIn("counts in CI only", control[0], "the note stays on a rule whose comment CI reads")


class TestAPathWithASpaceOrALetterOutsideAscii(unittest.TestCase):
    """git quotes such a path in its listings and ends it with a tab in a diff header; the checks read it all the same
    (review of the precision stage, 2026-11-12: a deleted test and a planted ignore went unseen in such a file)."""

    TEST = "tests/t\u00e9st dir/test_caf\u00e9.py"

    def repo(self, d: str) -> None:
        git(d, "init", "-q")
        write(d, self.TEST, OLD)
        write(d, "src dir/plain file.ts", "const x = 1;\n")
        write(d, "src/caf\u00e9.ts", "const y = 1;\n")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "init")

    def test_a_deleted_test_file_and_one_with_fewer_assertions_are_reported_under_a_non_ascii_name(self):
        with tempfile.TemporaryDirectory() as d:
            self.repo(d)
            write(d, self.TEST, "def test_a():\n    assert f() == 1\n")
            self.assertTrue(any("[R20.2]" in f and "caf\u00e9" in f for f in core_gates.check_test_custody(d, "HEAD")))
            os.remove(os.path.join(d, self.TEST))
            found = core_gates.check_test_custody(d, "HEAD")
        self.assertEqual([f.split(":")[0] for f in found if "[R20.1]" in f], [self.TEST], found)

    def test_a_diff_scan_reads_the_added_lines_of_a_file_whose_name_has_a_space_or_a_non_ascii_letter(self):
        planted = "// @ts-ignore\nconst n: number = 'x';\n"
        with tempfile.TemporaryDirectory() as d:
            self.repo(d)
            write(d, "src dir/plain file.ts", planted)
            write(d, "src/caf\u00e9.ts", planted)
            write(d, "src/new caf\u00e9.ts", planted)         # a file git does not know yet
            res = subprocess.run([sys.executable, "-I", "-c", CLI.format(top=TOP), "scan", "--diff", "HEAD"], cwd=d,
                                 capture_output=True, encoding="utf-8", timeout=60)
        hits = sorted(ln.split(":")[0] for ln in res.stdout.splitlines() if "[R9.2]" in ln)
        self.assertEqual(hits, ["src dir/plain file.ts", "src/caf\u00e9.ts", "src/new caf\u00e9.ts"], res.stdout + res.stderr)


class TestTheIdsAreRegisteredWhereRuleIdsAre(unittest.TestCase):
    def test_each_id_is_in_the_catalogue_as_a_preview_rule_and_r6_5_keeps_its_meaning(self):
        for rid in CUSTODY:
            with self.subTest(rule=rid):
                self.assertIn(rid, PR_RULES_122)
                self.assertEqual(rule_status(rid), PREVIEW)
        self.assertTrue(PR_RULES_122["R6.5"].startswith("Post-incident regression crystallization"))
        self.assertEqual(rule_status("R6.5"), STABLE)

    def test_the_recorded_ids_do_not_hold_them_until_their_release_and_the_notes_list_them_as_added_preview(self):
        """Changed on purpose (2026-11-13, B2): the ID file held them before the release that carries them, so the release notes
        found nothing added. The file holds what the last release held; the policy accepts the new rules as preview, and the
        notes list each under Added with its status."""
        recorded = stability.load()
        self.assertEqual([r for r in CUSTODY if r in recorded], [])
        self.assertEqual(stability.problems(recorded, stability.current()), [])
        added = stability.notes(recorded, stability.current()).split("Added:\n\n")[1].split("\n\n")[0]
        for rid in CUSTODY:
            self.assertRegex(added, rf"- {rid.replace('.', chr(92) + '.')} .*\(preview\)")

    def test_each_id_has_a_page_that_says_how_it_is_checked_and_that_it_is_not_measured(self):
        for rid in CUSTODY:
            with self.subTest(rule=rid):
                page = docs.render(rid)
                self.assertIn("Status: preview.", page)
                self.assertIn("`brevio-core check`", page.split("## How it is checked")[1].split("## ")[0])
                self.assertNotIn("Nothing yet", page)
                self.assertIn("Not measured", page)
                self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(docs.__file__), "..", "..", "..", "docs", "rules",
                                                            docs.filename(rid))))
                self.assertIn(rid, RULE_NOTES)

    def test_the_old_pages_point_to_the_new_ids_and_the_release_notes_name_the_builds_that_printed_the_old_ones(self):
        """D34: the R6.5 and R6.1 pages say the findings now print as R20.x, an ignore of R6.1 still reads for R20.5, and the
        notes say builds up to 0.9.1 (the release before this one) printed them under the old IDs."""
        for old, new in (("R6.5", "R20.1"), ("R6.1", "R20.5")):
            page = docs.render(old)
            self.assertIn("Test-custody findings now print as R20.x.", page, old)
            self.assertIn(f"[{new}]({new}.md)", page, old)
            self.assertIn("up to 0.9.1", page, old)
        for rid in CUSTODY:
            self.assertIn("Builds up to 0.9.1 printed them as", RULE_NOTES[rid], rid)
            self.assertNotIn("before 0.6.0", RULE_NOTES[rid], rid)
        self.assertEqual(core_baseline.ALIASES["R20.5"], "R6.1")
        self.assertEqual(core_baseline.ignore_reason("R20.5", "x  # brevio-core: ignore R6.1 -- fixture"),
                         "fixture (R6.1 is now R20.5; the old ID is read for one release)")

    def test_the_rule_index_marks_the_rules_that_check_runs_and_counts_them(self):
        index = docs.render_index()
        for rid in CUSTODY:
            self.assertIn(f"- [{rid}]({rid}.md)", index)
            self.assertRegex(index, rf"\[{rid}\]\({rid}\.md\) [^\n]*\(brevio-core check\) \(preview\)")
        self.assertIn(f"{len(CUSTODY)} more are checked by `brevio-core check`", index)

    def test_explain_prints_the_page_of_a_custody_rule(self):
        res = subprocess.run([sys.executable, "-I", "-c", CLI.format(top=TOP), "explain", "r20.3"],
                             capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("# R20.3: No test skip or focus marker added", res.stdout)

    def test_sarif_lists_the_ids_with_a_warning_level_for_a_preview_rule(self):
        log = core_cli.sarif([])
        rules = {r["id"]: r for r in log["runs"][0]["tool"]["driver"]["rules"]}
        for rid in CUSTODY:
            self.assertEqual(rules[rid]["defaultConfiguration"]["level"], "warning", rid)
        self.assertEqual(rules["R6.5"]["defaultConfiguration"]["level"], "error")
        self.assertEqual(json.loads(json.dumps(log))["version"], "2.1.0")


if __name__ == "__main__":
    unittest.main()
