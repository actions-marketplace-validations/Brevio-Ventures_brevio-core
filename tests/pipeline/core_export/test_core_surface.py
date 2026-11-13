"""The command-line surface of ``brevio-core``: ``--version``, the marks of ``rules`` and ``explain``, the notes after a scan and
the quiet end of a command whose output pipe has closed (2026-11-13, revisit of the command line).

Run in a clean interpreter with only the exported tree on its path. Standard library only: the exporter copies this file beside
the code and it runs unchanged in the exported tree."""
import contextlib
import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from brevio.pipeline import core_surface
from brevio.pipeline.code_rules import PR_RULES_122
from brevio.pipeline.code_rules.details import scanned_by

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
_RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
_WITH_TREE = ("import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli, core_surface; "
              "core_surface.TREE = sys.argv[2]; raise SystemExit(core_cli.main(sys.argv[3:]))")
BAD_LINES = "try:\n    x = 1\nexcept:\n    x = 0\n"       # R5.4, which has a fixer
HAS_FIXERS = os.path.isfile(os.path.join(ROOT, "brevio", "pipeline", "code_rules", "fixers.py"))      # the 0.3 set and later
HAS_CONFIG = os.path.isfile(os.path.join(ROOT, "brevio", "pipeline", "core_config.py"))             # the 0.6 set and later
ANY_LINE = "const x = y as any;\n"                         # R9.1, which has none


def run_cli(cwd, *args, code=_RUN, extra=()):
    return subprocess.run([sys.executable, "-I", "-c", code, ROOT, *extra, *args], cwd=cwd, capture_output=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=60)


def write(d, rel, text):
    with open(os.path.join(d, rel), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class TestVersion(unittest.TestCase):
    def test_the_version_is_read_from_the_version_file_of_the_tree(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "VERSION", "1.2.3\n")
            self.assertEqual(core_surface.package_version(d), "1.2.3")
            for flag in ("--version", "-V"):
                res = run_cli(d, flag, code=_WITH_TREE, extra=(d,))
                self.assertEqual((res.returncode, res.stdout, res.stderr), (0, "brevio-core 1.2.3\n", ""), flag)

    def test_without_a_version_file_the_installed_metadata_answers_and_a_malformed_one_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(core_surface.package_version(d, lambda name: {"Version": ["0.9.1"]}), "0.9.1")
            self.assertIsNone(core_surface.package_version(d, lambda name: {"Version": ["not a version"]}))
            self.assertIsNone(core_surface.package_version(d, lambda name: None))
            self.assertIsNone(core_surface.package_version(d))

    def test_an_unknown_version_is_said_on_standard_error_with_exit_code_one(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "--version", code=_WITH_TREE, extra=(d,))
        self.assertEqual((res.returncode, res.stdout), (1, ""))
        self.assertIn("version is not known", res.stderr)

    def test_the_full_help_lists_the_version_flag(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("--version", run_cli(d, "help", "--all").stdout)


class TestRulesAndExplain(unittest.TestCase):
    def test_the_json_list_says_which_rules_scan_runs_and_keeps_its_old_fields(self):
        """``rules --json`` had only ``id`` and ``summary``: no one could tell the 13 rules ``scan`` runs from the rest."""
        with tempfile.TemporaryDirectory() as d:
            data = json.loads(run_cli(d, "rules", "--json").stdout)
        self.assertEqual(data["count"], len(PR_RULES_122))
        rows = {r["id"]: r for r in data["rules"]}
        self.assertEqual(sorted(i for i, r in rows.items() if r["scanned"]), scanned_by("code"))
        self.assertEqual(sorted(i for i, r in rows.items() if r["surface"]), scanned_by("dev"))
        self.assertEqual({"id", "summary", "scanned", "surface", "status"}, set(rows["R2.6"]))
        self.assertFalse(rows["R2.6"]["scanned"])
        self.assertEqual(rows["R2.6"]["status"], "stable")

    def test_the_text_list_marks_scanned_rules_after_the_summary_and_notes_the_rest_on_standard_error(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "rules")
        lines = {ln.split(":", 1)[0]: ln for ln in res.stdout.splitlines()}
        self.assertTrue(lines["R5.4"].startswith("R5.4: "))
        self.assertTrue(lines["R5.4"].endswith("[scan]"))
        self.assertTrue(lines["R1.1"].endswith("[scan --surface]"))
        self.assertNotIn("[scan", lines["R2.6"])
        self.assertIn("documented only", res.stderr)

    def test_the_scanned_flag_lists_only_what_scan_checks_and_combines_with_a_query(self):
        with tempfile.TemporaryDirectory() as d:
            ids = [ln.split(":", 1)[0] for ln in run_cli(d, "rules", "--scanned").stdout.splitlines()]
            self.assertEqual(sorted(ids), sorted(scanned_by("code")))
            one = json.loads(run_cli(d, "rules", "--scanned", "--json", "-q", "R2.").stdout)
        self.assertEqual([r["id"] for r in one["rules"]], ["R2.8"])

    def test_explain_says_when_scan_does_not_run_the_rule(self):
        with tempfile.TemporaryDirectory() as d:
            documented = run_cli(d, "explain", "R2.6").stdout
            surface = run_cli(d, "explain", "R1.1").stdout
            scanned = run_cli(d, "explain", "R2.8").stdout
        self.assertEqual(documented.splitlines()[2], "`brevio-core scan` does not run this rule.")
        self.assertIn("`brevio-core scan --surface` runs this rule", surface.splitlines()[2])
        self.assertNotIn("does not run", scanned)


class TestScanNotes(unittest.TestCase):
    @unittest.skipUnless(HAS_FIXERS, "the fixers ship from the 0.3 set")
    def test_a_finding_with_a_fixer_names_the_fix_command_on_standard_error_only(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", BAD_LINES)
            res = run_cli(d, "scan", "a.py")
            self.assertEqual(res.returncode, 1)
            self.assertIn("1 fixable: brevio-core scan a.py --fix --dry-run", res.stderr)
            self.assertNotIn("fixable", res.stdout)
            for fmt in ("json", "markdown", "github", "agent"):
                self.assertNotIn("fixable", run_cli(d, "scan", "a.py", "--format", fmt).stderr, fmt)

    def test_a_finding_without_a_fixer_adds_no_fix_line(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "b.ts", ANY_LINE)
            res = run_cli(d, "scan", "b.ts")
            self.assertEqual(res.returncode, 1)
            self.assertNotIn("fixable", res.stderr)

    @unittest.skipUnless(HAS_CONFIG, "brevio-core.toml is read from the 0.6 set")
    def test_a_select_that_names_only_documented_rules_warns_and_a_default_scan_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", BAD_LINES)
            self.assertNotIn("leave no rule", run_cli(d, "scan", "a.py").stderr)
            write(d, "brevio-core.toml", 'select = ["R2.6"]\n')
            res = run_cli(d, "scan", "a.py")
            self.assertEqual(res.returncode, 0)
            self.assertIn("leave no rule that scan checks", res.stderr)
            write(d, "brevio-core.toml", 'select = ["R2"]\n')
            self.assertNotIn("leave no rule", run_cli(d, "scan", "a.py").stderr)      # R2.8 is scanned: the group does something


    @unittest.skipUnless(HAS_CONFIG, "the 0.6 set and later read brevio-core.toml")
    def test_an_ignore_that_leaves_no_scanned_rule_is_named_as_the_cause_not_the_select(self):
        """The first wording blamed ``select`` when ``ignore = ["*"]`` was the cause."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", BAD_LINES)
            write(d, "brevio-core.toml", 'ignore = ["*"]\n')
            err = run_cli(d, "scan", "a.py").stderr
            self.assertIn("select and ignore settings", err)
            self.assertNotIn("the select setting names", err)


class TestFixCommand(unittest.TestCase):
    @staticmethod
    def fixable(named, diff, findings, fix_module, text="except:"):
        err = io.StringIO()
        fields = lambda f: {"path": "a.py", "line": 3, "rule": f.split("[")[1].split("]")[0]}      # noqa: E731
        with contextlib.redirect_stderr(err):
            core_surface.print_fixable(findings, fix_module, named, diff, fields, lambda path, lno: text)
        return err.getvalue()

    def test_the_fix_command_keeps_the_paths_and_the_diff_base_it_was_given(self):
        class Fix:
            FIXERS = {"R5.4": None}

            @staticmethod
            def fix_line(path, line, numbered=None, lno=1):
                return line, ["R5.4"]

        for named, diff, want in ((["a.py"], None, "brevio-core scan a.py --fix --dry-run"),
                                  ([], "", "brevio-core scan --diff --fix --dry-run"),
                                  ([], "main", "brevio-core scan --diff main --fix --dry-run")):
            with self.subTest(named=named, diff=diff):
                self.assertEqual(self.fixable(named, diff, ["a.py:3: [R5.4] x"], Fix), f"1 fixable: {want}\n")

    def test_only_findings_whose_line_a_fixer_rewrites_are_counted(self):
        """The first count was by rule: an empty ``catch`` in a ``.ts`` file fires R5.4, which has a fixer for ``.py`` only, so
        ``5 fixable`` was printed and ``--fix --dry-run`` made 4 fixes."""
        class Fix:
            FIXERS = {"R5.4": None}

            @staticmethod
            def fix_line(path, line, numbered=None, lno=1):
                return line, []

        self.assertEqual(self.fixable(["a.py"], None, ["a.py:3: [R5.4] x"], Fix), "")

    @unittest.skipUnless(HAS_FIXERS, "the fixers ship from the 0.3 set")
    def test_the_count_equals_the_fixes_a_dry_run_makes(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", BAD_LINES)
            write(d, "b.ts", "try { a() } catch (e) {}\n// @ts-ignore\nlet q: number = 1;\n")      # R5.4 with no fixer, R9.2 with one
            res = run_cli(d, "scan", "a.py", "b.ts")
            self.assertIn("2 fixable:", res.stderr)
            self.assertIn("2 fix(es)", run_cli(d, "scan", "a.py", "b.ts", "--fix", "--dry-run").stderr.replace("in 2 file(s) ", ""))

    def test_a_build_without_the_fixers_says_nothing(self):
        self.assertEqual(self.fixable([], None, ["a.py:3: [R5.4] x"], None), "")


class TestClosedPipe(unittest.TestCase):
    def test_a_command_whose_reader_has_gone_ends_quietly(self):
        """``explain R2.8 | head -3`` ended in a BrokenPipeError traceback when the reader closed before the write (0.9.1 too)."""
        for args in (["explain", "R2.8"], ["rules"], ["rules", "--json"]):
            with tempfile.TemporaryDirectory() as d:
                proc = subprocess.Popen([sys.executable, "-I", "-c", _RUN, ROOT, *args], cwd=d, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
                proc.stdout.close()
                err = proc.stderr.read().decode("utf-8", "replace")
                proc.stderr.close()
                code = proc.wait(timeout=60)
            self.assertNotIn("Traceback", err, args)
            self.assertNotIn("BrokenPipeError", err, args)
            if sys.platform != "win32":
                self.assertEqual(code, core_surface.BROKEN_PIPE, args)

    def test_guarded_turns_a_broken_pipe_into_the_code_a_shell_reports_and_passes_other_codes_through(self):
        def broken():
            raise BrokenPipeError

        self.assertEqual(core_surface.guarded(lambda: 3), 3)
        self.assertEqual(core_surface.guarded(broken), core_surface.BROKEN_PIPE)

    def test_guarded_takes_the_error_windows_raises_for_a_closed_pipe_as_a_broken_pipe(self):
        """On Windows a write to a pipe whose reader has gone raises ``OSError`` 22 (``EINVAL``), not ``BrokenPipeError``: ``rules | more``
        ended in a traceback there (first Windows run of 2026-11-13)."""
        def closed():
            raise OSError(errno.EINVAL, "Invalid argument")

        def bad_path():
            raise OSError(errno.EINVAL, "Invalid argument", "a<b")

        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(core_surface.guarded(closed), core_surface.BROKEN_PIPE)
            with self.assertRaises(OSError):
                core_surface.guarded(bad_path)       # an invalid file name is the command's own error, which must stay visible
        with mock.patch.object(sys, "platform", "linux"), self.assertRaises(OSError):
            core_surface.guarded(closed)

    def test_guarded_survives_a_command_started_with_standard_output_closed(self):
        """With ``>&-`` ``sys.stdout`` is None: the flush in ``guarded`` raised AttributeError, where 0.9.1 printed nothing and went on."""
        saved = sys.stdout
        sys.stdout = None
        try:
            code = core_surface.guarded(lambda: 0)
        finally:
            sys.stdout = saved
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
