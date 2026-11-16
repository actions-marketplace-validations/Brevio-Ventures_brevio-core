"""What a careless or hostile input does to the learned guards: found by the review of the learn stage (2026-11-12,
improvement plan). Each test names a case the first version of the stage got wrong.

Standard library only: the exporter copies this file beside the code (the 0.2 set) and it runs unchanged there.
"""
import hashlib
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import tests.pipeline.core_export.test_core_guard_fixtures as fx
from brevio.pipeline import core_guards
from brevio.pipeline.gates import defect_compiler, guard_packs, guard_proof, guard_scan

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

KEY = guard_scan.EXCLUDE_KEY
RULE = f"guard:{fx.GUARD_ID}"


def with_exclude(d, *globs, watch="**/*.py"):
    fx.edit_guard(d, fx.GUARD_ID, file_patterns=[watch], **{KEY: list(globs)})


class TestTheExcludeListOfAGuard(unittest.TestCase):
    """A guard may list globs it does not watch (2026-11-13). The key is ``exclude_file_patterns``; a guard file without it
    behaves as before."""

    def test_a_scan_skips_the_excluded_folder_and_still_reports_the_rest(self):
        with fx.learned(TREE) as d:
            with_exclude(d, "src/legacy/**")
            fx.write(d, "src/other.py", fx.FIXED)
            fx.write(d, "src/legacy/old.py", fx.FIXED)
            out = TREE.run(d, "scan", "src/other.py", "src/legacy/old.py").stdout
            self.assertIn("src/other.py:1:", out)
            self.assertNotIn("src/legacy", out)
            whole = TREE.run(d, "scan").stdout
            self.assertIn("src/other.py:1:", whole)
            self.assertNotIn("src/legacy", whole)

    def test_a_guard_file_without_the_key_watches_the_same_paths_as_before(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, file_patterns=["**/*.py"])
            fx.write(d, "src/legacy/old.py", fx.FIXED)
            self.assertIn("src/legacy/old.py:1:", TREE.run(d, "scan", "src/legacy/old.py").stdout)

    def test_a_diff_scan_and_the_learn_check_skip_it_too(self):
        with fx.learned(TREE) as d:
            with_exclude(d, "src/legacy/**")
            fx.edit_guard(d, fx.GUARD_ID, status="active")      # the hook shows an active guard's hit
            fx.git(d, "add", "-A")
            fx.git(d, "commit", "-q", "-m", "exclude")
            fx.write(d, "src/legacy/old.py", fx.FIXED)
            fx.write(d, "src/other.py", fx.FIXED)
            diff = TREE.run(d, "scan", "--diff", "HEAD").stdout
            self.assertIn("src/other.py:1:", diff)
            self.assertNotIn("src/legacy", diff)
            self.assertEqual(TREE.run(d, "learn", "--check", "src/legacy/old.py").returncode, 0)
            self.assertEqual(TREE.run(d, "learn", "--check", "src/other.py").returncode, 1)

    def test_the_glob_dialect_is_the_one_of_the_watched_globs_and_does_not_depend_on_the_system(self):
        guard = {"file_patterns": ["**/*.py"], KEY: ["**/tests/**", "docs/*.py", "Vendor/**"]}
        self.assertFalse(guard_scan.watches(guard, "src/tests/test_a.py"))
        self.assertFalse(guard_scan.watches(guard, "tests/test_a.py"))        # ``**/`` also means "at the root"
        self.assertFalse(guard_scan.watches(guard, "docs/deep/a.py"))         # ``*`` crosses ``/``, as in a watched glob
        self.assertFalse(guard_scan.watches(guard, "src\\tests\\test_a.py"))   # a path written with backslashes
        self.assertTrue(guard_scan.watches(guard, "src/app.py"))
        self.assertTrue(guard_scan.watches(guard, "vendor/a.py"))            # case counts on every system

    def test_adding_an_exclude_to_a_live_guard_is_a_change_a_person_reviews(self):
        base = {"guards": {"g": {"status": "active", "patterns": ["xx"], "file_patterns": ["**/*.py"]}}}
        now = {"guards": {"g": {**base["guards"]["g"], KEY: ["src/**"]}}}
        out = core_guards.weakened(json.dumps(base), json.dumps(now))
        self.assertEqual([why for _n, why in out], [" (guard g: a path is now excluded)"])
        self.assertEqual(core_guards.weakened(json.dumps(now), json.dumps(now)), [])
        self.assertEqual(core_guards.weakened(json.dumps(now), json.dumps(base)), [])    # watching more is not weaker

    def test_a_guard_without_the_key_keeps_its_fingerprint_so_an_old_switch_off_record_still_matches(self):
        guard = {"patterns": ["b", "a"], "file_patterns": ["**/*.py"]}
        body = json.dumps({"patterns": ["a", "b"], "file_patterns": ["**/*.py"]}, sort_keys=True)
        self.assertEqual(defect_compiler.fingerprint(guard), hashlib.sha256(body.encode("utf-8")).hexdigest())
        self.assertNotEqual(defect_compiler.fingerprint({**guard, KEY: ["x/**"]}), defect_compiler.fingerprint(guard))

    def test_the_control_run_and_the_replay_honour_the_list(self):
        guard = {"patterns": ["compute_legacy_total"], "file_patterns": ["**/*.py"], "source_file": "src/calc.py"}
        diff = "diff --git a/src/calc.py b/src/calc.py\n--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1 +1 @@\n-result = compute_legacy_total(items)\n+result = compute_total(items)\n"
        fixed = ["result = compute_total(items)"]
        self.assertEqual(guard_proof.control_run(guard, diff, fixed), [])
        shut = guard_proof.control_run({**guard, KEY: ["src/**"]}, diff, fixed)
        self.assertEqual(shut, ["`compute_legacy_total` does not match the lines the fix removed"])
        with fx.repo_with_a_fix() as d:
            self.assertEqual(len(guard_proof.replay(guard, d, 10)["hits"]), 1)
            self.assertEqual(guard_proof.replay({**guard, KEY: ["src/**"]}, d, 10)["hits"], [])

    def test_learn_show_names_what_a_guard_does_not_watch(self):
        with fx.learned(TREE) as d:
            with_exclude(d, "src/legacy/**")
            self.assertIn("except:    src/legacy/**", TREE.run(d, "learn", "show", fx.GUARD_ID).stdout)


class TestAMalformedOrHostileExcludeList(unittest.TestCase):
    """The guard file comes with a pull request. A bad entry is named in a warning and left out, so the guard watches more,
    not less; the scan never stops and never hides a hit because of it."""

    BAD = {
        "not a list": "src/**",
        "an absolute path": ["/etc/**"],
        "a windows drive": ["C:/work/**"],
        "a home folder": ["~/code/**"],
        "a path that leaves the repository": ["../outside/**"],
        "a path that leaves it in the middle": ["src/../../x/**"],
        "a glob that is not text": [3],
        "an empty glob": [""],
        "a glob with a line break": ["src/\n**"],
        "a glob that is too long": ["a" * 201],
        "too many wildcards": ["*a" * 9],
        "too many entries": [f"d{i}/**" for i in range(21)],
    }

    def test_each_bad_list_is_named_in_a_warning_and_hides_nothing(self):
        for name, value in self.BAD.items():
            with self.subTest(name), fx.learned(TREE) as d:
                fx.edit_guard(d, fx.GUARD_ID, file_patterns=["**/*.py"], **{KEY: value})
                fx.write(d, "x/a.py", fx.FIXED)
                res = TREE.run(d, "scan", "x/a.py")
                self.assertIn("x/a.py:1:", res.stdout, res.stderr)       # a list of 21 entries is cut at 20
                self.assertEqual(res.returncode, 0)
                self.assertIn(f"guard:{fx.GUARD_ID}: ", res.stderr)

    def test_a_list_that_names_every_watched_path_is_not_used_and_is_named_in_a_warning(self):
        """Review 2026-11-14: ``**`` switched a guard off for the whole tree and said nothing."""
        for name, value in {"star": ["**"], "every file": ["**/*"], "wildcards": ["?*", "[!x]*"],
                            "in halves": ["src/**", "**/*.py"]}.items():
            with self.subTest(name), fx.learned(TREE) as d:
                fx.edit_guard(d, fx.GUARD_ID, file_patterns=["**/*.py"], **{KEY: value})
                fx.write(d, "src/a.py", fx.FIXED)
                res = TREE.run(d, "scan", "src/a.py")
                self.assertIn("src/a.py:1:", res.stdout, res.stderr)
                self.assertIn(f"guard:{fx.GUARD_ID}: ", res.stderr)

    def test_a_list_for_part_of_what_a_guard_watches_is_used_as_listed(self):
        guard = {"file_patterns": ["**/api/*.py", "**/main.py"], KEY: ["**/tests/**", "**/test_*.py", "**/conftest.py"]}
        self.assertEqual(guard_scan.exclude_problem(guard), "")
        self.assertFalse(guard_scan.watches(guard, "app/api/tests/a.py"))
        self.assertTrue(guard_scan.watches(guard, "app/api/a.py"))
        for pack in guard_packs.PACKS.values():
            for spec in pack["guards"]:
                with self.subTest(spec.get("label", spec["file_patterns"])):
                    self.assertEqual(guard_scan.exclude_problem(spec), "")
                    self.assertEqual(guard_scan.excludes_of(spec), list(spec.get(KEY, [])))

    def test_a_good_entry_next_to_a_bad_one_still_works(self):
        guard = {"file_patterns": ["**/*.py"], KEY: ["/etc/**", "src/legacy/**", 5]}
        self.assertFalse(guard_scan.watches(guard, "src/legacy/a.py"))
        self.assertTrue(guard_scan.watches(guard, "src/a.py"))
        self.assertIn("absolute", guard_scan.exclude_problem(guard))

    def test_a_glob_written_with_backslashes_names_the_same_files_on_every_system(self):
        guard = {"file_patterns": ["**/*.py"], KEY: ["src\\legacy\\**"]}
        self.assertFalse(guard_scan.watches(guard, "src/legacy/a.py"))
        self.assertFalse(guard_scan.watches(guard, "src\\legacy\\a.py"))
        self.assertTrue(guard_scan.watches(guard, "src/new/a.py"))

    def test_the_warning_names_the_bad_glob_and_never_prints_raw_control_text(self):
        named = guard_scan.exclude_problem({KEY: ["ok/*", "/etc/**"]})
        self.assertIn("`/etc/**`", named)
        hostile = guard_scan.exclude_problem({KEY: ["\x1b[2Jx\n"]})
        self.assertNotIn("\x1b", hostile)
        self.assertTrue(hostile.isprintable(), hostile)

    def test_the_exclude_cannot_name_a_path_outside_the_repository_and_matching_stays_fast(self):
        guard = {"file_patterns": ["**/*.py"], KEY: ["*a" * 8 + "b", "[a-z]*" * 8 + "?"]}
        path = "a" * 4000 + ".py"
        started = time.perf_counter()
        for _ in range(200):
            guard_scan.watches(guard, path)
        self.assertLess(time.perf_counter() - started, 5.0)
        slow = {"file_patterns": ["**/*.py"], KEY: ["*a" * 20 + "b"]}      # more wildcards than allowed: not matched at all
        self.assertEqual(guard_scan.excludes_of(slow), [])
        self.assertTrue(guard_scan.watches(slow, path))


if __name__ == "__main__":
    unittest.main()
