"""The fix verifier of ``brevio-core ai fix``: a model's reply is applied in memory and accepted only when the rules say
it is a fix (the 0.7 set, 2026-11-11).

Standard library only: the exporter copies this file beside the code (the 0.7 set) and it runs unchanged there. The
files are real ones in temporary folders. Nothing here calls a model: the replies are written out by hand, the way a
model might word them, including the ways it might try to cheat.
"""
from __future__ import annotations

import ast
import os
import secrets
import tempfile
import unittest

from brevio.pipeline import core_ai_verify as verify
from brevio.pipeline import core_patch
from brevio.pipeline.code_rules import _hardcoded_secret

TS = ["import { readFileSync } from 'fs';",
      "",
      "export function load(path: string) {",
      "  const raw = readFileSync(path, 'utf8');",
      "  const data = JSON.parse(raw) as any;",
      "  return data.items;",
      "}"]
PY = ["import sqlite3", "", "def find(db, uid):", "    cur = db.cursor()",
      '    cur.execute(f"select * from t where id = {uid}")', "    return cur.fetchall()"]
SPEC = ["import { test, expect } from '@playwright/test';",
        "test('home', async ({ page }) => {",
        "  await page.goto('/');",
        "  await page.waitForTimeout(500);",
        "  expect(await page.title()).toBe('Home');",
        "});"]
GOOD_TS = "  const data: { items: string[] } = JSON.parse(raw);"
GOOD_PY = '    cur.execute("select * from t where id = ?", (uid,))'


class Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def put(self, rel, lines, newline="\n", final=True):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(newline.join(lines) + (newline if final else ""))

    def window(self, rel, lno, rule, context=1):
        """The window the model would be shown, read the way ``ai fix`` reads it."""
        found = core_patch.locate(f"{rel}:{lno}: [{rule}] x", context, self.root)
        return verify.window_of(found, context)

    def check(self, window, reply):
        return verify.check(self.root, window, reply)

    def reasons(self, window, reply):
        verdict = self.check(window, reply)
        self.assertFalse(verdict.ok, verdict.diff)
        return " | ".join(verdict.reasons)


class TestTheWindow(Folder):
    def test_it_is_the_flagged_line_and_the_lines_around_it_as_far_as_the_file_goes(self):
        self.put("a.ts", TS)
        mid = self.window("a.ts", 5, "R9.1", 1)
        self.assertEqual((mid.start, mid.end, mid.lines), (4, 6, TS[3:6]))
        top = self.window("a.ts", 1, "R9.1", 3)
        self.assertEqual((top.start, top.end), (1, 4))
        bottom = self.window("a.ts", 7, "R9.1", 3)
        self.assertEqual((bottom.start, bottom.end, bottom.lines[-1]), (4, 7, "}"))
        self.assertEqual(self.window("a.ts", 5, "R9.1", 0).lines, [TS[4]])


class TestAFixThatPasses(Folder):
    def test_a_typed_replacement_is_accepted_and_comes_with_the_diff_and_nothing_is_written(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1")
        verdict = self.check(w, [TS[3], GOOD_TS, TS[5]])
        self.assertEqual((verdict.ok, verdict.reasons), (True, []))
        self.assertEqual(sorted(verdict.changes), ["a.ts"])
        old, new, _rules = verdict.changes["a.ts"]
        self.assertEqual(old.split("\n")[4], TS[4])
        self.assertEqual(new.split("\n")[4], GOOD_TS)
        self.assertIn(f"-{TS[4]}\n+{GOOD_TS}\n", verdict.diff)
        self.assertIn("--- a/a.ts\n+++ b/a.ts", verdict.diff)
        with open(os.path.join(self.root, "a.ts"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().split("\n")[4], TS[4])       # the file on disk is as it was

    def test_a_python_file_that_still_parses_is_accepted(self):
        self.put("db.py", PY)
        verdict = self.check(self.window("db.py", 5, "R2.8", 0), [GOOD_PY])
        self.assertTrue(verdict.ok, verdict.reasons)

    def test_the_file_keeps_its_line_endings_and_a_missing_final_newline(self):
        self.put("a.ts", TS, newline="\r\n", final=False)
        verdict = self.check(self.window("a.ts", 5, "R9.1", 0), [GOOD_TS])
        self.assertTrue(verdict.ok, verdict.reasons)
        old, new, _r = verdict.changes["a.ts"]
        self.assertEqual((old.count("\r\n"), new.count("\r\n"), new.endswith("}")), (6, 6, True))
        self.assertEqual(old.replace(TS[4], GOOD_TS), new)

    def test_a_longer_replacement_is_fine_within_the_small_allowance(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1", 0)
        verdict = self.check(w, ["  type Items = { items: string[] };", GOOD_TS])
        self.assertTrue(verdict.ok, verdict.reasons)


class TestAFixThatIsRefused(Folder):
    def test_a_reply_that_leaves_the_rule_flagging_the_line_is_refused_with_that_reason(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1")
        self.assertIn("[R9.1] still flags", self.reasons(w, [TS[3], TS[4], "  return data.entries;"]))

    def test_a_reply_that_reformats_the_line_but_keeps_the_cause_gets_a_new_finding_and_is_refused(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1", 0)
        why = self.reasons(w, ["  const data = JSON.parse(raw)  as any;"])
        self.assertIn("a new finding: [R9.1]", why)

    def test_a_reply_that_adds_a_new_finding_elsewhere_in_the_file_is_refused(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1")
        why = self.reasons(w, [TS[3], GOOD_TS, "  return (data as any).items;"])
        self.assertIn("a new finding: [R9.1]", why)

    def test_an_ignore_comment_is_refused_even_though_it_would_hide_the_finding(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1", 0)
        for comment in ("  // brevio-core: ignore R9.1 -- the payload is checked later", "  // @ts-ignore",
                        "  // eslint-disable-next-line @typescript-eslint/no-explicit-any", "  /* biome-ignore lint: x */"):
            why = self.reasons(w, [comment, TS[4]])
            self.assertIn("switches a check off", why, comment)

    def test_a_python_suppression_comment_is_refused_too(self):
        self.put("db.py", PY)
        w = self.window("db.py", 5, "R2.8", 0)
        for tail in ("  # noqa: S608", "  # type: ignore", "  # nosec", "  # pylint: disable=all", "  # pragma: no cover"):
            self.assertIn("switches a check off", self.reasons(w, [PY[4] + tail]), tail)

    def test_a_suppression_the_line_already_carried_is_not_the_replys_to_answer_for(self):
        self.put("db.py", [*PY[:4], PY[4] + "  # noqa: E501", PY[5]])
        w = self.window("db.py", 5, "R2.8", 0)
        verdict = self.check(w, [GOOD_PY + "  # noqa: E501"])
        self.assertEqual(verdict.reasons, [])

    def test_a_python_reply_that_no_longer_parses_is_refused(self):
        self.put("db.py", PY)
        w = self.window("db.py", 5, "R2.8", 0)
        why = self.reasons(w, ['    cur.execute("select * from t where id = ?", (uid,)'])
        self.assertIn("would no longer parse", why)

    def test_a_python_file_that_did_not_parse_before_cannot_be_checked(self):
        self.put("old.py", ["print 'hello'", PY[4]])
        w = self.window("old.py", 2, "R2.8", 0)
        self.assertIn("does not parse even before", self.reasons(w, [GOOD_PY]))

    def test_an_empty_reply_an_unchanged_reply_and_a_runaway_reply_are_refused(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1", 0)
        self.assertIn("holds no lines", self.reasons(w, [""]))
        self.assertIn("holds no lines", self.reasons(w, ["  ", ""]))
        self.assertIn("changes nothing", self.reasons(w, [TS[4]]))
        self.assertIn("a fix is small", self.reasons(w, [GOOD_TS] + ["  // more"] * (verify.MAX_EXTRA_LINES + 1)))

    def test_a_reply_that_is_a_diff_or_names_another_file_is_refused(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1", 0)
        for line in ("--- a/other.ts", "+++ b/other.ts", "diff --git a/x b/x", "@@ -1,2 +1,2 @@", "<<<<<<< SEARCH"):
            self.assertIn("diff or names another file", self.reasons(w, [line, GOOD_TS]), line)

    def test_a_file_that_changed_since_the_window_was_read_is_not_touched(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1")
        self.put("a.ts", [*TS[:4], "  const data = other(raw) as any;", *TS[5:]])
        self.assertIn("changed since the window was read", self.reasons(w, [TS[3], GOOD_TS, TS[5]]))

    def test_a_line_the_rule_does_not_flag_gives_nothing_to_verify(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 4, "R9.1", 0)       # a finding the scan does not make on that line
        self.assertIn("does not flag that line now", self.reasons(w, ["  const raw = readFileSync(path);"]))

    def test_a_protected_file_a_secret_file_and_mixed_line_endings_are_refused(self):
        self.put("brevio-core.toml", ["x = as any"])
        self.put("keys/server.pem", ["const k = x as any;"])
        self.put("m.ts", ["a", "b as any"])
        with open(os.path.join(self.root, "m.ts"), "w", encoding="utf-8", newline="") as fh:
            fh.write("a\r\nb as any\nc\n")
        for rel, lno, expect in (("brevio-core.toml", 1, "sets what the gate"), ("keys/server.pem", 1, "holds secrets"),
                                 ("m.ts", 2, "mixed line endings")):
            w = verify.Window(rel, "R9.1", lno, lno, ["x"])
            self.assertIn(expect, self.reasons(w, ["y"]), rel)

    def test_a_test_file_may_not_lose_an_assertion_to_make_a_finding_go_away(self):
        self.put("tests/e2e/home.spec.ts", SPEC)
        w = self.window("tests/e2e/home.spec.ts", 4, "R6.3", 1)
        honest = self.check(w, [SPEC[2], "  await expect(page).toHaveTitle('Home');", SPEC[4]])
        self.assertTrue(honest.ok, honest.reasons)
        why = self.reasons(w, [SPEC[2]])        # the sleep is gone, and so is the assertion beside it
        self.assertIn("home.spec.ts", why)

    def test_an_unreadable_file_is_refused_not_guessed_at(self):
        w = verify.Window("nowhere.ts", "R9.1", 1, 1, ["x"])
        self.assertIn("cannot be read", self.reasons(w, ["y"]))


class TestWhatTheVerifierWrites(Folder):
    def test_checking_a_reply_writes_nothing_even_when_it_is_accepted(self):
        self.put("a.ts", TS)
        before = sorted(os.listdir(self.root))
        here = os.getcwd()
        self.check(self.window("a.ts", 5, "R9.1"), [TS[3], GOOD_TS, TS[5]])
        self.assertEqual(sorted(os.listdir(self.root)), before)
        self.assertEqual(os.getcwd(), here)     # the folder it works in is left again

    def test_the_verifier_reads_the_config_of_the_project_folder_not_the_folder_it_is_run_from(self):
        self.put("a.ts", TS)
        self.put("brevio-core.toml", ['exclude = ["a.ts"]'])
        w = self.window("a.ts", 5, "R9.1", 0)
        here = os.getcwd()
        self.assertIn("does not flag that line now", self.reasons(w, [GOOD_TS]))     # the file is excluded in this project
        self.assertEqual(os.getcwd(), here)


# ---- the claims check of 2026-11-12: what the public text says must be true ---------------------------------------------

class TestALinkIsTheFileItLeadsTo(Folder):
    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_fix_through_a_link_into_a_protected_folder_is_refused_and_nothing_is_written(self):
        hooks = ".claude/hooks"
        self.put(f"{hooks}/guard.py", PY)
        os.symlink(os.path.join(self.root, ".claude", "hooks"), os.path.join(self.root, "lib"))
        w = self.window("lib/guard.py", 5, "R2.8", 0)
        verdict = self.check(w, [GOOD_PY])
        self.assertFalse(verdict.ok)
        self.assertIn("sets what the gate", " ".join(verdict.reasons))
        self.assertEqual(verdict.changes, {})

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_link_to_a_secret_file_is_refused_at_locating_and_at_checking(self):
        for target in (".env", ".envrc", ".netrc", "keys/app.p12"):
            self.put(target, ["TOKEN=1", "x as any"])
            os.symlink(os.path.join(self.root, *target.split("/")), os.path.join(self.root, "notes.ts"))
            with self.assertRaises(core_patch.PatchError, msg=target):
                core_patch.locate("notes.ts:2: [R9.1] x", 0, self.root)
            w = verify.Window("notes.ts", "R9.1", 2, 2, ["x as any"])
            self.assertIn("holds secrets", self.reasons(w, ["x"]), target)
            os.remove(os.path.join(self.root, "notes.ts"))


class TestNoSuppressionMarkerIsAddedAnywhereItWasNot(Folder):
    TS_MARKS = ("// tslint:disable-line", "// @ts-expect-error", "// @ts-nocheck", "// deno-lint-ignore no-explicit-any",
                "// prettier-ignore", "// oxlint-disable-next-line", "/* v8 ignore next */", "/* istanbul ignore next */",
                "/* c8 ignore next */", "// skipcq: JS-0001", "// nolint", "// NOLINT", "// eslint-disable-line",
                "/* eslint-disable */", "// biome-ignore lint/suspicious/noExplicitAny: x", "// phpcs:ignore", '@SuppressWarnings("all")',
                "#[allow(dead_code)]", "// fmt: off", "// noinspection JSUnusedGlobalSymbols")
    PY_MARKS = ("# nosemgrep", "# pylint: skip-file", "# pylint: disable=all", "# fmt: off", "# rubocop:disable Style/All",
                "# noqa", "# type: ignore", "# nosec", "# skipcq")

    def test_each_marker_the_tool_knows_or_another_tool_uses_is_refused_on_a_changed_line(self):
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1", 0)
        for mark in self.TS_MARKS:
            self.assertIn("switches a check off", self.reasons(w, [GOOD_TS + "  " + mark]), mark)
        self.put("db.py", PY)
        w = self.window("db.py", 5, "R2.8", 0)
        for mark in self.PY_MARKS:
            self.assertIn("switches a check off", self.reasons(w, [GOOD_PY + "  " + mark]), mark)

    def test_the_markers_that_hide_a_secret_or_turn_a_type_or_lint_setting_off_are_refused_too(self):
        # (2026-11-12, claims check) The last review found these verified: a secret scanner's allow comment and the inline
        # configuration comments of a type checker and a linter.
        self.put("a.ts", TS)
        w = self.window("a.ts", 5, "R9.1", 0)
        for mark in ("/* eslint no-explicit-any: off */", "/* eslint no-explicit-any: 0 */", "// gitleaks:allow", "// checkov:skip=CKV_1: x"):
            self.assertIn("switches a check off", self.reasons(w, [GOOD_TS + "  " + mark]), mark)
        self.put("db.py", PY)
        w = self.window("db.py", 5, "R2.8", 0)
        for mark in ("# pragma: allowlist secret", "# gitleaks:allow", "# trufflehog:ignore", "# mypy: disable-error-code=x",
                     "# pyright: reportGeneralTypeIssues=false", "# pyright: basic"):
            self.assertIn("switches a check off", self.reasons(w, [GOOD_PY + "  " + mark]), mark)
        for plain in ("# eslint-env node", "# parameters, not a format string"):        # a comment that is not a switch stays fine
            self.assertEqual(self.check(w, [GOOD_PY + "  " + plain]).reasons, [], plain)

    def test_a_marker_moved_from_one_line_onto_a_changed_line_is_refused_though_the_count_is_the_same(self):
        self.put("db.py", ["import sqlite3  # noqa: E401", "", "def find(db, uid):", "    cur = db.cursor()", PY[4], PY[5]])
        w = self.window("db.py", 5, "R2.8", 4)       # the window holds the first line, which keeps its marker
        self.assertEqual(w.lines[0], "import sqlite3  # noqa: E401")
        reply = ["import sqlite3", *w.lines[1:4], GOOD_PY + "  # noqa: E401", w.lines[5]]
        self.assertIn("switches a check off", self.reasons(w, reply))

    def test_a_marker_on_an_edited_line_in_a_block_that_changed_size_is_refused_unless_it_is_the_same_code(self):
        self.put("db.py", [*PY[:4], PY[4] + "  # noqa: E501", PY[5]])
        w = self.window("db.py", 5, "R2.8", 0)
        verdict = self.check(w, ["    # parameters, not a format string", GOOD_PY + "  # noqa: E501"])
        self.assertIn("switches a check off", " ".join(verdict.reasons))        # the edited line is not the code it replaced
        same_code = self.check(w, [PY[4] + "  # noqa: E501", "    cur.rowcount"])
        self.assertNotIn("switches a check off", " ".join(same_code.reasons))     # a line kept as it was keeps its marker

    def test_the_list_is_one_list_the_patch_check_reads_as_well(self):
        self.assertIs(verify.core_patch.SUPPRESSION, core_patch.SUPPRESSION)
        self.assertTrue(all(core_patch.SUPPRESSION.search(f"x  {m}") for m in (*self.TS_MARKS[:6], *self.PY_MARKS[:4])))


class TestAPythonFileStillCompiles(Folder):
    def test_a_reply_the_parser_accepts_but_the_compiler_refuses_is_refused(self):
        self.put("db.py", PY)
        w = self.window("db.py", 5, "R2.8", 0)
        self.assertIn("would no longer compile", self.reasons(w, ["    nonlocal zz", GOOD_PY]))
        self.put("top.py", ["import sqlite3", PY[4].strip(), "x = 1"])
        w = self.window("top.py", 2, "R2.8", 0)
        self.assertIn("would no longer compile", self.reasons(w, ["return 1", GOOD_PY.strip()]))

    def test_the_two_errors_do_parse_which_is_why_the_parser_alone_missed_them(self):
        for text in ("def f():\n    nonlocal zz\n    return 1\n", "return 1\n"):
            ast.parse(text)
            self.assertTrue(verify._compile_error(text)[0], text)
            self.assertFalse(verify._parse_error(text)[0], text)

    def test_a_file_that_does_not_compile_even_before_the_change_cannot_be_checked(self):
        self.put("old.py", ["def f():", "    nonlocal zz", PY[4]])
        w = self.window("old.py", 3, "R2.8", 0)
        self.assertIn("does not compile even before", self.reasons(w, [GOOD_PY]))


class TestATestKeepsItsAssertions(Folder):
    PYTEST = ["from pkg import f", "", "def test_f():", "    assert f(1) == 2", "    assert f(2) == 3"]

    def test_a_reply_that_turns_an_assertion_into_a_true_one_is_refused(self):
        self.put("tests/test_f.py", self.PYTEST)
        w = self.window("tests/test_f.py", 4, "R20.4", 0)
        why = self.reasons(w, ["    assert True"])
        self.assertIn("Tautological test assertion", why)
        self.assertIn("Tautological", self.reasons(w, ["    assert 1 == 1"]))

    def test_a_reply_that_adds_a_skip_or_focus_marker_is_refused_and_a_js_always_true_expect_too(self):
        self.put("tests/test_f.py", self.PYTEST)
        w = self.window("tests/test_f.py", 3, "R20.3", 0)
        self.assertIn("skip or focus", self.reasons(w, ["@pytest.mark.skip", "def test_f():"]))
        self.put("tests/e2e/home.spec.ts", SPEC)
        w = self.window("tests/e2e/home.spec.ts", 5, "R6.3", 0)
        self.assertIn("Tautological", self.reasons(w, ["  expect(true).toBe(true);"]))

    def test_the_same_check_is_the_one_patch_runs(self):
        old = "\n".join(self.PYTEST) + "\n"
        new = old.replace("assert f(1) == 2", "assert True")
        self.assertEqual(verify.core_patch.custody_reasons({"tests/test_f.py": (old, new, [])}),
                         core_patch.custody_reasons({"tests/test_f.py": (old, new, [])}))
        self.assertTrue(core_patch.custody_reasons({"tests/test_f.py": (old, new, [])}))


class TestTheStricterSecretCheck(unittest.TestCase):
    def forms(self):
        token = secrets.token_hex(32)
        return {
            "a private key block": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "the end of one": "-----END " + "OPENSSH PRIVATE KEY-----",
            "a url with a password": "DB = 'postgres://app:" + secrets.token_hex(5) + "@db.example.test:5432/main'",
            "an authorization literal": "curl -H 'Authorization: Bearer " + secrets.token_hex(16) + "' x",
            "a bearer token": "headers = {'h': 'Bearer " + secrets.token_urlsafe(24) + "'}",
            "a json web token": "const j = '" + "eyJ" + "hbGciOiJIUzI1NiJ9." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0." + secrets.token_urlsafe(16) + "';",
            "a quoted token on a key name": "api_key = '" + token + "'",
            "a quoted token on a token name": 'const authToken = "' + token[:40] + '";',
            "a token on a secret name": "client.secret: '" + token + "'",
            "a token on a password name": "db_password = \"" + secrets.token_urlsafe(30) + "\"",
        }

    def test_each_form_the_scanners_check_may_miss_is_flagged(self):
        for what, line in self.forms().items():
            self.assertTrue(verify.holds_secret(line), what)

    def test_the_scanners_own_detector_still_counts(self):
        aws = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';"
        self.assertTrue(verify.holds_secret(aws))

    def test_ordinary_code_is_not_flagged(self):
        for line in ("headers = {'Authorization': f'Bearer {key}'}", "key = os.environ['WORK_KEY']", "cache_key = compute(a, b)",
                     "const url = '" + "https://" + "example.test/v1/items';", "password = input('password: ')", "token = None",
                     "url = '" + "https://" + "user" + "@" + "example.test/x'", "secret_name = 'work-secret'", "x = 1  # a note"):
            self.assertFalse(verify.holds_secret(line), line)

    def test_the_line_numbers_are_those_of_the_rows_and_a_line_is_read_with_the_lines_above_it(self):
        rows = [(10, "x = 1"), (11, "-----BEGIN " + "PRIVATE KEY-----"), (12, "y = 2"), (13, "db = 'mysql://u:" + secrets.token_hex(4) + "@h/x'")]
        self.assertEqual(verify.secret_lines(rows), [11, 13])
        self.assertEqual(verify.secret_lines([(1, "ok"), (2, "fine")]), [])

    def test_the_gates_own_rule_is_not_this_function(self):
        url = "DB = 'postgres://app:" + secrets.token_hex(5) + "@db.example.test:5432/main'"
        self.assertTrue(verify.holds_secret(url))
        self.assertFalse(_hardcoded_secret(url, ""), "the gate's rule is as it was; the stricter check is for sending only")


if __name__ == "__main__":
    unittest.main()
