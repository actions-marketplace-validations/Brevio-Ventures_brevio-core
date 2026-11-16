"""Tests for brevio.pipeline.core_score and its CLI/operator integrations.

Rewritten 2026-11-12 (improvement plan, first five minutes): the earlier tests pinned a 0-100 score, a letter grade, a
"roast" verdict, a README badge and a signed receipt link. ``score`` and ``roast`` are now previews that print the findings
counted by rule and what to run next, so those tests now say what the output must not hold."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

from brevio.operator import gain_cmd
from brevio.pipeline import core_cli, core_init, core_score

GITHUB_HTTPS = "https://" + "github.com/"
SWALLOW = "def swallow():\n    try:\n        risky()\n    except:\n        pass\n"
CLEAN = "def add(a: int, b: int) -> int:\n    return a + b\n"
# What a visitor must not read in the output of score or roast: a grade, a number out of a hundred, a verdict, a badge, a link.
FORBIDDEN = re.compile(r"Grade|\bA\+|/100|roast|verdict|badge|receipt|https?://|agent-slop", re.I)


def write(d: str, name: str, text: str) -> str:
    path = os.path.join(d, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = core_cli.main(list(argv))
    return rc, out.getvalue()


class TestCoreScore(unittest.TestCase):
    def test_the_grade_the_badge_the_receipt_and_the_slug_helpers_are_gone(self) -> None:
        for name in ("grade_and_color", "build_roast", "detect_repo_slug", "_build_receipt_url"):
            self.assertFalse(hasattr(core_score, name), name)

    def test_a_clean_file_prints_a_count_of_zero_and_says_it_is_not_a_review(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            clean = write(d, "clean.py", CLEAN)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = core_score.main([clean])
            text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("brevio-core score (preview): 0 finding(s) in 1 file(s)", text)
        self.assertIn("not a review of the change", text)
        self.assertIsNone(FORBIDDEN.search(text.replace(core_cli.upgrade_line("score", True), "")), text)
        self.assertIn(core_cli.upgrade_line("score", True), text)

    def test_a_finding_is_counted_by_rule_and_the_next_step_names_the_explain_command(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = write(d, "bad.py", SWALLOW)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = core_score.main([bad, "--no-upgrade-line"])
            text = out.getvalue()
        self.assertEqual(rc, 0, "score reports; scan and qa are the commands that fail")
        self.assertIn("1 finding(s) in 1 file(s): 1 error-level, 0 warn-level", text)
        self.assertIn("[R5.4] 1 finding(s): No silent exception swallowing", text)
        self.assertIn("Next: `brevio-core explain R5.4`", text)
        self.assertIsNone(FORBIDDEN.search(text), text)

    def test_a_run_that_read_no_file_does_not_say_nothing_is_to_fix(self) -> None:
        """A mistyped ``--diff`` base reads no line; "nothing to fix" would read as a pass (review of the first-five stage)."""
        card = {"files": 0, "triggered_rules": []}
        self.assertEqual(core_score.next_step(card), "Next: no file was read, so nothing was counted; check the path or the --diff base.")
        self.assertIn("nothing to fix", core_score.next_step({"files": 1, "triggered_rules": []}))

    def test_a_guard_hit_is_named_as_a_learned_guard_and_not_by_repeating_its_id(self) -> None:
        rows = core_score._triggered_rules(["src/a.py:1: [guard:guard-x] trial guard \"x\" (reported only): the line holds `y`"])
        self.assertEqual([r["rule"] for r in rows], ["guard:guard-x"])
        self.assertIn("learned guard", rows[0]["title"])
        self.assertNotEqual(rows[0]["title"], rows[0]["rule"])

    def test_the_json_holds_the_counts_and_no_score_grade_badge_or_link(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = write(d, "bad.py", SWALLOW)
            rc, text = run("score", bad, "--json")
        payload = json.loads(text)
        self.assertEqual(rc, 0)
        self.assertEqual((payload["files"], payload["errors"], payload["warnings"], payload["finding_count"]), (1, 1, 0, 1))
        self.assertEqual(payload["triggered_rules"], [{"rule": "R5.4", "count": 1, "title": core_cli.PR_RULES_122["R5.4"]}])
        self.assertEqual(sorted(set(payload) & {"score", "grade", "color", "roast", "badge_markdown", "receipt_url",
                                                 "playground_links", "rules_encyclopedia_url"}), [])

    def test_the_markdown_form_is_a_table_of_rules_with_no_link(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = write(d, "bad.py", SWALLOW)
            rc, text = run("score", bad, "--format", "markdown", "--no-upgrade-line")
        self.assertEqual(rc, 0)
        self.assertIn("### brevio-core score (preview): 1 finding(s)", text)
        self.assertIn("| `R5.4` | 1 | ", text)
        self.assertIsNone(FORBIDDEN.search(text), text)

    def test_the_flags_of_the_grade_no_longer_fail_the_parser_but_a_made_up_flag_still_does(self) -> None:
        """Changed on purpose (D32, I38): these three flags used to exit 2 as unrecognised; they are accepted with a notice."""
        with tempfile.TemporaryDirectory() as d:
            clean = write(d, "clean.py", CLEAN)
            for flag in (["--badge"], ["--roast"], ["--min", "95"]):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(core_score.main([clean, *flag]), 0, flag)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                core_score.main([clean, "--grade"])
            self.assertEqual(cm.exception.code, 2)

    @unittest.skipUnless(core_cli.roast_command()[0], "a trimmed release leaves roast out")
    def test_roast_is_the_same_preview_under_its_old_name_and_prints_no_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = write(d, "bad.py", SWALLOW)
            _rc, roast = run("roast", bad, "--no-upgrade-line")
            _rc, score = run("score", bad, "--no-upgrade-line")
        self.assertEqual(roast, score)
        self.assertNotIn("Roast", roast)
        self.assertIn("(preview)", roast)

    def test_gain_prints_no_badge_no_receipt_and_ends_with_the_teams_line(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            old_cwd = os.getcwd()
            try:
                os.chdir(d)
                out_s = io.StringIO()
                with contextlib.redirect_stdout(out_s):
                    rc = gain_cmd.main([])
                out = out_s.getvalue()
            finally:
                os.chdir(old_cwd)
        self.assertEqual(rc, 0)
        self.assertNotIn("Badge", out)
        self.assertNotIn("receipt.svg", out)
        self.assertTrue(out.strip().endswith(core_cli.upgrade_line("gain", True)))

    def test_the_init_report_points_to_the_preview_count_and_not_to_a_grade(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            rel = core_init.Release(repo_url=GITHUB_HTTPS + "example-org/brevio-core", version="0.1.0")
            ctx = core_init.Ctx(d, rel, {})
            out_s = io.StringIO()
            with contextlib.redirect_stdout(out_s):
                core_init._report(ctx, [])
            out = out_s.getvalue()
        self.assertIn("Run `brevio-core score` (preview) for a count of findings by rule.", out)
        self.assertNotIn("A+ to F", out)

    def test_a_folder_scan_skips_nested_checkouts_and_vendored_and_virtualenv_folders(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            write(d, "app.py", CLEAN)
            write(d, os.path.join("src", "mod.py"), CLEAN)
            write(d, os.path.join(".claude", "worktrees", "w1", ".git"), "gitdir: elsewhere\n")   # a worktree holds a .git file
            write(d, os.path.join(".claude", "worktrees", "w1", "copy.py"), CLEAN)
            write(d, os.path.join("clone", ".git", "HEAD"), "ref: refs/heads/main\n")             # a clone holds a .git folder
            write(d, os.path.join("clone", "copy.py"), CLEAN)
            write(d, os.path.join("vendor", "lib.py"), CLEAN)
            write(d, os.path.join(".venv", "lib", "site.py"), CLEAN)
            write(d, os.path.join("node_modules", "pkg", "index.js"), "module.exports = 1;\n")
            visited = sorted(os.path.relpath(p, d).replace(os.sep, "/") for p in core_cli.collect_files([d]))
        self.assertEqual(visited, ["app.py", "src/mod.py"])

    def test_the_scorecard_counts_the_root_it_is_given_and_not_the_current_folder(self) -> None:
        with tempfile.TemporaryDirectory() as here, tempfile.TemporaryDirectory() as there:
            write(here, "noisy.py", SWALLOW)
            write(there, "quiet.py", CLEAN)
            old_cwd = os.getcwd()
            os.chdir(here)
            try:
                card = core_score.compute_scorecard(root=there)
            finally:
                os.chdir(old_cwd)
        self.assertEqual(card["files"], 1)
        self.assertEqual(card["finding_count"], 0)


class TestFlagsOfEarlierBuilds(unittest.TestCase):
    """D32 and I38 (2026-11-13): --min, --badge and --roast of 0.9.1 are accepted, say so on stderr, and bring back no score."""

    def go(self, *argv: str, name: str = "bad.py", text: str = SWALLOW) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            path = write(d, name, text)
            rc = core_score.main([path, "--no-upgrade-line", *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_min_stops_the_step_while_an_error_level_finding_is_counted(self) -> None:
        commands = [core_score.main, lambda a: core_cli.main(["score", *a])]
        if core_cli.roast_command()[0]:     # a trimmed release leaves roast out
            commands.append(lambda a: core_cli.main(["roast", *a]))
        for command in commands:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                rc = command([write(d, "bad.py", SWALLOW), "--min", "80"])
            self.assertEqual(rc, 1)
            self.assertIn("--min no longer applies", err.getvalue())
            self.assertIn("scan --fail-on new", err.getvalue())

    def test_min_on_a_clean_file_exits_0_and_still_says_it_no_longer_applies(self) -> None:
        rc, out, err = self.go("--min", "95", name="clean.py", text=CLEAN)
        self.assertEqual(rc, 0)
        self.assertIn("--min no longer applies", err)
        self.assertIn("0 finding(s)", out)

    def test_badge_and_roast_continue_with_a_notice_and_print_no_badge_or_score(self) -> None:
        rc, out, err = self.go("--badge", "--roast")
        self.assertEqual(rc, 0)
        self.assertIn("--badge, --roast no longer apply", err)
        self.assertIsNone(FORBIDDEN.search(out), out)

    def test_json_stays_pure_json_on_standard_output(self) -> None:
        rc, out, err = self.go("--json", "--min", "80")
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["errors"], 1)
        self.assertIn("--min", err)

    def test_without_the_old_flags_nothing_is_said(self) -> None:
        rc, _out, err = self.go()
        self.assertEqual((rc, err), (0, ""))

    def test_the_old_flags_are_not_in_the_help(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            core_score.main(["--help"])
        self.assertNotIn("--min", out.getvalue())
        self.assertNotIn("--badge", out.getvalue())


if __name__ == "__main__":
    unittest.main()
