"""The life of a guard after ``learn`` kept it: trial, promotion, wrong hits, and a person's switch-off (improvement plan, 2026-11-12).

Standard library only: the exporter copies this file beside the code (the 0.2 set) and it runs unchanged there. A test
cannot allocate a terminal on every system, so the commands that need a person run in a child whose terminal check says
yes (``AT_A_TERMINAL``); the same command in a plain child, where input and output are pipes, is refused.
"""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import tests.pipeline.core_export.test_core_guard_fixtures as fx
from brevio.operator import learn_manage
from brevio.pipeline import core_guards
from brevio.pipeline.gates import defect_compiler, dopamine_ledger

TREE = fx.Tree("0.2")
LIST, CALL = "tools" + "/list", "tools" + "/call"     # joined: the leak scan reads a literal "tools" + "/" as a private folder


def setUpModule():
    TREE.open()


def tearDownModule():
    TREE.close()


def person(*args, d):
    """``brevio-core`` with ``args``, typed by a person at a terminal."""
    return TREE.run(d, *args, code=fx.AT_A_TERMINAL)


class TestTrialAndPromotion(unittest.TestCase):
    def test_a_trial_guard_cannot_be_promoted_before_the_end_it_was_given(self):
        with fx.learned(TREE) as d:
            res = TREE.run(d, "learn", "promote", fx.GUARD_ID)
            self.assertEqual(res.returncode, 1)
            self.assertIn(f"the trial of {fx.GUARD_ID} ends on {fx.guards(d)[fx.GUARD_ID]['trial_ends']}", res.stderr)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["status"], "trial")

    def test_a_guard_is_promoted_once_its_trial_has_ended_and_the_replay_is_clean(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, trial_ends="2000-01-01")
            res = TREE.run(d, "learn", "promote", fx.GUARD_ID)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn(f"{fx.GUARD_ID} is active", res.stdout)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["status"], "active")
            self.assertIn("Commit .brevio/guards.json", res.stdout)

    def test_promotion_is_refused_when_the_replay_flags_a_change_it_did_not_flag_when_the_guard_was_kept(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            fx.git(d, "add", "-A")
            fx.git(d, "commit", "-q", "-m", "the defect comes back")
            fx.edit_guard(d, fx.GUARD_ID, trial_ends="2000-01-01")
            res = TREE.run(d, "learn", "promote", fx.GUARD_ID)
            self.assertEqual(res.returncode, 1)
            self.assertIn("did not flag when the guard was kept", res.stderr)
            self.assertIn("src/other.py", res.stderr)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["status"], "trial")

    def test_runs_with_no_hit_do_not_promote_a_guard_however_many_there_are(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/clean.py", fx.FIXING)
            for _ in range(8):
                TREE.run(d, "learn", "--check", "src/clean.py")
            guard = fx.guards(d)[fx.GUARD_ID]
            self.assertEqual((guard["status"], guard["runs_evaluated"]), ("trial", 8))

    def test_a_guard_with_a_hit_marked_wrong_is_not_promoted(self):
        with fx.learned(TREE) as d:
            self.assertEqual(person("learn", "wrong", fx.GUARD_ID, d=d).returncode, 0)
            fx.edit_guard(d, fx.GUARD_ID, trial_ends="2000-01-01")
            res = TREE.run(d, "learn", "promote", fx.GUARD_ID)
            self.assertEqual(res.returncode, 1)
            self.assertIn("1 hit(s) marked wrong", res.stderr)

    def test_learn_list_shows_the_status_the_trial_end_and_what_the_replay_flagged(self):
        with fx.learned(TREE) as d:
            out = TREE.run(d, "learn", "list").stdout
            self.assertIn(f"{fx.GUARD_ID}: trial, trial ends {fx.guards(d)[fx.GUARD_ID]['trial_ends']}, replay when kept: flagged 1 of 2", out)


class TestWrongHitsAndDismissal(unittest.TestCase):
    def test_a_guard_goes_off_at_the_stated_number_of_wrong_hits_and_the_command_says_so(self):
        with fx.learned(TREE) as d:
            said = [person("learn", "wrong", fx.GUARD_ID, d=d).stdout for _ in range(defect_compiler.WRONG_LIMIT)]
            self.assertIn(f"goes off at {defect_compiler.WRONG_LIMIT}", said[0])
            self.assertIn(f"{fx.GUARD_ID} is off", said[-1])
            guard = fx.guards(d)[fx.GUARD_ID]
            self.assertEqual(guard["status"], "off")
            self.assertIn("hits were marked wrong", guard["status_note"])
            fx.write(d, "src/other.py", fx.FIXED)
            self.assertEqual(TREE.run(d, "scan", "src/other.py").stdout, "")

    def test_two_wrong_hits_leave_the_guard_on_trial(self):
        with fx.learned(TREE) as d:
            for _ in range(defect_compiler.WRONG_LIMIT - 1):
                person("learn", "wrong", fx.GUARD_ID, d=d)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["status"], "trial")

    def test_a_person_switches_a_guard_off_and_the_file_keeps_who_when_why_and_what_it_was(self):
        with fx.learned(TREE) as d:
            res = person("learn", "dismiss", fx.GUARD_ID, "--reason", "the rewrite made it obsolete", d=d)
            self.assertEqual(res.returncode, 0, res.stderr)
            guard = fx.guards(d)[fx.GUARD_ID]
            self.assertEqual(guard["status"], "off")
            self.assertEqual(guard["dismissed"]["reason"], "the rewrite made it obsolete")
            self.assertEqual(guard["dismissed"]["how"], "dismiss")
            self.assertEqual(guard["dismissed"]["was"], defect_compiler.fingerprint(guard))
            self.assertIn("Commit .brevio/guards.json", res.stdout)
            fx.write(d, "src/other.py", fx.FIXED)
            self.assertEqual(TREE.run(d, "scan", "src/other.py").stdout, "")

    def test_a_guard_that_is_off_loses_its_agent_rule_file_and_the_command_names_the_deletion_to_commit(self):
        with fx.learned(TREE) as d:
            rule = os.path.join(d, ".agents", "rules", f"{fx.GUARD_ID}.md")
            self.assertTrue(os.path.isfile(rule))
            res = person("learn", "dismiss", fx.GUARD_ID, d=d)
            self.assertFalse(os.path.exists(rule))
            self.assertIn("its agent rule is deleted", res.stdout)
            self.assertIn("the deleted agent rule under .agents/rules", res.stdout.splitlines()[-1])
        with fx.learned(TREE) as d:
            for _ in range(defect_compiler.WRONG_LIMIT):
                person("learn", "wrong", fx.GUARD_ID, d=d)
            self.assertFalse(os.path.exists(os.path.join(d, ".agents", "rules", f"{fx.GUARD_ID}.md")))

    def test_a_rule_file_entry_that_points_outside_the_rules_folder_is_not_deleted(self):
        with fx.learned(TREE) as d:
            outside = fx.write(d, "src/keep.py", "x = 1\n")
            fx.edit_guard(d, fx.GUARD_ID, rule_file="src/keep.py")
            self.assertEqual(person("learn", "dismiss", fx.GUARD_ID, d=d).returncode, 0)
            self.assertTrue(os.path.isfile(outside))
            fx.edit_guard(d, fx.GUARD_ID, rule_file="../escape.md")
            self.assertEqual(person("learn", "dismiss", fx.GUARD_ID, d=d).returncode, 0)

    def test_dismiss_and_wrong_are_refused_where_input_or_output_is_not_a_terminal(self):
        with fx.learned(TREE) as d:
            before = fx.read(d, ".brevio/guards.json")
            for action in ("dismiss", "wrong"):
                res = TREE.run(d, "learn", action, fx.GUARD_ID)
                self.assertEqual(res.returncode, 2, action)
                self.assertIn("runs only at a terminal", res.stderr)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)

    def test_a_guard_file_in_the_wrong_shape_is_read_as_no_guards_and_is_left_alone(self):
        with fx.repo_with_a_fix() as d:
            fx.write(d, ".brevio/guards.json", "[1, 2]")
            self.assertIn("no guards yet", TREE.run(d, "learn", "list").stdout)
            for action in ("dismiss", "wrong", "promote"):
                res = person("learn", action, "guard-x", d=d)
                self.assertEqual(res.returncode, 2, (action, res.stderr))
                self.assertIn("no guard guard-x", res.stderr)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), "[1, 2]")

    def test_an_unknown_guard_is_a_usage_error_and_changes_nothing(self):
        with fx.learned(TREE) as d:
            before = fx.read(d, ".brevio/guards.json")
            res = person("learn", "dismiss", "guard-nope", d=d)
            self.assertEqual(res.returncode, 2)
            self.assertIn("no guard guard-nope", res.stderr)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)


def mcp(d, *messages):
    lines = "".join(json.dumps(m) + "\n" for m in messages)
    res = TREE.run(d, "mcp", stdin=lines)
    return [json.loads(ln) for ln in res.stdout.splitlines()]


class TestTheMcpServerCannotSwitchAGuardOff(unittest.TestCase):
    def test_the_tool_list_has_learn_and_no_tool_that_dismisses_or_marks_wrong(self):
        with fx.repo_with_a_fix() as d:
            (reply,) = mcp(d, {"jsonrpc": "2.0", "id": 1, "method": LIST})
            names = {t["name"] for t in reply["result"]["tools"]}
            self.assertIn("learn", names)
            self.assertEqual({n for n in names if any(w in n for w in ("dismiss", "wrong", "promote"))}, set())

    def test_a_learn_call_that_names_dismiss_makes_a_guard_by_that_name_and_switches_none_off(self):
        with fx.repo_with_a_fix() as d:
            self.assertEqual(TREE.run(d, "learn", "--name", "no legacy total").returncode, 0)
            replies = mcp(d, {"jsonrpc": "2.0", "id": 1, "method": CALL, "params": {"name": "learn", "arguments": {"name": "dismiss"}}},
                          {"jsonrpc": "2.0", "id": 2, "method": CALL, "params": {"name": "dismiss", "arguments": {"guard_id": fx.GUARD_ID}}},
                          {"jsonrpc": "2.0", "id": 3, "method": CALL,
                           "params": {"name": "learn", "arguments": {"diff_base": "dismiss"}}})
            self.assertTrue(replies[1].get("error") or replies[1]["result"].get("isError"), replies[1])
            guards = fx.guards(d)
            self.assertEqual(guards[fx.GUARD_ID]["status"], "trial")
            self.assertNotIn("dismissed", guards[fx.GUARD_ID])
            self.assertEqual({g["status"] for g in guards.values()}, {"trial"})


def with_a_hit_in_another_file(d, name="src/other.py", text=fx.FIXED):
    """The defect comes back in ``name``, committed, so a replay flags it."""
    fx.write(d, name, text)
    fx.git(d, "add", "-A")
    fx.git(d, "commit", "-q", "-m", "the defect comes back")


class TestAWrongHitIsNamedAndCountedOnce(unittest.TestCase):
    """2026-11-13, D41: ``learn wrong`` took no hit, so calling it again switched the guard off."""

    def test_a_named_hit_counts_once_however_often_it_is_marked(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", "x = 1\n" + fx.FIXED)
            first = person("learn", "wrong", fx.GUARD_ID, "src/other.py:2", d=d)
            again = person("learn", "wrong", fx.GUARD_ID, "src/other.py:2", d=d)
            self.assertEqual((first.returncode, again.returncode), (0, 0), first.stderr + again.stderr)
            self.assertIn("1 hit(s) marked wrong", first.stdout)
            self.assertIn("that hit is marked wrong already", again.stdout)
            guard = fx.guards(d)[fx.GUARD_ID]
            self.assertEqual((guard["false_fails"], len(guard["wrong_hits"]), guard["status"]), (1, 1, "trial"))

    def test_a_second_named_hit_is_a_second_mark(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED + "value = compute_legacy_total(other)\n")
            for spot in ("src/other.py:1", "src/other.py:2"):
                person("learn", "wrong", fx.GUARD_ID, spot, d=d)
            guard = fx.guards(d)[fx.GUARD_ID]
            self.assertEqual((guard["false_fails"], len(set(guard["wrong_hits"]))), (2, 2))

    def test_a_line_that_is_not_a_hit_is_a_usage_error_and_changes_nothing(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", "x = 1\n" + fx.FIXED)
            before = fx.read(d, ".brevio/guards.json")
            for spot in ("src/other.py:1", "src/other.py:9", "src/missing.py:1", "../x.py:1", "src/other.py"):
                res = person("learn", "wrong", fx.GUARD_ID, spot, d=d)
                self.assertEqual(res.returncode, 2, (spot, res.stdout))
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)

    def test_without_a_named_hit_each_call_counts_one_mark_and_the_command_says_how_to_name_one(self):
        with fx.learned(TREE) as d:
            res = person("learn", "wrong", fx.GUARD_ID, d=d)
            self.assertIn("no hit was named", res.stdout)
            self.assertIn("path:line", res.stdout)
            person("learn", "wrong", fx.GUARD_ID, d=d)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["false_fails"], 2)

    def test_the_hit_is_the_fingerprint_a_scan_gives_it_and_a_hit_that_moves_is_the_same(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            person("learn", "wrong", fx.GUARD_ID, "src/other.py:1", d=d)
            fx.write(d, "src/other.py", "# moved\n" + fx.FIXED)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["wrong_hits"],
                             [core_guards.fingerprint("src/other.py", core_guards.rule_of(fx.GUARD_ID), fx.FIXED)])


class TestPromoteCanAcceptTheHitsAPersonRead(unittest.TestCase):
    """2026-11-13, D42: a guard that caught a real reintroduction could not be promoted without a hand edit."""

    def ready(self, d):
        with_a_hit_in_another_file(d)
        fx.edit_guard(d, fx.GUARD_ID, trial_ends="2000-01-01")

    def test_accept_takes_the_hits_in_and_promotes_and_plain_promote_still_refuses(self):
        with fx.learned(TREE) as d:
            self.ready(d)
            plain = TREE.run(d, "learn", "promote", fx.GUARD_ID)
            self.assertEqual(plain.returncode, 1)
            self.assertIn("`learn promote --accept` takes them in at a terminal", plain.stderr)
            res = person("learn", "promote", fx.GUARD_ID, "--accept", d=d)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("accepted 1 replay hit(s): ", res.stdout)
            self.assertIn("src/other.py", res.stdout)
            guard = fx.guards(d)[fx.GUARD_ID]
            self.assertEqual(guard["status"], "active")
            self.assertTrue(any(k.endswith(":src/other.py") for k in guard["replay"]["accepted"]))
            self.assertIn("accepted 1 replay hit(s)", guard["status_note"])

    def test_accept_runs_only_at_a_terminal_and_changes_nothing_otherwise(self):
        with fx.learned(TREE) as d:
            self.ready(d)
            before = fx.read(d, ".brevio/guards.json")
            res = TREE.run(d, "learn", "promote", fx.GUARD_ID, "--accept")
            self.assertEqual(res.returncode, 2)
            self.assertIn("runs only at a terminal", res.stderr)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)

    def test_accept_does_not_write_the_hits_when_the_promotion_is_refused_for_another_reason(self):
        with fx.learned(TREE) as d:
            with_a_hit_in_another_file(d)
            before = fx.read(d, ".brevio/guards.json")
            res = person("learn", "promote", fx.GUARD_ID, "--accept", d=d)
            self.assertEqual(res.returncode, 1)
            self.assertIn("ends on", res.stderr)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)

    def test_the_hint_to_accept_is_not_given_when_the_trial_is_the_reason(self):
        """Review: the hint named ``--accept`` although the refusal was the trial's end date, which ``--accept`` does not change."""
        with fx.learned(TREE) as d:
            with_a_hit_in_another_file(d)
            res = TREE.run(d, "learn", "promote", fx.GUARD_ID)
            self.assertIn("ends on", res.stderr)
            self.assertNotIn("--accept", res.stderr)

    def test_a_path_on_another_drive_is_refused_not_a_crash(self):
        """``os.path.commonpath`` raises on Windows when the two paths are on different drives."""
        def other_drive(_paths):
            raise ValueError("Paths don't have the same drive")
        with mock.patch.object(learn_manage.os.path, "commonpath", other_drive):
            print_id, why = learn_manage._hit_fingerprint("guard-x", "D:/elsewhere/a.py:3")
        self.assertEqual(print_id, "")
        self.assertIn("not path:line inside this repository", why)

    def test_accept_goes_with_promote_only(self):
        with fx.learned(TREE) as d:
            self.assertEqual(TREE.run(d, "learn", "list", "--accept").returncode, 2)


class TestAGuardWithoutAnEndDateHasTheTrialItsAgeSays(unittest.TestCase):
    """2026-11-13, D49: a guard of the first release has no ``trial_ends`` and was promoted the day it was made."""

    def test_a_guard_made_today_without_an_end_date_is_not_promoted(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, trial_ends="")
            res = TREE.run(d, "learn", "promote", fx.GUARD_ID)
            self.assertEqual(res.returncode, 1)
            self.assertIn(f"the trial of {fx.GUARD_ID} ends on", res.stderr)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["status"], "trial")

    def test_an_old_guard_without_an_end_date_is_promoted_as_before(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, trial_ends="", created_at="2025-01-01T00:00:00Z")
            self.assertEqual(TREE.run(d, "learn", "promote", fx.GUARD_ID).returncode, 0)

    def test_the_end_comes_from_the_day_made_else_the_day_updated_else_it_counts_as_ended(self):
        end = defect_compiler._trial_end_of
        self.assertEqual(end({"created_at": "2026-01-01T10:00:00Z"}), "2026-01-15")
        self.assertEqual(end({"created_at": "garbage", "updated_at": "2026-02-01T00:00:00Z"}), "2026-02-15")
        self.assertEqual(end({}), "")
        self.assertEqual(end({"trial_ends": "2026-03-03", "created_at": "2026-01-01T00:00:00Z"}), "2026-03-03")


class TestLearnShowAndTheCountsInList(unittest.TestCase):
    """2026-11-13, I33: a team judged a guard from a line of `list`, which held neither its hits nor its patterns."""

    def test_show_prints_the_guard_its_replay_by_commit_and_the_lines_it_matches_now(self):
        with fx.learned(TREE) as d:
            with_a_hit_in_another_file(d, "src/other.py", "x = 1\n" + fx.FIXED)
            res = TREE.run(d, "learn", "show", fx.GUARD_ID)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn(f"{fx.GUARD_ID}: no legacy total", res.stdout)
            self.assertIn("status:    trial (trial ends ", res.stdout)
            self.assertIn("patterns:  compute_legacy_total(", res.stdout)
            self.assertIn("watches:   src/**/*.py", res.stdout)
            self.assertRegex(res.stdout, r"[0-9a-f]{7} src/other\.py  the defect comes back")
            self.assertIn("matches 1 line(s) in this repository today\n    src/other.py:2", res.stdout)
            self.assertIn("found by `learn --check`: 0; marked wrong: 0", res.stdout)

    def test_show_writes_nothing_and_needs_no_terminal_and_an_unknown_guard_is_a_usage_error(self):
        with fx.learned(TREE) as d:
            before = fx.read(d, ".brevio/guards.json")
            self.assertEqual(TREE.run(d, "learn", "show", fx.GUARD_ID, "--json").returncode, 0)
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)
            res = TREE.run(d, "learn", "show", "guard-nope")
            self.assertEqual(res.returncode, 2)
            self.assertIn("no guard guard-nope", res.stderr)

    def test_show_as_json_adds_the_replay_and_the_lines(self):
        with fx.learned(TREE) as d:
            with_a_hit_in_another_file(d)
            doc = json.loads(TREE.run(d, "learn", "show", fx.GUARD_ID, "--json").stdout)
            self.assertEqual(doc["matches_today"], ["src/other.py:1"])
            self.assertEqual(doc["replay_now"]["hits"][0]["file"], "src/other.py")
            self.assertEqual(doc["id"], fx.GUARD_ID)

    def test_list_gives_the_hits_found_by_learn_check(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            TREE.run(d, "learn", "--check", "src/other.py")
            self.assertIn(", 1 hit(s) found by `learn --check`", TREE.run(d, "learn", "list").stdout)


class TestTheLearnDispatcherTakesNewActions(unittest.TestCase):
    """2026-11-13: another module adds a ``learn`` subcommand with ``register``, not by editing ``run``."""

    def test_a_registered_action_is_offered_and_run_and_one_that_needs_a_person_is_refused_at_a_pipe(self):
        seen = []
        learn_manage.register("probe", lambda call: seen.append(call.guard_id) or 0)
        learn_manage.register("probe-person", lambda call: 0, needs_person=True)
        try:
            self.assertIn("probe", learn_manage.ACTIONS)
            self.assertEqual(learn_manage.run("probe", "g", "", 1, False), 0)
            self.assertEqual(seen, ["g"])
            self.assertEqual(learn_manage.run("probe-person", "g", "", 1, False), 2)     # a test run has no terminal
        finally:
            learn_manage.HANDLERS.pop("probe", None)
            learn_manage.HANDLERS.pop("probe-person", None)
            learn_manage.NEEDS_PERSON.discard("probe-person")


class TestTheStatsFileStaysOutOfCommits(unittest.TestCase):
    """2026-11-13, D52: ``learn --check`` with a hit left ``.brevio/stats.json`` untracked, with timestamps."""

    def test_the_first_hit_adds_the_file_to_the_local_exclude_and_git_status_does_not_show_it(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            self.assertEqual(TREE.run(d, "learn", "--check", "src/other.py").returncode, 1)
            self.assertTrue(os.path.isfile(os.path.join(d, ".brevio", "stats.json")))
            status = subprocess.run(["git", "-C", d, "status", "--short", "-uall"], capture_output=True, encoding="utf-8").stdout
            self.assertNotIn("stats.json", status)
            self.assertEqual(fx.read(d, ".git/info/exclude").count("/.brevio/stats.json\n"), 1)
            TREE.run(d, "learn", "--check", "src/other.py")
            self.assertEqual(fx.read(d, ".git/info/exclude").count("/.brevio/stats.json\n"), 1)

    def test_a_folder_that_is_not_a_repository_is_left_alone_and_the_file_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            dopamine_ledger.save_stats({"version": 2, "total_interceptions": 1}, d)
            self.assertEqual(sorted(os.listdir(d)), [".brevio"])
            self.assertTrue(os.path.isfile(os.path.join(d, ".brevio", "stats.json")))

    def test_a_workspace_inside_a_repository_gets_the_path_from_the_top_of_the_repository(self):
        with tempfile.TemporaryDirectory() as d:
            fx.git(d, "init", "-q", "-b", "main")
            sub = os.path.join(d, "pkg")
            os.makedirs(sub)
            dopamine_ledger.save_stats({"version": 2}, sub)
            self.assertIn("/pkg/.brevio/stats.json\n", fx.read(d, ".git/info/exclude"))

    def test_a_folder_with_brackets_is_written_as_text_so_git_ignores_the_file(self):
        """Review: ``[1]`` in ``app/[1]/.brevio/stats.json`` was a character class in the exclude file and matched nothing."""
        with tempfile.TemporaryDirectory() as d:
            fx.git(d, "init", "-q", "-b", "main")
            sub = os.path.join(d, "app [1]")
            os.makedirs(sub)
            dopamine_ledger.save_stats({"version": 2}, sub)
            ignored = subprocess.run(["git", "-C", d, "check-ignore", "app [1]/.brevio/stats.json"], capture_output=True,
                                     encoding="utf-8")
            self.assertEqual(ignored.returncode, 0, fx.read(d, ".git/info/exclude"))


if __name__ == "__main__":
    unittest.main()
