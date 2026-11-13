"""What a careless or hostile input does to the learned guards: found by the review of the learn stage (2026-11-12,
improvement plan). Each test names a case the first version of the stage got wrong.

Standard library only: the exporter copies this file beside the code (the 0.2 set) and it runs unchanged there.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import tests.pipeline.core_export.test_core_guard_fixtures as fx
from brevio.pipeline.gates import guard_packs, guard_proof, guard_scan

TREE = fx.Tree("0.2")


def setUpModule():
    TREE.open()


def tearDownModule():
    TREE.close()


def terminal(d, *args):
    return TREE.run(d, *args, code=fx.AT_A_TERMINAL)


class TestARegularExpressionInAGuardFile(unittest.TestCase):
    """A guard file arrives with a pull request; ``re`` has no time limit and holds the interpreter while it matches."""

    def test_the_patterns_of_the_starter_packs_are_still_run_as_regular_expressions(self):
        for name, pack in guard_packs.PACKS.items():
            for guard in pack["guards"]:
                for pattern in guard["patterns"]:
                    with self.subTest(pack=name, pattern=pattern):
                        self.assertEqual(guard_scan.regex_problem(pattern), "")

    def test_the_shapes_that_take_exponential_time_are_not_run_as_regular_expressions(self):
        for pattern in ("re:(a+)+$", "re:(a|aa)+b", "re:((x*)y)*z", r"re:(\w+\s?)*$", "re:(a{2,})+b", "re:.*.*.*.*x"):
            with self.subTest(pattern):
                self.assertNotEqual(guard_scan.regex_problem(pattern), "")
        self.assertEqual(guard_scan.regex_problem("re:foo\\(\\d+\\)"), "")
        self.assertEqual(guard_scan.regex_problem("re:[(+*]+x"), "")

    def test_a_scan_with_a_catastrophic_pattern_returns_with_a_warning_and_still_reports_the_literal_guards(self):
        with fx.learned(TREE) as d:
            doc = fx.read_json(d, ".brevio/guards.json")
            doc["guards"]["guard-evil"] = {"id": "guard-evil", "title": "evil", "status": "trial", "file_patterns": ["**/*.py"],
                                           "patterns": ["re:(a+)+$"]}
            fx.write(d, ".brevio/guards.json", json.dumps(doc))
            fx.write(d, "src/other.py", "a" * 40 + "b\n" + fx.FIXED)
            res = TREE.run(d, "scan", "src/other.py")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn(f"[guard:{fx.GUARD_ID}]", res.stdout)
            self.assertNotIn("guard-evil]", res.stdout)
            self.assertIn("guard-evil", res.stderr)
            self.assertIn("is not run as a regular expression", res.stderr)


class TestLearnWillNotOverwriteAGuard(unittest.TestCase):
    def test_a_second_learn_of_the_same_fix_keeps_the_promotion_the_wrong_marks_and_the_trial_date(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, status="active", false_fails=1, trial_ends="2001-01-01")
            before = fx.read(d, ".brevio/guards.json")
            res = TREE.run(d, "learn", "--diff", "HEAD~2..HEAD~1", "--name", "no legacy total")
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("already", res.stderr)
            self.assertIn("--name", res.stderr)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)

    def test_a_second_learn_does_not_bring_a_dismissed_guard_back_and_a_dry_run_says_the_same(self):
        with fx.learned(TREE) as d:
            self.assertEqual(terminal(d, "learn", "dismiss", fx.GUARD_ID).returncode, 0)
            before = fx.read(d, ".brevio/guards.json")
            for extra in ((), ("--dry-run",)):
                res = TREE.run(d, "learn", "--diff", "HEAD~2..HEAD~1", "--name", "no legacy total", *extra)
                self.assertEqual(res.returncode, 1, res.stdout)
                self.assertIn("already", res.stderr)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["status"], "off")


class TestAnActionTakesNoCompileFlag(unittest.TestCase):
    def test_dismiss_with_dry_run_is_a_usage_error_and_changes_nothing(self):
        with fx.learned(TREE) as d:
            before = fx.read(d, ".brevio/guards.json")
            for action in ("dismiss", "wrong", "promote"):
                for flag in ("--dry-run", "--staged", "--worktree"):
                    with self.subTest(action=action, flag=flag):
                        res = terminal(d, "learn", action, fx.GUARD_ID, flag)
                        self.assertEqual(res.returncode, 2, res.stdout)
                        self.assertIn("takes none of", res.stderr)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)


class TestTextFromTheGuardFileIsCleanedBeforeItReachesATerminal(unittest.TestCase):
    def test_learn_list_prints_no_control_character_of_a_guard_id_or_note(self):
        with fx.learned(TREE) as d:
            doc = fx.read_json(d, ".brevio/guards.json")
            doc["guards"]["g\x1b[31mred"] = {"title": "t", "status": "off", "status_note": "note\x1b]0;x\x07", "replay": "text"}
            fx.write(d, ".brevio/guards.json", json.dumps(doc))
            res = TREE.run(d, "learn", "list")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("\x1b", res.stdout)
            self.assertNotIn("\x07", res.stdout)
            self.assertIn(fx.GUARD_ID, res.stdout)


class TestAGuardDoesNotHitItsOwnFiles(unittest.TestCase):
    def test_a_guard_for_a_markdown_file_is_not_reported_in_its_own_rule_file_or_the_guard_file(self):
        with tempfile.TemporaryDirectory() as d:
            fx.git(d, "init", "-q", "-b", "main")
            fx.write(d, "NOTES.md", "use the old flag --legacy-mode here\n")
            fx.git(d, "add", ".")
            fx.git(d, "commit", "-q", "-m", "first")
            fx.write(d, "NOTES.md", "use the new flag --modern-mode here\n")
            fx.git(d, "commit", "-q", "-am", "fix flag")
            self.assertEqual(TREE.run(d, "learn").returncode, 0)
            fx.git(d, "add", "-A")
            res = TREE.run(d, "scan", "--diff", "HEAD")
            self.assertNotIn("[guard:", res.stdout)
            self.assertEqual(res.returncode, 0, res.stdout)
            files = [".brevio/guards.json", *(os.path.join(".agents", "rules", n) for n in os.listdir(os.path.join(d, ".agents", "rules")))]
            named = TREE.run(d, "scan", *(a for f in files for a in ("--file", f)))
            self.assertNotIn("[guard:", named.stdout)


class TestAPathWithSpacesAndNonAsciiLetters(unittest.TestCase):
    def test_a_fix_in_such_a_path_compiles_into_a_guard_that_hits_a_copy_of_the_old_line(self):
        with tempfile.TemporaryDirectory() as d:
            rel = "src dir/calç ü 日本.py"
            fx.git(d, "init", "-q", "-b", "main")
            fx.write(d, rel, fx.FIXED)
            fx.git(d, "add", ".")
            fx.git(d, "commit", "-q", "-m", "first")
            fx.write(d, rel, fx.FIXING)
            fx.git(d, "commit", "-q", "-am", "fix")
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            fx.write(d, "src dir/other ü.py", fx.FIXED)
            hit = TREE.run(d, "scan", "src dir/other ü.py")
            self.assertIn("src dir/other ü.py:1: [guard:", hit.stdout)
            self.assertEqual(fx.guards(d)[next(iter(fx.guards(d)))]["rule_file"].count("\\"), 0)


class TestPromotionLooksAtLeastAsFarBackAsTheReplayThatKeptTheGuard(unittest.TestCase):
    def test_a_narrow_replay_flag_does_not_skip_the_commit_that_brings_the_line_back(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, trial_ends="2001-01-01")
            fx.write(d, "src/again.py", fx.FIXED)
            fx.git(d, "add", "-A")
            fx.git(d, "commit", "-q", "-m", "the line is back")
            fx.write(d, "src/unrelated.py", "x = 1\n")
            fx.git(d, "add", "-A")
            fx.git(d, "commit", "-q", "-m", "something else")
            res = TREE.run(d, "learn", "promote", fx.GUARD_ID, "--replay", "1")
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("did not flag when the guard was kept", res.stderr)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["status"], "trial")


class TestTheReplayReadsAHugeHistoryOnlyUpToItsCap(unittest.TestCase):
    def test_a_commit_far_past_the_cap_is_cut_and_the_replay_says_so_and_a_normal_history_is_not(self):
        guard = {"file_patterns": ["**/*.py"], "patterns": ["compute_legacy_total(items)"]}
        with tempfile.TemporaryDirectory() as d:
            fx.git(d, "init", "-q", "-b", "main")
            fx.write(d, "a.py", fx.FIXED + "".join(f"value_{n} = {n}\n" for n in range(5000)))
            fx.git(d, "add", ".")
            fx.git(d, "commit", "-q", "-m", "a big file")
            normal = guard_proof.replay(guard, d)
            self.assertEqual((normal["stopped"], len(normal["hits"]), normal.get("failed")), (False, 1, None))
            with mock.patch.object(guard_proof, "MAX_LOG_CHARS", 300):
                cut = guard_proof.replay(guard, d)
            self.assertTrue(cut["stopped"])
            self.assertNotIn("failed", cut)
            self.assertIn("history cut short", guard_proof.describe(cut)[0])

    def test_a_replay_entry_that_is_not_a_table_is_read_as_no_stored_replay(self):
        self.assertEqual(guard_proof.unexplained({"replay": "text"}, {"hits": [{"commit": "abc", "file": "a.py"}]}), ["abc:a.py"])


class TestACredentialThatTheRuleDoesNotReadIsStillKeptOutOfAGuard(unittest.TestCase):
    """The control run read only the credential rule; a fix that removes a password literal, a URL with a password or a
    bearer token would have been written into a committed file. The values are joined at run time."""

    SHAPES = (('password = "' + "Sup3r" + 'Secret123!"', "Sup3rSecret"),
              ('DATABASE_URL = "postgres://admin:' + "hunter2pass" + '@db.example.com/app"', "hunter2pass"),
              ('headers = {"Authorization": "Bearer ' + "eyJhbGciOiJIUzI1NiJ9" + '.abcdefghijklmnop"}', "eyJhbGciOiJIUzI1NiJ9"))

    def test_the_guard_is_not_kept_and_nothing_printed_shows_the_value(self):
        for line, secret in self.SHAPES:
            with self.subTest(secret), tempfile.TemporaryDirectory() as d:
                fx.git(d, "init", "-q", "-b", "main")
                fx.write(d, "a.py", line + "\nx = 1\n")
                fx.git(d, "add", ".")
                fx.git(d, "commit", "-q", "-m", "first")
                fx.write(d, "a.py", "import os\nx = 1\n")
                fx.git(d, "commit", "-q", "-am", "remove it")
                shown = ""
                for args in ((), ("--json",), ("--dry-run",), ("--dry-run", "--json")):
                    res = TREE.run(d, "learn", *args)
                    self.assertEqual(res.returncode, 1, (args, res.stdout, res.stderr))
                    shown += res.stdout + res.stderr
                self.assertIn("looks like a credential", shown)
                self.assertNotIn(secret, shown)
                self.assertFalse(os.path.exists(os.path.join(d, ".brevio", "guards.json")))


if __name__ == "__main__":
    unittest.main()
