"""The learned guards in the scan, and the proof a guard goes through before it is kept (improvement plan, 2026-11-12).

Standard library only: the exporter copies this file beside the code (the 0.2 set) and it runs unchanged there. Every
command runs on a temporary git repository in a clean ``-I`` child interpreter.
"""
import json
import os
import subprocess
import tempfile
import unittest

import tests.pipeline.core_export.test_core_guard_fixtures as fx
from brevio.pipeline import core_guards
from brevio.pipeline.gates import defect_compiler, guard_proof

TREE = fx.Tree("0.2")


def setUpModule():
    TREE.open()


def tearDownModule():
    TREE.close()


class TestScanRunsTheGuards(unittest.TestCase):
    def test_a_guard_hit_is_a_finding_whose_rule_is_guard_and_the_id_in_scan(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", "x = 1\n" + fx.FIXED)
            res = TREE.run(d, "scan", "src/other.py")
            self.assertRegex(res.stdout, rf"^src/other\.py:2: \[guard:{fx.GUARD_ID}\] ", res.stderr)
            self.assertIn("1 finding(s)", res.stderr)

    def test_a_guard_on_trial_is_labelled_trial_and_does_not_fail_the_run(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            res = TREE.run(d, "scan", "src/other.py")
            self.assertIn("trial guard", res.stdout)
            self.assertIn("reported only", res.stdout)
            self.assertEqual(res.returncode, 0, res.stderr)

    def test_an_active_guard_is_a_warning_and_does_not_fail_the_run_unless_the_config_raises_it(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, status="active")
            fx.write(d, "src/other.py", fx.FIXED)
            res = TREE.run(d, "scan", "src/other.py")
            self.assertIn(f"[guard:{fx.GUARD_ID}] learned guard", res.stdout)
            self.assertNotIn("trial", res.stdout)
            self.assertEqual(res.returncode, 0, res.stderr)

    def test_a_scan_that_reports_a_guard_hit_changes_nothing_in_the_guard_file(self):
        with fx.learned(TREE) as d:
            before = fx.read(d, ".brevio/guards.json")
            fx.write(d, "src/other.py", fx.FIXED)
            TREE.run(d, "scan", "src/other.py")
            TREE.run(d, "scan")
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)

    def test_a_guard_that_is_off_and_a_file_it_does_not_watch_report_nothing(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            fx.write(d, "docs/notes.md", fx.FIXED)
            self.assertEqual(TREE.run(d, "scan", "docs/notes.md").stdout, "")
            fx.edit_guard(d, fx.GUARD_ID, status="off")
            self.assertEqual(TREE.run(d, "scan", "src/other.py").stdout, "")

    def test_a_diff_scan_reports_only_the_lines_the_change_adds(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            fx.git(d, "add", "-A")
            fx.git(d, "commit", "-q", "-m", "an old line that holds the text")
            fx.write(d, "src/new.py", "y = 2\n" + fx.FIXED)
            res = TREE.run(d, "scan", "--diff", "HEAD")
            self.assertIn("src/new.py:2:", res.stdout)
            self.assertNotIn("src/other.py", res.stdout)

    def test_a_guard_id_with_odd_characters_cannot_break_the_finding_line(self):
        with fx.learned(TREE) as d:
            doc = fx.read_json(d, ".brevio/guards.json")
            doc["guards"]["x] [R1.1 y\n"] = {**doc["guards"][fx.GUARD_ID], "id": "x", "title": "a\nb"}
            fx.write(d, ".brevio/guards.json", json.dumps(doc))
            fx.write(d, "src/other.py", fx.FIXED)
            lines = TREE.run(d, "scan", "src/other.py").stdout.splitlines()
            self.assertEqual(len(lines), 2, lines)
            self.assertTrue(all(ln.startswith("src/other.py:1: [guard:") for ln in lines), lines)


class TestTheControlRun(unittest.TestCase):
    def test_a_guard_that_also_matches_the_fixed_file_is_not_written_and_the_reason_is_printed(self):
        with tempfile.TemporaryDirectory() as d:
            fx.git(d, "init", "-q", "-b", "main")
            fx.write(d, "src/calc.py", fx.FIXED + "total = 0\n" + fx.FIXED)
            fx.git(d, "add", ".")
            fx.git(d, "commit", "-q", "-m", "first")
            fx.write(d, "src/calc.py", fx.FIXING + "total = 0\n" + fx.FIXED)
            fx.git(d, "commit", "-q", "-am", "fix one of two")
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 1)
            self.assertIn("fails the control run", res.stderr)
            self.assertIn("also matches 1 line(s) of the fixed file, the first at line 3", res.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, ".brevio", "guards.json")))
            self.assertFalse(os.path.exists(os.path.join(d, ".agents")))

    def test_a_pattern_that_looks_like_a_credential_is_not_kept(self):
        with tempfile.TemporaryDirectory() as d:
            fx.git(d, "init", "-q", "-b", "main")
            fx.write(d, "src/key.ts", fx.AWS_EXAMPLE_LINE)
            fx.git(d, "add", ".")
            fx.git(d, "commit", "-q", "-m", "first")
            fx.write(d, "src/key.ts", "const apiKey = process.env.API_KEY;\n")
            fx.git(d, "commit", "-q", "-am", "fix: read the key from the environment")
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 1)
            self.assertIn("looks like a credential", res.stderr)
            self.assertNotIn("AKIA", res.stderr.upper())
            dry = TREE.run(d, "learn", "--dry-run")
            self.assertNotIn("AKIA", (dry.stdout + dry.stderr).upper())
            self.assertIn("hidden", dry.stdout)
            self.assertNotIn("AKIA", TREE.run(d, "learn", "--dry-run", "--json").stdout.upper())
            self.assertFalse(os.path.exists(os.path.join(d, ".brevio")))

    def test_a_guard_that_passes_says_so_and_is_kept(self):
        with fx.repo_with_a_fix() as d:
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("Control run:  passed", res.stdout)
            self.assertEqual(list(fx.guards(d)), ["guard-fix-result-compute_legacy_total-items-in-calc-py"])

    def test_a_fix_in_a_range_is_checked_against_the_file_at_the_end_of_the_range(self):
        with fx.repo_with_a_fix() as d:
            fx.write(d, "src/calc.py", fx.FIXED)       # the working tree goes back, the committed fix is the range's end
            res = TREE.run(d, "learn", "--dry-run", "--diff", "HEAD~1..HEAD")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("control run: passed", res.stdout)

    def test_the_dry_run_of_a_failing_guard_exits_1_and_says_it_would_not_be_kept(self):
        with tempfile.TemporaryDirectory() as d:
            fx.git(d, "init", "-q", "-b", "main")
            fx.write(d, "src/calc.py", fx.FIXED + fx.FIXED)
            fx.git(d, "add", ".")
            fx.git(d, "commit", "-q", "-m", "first")
            fx.write(d, "src/calc.py", fx.FIXING + fx.FIXED)
            fx.git(d, "commit", "-q", "-am", "fix")
            res = TREE.run(d, "learn", "--dry-run")
            self.assertEqual(res.returncode, 1)
            self.assertIn("FAILED, so this guard would not be kept", res.stdout)
            self.assertIn("dry run: nothing written\n", res.stdout)


class TestTheReplay(unittest.TestCase):
    def _history(self, d):
        """The defect comes in (a.py), something else (b.py), it comes in again (c.py), and a.py is fixed."""
        fx.git(d, "init", "-q", "-b", "main")
        shas = []
        for name, text in (("a", fx.FIXED), ("b", "z = 1\n"), ("c", fx.FIXED)):
            fx.write(d, f"src/{name}.py", text)
            fx.git(d, "add", ".")
            fx.git(d, "commit", "-q", "-m", f"add {name}")
            shas.append(subprocess.run(["git", "-C", d, "rev-parse", "--short=7", "HEAD"], capture_output=True,
                                       encoding="utf-8", check=True).stdout.strip())
        fx.write(d, "src/a.py", fx.FIXING)
        fx.git(d, "commit", "-q", "-am", "fix a")
        return shas

    def test_the_dry_run_replays_recent_commits_and_lists_what_it_would_have_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            first, _second, third = self._history(d)
            res = TREE.run(d, "learn", "--dry-run")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("replay: would have flagged 2 of 4 past change(s)", res.stdout)
            self.assertIn(f"{first} src/a.py  add a", res.stdout)
            self.assertIn(f"{third} src/c.py  add c", res.stdout)
            self.assertIn("matches 1 line(s) in this repository today", res.stdout)
            self.assertFalse(os.path.exists(os.path.join(d, ".brevio")), "a dry run writes nothing")

    def test_the_number_of_commits_replayed_is_a_flag_with_a_default(self):
        with tempfile.TemporaryDirectory() as d:
            self._history(d)
            self.assertIn("flagged 0 of 1 past change(s)", TREE.run(d, "learn", "--dry-run", "--replay", "1").stdout)
            self.assertEqual(TREE.run(d, "learn", "--dry-run", "--replay", "0").returncode, 2)
            self.assertIn(f"default: {guard_proof.REPLAY_COMMITS})", TREE.run(d, "learn", "--help").stdout.replace("\n", " "))

    def test_a_replay_that_cannot_read_the_history_says_so_and_does_not_report_a_clean_one(self):
        guard = {"patterns": ["a line of text"], "file_patterns": ["*.py"]}
        with tempfile.TemporaryDirectory() as d:          # not a git repository
            result = guard_proof.replay(guard, d, 5)
        self.assertEqual((result["failed"], result["checked"], result["hits"]), (True, 0, []))
        self.assertEqual(guard_proof.describe(result), ["replay: the history could not be read, so nothing is known about past changes"])

    def test_a_kept_guard_records_what_its_replay_flagged_and_when_its_trial_ends(self):
        with tempfile.TemporaryDirectory() as d:
            first, _second, third = self._history(d)
            self.assertEqual(TREE.run(d, "learn").returncode, 0)
            (guard,) = fx.guards(d).values()
            self.assertEqual((guard["replay"]["checked"], guard["replay"]["flagged"]), (4, 2))
            self.assertEqual(sorted(k.split(":")[1] for k in guard["replay"]["accepted"]), ["src/a.py", "src/c.py"])
            self.assertRegex(guard["trial_ends"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertEqual(guard["status"], "trial")


class TestWhatLearnPrints(unittest.TestCase):
    def test_learn_ends_with_one_line_naming_the_files_to_commit(self):
        with fx.repo_with_a_fix() as d:
            res = TREE.run(d, "learn", "--name", "no legacy total")
            last = res.stdout.rstrip("\n").splitlines()[-1]
            self.assertEqual(last, f"Commit .brevio/guards.json and .agents/rules/{fx.GUARD_ID}.md so your team and their agents get the change.")
            self.assertEqual(sum(1 for ln in res.stdout.splitlines() if ln.startswith("Commit ")), 1)

    def test_a_default_title_is_cut_at_a_whole_word_and_a_short_one_is_left_alone(self):
        text = "result = gateway.submit(card, amount, token, retries=0)"
        title = defect_compiler.short_title(text)
        self.assertEqual(title, "result = gateway.submit(card, amount...")
        self.assertFalse(title[:-3].endswith(("tok", "ti", "toke")))
        self.assertEqual(defect_compiler.short_title("result = compute_total(items)"), "result = compute_total(items)")

    def test_the_guard_rule_file_does_not_promise_what_a_scan_cannot_show(self):
        with fx.repo_with_a_fix() as d:
            TREE.run(d, "learn", "--name", "no legacy total")
            text = fx.read(d, f".agents/rules/{fx.GUARD_ID}.md")
            self.assertNotRegex(text, r"(?i)\b(never|always)\b")
            self.assertIn("compute_legacy_total", text)


def history(*commits):
    """A repository built from ``(message, {path: text})`` commits, oldest first."""
    d = tempfile.TemporaryDirectory()
    fx.git(d.name, "init", "-q", "-b", "main")
    for message, files in commits:
        for path, text in files.items():
            fx.write(d.name, path, text)
        fx.git(d.name, "add", "-A")
        fx.git(d.name, "commit", "-q", "-m", message)
    return d


LOOP = "for (const id of ids) {\n  await push(id);\n}\n"
BATCH = "await pushAll(ids);\n"


class TestAGuardWatchesTheFolderOfTheFixAndNeedsItsWholeSet(unittest.TestCase):
    """2026-11-13, D45: a guard did hit correct code in another folder, and on one of its lines."""

    def learned_loop(self):
        d = history(("add the loop", {"src/api/handler.ts": LOOP}), ("push in one call", {"src/api/handler.ts": BATCH}))
        res = TREE.run(d.name, "learn", "--name", "loop push")
        self.assertEqual(res.returncode, 0, res.stderr)
        return d, next(iter(fx.guards(d.name).values()))

    def test_a_guard_from_a_fix_in_a_folder_watches_that_folder_only(self):
        d, guard = self.learned_loop()
        with d:
            self.assertEqual(guard["file_patterns"], ["src/api/**/*.ts"])
            fx.write(d.name, "src/ui/queue.ts", LOOP)
            self.assertEqual(TREE.run(d.name, "scan", "src/ui/queue.ts").stdout, "")
            fx.write(d.name, "src/api/other.ts", LOOP)
            self.assertIn("guard:", TREE.run(d.name, "scan", "src/api/other.ts").stdout)

    def test_a_fix_of_a_file_at_the_root_watches_files_of_its_kind(self):
        self.assertEqual(defect_compiler._derive_file_globs("main.py"), ["**/*.py"])
        self.assertEqual(defect_compiler._derive_file_globs("src/a/b.py"), ["src/a/**/*.py"])

    def test_patterns_from_one_hunk_are_all_needed_and_one_of_them_alone_is_no_hit(self):
        d, guard = self.learned_loop()
        with d:
            self.assertEqual(guard["match"], "all")
            self.assertEqual(len(guard["patterns"]), 2)
            fx.write(d.name, "src/api/one.ts", "await push(id);\n")
            self.assertEqual(TREE.run(d.name, "scan", "src/api/one.ts").stdout, "")
            fx.write(d.name, "src/api/both.ts", "x();\n" + LOOP)
            self.assertIn("src/api/both.ts:2:", TREE.run(d.name, "scan", "src/api/both.ts").stdout)

    def test_a_guard_without_the_match_field_keeps_meaning_any_one_pattern(self):
        d, guard = self.learned_loop()
        with d:
            doc = fx.read_json(d.name, ".brevio/guards.json")
            del doc["guards"][guard["id"]]["match"]
            fx.write(d.name, ".brevio/guards.json", json.dumps(doc))
            fx.write(d.name, "src/api/one.ts", "await push(id);\n")
            self.assertIn("guard:", TREE.run(d.name, "scan", "src/api/one.ts").stdout)

    def test_the_replay_also_needs_the_whole_set_in_one_changes_lines(self):
        d, guard = self.learned_loop()
        with d:
            fx.write(d.name, "src/api/one.ts", "await push(id);\n")
            fx.git(d.name, "add", "-A")
            fx.git(d.name, "commit", "-q", "-m", "one line only")
            self.assertEqual(guard_proof.replay(guard, d.name, 10)["hits"][0]["subject"], "add the loop")
            self.assertEqual(len(guard_proof.replay(guard, d.name, 10)["hits"]), 1)

    def test_a_change_that_makes_a_guard_need_all_its_patterns_is_reported_for_review(self):
        before = {"guards": {"g": {"status": "trial", "patterns": ["a" * 9, "b" * 9], "file_patterns": ["*.py"]}}}
        after = {"guards": {"g": {**before["guards"]["g"], "match": "all"}}}
        out = core_guards.weakened(json.dumps(before), json.dumps(after))
        self.assertIn("needed for a hit", out[0][1])
        self.assertEqual(core_guards.weakened(json.dumps(after), json.dumps(before)), [])


class TestAnIncidentalPatternIsDroppedNotTheWholeGuard(unittest.TestCase):
    """2026-11-13, I32: a fix that moved ``import os`` made the control run refuse the guard, and the title was the import."""

    BEFORE = 'import os\nimport subprocess\n\n\ndef run(x):\n    subprocess.call("ls " + x, shell=True)\n'
    AFTER = 'import subprocess\nimport os\n\n\ndef run(x):\n    subprocess.call(["ls", x])\n'

    def test_a_pattern_that_is_also_in_the_fixed_file_is_dropped_and_the_guard_is_kept(self):
        with history(("first", {"src/c.py": self.BEFORE}), ("fix", {"src/c.py": self.AFTER})) as d:
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("dropped: import os (also in the fixed file)", res.stdout)
            (guard,) = fx.guards(d).values()
            self.assertEqual(guard["patterns"], ['subprocess.call("ls " + x, shell=True)'])
            self.assertNotIn("match", guard)
            self.assertNotIn("import os", guard["id"])
            self.assertIn("subprocess", guard["id"])

    def test_the_dry_run_names_what_it_dropped(self):
        with history(("first", {"src/c.py": self.BEFORE}), ("fix", {"src/c.py": self.AFTER})) as d:
            res = TREE.run(d, "learn", "--dry-run", "--json")
            self.assertIn("dropped: import os (also in the fixed file)", json.loads(res.stdout)["notes"])

    def test_a_guard_whose_every_pattern_is_in_the_fixed_file_is_still_refused(self):
        with history(("first", {"src/c.py": "import os\nimport sys\n"}), ("fix", {"src/c.py": "import sys\nimport os\n"})) as d:
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 1)
            self.assertIn("fails the control run", res.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, ".brevio", "guards.json")))


class TestAGuardKeepsTheCallNotOnlyTheReturnedLine(unittest.TestCase):
    """2026-11-13, I6: ``return yaml.load(f)`` did not find ``cfg = yaml.load(open("x"))``."""

    LOADER = "import yaml\n\n\ndef read(f):\n    return yaml.load(f)\n"
    SAFE = "import yaml\n\n\ndef read(f):\n    return yaml.safe_load(f)\n"
    OTHER = 'import yaml\ncfg = yaml.load(open("x"))\n'

    def repo(self):
        return history(("add the loader", {"src/a.py": self.LOADER}), ("add other code", {"src/b.py": self.OTHER}),
                       ("use the safe loader", {"src/a.py": self.SAFE}))

    def test_the_dry_run_keeps_the_call_and_shows_both_counts(self):
        with self.repo() as d:
            out = TREE.run(d, "learn", "--dry-run").stdout
            self.assertIn("patterns: yaml.load(", out)
            self.assertIn("broader pattern kept: `yaml.load(` instead of the removed line", out)
            self.assertIn("matches 1 line(s) in this repository today (the line alone: 0)", out)
            self.assertIn("(the line alone: 1)", out)

    def test_the_kept_guard_reports_the_other_assignment(self):
        with self.repo() as d:
            self.assertEqual(TREE.run(d, "learn").returncode, 0)
            (guard,) = fx.guards(d).values()
            self.assertEqual(guard["patterns"], ["yaml.load("])
            self.assertIn("src/b.py:2:", TREE.run(d, "scan", "src/b.py").stdout)

    def test_a_call_the_fixed_file_also_makes_is_not_kept_and_the_removed_line_is(self):
        before, after = "def f(items):\n    return compute(items)\n", "def f(items):\n    return compute(items, 2)\n"
        with history(("first", {"src/m.py": before}), ("fix", {"src/m.py": after})) as d:
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("broader pattern", res.stdout)
            self.assertEqual(next(iter(fx.guards(d).values()))["patterns"], ["return compute(items)"])

    def test_the_call_text_is_taken_from_a_return_or_an_assignment_only(self):
        call = defect_compiler.call_text
        self.assertEqual((call("return yaml.load(f)"), call("cfg = yaml.load(f)"), call("x = await fetch_all(u)")),
                         ("yaml.load(", "yaml.load(", "fetch_all("))
        self.assertEqual((call("return a == b"), call("yaml.load(f)"), call("return len(x)"), call("a == b.cdefghij(1)")),
                         ("", "", "", ""))


class TestWhatLearnSaysFirst(unittest.TestCase):
    """2026-11-13, I36: the change that brought the line in is the second line, and the title is its subject."""

    def test_the_second_line_names_the_commit_that_brought_the_line_in_and_where_the_guard_reports(self):
        with history(("add the unsafe loader", {"src/a.py": TestAGuardKeepsTheCallNotOnlyTheReturnedLine.LOADER}),
                     ("use the safe loader", {"src/a.py": TestAGuardKeepsTheCallNotOnlyTheReturnedLine.SAFE})) as d:
            lines = TREE.run(d, "learn").stdout.splitlines()
            self.assertEqual(lines[0], "brevio-core learn: guard kept")
            self.assertRegex(lines[1], r"^  Brought in by: [0-9a-f]{7} add the unsafe loader \(\d{4}-\d{2}-\d{2}\); "
                                       r"it will report in src/\*\*/\*\.py$")
            (guard,) = fx.guards(d).values()
            self.assertEqual(guard["title"], "add the unsafe loader")
            self.assertTrue(guard["id"].startswith("guard-fix-"), guard["id"])

    def test_a_name_given_with_name_is_the_title_and_a_line_not_found_says_so(self):
        with fx.repo_with_a_fix() as d:
            res = TREE.run(d, "learn", "--name", "no legacy total", "--replay", "1")
            self.assertIn("Brought in by: not found in the last 1 change(s); it will report in src/**/*.py", res.stdout)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["title"], "no legacy total")

    def test_a_guard_id_does_not_follow_the_subject_so_the_same_fix_is_the_same_guard(self):
        with fx.repo_with_a_fix() as d:
            self.assertEqual(TREE.run(d, "learn").returncode, 0)
            again = TREE.run(d, "learn")
            self.assertEqual(again.returncode, 1)
            self.assertIn("is in .brevio/guards.json already", again.stderr)


class TestARefusalSaysWhyAndWhatNext(unittest.TestCase):
    """2026-11-13, D48: the refusal for an add-only fix or a short line said only that no pattern could be extracted."""

    def refused(self, *commits):
        with history(*commits) as d:
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 1)
            self.assertIn("Could not extract a deterministic fault pattern from the diff.", res.stderr)
            return res.stderr

    def test_a_diff_that_only_adds_lines_says_so(self):
        err = self.refused(("first", {"src/a.py": "x = 1\n"}), ("add", {"src/a.py": "x = 1\ny = compute_value(x)\n"}))
        self.assertIn("only adds lines", err)
        self.assertIn("`brevio-core rules`", err)

    def test_a_removed_line_under_eight_characters_is_named(self):
        err = self.refused(("first", {"src/a.py": "try:\n    go()\nexcept:\n    pass\n"}),
                           ("fix", {"src/a.py": "try:\n    go()\nexcept ValueError:\n    pass\n"}))
        self.assertIn("The removed line `except:` is under 8 characters, too general to match safely.", err)

    def test_the_explanations_come_from_the_diff(self):
        explain = defect_compiler.explain_no_guard
        self.assertIn("only adds lines", explain("--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n x = 1\n+y = compute_value(x)\n"))
        self.assertIn("bare keywords", explain("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-return null;\n+return value;\n"))
        self.assertIn("comments or have 4 characters", explain("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-# note about it\n+y = 2 + 2\n"))


class TestReviewOfTheGuardChanges(unittest.TestCase):
    """2026-11-13, second look at D45, I36, D48 and D42: what the first build left open."""

    PAGE = "export default function Page() {\n  return fetchOldWay(params.id);\n}\n"
    FIXED = "export default function Page() {\n  return fetchNewWay(params.id);\n}\n"

    def test_a_folder_with_brackets_is_still_watched_after_the_wider_glob_went(self):
        """A route folder such as ``app/[id]`` is a character class to ``fnmatch``: its glob must name it as text."""
        path = "src/app/[id]/page.tsx"
        with history(("add page", {path: self.PAGE}), ("new way", {path: self.FIXED})) as d:
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 0, res.stderr)
            fx.write(d, "src/app/[id]/other.tsx", self.PAGE)
            fx.write(d, "src/lib/other.tsx", self.PAGE)
            scan = TREE.run(d, "scan", "src/app/[id]/other.tsx", "src/lib/other.tsx")
            self.assertIn("src/app/[id]/other.tsx:2:", scan.stdout)
            self.assertNotIn("src/lib/other.tsx", scan.stdout)

    def test_a_subject_that_looks_like_a_credential_is_not_the_title_and_is_not_printed(self):
        secret = "AKIA" + "ABCDEFGHIJKLMNOP"
        with history((f"drop {secret} from the loader", {"src/a.py": TestAGuardKeepsTheCallNotOnlyTheReturnedLine.LOADER}),
                     ("use the safe loader", {"src/a.py": TestAGuardKeepsTheCallNotOnlyTheReturnedLine.SAFE})) as d:
            res = TREE.run(d, "learn")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn(secret, res.stdout)
            (guard,) = fx.guards(d).values()
            self.assertNotIn(secret, json.dumps(guard))
            self.assertNotIn(secret, fx.read(d, guard["rule_file"]))

    def test_a_short_removed_line_with_control_characters_is_printed_without_them(self):
        text = defect_compiler.explain_no_guard("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-\x1b[2Jab\n+y = compute_value(2)\n")
        self.assertNotIn("\x1b", text)
        self.assertIn("under 8 characters", text)


if __name__ == "__main__":
    unittest.main()
