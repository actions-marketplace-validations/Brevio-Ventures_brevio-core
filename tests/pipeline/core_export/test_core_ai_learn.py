"""``brevio-core ai learn``: a model's patterns for a guard, kept only if the checks of ``learn`` pass (preview; the 0.7 set,
improvement plan, 2026-11-12).

Standard library only: the endpoint is a fake one on a loopback port, the repository is a real one in a temporary folder with
a bug and its fix as two commits, and the settings file is one in a temporary place. Nothing here reaches another machine.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest

from brevio.pipeline import core_ai_learn as learn
from brevio.pipeline import core_ai_state as st
from brevio.pipeline import core_ai_text as words
from brevio.pipeline import core_cli
from tests.pipeline.core_export.test_core_ai import HTTPS, Answers, Endpoint, Project, fenced

BUG = "  const result = gateway.submit(card, amount, 0);"
FIXED = "  const result = gateway.submit(card, amount, DEFAULT_TIMEOUT_MS);"
PAY = ["export function charge(card: string, amount: number) {", "{line}", "  return result;", "}"]
IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false")
SPECIFIC = r"gateway\.submit\(card, amount, 0\)"            # hits the removed line, not the fixed file
TOO_BROAD = r"gateway\.submit\(card, amount, \w+\)"         # hits the fixed file too
NESTED = "(a+)+$"
NO_HIT = r"gateway\.refund\(card, amount\)"                 # hits neither


def reply(*patterns):
    return fenced(list(patterns), lead="Patterns:")


class Learning(Project):
    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        self.put("pay.ts", [x.replace("{line}", BUG) for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "first")
        self.put("pay.ts", [x.replace("{line}", FIXED) for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "fix: the call had no timeout")
        self.ep = Endpoint(self, replies=(reply(SPECIFIC, TOO_BROAD, NESTED),))
        self.connect(self.ep, "--use", "explain,fix,learn")
        self.agree("learn")

    def git(self, *argv):
        subprocess.run(["git", "-C", self.repo, *argv], check=True, capture_output=True, timeout=60)

    def guards(self):
        path = os.path.join(self.repo, ".brevio", "guards.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["guards"]

    def learn(self, *extra, ask=None):
        return self.run_ai("learn", *extra, ask=ask)

    def only_guard(self):
        found = self.guards()
        self.assertEqual(len(found), 1, found)
        return next(iter(found.values()))


class TestWhatIsSent(Learning):
    def test_the_request_holds_the_removed_line_as_written_and_the_extension_and_no_file_name(self):
        code, out, err = self.learn("--dry-run")
        self.assertEqual((code, self.ep.bodies, self.guards()), (0, [], {}), err)
        self.assertIn("gateway.submit(card, amount, 0)", out)
        self.assertIn("A fix to a .ts file removed the lines below", out)
        self.assertNotIn("pay.ts", out)
        self.assertNotIn("DEFAULT_TIMEOUT_MS", out)         # the fixed side stays here
        self.assertIn("--dry-run: nothing was sent.", out)

    def test_whitespace_is_folded_in_what_is_sent_because_that_is_how_patterns_are_matched(self):
        self.put("pay.ts", [x.replace("{line}", "    const   result =  gateway.submit(card, amount, 0);") for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "again")
        self.put("pay.ts", [x.replace("{line}", FIXED) for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "fix again")
        out = self.learn("--dry-run")[1]
        self.assertIn("const result = gateway.submit(card, amount, 0);", out)

    def test_a_removed_line_over_the_cap_is_left_out_and_when_all_are_it_stops(self):
        long_line = "  const result = gateway.submit(card, amount, 0, " + "x" * 220 + ");"
        self.put("pay.ts", [x.replace("{line}", long_line) for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "long")
        self.put("pay.ts", [x.replace("{line}", FIXED) for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "fix long")
        code, _out, err = self.learn("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn(f"over {learn.MAX_CHARS} characters", err)

    def test_the_first_use_text_names_the_numbers_of_the_code_and_says_no_file_name_goes(self):
        cfg = st.load(self.cfg_path)
        st.save(cfg._replace(repos={}))
        ask = Answers("y", "y", "n")
        code, out, err = self.learn(ask=ask)
        self.assertEqual(code, 3, err)
        self.assertEqual(ask.asked[0], "Agree to ai learn in this repository? [y/N] ")
        self.assertIn(f"at most {learn.MAX_LINES} lines, none longer than {learn.MAX_CHARS} characters", out)
        self.assertIn("No file name and no path", out)
        self.assertIn("`learn` takes no --yes", out)
        self.assertEqual(out.count("First use of brevio-core ai learn"), 1)
        self.assertEqual(words.first_use("learn", "endpoint", *learn.numbers()) in out, True)


class TestTheFlowAndWhatIsKept(Learning):
    def test_only_the_pattern_that_passes_every_check_is_kept_and_the_others_are_refused_with_their_reasons(self):
        ask = Answers("y", "y")
        code, out, err = self.learn(ask=ask)
        self.assertEqual(code, 0, err)
        self.assertEqual(ask.asked[0], "Send it? [y/N] ")
        self.assertTrue(ask.asked[1].startswith("Keep this guard on trial?"))
        self.assertIn("accepted 1, refused 2:", out)
        self.assertIn("pattern 2: it also matches 1 line(s) of the fixed file", out)
        self.assertIn("pattern 3: a group that holds a repeat or an alternative is repeated", out)
        self.assertIn("replay: would have flagged 1 of 2 past change(s)", out)
        guard = self.only_guard()
        self.assertEqual(guard["patterns"], ["re:" + SPECIFIC])
        self.assertEqual((guard["status"], guard["origin"]), ("trial", "model-proposed"))
        self.assertTrue(guard["title"].endswith("(model-proposed)"))
        self.assertEqual(guard["replay"]["flagged"], 1)
        with open(os.path.join(self.repo, guard["rule_file"]), encoding="utf-8") as fh:
            rule = fh.read()
        self.assertIn("gateway.submit(card, amount, 0)", rule)

    def test_the_kept_guard_is_on_trial_like_a_hand_made_one_it_reports_a_returning_line_and_does_not_fail_the_run(self):
        self.learn(ask=Answers("y", "y"))
        self.put("pay.ts", [x.replace("{line}", BUG) for x in PAY])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = core_cli.main(["scan", "--diff", "HEAD"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("guard:" + self.only_guard()["id"], out.getvalue() + err.getvalue())

    def test_the_guard_is_not_written_without_the_second_yes(self):
        code, out, _err = self.learn(ask=Answers("y", "n"))
        self.assertEqual(code, 3)
        self.assertIn("not written", out)
        self.assertEqual(self.guards(), {})
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".agents", "rules")))

    def test_it_takes_no_yes_and_needs_a_terminal_and_sends_nothing_without_one(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            self.learn("--yes")
        self.assertEqual(cm.exception.code, 2)
        code, _out, err = self.learn()
        self.assertEqual(code, 3)
        self.assertIn("needs a person at a terminal", err)
        self.assertEqual((self.ep.bodies, self.guards()), ([], {}))

    def test_a_pattern_that_hits_neither_side_or_only_the_fixed_side_is_refused_and_nothing_is_kept(self):
        self.ep.replies[:] = [reply(NO_HIT, TOO_BROAD)]
        code, out, _err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 1)
        self.assertIn("accepted 0, refused 2:", out)
        self.assertIn("does not match the lines the fix removed", out)
        self.assertIn("(none was accepted)", out)
        self.assertEqual(self.guards(), {})

    def test_a_reply_with_no_fenced_block_or_with_more_than_three_patterns_is_refused_whole_and_not_asked_again(self):
        for text in ("no block here", reply(*[f"gateway\\.submit\\(card, amount, {n}\\)" for n in range(4)]), reply()):
            self.ep.replies[:] = [text]
            self.ep.bodies.clear()
            code, out, _err = self.learn(ask=Answers("y", "y"))
            self.assertEqual(code, 1, text)
            self.assertIn("nothing was written", out)
            self.assertEqual((len(self.ep.bodies), self.guards()), (1, {}))

    def test_a_pattern_of_the_slow_kinds_is_refused_before_it_is_ever_matched(self):
        self.ep.replies[:] = [reply(r"(a|aa)+b$", r"(\w+\s?)*end_of_it", r"foo(bar)\1baz")]
        code, out, _err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 1)
        self.assertEqual(out.count("a group that holds a repeat"), 2)
        self.assertIn("a backreference or a numbered escape", out)

    def test_a_pattern_whose_bounded_repeats_multiply_into_a_slow_match_is_refused_before_it_is_ever_matched(self):
        slow = r"gateway.{0,50}\(.{0,50}\(.{0,50},.{0,50}, .{0,50}\)"        # review 2026-11-12: this took minutes on a long line
        self.ep.replies[:] = [reply(slow, SPECIFIC)]
        code, out, _err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 0)
        self.assertIn("pattern 1: its repeats and alternatives can be tried in too many ways on one line", out)
        self.assertEqual(self.only_guard()["patterns"], ["re:" + SPECIFIC])

    def test_a_ref_that_starts_with_a_hyphen_is_refused_and_the_diff_tool_is_never_given_it_as_an_option(self):
        marker = os.path.join(self.repo, "written-by-the-diff-tool.txt")
        code, _out, err = self.learn(f"--diff=--output={marker}", "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("starts with a hyphen", err)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(self.ep.bodies, [])

    def test_a_pattern_that_looks_like_a_credential_is_refused_and_is_not_echoed_in_the_reasons(self):
        secret = 'gateway\\.submit\\(card, amount, 0\\); password = "' + "x" * 8 + '"'
        self.ep.replies[:] = [reply(secret, SPECIFIC)]
        code, out, _err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 0)
        self.assertIn("pattern 1: a pattern looks like a credential, and guards are committed", out)
        self.assertNotIn("x" * 8, out.split("accepted 1, refused 1:")[1].split("replay")[0])
        self.assertEqual(self.only_guard()["patterns"], ["re:" + SPECIFIC])

    def test_a_removed_line_that_looks_like_a_credential_but_not_like_a_secret_stops_the_call(self):
        self.put("pay.ts", [x.replace("{line}", '  const connect = open("postgres://user:hunter22' + "@" + 'db.example.test/x");') for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "url with a password")
        self.put("pay.ts", [x.replace("{line}", "  const connect = open(process.env.DB);") for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "no password")
        self.ep.bodies.clear()
        code, out, err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 3)
        self.assertNotIn("hunter22", out + err)
        self.assertEqual((self.ep.bodies, self.guards()), ([], {}))

    def test_an_existing_guard_of_the_same_name_is_not_overwritten_and_nothing_is_sent(self):
        self.learn(ask=Answers("y", "y"))
        self.ep.bodies.clear()
        code, _out, err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 1)
        self.assertIn("learn does not overwrite a guard", err)
        self.assertEqual(self.ep.bodies, [])


class TestTheSameChecksAsTheOtherCommands(Learning):
    def test_it_is_off_until_it_is_turned_on_and_a_repository_can_keep_it_off(self):
        self.connect(self.ep)           # explain and fix only
        code, _out, err = self.learn(ask=Answers("y", "y"))
        self.assertEqual((code, "learn is off" in err), (3, True))
        self.connect(self.ep, "--use", "learn")
        self.put("brevio-core.toml", ["[ai]", 'allow = ["explain"]'])
        code, _out, err = self.learn(ask=Answers("y", "y"))
        self.assertEqual((code, "allows only: explain" in err), (3, True))
        self.assertEqual(self.ep.bodies, [])

    def test_a_repository_can_deny_the_path_of_the_fix_and_nothing_is_sent(self):
        self.put("brevio-core.toml", ["[ai]", 'deny_paths = ["pay.ts"]'])
        code, _out, err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 3)
        self.assertIn("denies its path", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_removed_line_that_looks_like_a_secret_stops_the_call_and_the_line_is_not_shown(self):
        key = "AKIA" + "IOSFODNN7EXAMPLE"
        self.put("pay.ts", [x.replace("{line}", f"  const apiKey = '{key}';") for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "key")
        self.put("pay.ts", [x.replace("{line}", "  const apiKey = process.env.KEY;") for x in PAY])
        self.git("add", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "no key")
        code, out, err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 3)
        self.assertIn("look like a secret", err)
        self.assertNotIn(key, out + err)
        self.assertEqual(self.ep.bodies, [])

    def test_the_file_of_the_fix_that_holds_secrets_is_never_sent(self):
        self.put(".env", [f"TOKEN_ENDPOINT={HTTPS}one.example.test/a", f"TOKEN_ENDPOINT={HTTPS}one.example.test/b"])
        self.git("add", "-f", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "env")
        self.put(".env", [f"TOKEN_ENDPOINT={HTTPS}two.example.test/c", "other"])
        self.git("add", "-f", "-A")
        self.git(*IDENTITY, "commit", "-q", "-m", "env fix")
        code, _out, err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 3)
        self.assertIn("holds secrets", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_limit_of_one_call_a_day_stops_the_second_ask(self):
        self.limits(max_calls_per_day=1)
        self.learn(ask=Answers("y", "n"))
        code, _out, err = self.learn(ask=Answers("y", "y"))
        self.assertEqual(code, 3)
        self.assertIn("max_calls_per_day", err)
        self.assertEqual(len(self.ep.bodies), 1)

    def test_the_kill_switch_stops_it(self):
        code, _out, err = self.run_ai("learn", env={**self.env, "BREVIO_AI": "off"}, ask=Answers("y", "y"))
        self.assertEqual(code, 3)
        self.assertIn("off", err)
        self.assertEqual(self.ep.bodies, [])


if __name__ == "__main__":
    unittest.main()
