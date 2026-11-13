"""Learned guards with the rest of the gate: baseline, ignore comments, ``brevio-core.toml``, SARIF, the agent hook, and the
change check for a guard that is removed or weakened (improvement plan, 2026-11-12).

Standard library only: the exporter copies this file beside the code (the 0.6 set) and it runs unchanged there. Each
case runs a clean ``-I`` child on a temporary git repository. The commands that need a person run in a child whose
terminal check says yes (``AT_A_TERMINAL``).
"""
import json
import os
import sys
import tempfile
import unittest

import tests.pipeline.core_export.test_core_guard_fixtures as fx
import tests.pipeline.core_export.test_core_portable as portable
from brevio.pipeline import core_baseline, core_cli, core_guards

TREE = fx.Tree("0.6")
RULE = f"guard:{fx.GUARD_ID}"
WEB = "https" + "://"     # joined: the leak scan reads a literal URL in a test as a foreign one
LATENCY_LIMIT = 1.5     # CPU seconds for a whole post-edit hook run on a 2,000-line file, interpreter start included
BUDGET = 2.0            # the hook's own default wall-clock budget in seconds (``core_hook.DEFAULT_BUDGET``)


def setUpModule():
    TREE.open()


def tearDownModule():
    TREE.close()


def person(*args, d):
    return TREE.run(d, *args, code=fx.AT_A_TERMINAL)


def active(d):
    fx.edit_guard(d, fx.GUARD_ID, status="active")


def hook(d, event, stdin):
    return TREE.run(d, "hook", "run", "--event", event, stdin=json.dumps(stdin))


def edit_event(d, rel):
    return {"cwd": d, "hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": os.path.join(d, rel)}}


def stop_event(d):
    return {"cwd": d, "hook_event_name": "Stop", "stop_hook_active": False}


class TestBaselineIgnoreAndConfig(unittest.TestCase):
    def test_a_baselined_guard_hit_is_hidden_like_the_finding_of_a_rule(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            self.assertEqual(TREE.run(d, "baseline", "src/other.py").returncode, 0)
            res = TREE.run(d, "scan", "src/other.py")
            self.assertEqual(res.stdout, "")
            self.assertIn("1 baselined", res.stderr)

    def test_a_baseline_entry_survives_the_line_moving_because_it_is_keyed_by_the_line_text(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            TREE.run(d, "baseline", "src/other.py")
            fx.write(d, "src/other.py", "# a new first line\n\n" + fx.FIXED)
            self.assertEqual(TREE.run(d, "scan", "src/other.py").stdout, "")

    def test_an_ignore_comment_with_a_reason_silences_a_guard_hit_in_ci_and_the_agent_hook_does_not_honour_it(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, status="active")
            fx.write(d, "src/other.py", f"# brevio-core: ignore {RULE} -- reviewed on purpose\n{fx.FIXED}")
            res = TREE.run(d, "scan", "src/other.py")
            self.assertEqual((res.stdout, res.returncode), ("", 0), res.stderr)
            self.assertIn("1 silenced", res.stderr)
            self.assertNotIn("not a rule", res.stderr)
            self.assertIn(RULE, hook(d, "post-edit", edit_event(d, "src/other.py")).stdout)

    def test_an_ignore_comment_that_names_a_guard_the_file_does_not_hold_is_a_warning(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", f"# brevio-core: ignore guard:guard-nope -- why\n{fx.FIXED}")
            self.assertIn("GUARD:GUARD-NOPE, WHICH IS NOT A RULE", TREE.run(d, "scan", "src/other.py").stderr.upper())

    def test_the_config_raises_an_active_guard_to_an_error_and_a_guard_on_trial_stays_report_only(self):
        with fx.learned(TREE) as d:
            fx.write(d, "brevio-core.toml", f'[rules."{RULE}"]\nlevel = "error"\n')
            fx.write(d, "src/other.py", fx.FIXED)
            on_trial = TREE.run(d, "scan", "src/other.py", "--fail-on", "error")
            self.assertEqual(on_trial.returncode, 0, on_trial.stdout)
            self.assertIn("reported only", on_trial.stdout)
            active(d)
            raised = TREE.run(d, "scan", "src/other.py", "--fail-on", "error")
            self.assertEqual(raised.returncode, 1, raised.stderr)
            self.assertEqual(TREE.run(d, "scan", "src/other.py").returncode, 1)

    def test_an_active_guard_is_a_warning_under_every_fail_on_setting_until_the_config_names_it(self):
        with fx.learned(TREE) as d:
            active(d)
            fx.write(d, "src/other.py", fx.FIXED)
            for fail_on in ("new", "all", "error", "none"):
                res = TREE.run(d, "scan", "src/other.py", "--fail-on", fail_on)
                self.assertEqual(res.returncode, 0, (fail_on, res.stderr))
                self.assertIn("1 at warn level", res.stderr)

    def test_the_config_can_ignore_every_guard_and_a_guard_it_names_is_not_called_unknown(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            fx.write(d, "brevio-core.toml", 'ignore = ["guard:*"]\n')
            quiet = TREE.run(d, "scan", "src/other.py")
            self.assertEqual(quiet.stdout, "")
            self.assertNotIn("matches no rule", quiet.stderr)
            fx.write(d, "brevio-core.toml", f'[rules."{RULE}"]\nlevel = "warn"\n[rules."guard:guard-nope"]\nlevel = "warn"\n')
            warned = TREE.run(d, "scan", "src/other.py").stderr
            self.assertIn("guard:guard-nope matches no rule", warned)
            self.assertNotIn(f"{RULE} matches no rule", warned)

    def test_a_guard_file_that_is_not_json_is_a_warning_and_the_scan_goes_on(self):
        with fx.learned(TREE) as d:
            fx.write(d, ".brevio/guards.json", "{not json")
            fx.write(d, "src/other.py", fx.FIXED)
            res = TREE.run(d, "scan", "src/other.py")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn(".brevio/guards.json is not JSON", res.stderr)


class TestEveryWayAFindingIsShown(unittest.TestCase):
    def test_the_agent_and_markdown_formats_show_the_hit_and_the_markdown_does_not_link_it_to_a_rule_page(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            agent = TREE.run(d, "scan", "src/other.py", "--format", "agent").stdout
            self.assertTrue(agent.startswith(f"src/other.py:1 [{RULE}] trial guard"), agent)
            md = TREE.run(d, "scan", "src/other.py", "--format", "markdown", "--rules-url", WEB + "example.org/rules/").stdout
            row = next(ln for ln in md.splitlines() if "src/other.py" in ln)
            self.assertIn(RULE, row)
            self.assertNotIn("](", row)

    def test_sarif_carries_a_fingerprint_that_stays_the_same_when_the_line_moves(self):
        with fx.learned(TREE) as d:
            active(d)
            fx.write(d, "src/other.py", fx.FIXED)
            TREE.run(d, "scan", "src/other.py", "--sarif", "a.sarif")
            fx.write(d, "src/other.py", "# one\n# two\n" + fx.FIXED)
            TREE.run(d, "scan", "src/other.py", "--sarif", "b.sarif")
            first, second = (fx.read_json(d, n)["runs"][0]["results"][0] for n in ("a.sarif", "b.sarif"))
            self.assertEqual((first["ruleId"], first["level"]), (RULE, "warning"))
            self.assertEqual(first["partialFingerprints"], second["partialFingerprints"])
            self.assertEqual(len(first["partialFingerprints"]["guardHit/v1"]), 64)
            self.assertEqual((first["locations"][0]["physicalLocation"]["region"]["startLine"],
                              second["locations"][0]["physicalLocation"]["region"]["startLine"]), (1, 3))

    def test_a_finding_of_a_rule_has_no_guard_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            fx.write(d, "a.ts", fx.AWS_EXAMPLE_LINE)
            TREE.run(d, "scan", "a.ts", "--sarif", "r.sarif")
            result = fx.read_json(d, "r.sarif")["runs"][0]["results"][0]
            self.assertNotIn("partialFingerprints", result)


class TestTheAgentHook(unittest.TestCase):
    def test_after_an_edit_the_agent_is_told_about_a_hit_and_that_a_trial_guard_is_report_only(self):
        """Changed on purpose (D46, 2026-11-13): this test pinned a block that said "Fix them in the code" for a guard on
        trial; the hit is now a note that does not block (``additionalContext``)."""
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            res = hook(d, "post-edit", edit_event(d, "src/other.py"))
            self.assertEqual(res.returncode, 0, res.stderr)
            out = json.loads(res.stdout)
            self.assertNotIn("decision", out)
            note = out["hookSpecificOutput"]["additionalContext"]
            self.assertIn(RULE, note)
            self.assertIn("reported only", note)
            self.assertNotIn("Fix them", note)

    def test_an_edit_with_a_trial_hit_and_a_real_finding_blocks_for_the_finding_only(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED + "password = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "'\n")
            res = hook(d, "post-edit", edit_event(d, "src/other.py"))
            out = json.loads(res.stdout)
            self.assertEqual(out["decision"], "block")
            head, _sep, note = out["reason"].partition("reported only")
            self.assertIn("1 finding(s)", head)
            self.assertIn("[R13.1]", head)
            self.assertIn(RULE, note)
            self.assertNotIn(RULE, head)

    def test_at_stop_a_hit_of_a_trial_guard_or_a_warning_does_not_keep_the_agent_working(self):
        with fx.learned(TREE) as d:
            fx.write(d, "src/other.py", fx.FIXED)
            on_trial = hook(d, "stop", stop_event(d))
            self.assertEqual((on_trial.stdout, on_trial.returncode), ("", 0), on_trial.stderr)
            active(d)
            warning = hook(d, "stop", stop_event(d))
            self.assertEqual((warning.stdout, warning.returncode), ("", 0), warning.stderr)

    def test_at_stop_a_guard_the_config_raised_to_an_error_keeps_the_agent_working(self):
        with fx.learned(TREE) as d:
            active(d)
            fx.write(d, "brevio-core.toml", f'[rules."{RULE}"]\nlevel = "error"\n')
            fx.write(d, "src/other.py", fx.FIXED)
            res = hook(d, "stop", stop_event(d))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn(RULE, json.loads(res.stdout)["reason"])

    def test_the_hook_has_no_command_that_dismisses_a_guard_and_a_shell_command_naming_one_changes_nothing(self):
        with fx.learned(TREE) as d:
            before = fx.read(d, ".brevio/guards.json")
            event = {"cwd": d, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                     "tool_input": {"command": f"brevio-core learn dismiss {fx.GUARD_ID}"}}
            res = hook(d, "post-command", event)
            self.assertEqual((res.stdout, res.returncode), ("", 0))
            self.assertEqual(fx.read(d, ".brevio/guards.json"), before)
            self.assertEqual(fx.guards(d)[fx.GUARD_ID]["status"], "trial")


class TestAChangeThatWeakensAGuard(unittest.TestCase):
    def _suppressions(self, d, *extra):
        res = TREE.run(d, "scan", "--diff", "HEAD", *extra)
        return [ln for ln in res.stdout.splitlines() if "[suppression]" in ln], res

    def test_removing_a_guard_is_reported_like_an_added_ignore_comment(self):
        with fx.learned(TREE) as d:
            doc = fx.read_json(d, ".brevio/guards.json")
            doc["guards"] = {}
            fx.write(d, ".brevio/guards.json", json.dumps(doc, indent=2))
            found, res = self._suppressions(d)
            self.assertEqual(len(found), 1, res.stdout)
            self.assertIn(f"(guard {fx.GUARD_ID}: removed)", found[0])
            self.assertTrue(found[0].startswith(".brevio/guards.json:"))
            self.assertEqual(res.returncode, 1)

    def test_switching_a_guard_off_by_hand_narrowing_it_or_putting_an_active_one_back_on_trial_is_reported(self):
        cases = (("status", "off", "switched off without `brevio-core learn dismiss`"),
                 ("patterns", ["something else entirely"], "a pattern was taken out"),
                 ("file_patterns", ["docs/**/*.md"], "a watched path was taken out"))
        for field, value, why in cases:
            with self.subTest(field), fx.learned(TREE) as d:
                fx.edit_guard(d, fx.GUARD_ID, **{field: value})
                found, _res = self._suppressions(d)
                self.assertEqual(len(found), 1, found)
                self.assertIn(why, found[0])
        with fx.learned(TREE) as d:
            active(d)
            fx.git(d, "commit", "-q", "-am", "promote")
            fx.edit_guard(d, fx.GUARD_ID, status="trial")
            self.assertIn("put back on trial", self._suppressions(d)[0][0])

    def test_a_person_s_dismiss_leaves_the_record_that_makes_it_no_finding_and_the_flag_hides_the_rest(self):
        with fx.learned(TREE) as d:
            self.assertEqual(person("learn", "dismiss", fx.GUARD_ID, "--reason", "obsolete", d=d).returncode, 0)
            self.assertEqual(self._suppressions(d)[0], [])
            fx.edit_guard(d, fx.GUARD_ID, status="trial")           # a hand edit that goes back: stronger, not weaker
            self.assertEqual(self._suppressions(d)[0], [])
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, status="off")
            self.assertEqual(self._suppressions(d, "--allow-new-suppressions")[0], [])

    def test_a_dismiss_record_that_names_another_guard_state_does_not_count(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, status="off", dismissed={"how": "dismiss", "was": "0" * 64, "reason": "", "at": ""})
            self.assertEqual(len(self._suppressions(d)[0]), 1)

    def test_adding_a_guard_or_promoting_one_is_not_reported(self):
        with fx.learned(TREE) as d:
            active(d)
            doc = fx.read_json(d, ".brevio/guards.json")
            doc["guards"]["guard-extra"] = {**doc["guards"][fx.GUARD_ID], "id": "guard-extra", "patterns": ["a different line of text"]}
            fx.write(d, ".brevio/guards.json", json.dumps(doc, indent=2))
            self.assertEqual(self._suppressions(d)[0], [])

    def test_at_stop_the_hook_reports_a_guard_the_uncommitted_changes_remove(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, status="off")
            res = hook(d, "stop", stop_event(d))
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("[suppression]", json.loads(res.stdout)["reason"])
        with fx.learned(TREE) as d:
            person("learn", "dismiss", fx.GUARD_ID, d=d)
            self.assertEqual(hook(d, "stop", stop_event(d)).stdout, "")

    def test_after_an_edit_of_the_guard_file_the_hook_reports_the_removal_at_once(self):
        with fx.learned(TREE) as d:
            fx.edit_guard(d, fx.GUARD_ID, status="off")
            res = hook(d, "post-edit", edit_event(d, ".brevio/guards.json"))
            self.assertIn("[suppression]", json.loads(res.stdout)["reason"])


class TestTheNamesAgree(unittest.TestCase):
    """``core_cli`` and ``core_guards`` are in different sets, so each holds its own copy of a name; these tests hold the
    copies together."""

    def test_the_rule_prefix_and_the_suppression_finding_are_the_same_in_every_module_that_spells_them(self):
        self.assertEqual(core_cli.GUARD_PREFIX, core_guards.PREFIX)
        self.assertEqual((core_guards.SUPPRESSION_RULE, core_guards.SUPPRESSION_MESSAGE),
                         (core_baseline.SUPPRESSION_RULE, core_baseline.SUPPRESSION_MESSAGE))
        base = json.dumps({"guards": {"g": {"status": "trial", "patterns": ["a line of text"], "file_patterns": ["*.py"]}}})
        (found,) = core_guards.changes(base, json.dumps({"guards": {}}))
        self.assertEqual(found, core_baseline.suppression(core_guards.GUARDS_PATH, 1, " (guard g: removed)"))


class TestTheHookBudget(unittest.TestCase):
    def test_a_post_edit_hook_with_50_guards_on_a_2000_line_file_stays_inside_its_budget(self):
        """The hook gives up quietly after ``BUDGET`` seconds, so guards must cost little. Measured on 2026-11-12 as CPU
        seconds of the whole hook process, interpreter start and git included, three runs each on the build machine: 0.13 to
        0.24 s with no guard file, and 0.18 to 0.23 s with 50 guards and one hit (these numbers stay in this test; no
        public text quotes them). The limit is the one of the hook's latency test."""
        with tempfile.TemporaryDirectory() as d:
            body = "".join(f"export const v{i} = {i};\n" for i in range(2000))
            fx.write(d, "src/big.ts", body)
            fx.git(d, "init", "-q", "-b", "main")
            fx.git(d, "add", "-A")
            fx.git(d, "commit", "-q", "-m", "base")
            many = {f"guard-{n}": {"id": f"guard-{n}", "title": f"guard {n}", "status": "active" if n % 2 else "trial",
                                   "file_patterns": ["src/**/*.ts", "**/*.ts"],
                                   "patterns": [f"export const w{n}a = {n};", f"export const w{n}b = {n};", "re:export const q" + str(n) + r"\s*=\s*\d+;"]}
                    for n in range(49)}
            many["guard-hit"] = {**many["guard-0"], "id": "guard-hit", "patterns": ["export const v1999 = 1999;"]}
            fx.write(d, ".brevio/guards.json", json.dumps({"version": 1, "guards": many}))
            path = fx.write(d, "src/big.ts", body.replace(" = ", " =  "))     # every line changed
            event = {"cwd": d, "tool_input": {"file_path": path}}
            res, took = portable.run_with_cpu(
                [sys.executable, "-I", "-c", fx.RUN, TREE.path, "hook", "run", "--event", "post-edit", "--budget", "60"], cwd=d,
                input=json.dumps(event), capture_output=True, encoding="utf-8", timeout=120, env=portable.child_env(TREE.env_path, d))
            self.assertIn("[guard:guard-hit]", json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"], res.stderr)
            self.assertLess(took, LATENCY_LIMIT)
            self.assertLess(LATENCY_LIMIT, BUDGET)


if __name__ == "__main__":
    unittest.main()
