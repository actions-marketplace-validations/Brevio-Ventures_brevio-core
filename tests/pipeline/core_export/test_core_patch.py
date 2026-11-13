"""``brevio-core patch`` and the finding packet: an edit is written only when the rules find it clean (2026-11-11).

Standard library only: the exporter copies this file beside the code (the 0.6 set) and it runs unchanged there. The
files are real ones in temporary folders; git is not needed, because the patch reads and writes the working tree only.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import unittest.mock

from brevio.pipeline import core_cli, core_patch

# A fake key in the AWS access key format (AWS's documented example key ID), joined at run time so this file is not
# itself a finding of a secret scan.
SECRET = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';"
OPEN, MID, CLOSE = "<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE"
_AGENTS = "." + "agents"      # joined: the leak scan reads the literal as a private folder


def block(path, search, replace):
    """One SEARCH/REPLACE block as ``heal`` prints it, with its ``#### File:`` header."""
    return f"#### File: {path}:1 [R1.1]\n{OPEN}\n{search}\n{MID}\n{replace}\n{CLOSE}\n"


def diff(path, old, new, start=1):
    """A unified diff of one hunk that swaps ``old`` for ``new`` (each a list of lines)."""
    head = f"--- a/{path}\n+++ b/{path}\n@@ -{start},{len(old)} +{start},{len(new)} @@\n"
    return head + "".join(f"-{x}\n" for x in old) + "".join(f"+{x}\n" for x in new)


class Folder(unittest.TestCase):
    """A temporary project folder; ``put`` writes a file, ``get`` reads one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def put(self, rel, text, newline=""):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
        return path

    def get(self, rel):
        with open(os.path.join(self.root, *rel.split("/")), encoding="utf-8", newline="") as fh:
            return fh.read()

    def check(self, text, **kw):
        return core_patch.check_patch(text, self.root, **kw)


class TestReadingAPatch(unittest.TestCase):
    def test_a_block_takes_its_file_from_the_header_heal_prints(self):
        edits = core_patch.parse_blocks(block("src/a.py", "x = 1", "x = 2"))
        self.assertEqual([(e.path, e.search, e.replace, e.hint) for e in edits], [("src/a.py", ["x = 1"], ["x = 2"], -1)])

    def test_a_path_on_a_line_of_its_own_above_a_fence_names_the_file(self):
        text = f"src/b.ts\n```ts\n{OPEN}\na\n{MID}\nb\n{CLOSE}\n```\n"
        self.assertEqual(core_patch.parse_blocks(text)[0].path, "src/b.ts")

    def test_a_second_block_without_a_header_stays_with_the_file_before_it(self):
        text = block("a.py", "1", "2") + f"{OPEN}\n3\n{MID}\n4\n{CLOSE}\n"
        self.assertEqual([e.path for e in core_patch.parse_blocks(text)], ["a.py", "a.py"])

    def test_a_block_with_no_file_anywhere_is_an_error_unless_a_default_is_given(self):
        text = f"{OPEN}\na\n{MID}\nb\n{CLOSE}\n"
        with self.assertRaisesRegex(core_patch.PatchError, "names no file"):
            core_patch.parse_blocks(text)
        self.assertEqual(core_patch.parse_blocks(text, "z.py")[0].path, "z.py")

    def test_a_block_that_never_ends_is_an_error_not_a_guess(self):
        with self.assertRaisesRegex(core_patch.PatchError, "no ======="):
            core_patch.parse_blocks(f"a.py\n{OPEN}\nx\n")

    def test_a_unified_diff_gives_one_edit_per_hunk_with_its_start_line(self):
        text = diff("a.py", ["x = 1"], ["x = 2"], start=4)
        edit = core_patch.parse_diff(text).edits[0]
        self.assertEqual((edit.path, edit.search, edit.replace, edit.hint), ("a.py", ["x = 1"], ["x = 2"], 3))

    def test_a_new_file_diff_has_no_old_lines_and_a_deleted_one_is_listed(self):
        made = core_patch.parse_diff("--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a\n+b\n")
        self.assertEqual((made.edits[0].search, made.edits[0].replace, made.deletes), ([], ["a", "b"], []))
        gone = core_patch.parse_diff("--- a/o.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-a\n")
        self.assertEqual(gone.deletes, ["o.py"])

    def test_a_rename_and_a_binary_patch_are_not_supported(self):
        for text in ("rename from a.py\nrename to b.py\n", "Binary files a/x.png and b/x.png differ\n"):
            with self.assertRaisesRegex(core_patch.PatchError, "not supported"):
                core_patch.parse_diff(text)
        with self.assertRaisesRegex(core_patch.PatchError, "rename"):
            core_patch.parse_diff("--- a/a.py\n+++ b/b.py\n@@ -1,1 +1,1 @@\n-a\n+b\n")

    def test_a_hunk_shorter_than_its_header_says_is_an_error(self):
        with self.assertRaisesRegex(core_patch.PatchError, "shorter"):
            core_patch.parse_diff("--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n a\n-b\n")

    def test_blocks_and_a_diff_in_one_patch_are_refused_so_no_part_is_dropped_unseen(self):
        text = block("a.py", "1", "2") + diff("b.py", ["x"], ["y"])
        with self.assertRaisesRegex(core_patch.PatchError, "mixes"):
            core_patch.parse_patch(text)

    def test_text_that_is_neither_blocks_nor_a_diff_is_an_error(self):
        for text in ("", "   \n", "just some words\n"):
            with self.assertRaises(core_patch.PatchError):
                core_patch.parse_patch(text)

    def test_windows_line_ends_in_the_patch_read_the_same(self):
        edits = core_patch.parse_patch(block("a.py", "x = 1", "x = 2").replace("\n", "\r\n")).edits
        self.assertEqual((edits[0].search, edits[0].replace), (["x = 1"], ["x = 2"]))


class TestApplyingInMemory(unittest.TestCase):
    def apply(self, old, search, replace, hint=-1):
        return core_patch.apply_edits("f.py", old, [core_patch.Edit("f.py", search, replace, hint)])

    def test_the_one_place_the_text_fits_is_replaced(self):
        self.assertEqual(self.apply("a\nb\nc\n", ["b"], ["B", "B2"]), "a\nB\nB2\nc\n")

    def test_text_that_fits_in_two_places_is_refused_so_no_wrong_one_is_edited(self):
        with self.assertRaisesRegex(core_patch.PatchError, "matches 2 places"):
            self.apply("x\ny\nx\n", ["x"], ["z"])

    def test_text_that_is_not_in_the_file_is_refused(self):
        with self.assertRaisesRegex(core_patch.PatchError, "not in the file"):
            self.apply("a\n", ["nope"], ["b"])

    def test_trailing_blanks_in_the_search_do_not_stop_it_fitting(self):
        self.assertEqual(self.apply("a  \nb\n", ["a"], ["c"]), "c\nb\n")

    def test_an_empty_replacement_deletes_the_lines(self):
        self.assertEqual(self.apply("a\nb\nc\n", ["b"], []), "a\nc\n")

    def test_an_empty_search_creates_a_file_and_is_refused_on_one_that_has_text(self):
        self.assertEqual(core_patch.apply_edits("n.py", None, [core_patch.Edit("n.py", [], ["a", "b"], -1)]), "a\nb\n")
        with self.assertRaisesRegex(core_patch.PatchError, "only creates a new file"):
            self.apply("old\n", [], ["new"])

    def test_the_end_of_line_style_and_the_last_newline_of_the_file_are_kept(self):
        self.assertEqual(self.apply("a\r\nb\r\n", ["b"], ["c"]), "a\r\nc\r\n")
        self.assertEqual(self.apply("a\nb", ["b"], ["c"]), "a\nc")

    def test_a_file_with_both_kinds_of_line_end_is_refused_rather_than_made_uniform(self):
        with self.assertRaisesRegex(core_patch.PatchError, "mixed line endings"):
            self.apply("a\r\nb\n", ["b"], ["c"])

    def test_a_form_feed_inside_a_line_does_not_split_it(self):
        self.assertEqual(self.apply("a\x0cb\nc\n", ["c"], ["d"]), "a\x0cb\nd\n")

    def test_a_hunk_that_only_adds_lines_lands_after_the_line_its_header_names(self):
        text = core_patch.apply_edits("f.py", "a\nb\nc\n", core_patch.parse_diff("--- a/f.py\n+++ b/f.py\n@@ -2,0 +3 @@\n+new\n").edits)
        self.assertEqual(text, "a\nb\nnew\nc\n")

    def test_a_hunk_whose_line_number_is_stale_still_lands_when_one_place_fits(self):
        text = self.apply("a\nb\nc\nd\n", ["c"], ["C"], hint=0)
        self.assertEqual(text, "a\nb\nC\nd\n")

    def test_hunks_of_one_file_apply_bottom_up_so_each_start_line_stays_true(self):
        edits = [core_patch.Edit("f.py", ["a"], ["a", "a2"], 0), core_patch.Edit("f.py", ["c"], ["C"], 2)]
        self.assertEqual(core_patch.apply_edits("f.py", "a\nb\nc\n", edits), "a\na2\nb\nC\n")


class TestTheVerdict(Folder):
    def test_a_clean_edit_is_accepted_with_its_diff_and_nothing_is_written(self):
        self.put("a.py", "import requests\nresp = requests.get(url)\n")
        v = self.check(block("a.py", "resp = requests.get(url)", "resp = requests.get(url, timeout=30)"))
        self.assertTrue(v.ok, v.reasons)
        self.assertIn("+resp = requests.get(url, timeout=30)", v.diff)
        self.assertEqual(self.get("a.py"), "import requests\nresp = requests.get(url)\n")

    def test_an_edit_that_leaves_a_finding_on_a_line_it_changed_is_refused_with_the_finding(self):
        self.put("a.ts", "export const a = 1;\n")
        v = self.check(block("a.ts", "export const a = 1;", SECRET))
        self.assertFalse(v.ok)
        self.assertTrue(any("a.ts:1: [R13.1]" in r for r in v.reasons), v.reasons)

    def test_an_old_finding_on_a_line_the_edit_did_not_change_does_not_refuse_it(self):
        self.put("a.ts", f"{SECRET}\nexport const a = 1;\n")
        v = self.check(block("a.ts", "export const a = 1;", "export const a = 2;"))
        self.assertTrue(v.ok, v.reasons)

    def test_an_ignore_comment_does_not_silence_a_rule_and_is_itself_a_reason(self):
        self.put("a.ts", "export const a = 1;\n")
        v = self.check(block("a.ts", "export const a = 1;", SECRET + "  // brevio-core: ignore R13.1 -- a test key"))
        self.assertFalse(v.ok)
        self.assertTrue(any("[R13.1]" in r for r in v.reasons), v.reasons)
        self.assertTrue(any("[suppression]" in r for r in v.reasons), v.reasons)

    def test_the_baseline_is_not_read_so_it_cannot_hide_a_finding_of_the_edit(self):
        self.put("a.ts", f"{SECRET}\n")
        base = core_cli.baseline_tools()
        self.put(base.BASELINE_PATH, "")
        v = self.check(block("a.ts", SECRET, SECRET.replace("AKIA", "ASIA")))
        self.assertFalse(v.ok)

    def test_a_suppression_with_no_reason_is_refused_too(self):
        self.put("a.py", "x = 1\n")
        v = self.check(block("a.py", "x = 1", "x = 2  # brevio-core: ignore R10.1"))
        self.assertFalse(v.ok)
        self.assertTrue(any("[suppression]" in r for r in v.reasons), v.reasons)

    def test_a_protected_file_is_refused_however_clean_the_edit_is(self):
        for rel in ("brevio-core.toml", "sub/brevio-core.toml", ".brevio/baseline.json", ".brevio/guards.json",
                    f"{_AGENTS}/rules/guard-x.md", ".github/workflows/ci.yml", "CLAUDE.md", "AGENTS.md",
                    ".claude/settings.json", ".cursor/hooks.json", ".cursor/rules/x.mdc", "SKILL.md",
                    "packages/web/.claude/settings.json"):
            with self.subTest(rel=rel):
                self.put(rel, "a\n")
                v = self.check(block(rel, "a", "b"))
                self.assertFalse(v.ok)
                self.assertTrue(any("protected" in r for r in v.reasons), v.reasons)
                self.assertEqual(self.get(rel), "a\n")

    def test_a_file_that_holds_secrets_is_refused_so_no_verdict_prints_its_lines(self):
        for rel in (".env", ".env.local", "deploy/server.pem", "id_rsa", "keys/app.key"):
            with self.subTest(rel=rel):
                self.put(rel, "one\ntwo\nthree\nfour\n")
                v = self.check(block(rel, "two", "2"))
                self.assertFalse(v.ok)
                self.assertTrue(any("holds secrets" in r for r in v.reasons), v.reasons)
                self.assertEqual(v.diff, "")
                self.assertEqual(self.get(rel), "one\ntwo\nthree\nfour\n")
        self.put(".env.example", "A=\nB=\n")
        self.assertTrue(self.check(block(".env.example", "B=", "B=\nC=")).ok)

    def test_a_path_that_leaves_the_folder_or_enters_git_is_refused(self):
        for rel in ("../outside.py", "/etc/passwd", "a/../../b.py", ".git/config", "C:/x.py"):
            with self.subTest(rel=rel):
                v = self.check(block(rel, "a", "b"))
                self.assertFalse(v.ok)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_link_counts_as_what_it_points_to(self):
        self.put("CLAUDE.md", "rules\n")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        with open(os.path.join(outside.name, "t.py"), "w", encoding="utf-8") as fh:
            fh.write("a\n")
        try:
            os.symlink("CLAUDE.md", os.path.join(self.root, "notes.md"))
            os.symlink(os.path.join(outside.name, "t.py"), os.path.join(self.root, "away.py"))
        except OSError:
            self.skipTest("symbolic links are not allowed here")
        self.assertTrue(any("protected" in r for r in self.check(block("notes.md", "rules", "x")).reasons))
        self.assertTrue(any("leaves the project folder" in r for r in self.check(block("away.py", "a", "b")).reasons))

    def test_deleting_a_test_file_is_refused_with_the_test_custody_rule(self):
        self.put("tests/test_a.py", "def test_a():\n    assert 1\n")
        v = self.check("--- a/tests/test_a.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def test_a():\n-    assert 1\n")
        self.assertFalse(v.ok)
        self.assertTrue(any("[R20.1] Existing test file would be deleted" in r for r in v.reasons), v.reasons)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "tests", "test_a.py")))

    def test_deleting_any_other_file_is_refused_too_because_a_patch_does_not_delete(self):
        self.put("a.py", "x = 1\n")
        v = self.check("--- a/a.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n")
        self.assertFalse(v.ok)
        self.assertTrue(any("does not delete files" in r for r in v.reasons), v.reasons)

    def test_an_edit_that_removes_an_assertion_from_a_test_is_refused(self):
        self.put("tests/test_a.py", "def test_a():\n    assert f() == 1\n    assert g() == 2\n")
        v = self.check(block("tests/test_a.py", "    assert g() == 2", "    pass"))
        self.assertFalse(v.ok)
        self.assertTrue(any("removed or weakened" in r for r in v.reasons), v.reasons)

    def test_an_edit_that_adds_a_skip_to_a_test_is_refused(self):
        self.put("tests/test_a.py", "def test_a():\n    assert f() == 1\n")
        v = self.check(block("tests/test_a.py", "def test_a():", "@unittest.skip('later')\ndef test_a():"))
        self.assertFalse(v.ok)
        self.assertTrue(any("[R20.3]" in r for r in v.reasons), v.reasons)

    def test_an_edit_that_adds_a_test_and_keeps_every_assertion_is_accepted(self):
        self.put("tests/test_a.py", "def test_a():\n    assert f() == 1\n")
        v = self.check(block("tests/test_a.py", "    assert f() == 1", "    assert f() == 1\n    assert f() > 0"))
        self.assertTrue(v.ok, v.reasons)

    def test_an_edit_that_leaves_a_conflict_marker_is_refused(self):
        self.put("a.py", "x = 1\n")
        v = self.check(block("a.py", "x = 1", "<<<<<<< ours\nx = 2"))
        self.assertFalse(v.ok)

    def test_text_that_does_not_fit_the_file_or_a_file_that_is_missing_is_refused_with_the_path(self):
        self.put("a.py", "x = 1\n")
        for text in (block("a.py", "y = 2", "y = 3"), block("none.py", "y = 2", "y = 3")):
            v = self.check(text)
            self.assertFalse(v.ok)
            self.assertTrue(any(r.startswith(("a.py:", "none.py:")) for r in v.reasons), v.reasons)

    def test_an_edit_that_changes_nothing_is_refused(self):
        self.put("a.py", "x = 1\n")
        v = self.check(block("a.py", "x = 1", "x = 1"))
        self.assertEqual((v.ok, v.reasons), (False, ["the patch changes nothing"]))

    def test_a_file_that_is_not_text_is_refused_and_not_half_read(self):
        with open(os.path.join(self.root, "b.py"), "wb") as fh:
            fh.write(b"\xff\xfe\x00 not utf-8\n")
        v = self.check(block("b.py", "a", "b"))
        self.assertFalse(v.ok)
        self.assertTrue(any("cannot be read as text" in r for r in v.reasons), v.reasons)

    def test_a_broken_brevio_core_toml_gives_a_reason_not_a_crash(self):
        self.put("a.py", "x = 1\n")
        self.put("brevio-core.toml", "this is [not toml\n")
        v = self.check(block("a.py", "x = 1", "x = 2"))
        self.assertFalse(v.ok)
        self.assertEqual(len(v.reasons), 1)

    def test_a_rule_the_config_turns_off_does_not_refuse_an_edit_that_trips_it(self):
        self.put("brevio-core.toml", 'ignore = ["R13.1"]\n')
        self.put("a.ts", "export const a = 1;\n")
        self.assertTrue(self.check(block("a.ts", "export const a = 1;", SECRET)).ok)

    def test_a_new_file_is_accepted_when_it_is_clean_and_the_patch_adds_it(self):
        v = self.check("--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1,2 @@\n+def f():\n+    return 1\n")
        self.assertTrue(v.ok, v.reasons)
        self.assertEqual(v.changes["src/new.py"][1], "def f():\n    return 1\n")

    def test_the_new_text_is_what_is_scanned_not_the_old_file_on_disk(self):
        self.put("a.ts", "export const a = 1;\n")
        v = self.check(diff("a.ts", ["export const a = 1;"], [SECRET]))
        self.assertFalse(v.ok)

    def test_the_verdict_lists_files_reasons_and_diff_as_data(self):
        self.put("a.py", "x = 1\n")
        data = self.check(block("a.py", "x = 1", "x = 2")).data()
        self.assertEqual((data["ok"], data["files"], data["reasons"]), (True, ["a.py"], []))
        self.assertIn("-x = 1", data["diff"])


class TestWriting(Folder):
    def test_nothing_is_written_when_one_of_two_files_is_refused(self):
        self.put("a.py", "x = 1\n")
        self.put("b.ts", "export const b = 1;\n")
        text = block("a.py", "x = 1", "x = 2") + block("b.ts", "export const b = 1;", SECRET)
        self.assertEqual(self._run_text(text), core_patch.EXIT_REFUSED)
        self.assertEqual((self.get("a.py"), self.get("b.ts")), ("x = 1\n", "export const b = 1;\n"))

    def _run_text(self, text, *args):
        with unittest.mock.patch.object(sys, "stdin", io.StringIO(text)), contextlib.redirect_stdout(io.StringIO()) as out:
            code = core_patch.main(["-", "--root", self.root, *args])
        self.out = out.getvalue()
        return code

    def test_an_accepted_patch_writes_every_file_and_keeps_the_mode(self):
        path = self.put("a.py", "x = 1\n")
        os.chmod(path, 0o755)
        text = diff("a.py", ["x = 1"], ["x = 2"]) + "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1 @@\n+y = 1\n"
        self.assertEqual(self._run_text(text), core_patch.EXIT_WRITTEN, self.out)
        self.assertEqual((self.get("a.py"), self.get("pkg/new.py")), ("x = 2\n", "y = 1\n"))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o755)

    def test_a_file_that_changed_since_the_check_stops_the_write_before_it_starts(self):
        self.put("a.py", "x = 1\n")
        v = self.check(block("a.py", "x = 1", "x = 2"))
        self.put("a.py", "x = 1\n# edited meanwhile\n")
        with self.assertRaisesRegex(core_patch.PatchError, "changed on disk"):
            core_patch.write_changes(v.changes, self.root)
        self.assertEqual(self.get("a.py"), "x = 1\n# edited meanwhile\n")

    def test_a_write_that_fails_part_way_puts_back_what_was_written(self):
        self.put("a.py", "x = 1\n")
        self.put("b.py", "y = 1\n")
        v = self.check(block("a.py", "x = 1", "x = 2") + block("b.py", "y = 1", "y = 2"))
        real, calls = core_cli.write_atomic, []

        def flaky(path, text):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
            real(path, text)
        with unittest.mock.patch.object(core_cli, "write_atomic", flaky):
            with self.assertRaisesRegex(core_patch.PatchError, "put back"):
                core_patch.write_changes(v.changes, self.root)
        self.assertEqual((self.get("a.py"), self.get("b.py")), ("x = 1\n", "y = 1\n"))


class TestTheCommand(Folder):
    def run_patch(self, text, *args):
        with unittest.mock.patch.object(sys, "stdin", io.StringIO(text)):
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                code = core_patch.main(["-", "--root", self.root, *args])
        return code, out.getvalue(), err.getvalue()

    def test_exit_0_when_written_and_the_file_has_the_new_text(self):
        self.put("a.py", "x = 1\n")
        code, out, _err = self.run_patch(block("a.py", "x = 1", "x = 2"))
        self.assertEqual((code, self.get("a.py")), (0, "x = 2\n"))
        self.assertIn("wrote a.py", out)

    def test_exit_1_when_refused_with_the_reasons_printed_and_the_file_untouched(self):
        self.put("a.ts", "export const a = 1;\n")
        code, out, _err = self.run_patch(block("a.ts", "export const a = 1;", SECRET))
        self.assertEqual((code, self.get("a.ts")), (1, "export const a = 1;\n"))
        self.assertIn("refused:", out)
        self.assertIn("[R13.1]", out)

    def test_dry_run_prints_the_verdict_and_the_diff_and_writes_nothing(self):
        self.put("a.py", "x = 1\n")
        code, out, _err = self.run_patch(block("a.py", "x = 1", "x = 2"), "--dry-run")
        self.assertEqual((code, self.get("a.py")), (0, "x = 1\n"))
        self.assertIn("accepted: a.py", out)
        self.assertIn("+x = 2", out)

    def test_dry_run_of_a_refused_edit_still_exits_1(self):
        self.put("a.ts", "export const a = 1;\n")
        code, out, _err = self.run_patch(block("a.ts", "export const a = 1;", SECRET), "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("refused:", out)

    def test_json_gives_the_verdict_for_a_script(self):
        self.put("a.py", "x = 1\n")
        code, out, _err = self.run_patch(block("a.py", "x = 1", "x = 2"), "--json")
        data = json.loads(out)
        self.assertEqual((code, data["ok"], data["written"], data["files"]), (0, True, ["a.py"], ["a.py"]))

    def test_exit_2_for_no_patch_a_missing_file_or_a_missing_folder(self):
        self.assertEqual(self.run_patch("   \n")[0], 2)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(core_patch.main([os.path.join(self.root, "none.patch"), "--root", self.root]), 2)
        self.assertEqual(self.run_patch("x", "--root", os.path.join(self.root, "missing"))[0], 2)

    def test_a_patch_read_from_a_file_with_the_path_flag_for_a_bare_block(self):
        self.put("a.py", "x = 1\n")
        patch_file = os.path.join(self.root, "edit.patch")
        with open(patch_file, "w", encoding="utf-8") as fh:
            fh.write(f"{OPEN}\nx = 1\n{MID}\nx = 2\n{CLOSE}\n")
        with contextlib.redirect_stdout(io.StringIO()):
            code = core_patch.main([patch_file, "--root", self.root, "--path", "a.py"])
        self.assertEqual((code, self.get("a.py")), (0, "x = 2\n"))

    def test_the_command_is_listed_by_the_cli_and_runs_through_it(self):
        tools, helps = core_cli.line_commands()
        self.assertIn("patch", tools)
        self.assertEqual(helps["patch"], core_patch.HELP)
        self.put("a.py", "x = 1\n")
        patch_file = os.path.join(self.root, "edit.patch")
        with open(patch_file, "w", encoding="utf-8") as fh:
            fh.write(block("a.py", "x = 1", "x = 2"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(core_cli.main(["patch", patch_file, "--root", self.root, "--dry-run"]), 0)

    def test_the_working_folder_is_put_back_after_the_command(self):
        before = os.getcwd()
        self.put("a.py", "x = 1\n")
        self.run_patch(block("a.py", "x = 1", "x = 2"))
        self.assertEqual(os.getcwd(), before)


class TestTheFindingPacket(Folder):
    def lines(self, n=9):
        return "".join(f"line {i}\n" for i in range(1, n + 1))

    def test_a_packet_holds_the_rule_text_the_line_and_the_extension_and_no_context_by_default(self):
        self.put("src/a.ts", self.lines())
        p = core_patch.finding_packet("src/a.ts:5: [R9.2] a ts-ignore", 0, self.root)
        self.assertEqual((p["rule"], p["path"], p["line"], p["extension"], p["text"]), ("R9.2", "src/a.ts", 5, ".ts", "line 5"))
        self.assertEqual((p["before"], p["after"]), ([], []))
        self.assertTrue(p["why"] and p["bad"] and p["good"] and p["summary"])

    def test_the_agent_format_with_a_hint_reads_the_same_as_the_scan_format(self):
        self.put("src/a.ts", self.lines())
        scan = core_patch.finding_packet("src/a.ts:5: [R9.2] a ts-ignore", 0, self.root)
        agent = core_patch.finding_packet("src/a.ts:5 [R9.2] a ts-ignore -> use ts-expect-error", 0, self.root)
        self.assertEqual(scan, agent)

    def test_context_lines_come_from_both_sides_and_stop_at_the_ends_of_the_file(self):
        self.put("a.py", self.lines(4))
        p = core_patch.finding_packet("a.py:2: [R5.4] bare except", 3, self.root)
        self.assertEqual((p["before"], p["after"]), (["line 1"], ["line 3", "line 4"]))
        text = core_patch.packet_text(p)
        self.assertIn("> 2 | line 2", text)
        self.assertIn("  3 | line 3", text)

    def test_a_secrets_file_a_missing_line_a_path_outside_and_a_wild_context_are_refused(self):
        self.put(".env", "KEY=1\n")
        self.put("a.py", "x\n")
        for finding, ctx in ((".env:1: [R13.1] secret", 0), ("a.py:9: [R5.4] x", 0), ("../x.py:1: [R5.4] x", 0),
                             ("a.py:1: [R5.4] x", 51), ("a.py:1: [R5.4] x", -1), ("not a finding", 0), ("none.py:1: [R5.4] x", 0)):
            with self.subTest(finding=finding, ctx=ctx):
                with self.assertRaises(core_patch.PatchError):
                    core_patch.finding_packet(finding, ctx, self.root)

    def test_a_rule_the_catalogue_does_not_know_still_gives_the_line_and_the_message(self):
        self.put("a.py", "x\n")
        p = core_patch.finding_packet("a.py:1: [suppression] new suppression", 0, self.root)
        self.assertEqual((p["summary"], p["why"], p["text"]), ("", "", "x"))

    def test_a_very_long_line_is_cut(self):
        self.put("a.js", "x" * 5000 + "\n")
        self.assertLess(len(core_patch.finding_packet("a.js:1: [R5.4] x", 0, self.root)["text"]), 500)


# ---- the claims check of 2026-11-12: what the public text says must be true ---------------------------------------------

class TestAPathIsCheckedAsNamedAndAsItResolves(Folder):
    SECRET_NAMES = (".env", ".envrc", ".env.local", ".netrc", ".npmrc", ".pypirc", "keys/app.p12", "keys/app.pfx", "keys/app.jks",
                    "keys/app.keystore", "credentials", "credentials.json", "aws/credentials", "server.pem", "app.key", "id_ed25519",
                    ".git-credentials", ".htpasswd", ".pgpass", ".my.cnf", ".s3cfg", ".dockercfg", "ops/kubeconfig", "infra/prod.tfvars",
                    "vault.kdbx", "config/secrets.yml", "secret.json", "service-account-prod.json")

    def test_the_usual_credential_files_are_secret_names_and_ordinary_names_are_not(self):
        for rel in self.SECRET_NAMES:
            self.assertTrue(core_patch.secret_name(rel), rel)
        for rel in ("src/env.py", "keyboard.ts", "README.md", "pkg/credits.py", "monkey.py", ".github/x.yml", "npmrc.md",
                    "src/secrets.py", "src/secrets.ts", "secret_utils.py", "kubeconfig.md", "tfvars.py"):
            self.assertFalse(core_patch.secret_name(rel), rel)
        self.assertFalse(core_patch.secret_name(".env.example", templates_ok=True))
        self.assertTrue(core_patch.secret_name(".env.example"))

    def test_a_packet_for_any_of_them_is_refused_by_name(self):
        for rel in self.SECRET_NAMES:
            self.put(rel, "one\ntwo\n")
            with self.assertRaises(core_patch.PatchError, msg=rel) as cm:
                core_patch.locate(f"{rel}:2: [R1.1] x", 0, self.root)
            self.assertIn("holds secrets", str(cm.exception))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_link_to_a_secret_file_is_that_file_for_locating_packets_patching_and_checking_an_edit(self):
        for rel in self.SECRET_NAMES[:6]:
            self.put(rel, "one\ntwo\nthree\n")
            link = os.path.join(self.root, "notes.txt")
            os.symlink(os.path.join(self.root, *rel.split("/")), link)
            with self.assertRaises(core_patch.PatchError, msg=rel):
                core_patch.locate("notes.txt:2: [R1.1] x", 0, self.root)
            with self.assertRaises(core_patch.PatchError, msg=rel):
                core_patch.finding_packet("notes.txt:2: [R1.1] x", 1, self.root)
            v = self.check(block("notes.txt", "two", "2"))
            self.assertFalse(v.ok)
            self.assertTrue(any("holds secrets" in r for r in v.reasons), v.reasons)
            self.assertEqual(self.get(rel), "one\ntwo\nthree\n")
            self.assertTrue(core_patch.blocked("notes.txt", self.root), rel)
            os.remove(link)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_link_is_named_as_one_and_so_is_a_file_behind_a_linked_folder_and_a_plain_file_is_not(self):
        # (2026-11-12, claims check) A write to the name of a link replaced it with a copy and left its target as it was.
        self.put("lib/real.py", "a\nb\n")
        os.symlink(os.path.join("lib", "real.py"), os.path.join(self.root, "alias.py"))
        os.symlink("lib", os.path.join(self.root, "shared"))
        self.assertIn("is a link to lib/real.py", core_patch.link_reason("alias.py", self.root))
        self.assertIn("is a link to lib/real.py", core_patch.link_reason("shared/real.py", self.root))
        self.assertEqual(core_patch.link_reason("lib/real.py", self.root), "")
        self.assertEqual(core_patch.link_reason("../outside.py", self.root), "")      # not a link: the path check says what is wrong

    @unittest.skipUnless(hasattr(os, "link"), "needs hard links")
    def test_a_file_with_a_second_name_is_refused_when_one_name_is_asked_for_and_read_otherwise(self):
        # (2026-11-12, claims check) A hard link to a secret file has an ordinary name and no name check can tell.
        secret = self.put(".env", "one\ntwo\nthree\n")
        os.link(secret, os.path.join(self.root, "notes.txt"))
        with self.assertRaises(core_patch.PatchError) as cm:
            core_patch.locate("notes.txt:2: [R1.1] x", 0, self.root, single_name=True)
        self.assertIn("more than one name", str(cm.exception))
        self.assertEqual(core_patch.locate("notes.txt:2: [R1.1] x", 0, self.root).lines[1], "two")     # the default is as it was
        self.put("src/plain.py", "one\ntwo\n")
        self.assertEqual(core_patch.locate("src/plain.py:2: [R1.1] x", 0, self.root, single_name=True).line, 2)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_link_into_a_protected_folder_is_protected_for_a_patch_and_for_blocked(self):
        self.put(".claude/hooks/guard.py", "a\nb\n")
        os.symlink(os.path.join(self.root, ".claude", "hooks"), os.path.join(self.root, "lib"))
        self.assertIn("sets what the gate", core_patch.blocked("lib/guard.py", self.root))
        v = self.check(block("lib/guard.py", "a", "c"))
        self.assertFalse(v.ok)
        self.assertEqual(self.get(".claude/hooks/guard.py"), "a\nb\n")
        self.assertEqual(core_patch.blocked("src/ok.py", self.root), "")

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_link_into_a_denied_folder_is_denied_and_the_resolved_place_is_on_the_packet(self):
        self.put("private/notes.py", "one\ntwo\n")
        self.put("src/plain.py", "one\ntwo\n")
        os.symlink(os.path.join(self.root, "private"), os.path.join(self.root, "docs"))
        deny = lambda rel: rel.startswith("private/")        # what a deny_paths glob decides
        with self.assertRaises(core_patch.Denied) as cm:
            core_patch.locate("docs/notes.py:2: [R1.1] x", 0, self.root, deny=deny)
        self.assertIn("deny_paths", str(cm.exception))
        with self.assertRaises(core_patch.Denied):
            core_patch.locate("private/notes.py:2: [R1.1] x", 0, self.root, deny=deny)
        found = core_patch.locate("src/plain.py:2: [R1.1] x", 0, self.root, deny=deny)
        self.assertEqual((found.path, found.target), ("src/plain.py", "src/plain.py"))
        self.assertTrue(issubclass(core_patch.Denied, core_patch.PatchError))
        linked = core_patch.locate("docs/notes.py:2: [R1.1] x", 0, self.root)      # with no deny list it is read, and says where it led
        self.assertEqual((linked.path, linked.target), ("docs/notes.py", "private/notes.py"))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symbolic links")
    def test_a_link_to_git_is_refused(self):
        self.put(".git/config", "[core]\n")
        os.symlink(os.path.join(self.root, ".git"), os.path.join(self.root, "g"))
        with self.assertRaises(core_patch.PatchError):
            core_patch.locate("g/config:1: [R1.1] x", 0, self.root)


class TestTheEditAddsNoSuppressionAnywhereItWasNot(Folder):
    def test_a_marker_of_another_tool_on_a_changed_line_is_refused(self):
        for mark in ("# nosemgrep", "# pylint: skip-file", "# fmt: off", "# skipcq", "# rubocop:disable Style/All"):
            self.put("a.py", "x = 1\ny = 2\n")
            v = self.check(block("a.py", "y = 2", f"y = 3  {mark}"))
            self.assertFalse(v.ok, mark)
            self.assertTrue(any("switches a check off" in r for r in v.reasons), (mark, v.reasons))
        for mark in ("// tslint:disable-line", "// deno-lint-ignore", "// oxlint-disable-next-line", "/* v8 ignore next */",
                     "// prettier-ignore", "// @ts-expect-error"):
            self.put("a.ts", "const x = 1;\nconst y = 2;\n")
            v = self.check(block("a.ts", "const y = 2;", f"const y = 3; {mark}"))
            self.assertFalse(v.ok, mark)
            self.assertTrue(any("switches a check off" in r for r in v.reasons), (mark, v.reasons))

    def test_a_marker_moved_onto_a_changed_line_is_refused_even_though_the_count_is_the_same(self):
        self.put("a.py", "import os  # noqa: E401\nx = 1\ny = 2\n")
        v = self.check(block("a.py", "import os  # noqa: E401\nx = 1\ny = 2", "import os\nx = 1\ny = 3  # noqa: E401"))
        self.assertFalse(v.ok)
        self.assertTrue(any("switches a check off" in r for r in v.reasons), v.reasons)

    def test_a_marker_on_a_line_the_edit_does_not_touch_is_not_the_edits_to_answer_for(self):
        self.put("a.py", "import os  # noqa: E401\nx = 1\ny = 2\n")
        self.assertTrue(self.check(block("a.py", "y = 2", "y = 3")).ok)

    def test_a_new_file_with_a_marker_in_it_is_refused(self):
        v = self.check(block("new.py", "", "x = 1  # nosemgrep"))
        self.assertFalse(v.ok)

    def test_the_marker_list_is_one_list_with_the_tools_own_comment_in_it(self):
        for text in ("x  # noqa", "x  # type: ignore", "x  // eslint-disable-line", "x  // @ts-ignore", "#[allow(unused)]",
                     "x  // biome-ignore lint: y", "x  # brevio-core: ignore R9.1 -- a reason here"):
            self.assertTrue(core_patch.line_markers(text), text)
        self.assertFalse(core_patch.line_markers("x = compute(a, b)  # a note"))


class TestWhatAnEditDoesToTests(Folder):
    def test_custody_reasons_is_the_check_the_verdict_runs(self):
        old = "from pkg import f\n\ndef test_f():\n    assert f(1) == 2\n"
        new = old.replace("assert f(1) == 2", "assert True")
        found = core_patch.custody_reasons({"tests/test_f.py": (old, new, [])})
        self.assertTrue(any("Tautological" in m for m in found), found)
        self.put("tests/test_f.py", old)
        v = self.check(block("tests/test_f.py", "    assert f(1) == 2", "    assert True"))
        self.assertTrue(any("Tautological" in r for r in v.reasons), v.reasons)
        self.assertEqual(core_patch.custody_reasons({"src/a.py": ("x\n", "y\n", [])}), [])


if __name__ == "__main__":
    unittest.main()
