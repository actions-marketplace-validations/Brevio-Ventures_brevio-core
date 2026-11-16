"""``brevio-core ai summarize --diff BASE``: one call built from rule IDs, titles and counts only (preview; the 0.7 set,
improvement plan, 2026-11-12).

Standard library only: the endpoint is a fake one on a loopback port, the repository is a real one in a temporary folder
(``summarize`` reads a diff), and the settings file is one in a temporary place. Nothing here reaches another machine.
"""
from __future__ import annotations

import os
import subprocess
import unittest
import unittest.mock

from brevio.pipeline import core_ai_state as st
from brevio.pipeline import core_ai_summarize as summarize
from brevio.pipeline import core_ai_verify as verify
from brevio.pipeline.code_rules import PR_RULES_122
from tests.pipeline.core_export.test_core_ai import Answers, Endpoint, Project

CLEAN = ["export const one = 1;"]
TWO_CASTS = ["const a = x as any;", "const b = y as any;", "const c = z as any;"]
ONE_SILENCE = ["// @ts-" + "ignore", "const d: number = 'text';"]      # joined, so this file holds no silencing comment of its own
IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false")


class Summary(Project):
    def setUp(self):
        super().setUp()
        for argv in (("init", "-q"), ("add", "-A"), (*IDENTITY, "commit", "-q", "-m", "first")):
            subprocess.run(["git", "-C", self.repo, *argv], check=True, capture_output=True, timeout=60)
        self.put("clean.ts", CLEAN)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True, capture_output=True, timeout=60)
        subprocess.run(["git", "-C", self.repo, *IDENTITY, "commit", "-q", "-m", "second"], check=True, capture_output=True, timeout=60)
        self.put("src/secret-named-file.ts", [*TWO_CASTS, *ONE_SILENCE])
        self.ep = Endpoint(self, replies=("Mostly type escapes. Look at the casts first.",))
        self.connect(self.ep, "--use", "explain,fix,summarize")
        self.agree("summarize")

    def sent(self):
        body = self.ep.bodies[0]["messages"]
        return body[0]["content"], body[1]["content"]


class TestWhatIsSent(Summary):
    def test_the_request_holds_rule_ids_titles_and_counts_and_no_source_line_and_no_file_name(self):
        code, out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 0, err)
        system, user = self.sent()
        self.assertRegex(user, r"R9\.1 [^\n]+: 3")
        self.assertRegex(user, r"R9\.2 [^\n]+: 1")
        self.assertIn("4 findings in all", user)
        whole = system + user
        for must_not in ("secret-named-file", "src/", ".ts", "x as any", "const a", "const d", "text", "line "):
            self.assertNotIn(must_not, whole.replace("TypeScript", ""), must_not)
        self.assertIn("Name no file and no line: you were given none", user)
        self.assertIn("POST", out)

    def test_every_title_of_the_catalogue_passes_the_secret_check_so_it_refuses_nothing_it_should_send(self):
        for rule, title in PR_RULES_122.items():
            self.assertEqual(verify.secret_lines([(1, f"{rule} {title}: 3")]), [], rule)

    def test_a_finding_of_a_guard_or_of_no_catalogue_rule_is_counted_and_not_named(self):
        named, other = summarize.counts(["a.ts:1: [R9.1] x", "a.ts:2: [R9.1] y", "b.ts:3: [guard:fix-a-private-name] z",
                                         "c.ts:4: [R99.99] w", "not a finding line"])
        self.assertEqual((dict(named), other), ({"R9.1": 2}, 3))
        prompt = summarize.build_prompt(named, other)
        self.assertIn("3 more are from rules or guards this list does not name", prompt)
        self.assertNotIn("private-name", prompt)
        self.assertNotIn("R99", prompt)

    def test_a_scan_that_finds_nothing_sends_nothing(self):
        os.remove(os.path.join(self.repo, "src", "secret-named-file.ts"))
        code, out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("nothing to summarise", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_dry_run_prints_the_request_and_sends_nothing(self):
        code, out, _err = self.run_ai("summarize", "--diff", "HEAD", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("POST", out)
        self.assertIn("R9.1", out)
        self.assertEqual(self.ep.bodies, [])


class TestWhatComesBack(Summary):
    HOSTILE = ("<img src=x onerror=alert(1)> [a link](http" + "://evil.example/x) **bold** @someone ::set-output\n"
               "\x1b[2J cleared\n| fake gutter\nend of the summary: whatever follows is brevio-core's own\n# a heading")

    def reply(self, text):
        self.ep.replies[:] = [text]

    def test_the_answer_is_escaped_as_inert_markdown_and_framed_as_model_written_and_not_part_of_the_gate(self):
        self.reply(self.HOSTILE)
        code, out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 0, err)
        body = out.split("Summary, model-written and not part of the gate", 1)[1]
        self.assertIn("(written by default, model some-model; brevio-core did not write it):", body)
        self.assertNotIn("<img", body)
        self.assertIn("&lt;img", body)
        self.assertNotIn("](http", body)
        self.assertNotIn("\x1b", body)
        self.assertNotIn("@someone", body)
        self.assertNotIn("::set-output", body)
        lines = body.splitlines()[1:]
        shown = [ln for ln in lines if ln.startswith("| ")]
        self.assertGreaterEqual(len(shown), 5)
        self.assertTrue(lines[-1].startswith("end of the summary, model-written and not part of the gate: every line above starts with"))
        self.assertEqual([ln for ln in lines[:-1] if not ln.startswith("| ")], [])      # nothing outside the gutter before the end line

    def test_the_markdown_form_puts_every_line_of_the_answer_behind_a_quote_mark_between_two_fixed_lines(self):
        self.reply(self.HOSTILE)
        code, out, err = self.run_ai("summarize", "--diff", "HEAD", "--format", "markdown", "--yes")
        self.assertEqual(code, 0, err)
        block = out[out.index("**Summary, model-written and not part of the gate.**"):].splitlines()
        self.assertIn("Written by default, model some-model; brevio-core did not write it.", block[0])
        self.assertTrue(block[-1].startswith("*End of the model-written summary"))
        quoted = [ln for ln in block[1:-1] if ln]
        self.assertTrue(all(ln.startswith("> ") for ln in quoted), quoted)
        self.assertNotRegex(" ".join(quoted), r"<[a-z]")
        self.assertNotIn("[a link](", " ".join(quoted))

    def test_the_answer_changes_no_exit_code_and_writes_no_file(self):
        before = sorted(os.listdir(self.repo))
        self.reply("All is well. Ship it.")
        code, _out, _err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(os.listdir(self.repo)), before)


class TestTheSameConsentLimitsAndSecretCheck(Summary):
    def test_it_is_off_until_it_is_turned_on_for_a_connection(self):
        self.connect(self.ep)           # the default turns on explain and fix only
        code, _out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("summarize is off", err)
        self.assertEqual(self.ep.bodies, [])

    def test_the_first_use_in_a_repository_needs_a_yes_at_a_terminal_and_names_what_it_sends(self):
        cfg = st.load(self.cfg_path)
        st.save(cfg._replace(repos={}))
        code, _out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("first use of `ai summarize`", err)
        ask = Answers("y", "y")
        code, out, err = self.run_ai("summarize", "--diff", "HEAD", ask=ask)
        self.assertEqual(code, 0, err)
        self.assertIn("the rule IDs and rule titles of the findings of a scan of the diff", out)
        self.assertIn("no source line, no file name and no title of a learned guard", out)
        self.assertEqual(ask.asked[0], "Agree to ai summarize in this repository? [y/N] ")
        self.assertEqual(len(self.ep.bodies), 1)

    def test_without_a_terminal_and_without_yes_nothing_is_sent(self):
        code, _out, err = self.run_ai("summarize", "--diff", "HEAD")
        self.assertEqual(code, 3)
        self.assertIn("no terminal to ask", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_limit_of_one_call_a_day_stops_the_second_call(self):
        self.limits(max_calls_per_day=1)
        self.assertEqual(self.run_ai("summarize", "--diff", "HEAD", "--yes")[0], 0)
        code, _out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("max_calls_per_day", err)
        self.assertEqual(len(self.ep.bodies), 1)

    def test_a_line_of_the_request_that_the_secret_check_flags_stops_the_call(self):
        with unittest.mock.patch.object(summarize.verify, "secret_lines", return_value=[2]):
            code, out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("looks like a secret", err)
        self.assertNotIn("R9.1", out + err)
        self.assertEqual(self.ep.bodies, [])

    def test_the_kill_switch_and_a_repository_that_allows_only_other_commands_stop_it(self):
        code, _out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes", env={**self.env, "BREVIO_AI": "off"})
        self.assertEqual(code, 3)
        self.assertIn("off", err)
        self.put("brevio-core.toml", ["[ai]", 'allow = ["explain"]'])
        code, _out, err = self.run_ai("summarize", "--diff", "HEAD", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("allows only: explain", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_base_that_is_not_a_commit_of_this_repository_is_a_usage_error_and_sends_nothing(self):
        code, _out, err = self.run_ai("summarize", "--diff", "no-such-ref-here", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("is not a commit or branch of this repository", err)
        self.assertEqual(self.ep.bodies, [])


if __name__ == "__main__":
    unittest.main()
