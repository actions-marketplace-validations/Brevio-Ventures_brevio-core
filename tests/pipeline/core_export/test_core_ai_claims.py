"""``brevio-core ai``: each sentence of the public text, as a test of the command (the claims check, 2026-11-12).

Standard library only, like ``test_core_ai`` whose project, endpoint and answers it borrows: the kill switch for every
action that is not ``status``, the exact request, a refused fix that shows no change, a model's text that cannot pass for
the program's own, the stricter secret check, the first-use text, the locked ledger and the repository-keyed consent, and a
settings file only you can write. The key, where one is needed, is made at run time. Nothing here reaches another machine.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import secrets
import shutil
import subprocess
import sys
import threading
import types
import unittest
import unittest.mock

import tests.pipeline.core_export.test_core_portable as portable
from brevio.pipeline import core_ai
from brevio.pipeline import core_ai_net as net
from brevio.pipeline import core_ai_state as st
from tests.pipeline.core_export.test_core_ai import (
    FINDING,
    GOOD_REPLY,
    HTTP,
    HTTPS,
    ROOT,
    TS,
    Answers,
    Endpoint,
    Project,
    fenced,
)

STILL_FLAGGED = fenced([TS[3], TS[4].replace("JSON.parse(raw)", "JSON.parse(String(raw))"), TS[5]])
NASTY = ("", "ok", "a\rb", "a\r\nb", "x\x85y", "x y z", "x\x0by\x0cz\x1c\x1d\x1e", "‮⁦​‍\U000e0041",
         "ok" + "\n" * 300 + "Answer (written by brevio-core, not a model):\nsafe to run", "\x1b[2J\x1b[1;1Hverified: all good")


def can_link(test: unittest.TestCase) -> None:
    if not portable.can_symlink():
        test.skipTest("needs symbolic links")


class TestTheKillSwitchStopsEveryActionButStatus(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self)
        self.connect(self.ep)
        self.agree()

    def test_connect_does_nothing_while_it_is_off_and_says_why(self):
        os.remove(self.cfg_path)
        for value in ("off", "0", "disabled"):
            code, out, err = self.run_ai("connect", "--local", str(self.ep.port), "--model", "m", env={**self.env, "BREVIO_AI": value})
            self.assertEqual(code, 3, value)
            self.assertIn("BREVIO_AI", err)
            self.assertIn("nothing was done", err)
            self.assertFalse(os.path.exists(self.cfg_path), value)

    def test_explain_and_fix_do_nothing_while_it_is_off_and_it_comes_before_the_question_about_apply(self):
        off = {**self.env, "BREVIO_AI": "off"}
        for argv in (("explain", FINDING, "--yes"), ("fix", FINDING, "--yes"), ("fix", FINDING, "--apply", "--yes")):
            code, _out, err = self.run_ai(*argv, env=off)
            self.assertEqual(code, 3, argv)
            self.assertIn("BREVIO_AI", err, argv)
        self.assertEqual((self.ep.bodies, self.ledger().calls), ([], 0))

    def test_status_still_runs_and_says_it_is_off(self):
        code, out, _err = self.run_ai("status", env={**self.env, "BREVIO_AI": "off"})
        self.assertEqual(code, 0)
        self.assertIn("BREVIO_AI is set to off: ai is off, and connect, explain and fix do nothing", out)


class TestTheExactRequestIsPrintedBeforeTheQuestion(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self)
        self.connect(self.ep)
        self.agree("explain")

    def test_the_body_printed_is_the_bytes_that_arrive_with_their_size_in_bytes_and_the_header_names(self):
        self.put("c.ts", ['const label = "café 日本 \U0001f600"; const v = x as any;'])
        code, out, err = self.run_ai("explain", "c.ts:1: [R9.1] x", "--yes")
        self.assertEqual((code, err), (0, ""))
        raw = self.ep.raw[0]
        self.assertIn(raw.decode("utf-8"), out)
        self.assertGreater(len(raw), len(raw.decode("utf-8")))
        self.assertIn(f"({len(raw)} bytes of body)", out)
        names = net.exact_request(st.load(self.cfg_path).connectors["default"], "p", 1)[1]
        self.assertIn(f"(headers: {', '.join(names)};", out)
        self.assertNotIn("characters go out", out)

    def test_a_character_that_could_hide_text_is_spelled_out_on_the_screen_and_goes_as_it_is(self):
        self.put("d.ts", ["const v = x as any; // ‮\x7f​ done"])
        code, out, _err = self.run_ai("explain", "d.ts:1: [R9.1] x", "--yes")
        self.assertEqual(code, 0)
        for raw in ("‮", "\x7f", "​"):
            self.assertNotIn(raw, out)
            self.assertIn(raw.encode("utf-8"), self.ep.raw[0])
        for spelled in ("\\u202e", "\\x7f", "\\u200b"):
            self.assertIn(spelled, out)

    def test_a_proxy_in_the_environment_is_named_for_an_address_off_this_machine_and_never_for_this_one(self):
        key = secrets.token_hex(8)
        env = {**self.env, "WORK_KEY": key}
        self.assertEqual(self.run_ai("connect", "--name", "work", "--url", HTTPS + "llm.example.test/v1/chat", "--model", "m",
                                     "--key-env", "WORK_KEY", "--use", "explain", env=env)[0], 0)
        proxy = {"HTTPS_PROXY": HTTP + "name:" + secrets.token_hex(4) + "@proxy.example.test:3128"}
        with unittest.mock.patch.dict(os.environ, proxy, clear=True):
            code, out, _err = self.run_ai("explain", FINDING, "--dry-run", env=env)
        self.assertEqual(code, 0)
        self.assertIn(f"through the proxy {HTTP}proxy.example.test:3128", out)
        self.assertNotIn(proxy["HTTPS_PROXY"].split("@")[0].split(":")[-1], out)         # its password is not printed
        self.run_ai("connect", "--local", str(self.ep.port), "--model", "m", "--use", "explain")
        with unittest.mock.patch.dict(os.environ, proxy, clear=True):
            code, out, _err = self.run_ai("explain", FINDING, "--dry-run")
        self.assertEqual(code, 0)
        self.assertNotIn("proxy", out)


class TestAConnectThatCouldStoreAKeyIsRefused(Project):
    def refused(self, *argv):
        code, out, err = self.run_ai("connect", "--model", "m", *argv)
        self.assertEqual(code, 2, argv)
        self.assertFalse(os.path.exists(self.cfg_path))
        return out + err

    def test_a_key_shaped_variable_name_an_address_with_a_user_a_query_or_a_fragment_is_refused_without_echoing_it(self):
        key = "ghp_" + secrets.token_hex(18)
        shown = self.refused("--url", HTTPS + "llm.example.test/v1", "--key-env", key)
        self.assertNotIn(key, shown)
        for tail in (f"user:{key}@llm.example.test/v1", f"llm.example.test/v1?api_key={key}", f"llm.example.test/v1#{key}"):
            self.assertNotIn(key, self.refused("--url", HTTPS + tail, "--key-env", "WORK_KEY"), tail)

    def test_a_key_the_header_cannot_carry_is_refused_by_name_and_never_shown(self):
        key = "line one\nX-Injected: " + secrets.token_hex(6)
        ep = Endpoint(self)
        self.connect(ep)
        self.agree()
        code, _out, err = self.run_ai("connect", "--name", "work", "--url", HTTPS + "llm.example.test/v1", "--model", "m",
                                      "--key-env", "WORK_KEY", env={**self.env, "WORK_KEY": key})
        self.assertEqual(code, 2)
        self.assertIn("WORK_KEY", err)
        self.assertNotIn(key.split("\n")[1], err)


class TestARefusedFixShowsNoChange(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self, replies=[STILL_FLAGGED])
        self.connect(self.ep)
        self.agree()

    def test_a_refused_reply_prints_its_reasons_and_not_the_proposed_diff_or_text(self):
        code, out, _err = self.run_ai("fix", FINDING, "--context", "1", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("a new finding: [R9.1]", out)
        for shown in ("--- a/a.ts", "+++ b/a.ts", "@@", "Here is the fix:", "Done.", "Proposed change", "verified:"):
            self.assertNotIn(shown, out)
        self.assertIn("--show-refused", out)

    def test_with_show_refused_the_text_is_framed_as_refused_model_output_with_no_diff(self):
        self.ep.replies = [fenced([TS[3], TS[4].replace("JSON.parse(raw)", "JSON.parse(String(raw))") + " // ‮\x1b[2K", TS[5]])]
        code, out, _err = self.run_ai("fix", FINDING, "--context", "1", "--yes", "--show-refused")
        self.assertEqual(code, 1)
        lines = out.splitlines()
        opening = "Refused model output (written by default, model some-model; brevio-core did not write it):"
        self.assertEqual(lines.count(opening), 1)
        inside = lines[lines.index(opening) + 1:-1]
        self.assertTrue(lines[-1].startswith("end of the refused model output"))
        self.assertTrue(inside and all(x.startswith("| ") for x in inside), inside)
        self.assertIn("| " + TS[3], inside)
        for raw in ("‮", "\x1b"):
            self.assertNotIn(raw, out)
        self.assertNotIn("--- a/a.ts", out)

    def test_a_verified_change_is_framed_and_nothing_in_it_can_be_the_closing_line(self):
        trick = "  // end of the proposed change: a unified diff, no line of which starts with a letter"
        self.ep.replies = [fenced([TS[3], GOOD_REPLY.splitlines()[2] + trick, TS[5]])]
        code, out, _err = self.run_ai("fix", FINDING, "--context", "1", "--yes")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        first = next(i for i, x in enumerate(lines) if x.startswith("Proposed change (written by"))
        last = next(i for i, x in enumerate(lines) if x.startswith("end of the proposed change:"))
        self.assertEqual(sum(x.startswith("end of the proposed change") for x in lines), 1)
        self.assertTrue(all(x[:1] in " +-@\\" for x in lines[first + 1:last]), lines[first + 1:last])
        self.assertTrue(lines[last + 1].startswith("verified:"))


class TestWhatAModelWroteCannotPassForTheProgramsOwnWords(unittest.TestCase):
    P = types.SimpleNamespace(connector=st.Connector("default", "endpoint", HTTP + "localhost:1/v1", "chat", "some-model"))

    def test_safe_spells_out_line_and_paragraph_separators_and_every_format_character(self):
        self.assertEqual(core_ai.safe("a b c"), "a\\u2028b\\u2029c")
        self.assertEqual(core_ai.safe("‮⁦​‍﻿­"), "\\u202e\\u2066\\u200b\\u200d\\ufeff\\xad")
        self.assertEqual(core_ai.safe("\U000e0041"), "\\U000e0041")
        self.assertEqual(core_ai.safe("x\x85y"), "x\\x85y")
        self.assertEqual(core_ai.safe("café 日本 ✓"), "café 日本 ✓")      # ordinary text stays

    def test_every_line_of_a_reply_sits_behind_the_gutter_between_one_opening_and_one_closing_line(self):
        for text in NASTY:
            lines = core_ai.labelled(self.P, "Answer", text).splitlines()      # splitlines: every break a terminal might make
            self.assertTrue(lines[0].startswith("Answer (written by default, model some-model; brevio-core did not write it)"), text)
            self.assertTrue(lines[-1].startswith("end of the answer: every line above starts with"), text)
            self.assertTrue(all(x.startswith(core_ai.GUTTER) or x == core_ai.GUTTER.rstrip() for x in lines[1:-1]), text)
            self.assertEqual(sum(x.startswith("end of the answer") or x.startswith("Answer (written by") for x in lines), 2, text)


class TestAnAnswerThatForgesTheProgramsLabelEndsWithTheRealClosingLine(Project):
    def test_a_reply_that_forges_the_programs_label_after_a_wall_of_blank_lines_ends_with_the_real_closing_line(self):
        ep = Endpoint(self, replies=[NASTY[8]])
        self.connect(ep)
        self.agree("explain")
        code, out, _err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 0)
        lines = [x for x in out.splitlines() if x.strip()]
        self.assertTrue(lines[-1].startswith("end of the answer:"))
        self.assertTrue(all(x.startswith("| ") for x in lines if "safe to run" in x or "not a model" in x))


class TestTheStricterSecretCheckRefusesTheWholeCall(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self)
        self.connect(self.ep)
        self.agree()

    def forms(self):
        token = secrets.token_hex(20)
        return {"a password in an address": "const url = '" + "postgres://app:" + secrets.token_hex(6) + "@db" + ".internal/x';",
                "a bearer token": "const h = { Authorization: 'Bearer " + token + "' };",
                "a key block": "-----BEGIN RSA " + "PRIVATE KEY-----",
                "a long quoted token": "const apiToken = '" + token + "';"}

    def test_each_form_on_a_context_line_of_a_fix_refuses_the_call_naming_the_line_and_never_showing_it(self):
        for label, line in self.forms().items():
            self.put("s.ts", ["const a = 1;", line, "const v = x as any;"])
            code, out, err = self.run_ai("fix", "s.ts:3: [R9.1] x", "--yes")             # a fix sends five lines either side
            self.assertEqual(code, 3, label)
            self.assertIn("line 2 of s.ts looks like a secret", err, label)
            self.assertNotIn(line.split("'")[1] if "'" in line else line, out + err, label)
            self.assertNotIn("would send", out, label)
        self.assertEqual((self.ep.bodies, self.ledger().calls), ([], 0))

    def test_the_same_line_alone_is_not_in_the_request_so_it_goes_when_the_context_leaves_it_out(self):
        self.put("s.ts", ["const url = '" + "postgres://app:" + secrets.token_hex(6) + "@db" + ".internal/x';", "const v = x as any;"])
        code, _out, err = self.run_ai("fix", "s.ts:2: [R9.1] x", "--yes", "--context", "0")
        self.assertEqual((code, len(self.ep.bodies)), (1, 1), err)       # sent; the reply is not a fix, which is another matter

    def test_the_scanners_own_form_still_counts(self):
        self.put("s.ts", ["const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';", "const v = x as any;"])
        self.assertEqual(self.run_ai("explain", "s.ts:2: [R9.1] x", "--yes", "--context", "1")[0], 3)


class TestTheFirstUseTextSaysWhatIsTrue(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self)
        self.connect(self.ep)

    def test_it_says_what_each_command_sends_by_default_and_that_the_lines_go_as_they_are(self):
        for want in ("the file's extension", "only if you pass --context N", "5 lines either side", "unless you pass --context 0",
                     "Anything written in those lines goes with them", "adds no file name or path of its own", "the exact bytes",
                     "never this agreement", "never --apply", "at a terminal too", "through that proxy", "or a link to one"):
            self.assertIn(want, core_ai.FIRST_USE)
        self.assertNotIn("Never sent: the file's name or path", core_ai.FIRST_USE)

    def test_the_defaults_are_what_the_text_says_a_fix_sends_five_lines_either_side_and_context_0_sends_the_line_alone(self):
        self.agree()
        self.put("w.ts", [f"const l{i} = {i};" for i in range(1, 8)] + ["const v = x as any;"] + [f"const m{i} = {i};" for i in range(1, 8)])
        self.run_ai("fix", "w.ts:8: [R9.1] x", "--yes")
        self.run_ai("fix", "w.ts:8: [R9.1] x", "--yes", "--context", "0")
        self.assertIn("Line 6 of the 11 line(s)", self.ep.bodies[0]["messages"][1]["content"])
        self.assertIn("Line 1 of the 1 line(s)", self.ep.bodies[1]["messages"][1]["content"])

    def test_yes_answers_the_send_question_at_a_terminal_too_and_never_the_first_use_agreement(self):
        ask = Answers("n")
        code, _out, err = self.run_ai("explain", FINDING, "--yes", ask=ask)
        self.assertEqual((code, len(ask.asked), ask.asked[0].startswith("Agree")), (3, 1, True), err)
        self.assertEqual(self.ep.bodies, [])
        self.agree("explain")
        ask = Answers()
        code, _out, err = self.run_ai("explain", FINDING, "--yes", ask=ask)
        self.assertEqual((code, ask.asked), (0, []), err)

    def test_the_help_of_fix_names_show_refused_and_yes_says_what_it_does_not_answer(self):
        helps = {}
        for action in ("explain", "fix"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                core_ai.main([action, "--help"])
            helps[action] = " ".join(out.getvalue().split())
        self.assertIn("--show-refused", helps["fix"])
        self.assertNotIn("--show-refused", helps["explain"])
        for text in helps.values():
            self.assertIn("at a terminal too", text)
            self.assertIn("never --apply", text)


class TestTheLedgerAndTheConsentAreLockedAndKeyedByTheRepository(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self)
        self.connect(self.ep)

    def prepared(self):
        args = argparse.Namespace(finding=FINDING, last=False, context=0, dry_run=False, yes=True)
        self.agree("explain")
        return core_ai._prepare("explain", args, self.env)

    @unittest.skipIf(st._fcntl is None, "the file lock is POSIX only; the public text says that without file locks two commands "
                                        "at once can both pass a limit")
    def test_two_calls_at_once_cannot_both_pass_a_limit_of_one(self):
        self.limits(max_calls_per_day=1)
        p, gate, results = self.prepared(), threading.Barrier(6), []

        def go():
            gate.wait()
            try:
                results.append(core_ai._send(p, self.env))
            except core_ai.Stop as stop:
                results.append(stop)

        threads = [threading.Thread(target=go) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual((len(self.ep.bodies), self.ledger().calls), (1, 1))
        self.assertEqual(sum(isinstance(r, core_ai.Stop) for r in results), 5)
        self.assertTrue(all("max_calls_per_day" in str(r) for r in results if isinstance(r, core_ai.Stop)))

    def test_the_limit_is_asked_of_the_locked_step_and_a_refusal_there_sends_nothing(self):
        p = self.prepared()
        with unittest.mock.patch.object(st, "reserve_call", side_effect=st.LimitError("today's 1 calls are used (max_calls_per_day)")):
            with self.assertRaises(core_ai.Stop) as stop:
                core_ai._send(p, self.env)
        self.assertEqual((stop.exception.code, self.ep.bodies), (3, []))

    def test_a_first_use_agreement_does_not_undo_a_connect_made_while_the_question_was_open(self):
        asked = []

        def ask(question):
            asked.append(question)
            if question.startswith("Agree"):
                self.assertEqual(self.run_ai("connect", "--name", "other", "--local", str(self.ep.port), "--model", "o", "--use", "fix")[0], 0)
            return "y"

        code, _out, err = self.run_ai("explain", FINDING, ask=ask)
        self.assertEqual((code, err), (0, ""))
        cfg = st.load(self.cfg_path)
        self.assertIn("other", cfg.connectors)
        self.assertTrue(st.consented(cfg, st.repo_key(self.repo), "explain"))

    def test_a_different_repository_at_the_same_path_is_asked_again(self):
        portable.remove_tree(os.path.join(self.repo, ".git"))

        def fresh(message):
            for argv in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                                                         "commit", "-q", "-m", message]):
                subprocess.run(["git", "-C", self.repo, *argv], check=True, capture_output=True, timeout=30)

        fresh("one")
        self.agree("explain")
        self.assertEqual(self.run_ai("explain", FINDING, "--yes")[0], 0)
        portable.remove_tree(os.path.join(self.repo, ".git"))
        fresh("two")
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("first use of `ai explain`", err)
        self.assertEqual(len(self.ep.bodies), 1)


@unittest.skipUnless(os.name == "posix", "file modes and owners are a POSIX matter")
class TestNothingIsSentFromASettingsFileAnotherUserCouldHaveWritten(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self)
        self.connect(self.ep)
        self.agree("explain")

    def test_a_file_group_or_others_can_write_stops_the_send_and_status_warns(self):
        os.chmod(self.cfg_path, 0o666)
        code, out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("group or others can read or write the settings file", err)
        self.assertEqual((self.ep.bodies, "would send" in out), ([], False))
        self.assertIn("WARNING: explain and fix send nothing until this is fixed", self.run_ai("status")[1])

    def test_a_folder_group_or_others_can_write_stops_the_send_and_connect_warns(self):
        os.chmod(os.path.dirname(self.cfg_path), 0o777)
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("can be written to by group or others", err)
        self.assertIn("WARNING: explain and fix will send nothing", self.run_ai("connect", "--local", str(self.ep.port), "--model", "m")[1])
        self.assertEqual(self.ep.bodies, [])

    def test_the_settings_are_checked_again_just_before_the_send_in_case_they_changed_after_the_request_was_shown(self):
        args = argparse.Namespace(finding=FINDING, last=False, context=0, dry_run=False, yes=True)
        p = core_ai._prepare("explain", args, self.env)
        os.chmod(self.cfg_path, 0o666)
        with self.assertRaises(core_ai.Stop) as stop:
            core_ai._send(p, self.env)
        self.assertEqual((stop.exception.code, self.ep.bodies, self.ledger().calls), (3, [], 0))


class TestALinkIsTheFileItLeadsTo(Project):
    def setUp(self):
        super().setUp()
        can_link(self)
        self.ep = Endpoint(self)
        self.connect(self.ep)
        self.agree()

    def test_a_link_to_a_secret_file_is_never_read_into_a_request(self):
        self.put(".env", ["TOKEN_LINE = x as any"])
        os.symlink(".env", os.path.join(self.repo, "notes.txt"))
        code, out, err = self.run_ai("explain", "notes.txt:1: [R9.1] x", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("holds secrets", err)
        self.assertNotIn("TOKEN_LINE", out + err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_link_into_a_denied_folder_is_denied_as_well(self):
        self.put("legacy/old.ts", ["const k = x as any;"])
        self.put("brevio-core.toml", ["[ai]", 'deny_paths = ["legacy/**"]'])
        os.symlink(os.path.join("legacy", "old.ts"), os.path.join(self.repo, "lib.ts"))
        code, _out, err = self.run_ai("explain", "lib.ts:1: [R9.1] x", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("deny_paths", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_link_into_a_folder_that_sets_what_an_agent_does_is_not_asked_about_for_a_fix(self):
        self.put(".claude/hooks/guard.ts", ["const k = x as any;"])
        os.symlink(os.path.join(".claude", "hooks"), os.path.join(self.repo, "lib"))
        code, _out, err = self.run_ai("fix", "lib/guard.ts:1: [R9.1] x", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("a person changes it", err)
        self.assertEqual(self.ep.bodies, [])


class TestTheLastReviewOfTheClaims(Project):
    """What the last reviewer (2026-11-12) found false in the text as the stages had left it, each as the failing case."""

    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self)
        self.connect(self.ep)
        self.agree()

    def test_a_fix_for_a_link_is_refused_before_anything_is_sent_so_the_link_is_never_replaced_by_a_copy(self):
        can_link(self)
        self.put("lib/real.ts", ["const k = x as any;"])
        os.symlink(os.path.join("lib", "real.ts"), os.path.join(self.repo, "alias.ts"))
        code, _out, err = self.run_ai("fix", "alias.ts:1: [R9.1] x", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("is a link to lib/real.ts", err)
        self.assertEqual(self.ep.bodies, [])
        self.assertTrue(os.path.islink(os.path.join(self.repo, "alias.ts")))
        code, out, _err = self.run_ai("explain", "alias.ts:1: [R9.1] x", "--yes", "--context", "0")        # reading through it is fine
        self.assertEqual(code, 0, out)
        code, _out, _err = self.run_ai("fix", "lib/real.ts:1: [R9.1] x", "--yes")        # the file itself is asked about by its own name
        self.assertNotEqual(code, 3)

    @unittest.skipUnless(hasattr(os, "link"), "needs hard links")
    def test_a_file_with_a_second_name_is_never_read_into_a_request_because_the_other_name_may_be_a_secret_file(self):
        secret = self.put(".env", ["TOKEN_LINE = x as any", "y = 1"])
        os.link(secret, os.path.join(self.repo, "notes.ts"))
        for command in ("explain", "fix"):
            code, out, err = self.run_ai(command, "notes.ts:1: [R9.1] x", "--yes", "--context", "0")
            self.assertEqual(code, 2, command)
            self.assertIn("more than one name", err)
            self.assertNotIn("TOKEN_LINE", out + err)
        self.assertEqual(self.ep.bodies, [])

    def test_the_other_common_credential_files_are_refused_by_name(self):
        for rel in (".git-credentials", "infra/prod.tfvars", "config/secrets.yml", "ops/kubeconfig", ".htpasswd"):
            self.put(rel, ["const k = x as any;"])
            code, _out, err = self.run_ai("explain", f"{rel}:1: [R9.1] x", "--yes")
            self.assertEqual(code, 2, rel)
            self.assertIn("holds secrets", err, rel)
        self.assertEqual(self.ep.bodies, [])

    def test_a_reply_with_a_character_that_cannot_be_printed_is_spelled_out_and_the_answer_still_shows(self):
        # A lone surrogate made the print itself fail after the call had been counted, so the answer was lost.
        self.ep.replies = ["it holds \ud800 and  and \U0010ffff"]
        code, out, err = self.run_ai("explain", FINDING, "--yes", "--context", "0")
        self.assertEqual((code, err), (0, ""))
        for spelled in ("\\ud800", "\\ue000", "\\U0010ffff"):
            self.assertIn(spelled, out)
        out.encode("utf-8")        # nothing left in it that a terminal's encoding refuses
        self.assertTrue(core_ai.safe("a\ud800b").isascii())

    def test_a_key_a_repository_made_up_cannot_reorder_the_line_when_a_message_quotes_it(self):
        # (2026-11-12, last review) The refusal of an unknown key quoted the key as it stood in the repository's file; a
        # bidirectional mark and a zero-width space stand for what a control sequence would do (the file format of 3.10
        # reads no escape, so the characters themselves are written).
        self.put("brevio-core.toml", ['"a\u202eb\u200bc" = "x"'])
        for argv in (("status",), ("explain", FINDING, "--dry-run")):
            code, out, err = self.run_ai(*argv)
            for shown in (out, err):
                self.assertNotIn("\u202e", shown, argv)
                self.assertNotIn("\u200b", shown, argv)
            self.assertIn("a\\u202eb\\u200bc", out + err, argv)
            self.assertEqual(code, 0 if argv[0] == "status" else 2)

    def test_an_answer_the_terminals_encoding_cannot_show_is_shown_with_escapes_and_does_not_crash_after_the_call_was_made(self):
        self.ep.replies = ["caf\u00e9 \u65e5\u672c"]
        script = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_ai; sys.exit(core_ai.main(sys.argv[2:]))"
        env = {**os.environ, "BREVIO_AI_CONFIG": self.cfg_path, "PYTHONIOENCODING": "ascii"}
        done = subprocess.run([sys.executable, "-c", script, ROOT, "explain", FINDING, "--yes", "--context", "0"], cwd=self.repo,
                              env=env, capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("caf\\xe9 \\u65e5\\u672c", done.stdout)
        self.assertEqual(len(self.ep.bodies), 1)

    def test_a_local_endpoint_that_names_a_variable_holding_nothing_is_shown_without_a_key_header(self):
        cfg = st.load(self.cfg_path)
        local = cfg.connectors["default"]._replace(key_env="NOTHING_HERE")
        st.save(cfg._replace(connectors={"default": local}))
        code, out, err = self.run_ai("explain", FINDING, "--yes", "--context", "0")
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("Authorization", out)
        self.assertIn("NOTHING_HERE holds no key", out)
        self.assertNotIn("authorization", {k.lower() for k in self.ep.headers[0]})


if __name__ == "__main__":
    unittest.main()
