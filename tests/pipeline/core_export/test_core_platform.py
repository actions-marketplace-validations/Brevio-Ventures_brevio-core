"""The Windows and POSIX differences of the command line (``core_platform``), run with a legacy code page forced.

Standard library only: the exporter copies this file beside the code (the 0.1 set) and it runs unchanged there. The first
run of the exported tree on Windows (2026-11-12) failed on a non-ASCII character in a pipe, on git output read with the
wrong code page and on a path on another drive. Each case here makes that happen on any system: a stream is set to
``cp1252`` before the code runs, the locale is made ASCII, and ``os.path.relpath`` is made to refuse as it does across drives.
"""
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock

import tests.pipeline.core_export.test_core_portable as portable
from brevio.pipeline import core_platform

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LEGACY = "".join(f"sys.{name}.reconfigure(encoding='cp1252'); " for name in ("stdin", "stdout", "stderr"))
BOXES = "═✅ — café"          # box line, check mark, dash and an accent: only the accent is in cp1252


def python(code, *args, stdin=b"", env=None):
    """Run ``code`` under ``python -I`` with ROOT first on its path; return the finished process (bytes)."""
    script = "import sys; sys.path.insert(0, sys.argv[1]); " + code
    return subprocess.run([sys.executable, "-I", "-c", script, ROOT, *args], input=stdin, capture_output=True, timeout=60,
                          env=env or portable.child_env(os.path.dirname(sys.executable)))


class TestTheStreamsAreUtf8(unittest.TestCase):
    def test_a_pipe_with_a_legacy_code_page_cannot_print_what_the_commands_print_until_the_entry_point_sets_utf8(self):
        before = python(LEGACY + f"print({BOXES!r})")
        self.assertNotEqual(before.returncode, 0, "the premise: cp1252 cannot encode the box line")
        self.assertIn(b"UnicodeEncodeError", before.stderr)

    def test_after_use_utf8_streams_the_same_text_goes_out_as_utf_8(self):
        done = python(LEGACY + f"from brevio.pipeline import core_platform; core_platform.use_utf8_streams(); print({BOXES!r})")
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        self.assertEqual(done.stdout.decode("utf-8").strip(), BOXES)

    def test_after_use_utf8_streams_what_arrives_on_standard_input_is_read_as_utf_8(self):
        done = python(LEGACY + "from brevio.pipeline import core_platform; core_platform.use_utf8_streams(); "
                      "print(ascii(sys.stdin.read()))", stdin="café —".encode())
        self.assertEqual(done.stdout.decode("ascii").strip(), ascii("café —"))

    def test_the_command_line_entry_point_sets_utf8_before_anything_prints(self):
        done = python(LEGACY + "from brevio.pipeline import core_cli\n"
                      "core_cli.core_platform.use_utf8_streams = lambda: sys.stdout.write('called\\n')\n"
                      "try:\n    core_cli.main(['--help'])\nexcept SystemExit:\n    pass\n")
        self.assertTrue(done.stdout.replace(b"\r\n", b"\n").startswith(b"called\n"), done.stdout[:80])   # text mode: CRLF on Windows

    def test_a_stream_that_is_utf_8_already_is_left_as_it_is(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
        with unittest.mock.patch.object(sys, "stdout", stream):
            core_platform.use_utf8_streams()
        self.assertEqual(stream.errors, "strict")

    def test_a_stream_that_cannot_be_reconfigured_is_skipped_without_an_error(self):
        with unittest.mock.patch.object(sys, "stdout", io.StringIO()), unittest.mock.patch.object(sys, "stderr", io.StringIO()):
            core_platform.use_utf8_streams()


class TestWhatGitPrintsIsReadAsUtf8(unittest.TestCase):
    def test_a_commit_message_with_an_accent_is_read_though_the_locale_is_ascii(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from brevio.pipeline import core_cli\n"
                "print(ascii(core_cli._git('log', '-1', '--format=%s')))\n")
        env = portable.child_env(portable.git_only_path(self.folder()), PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C")
        done = subprocess.run([sys.executable, "-c", code, ROOT], cwd=self.repo, capture_output=True, timeout=60, env=env)
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        self.assertEqual(done.stdout.decode("ascii").strip(), ascii("caf\u00e9 \u2014 fixed\n"))

    def folder(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def setUp(self):
        self.repo = self.folder()
        subprocess.run(["git", "-C", self.repo, "init", "-q"], check=True, capture_output=True, timeout=60)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",
                        "-m", "caf\u00e9 \u2014 fixed"], check=True, capture_output=True, timeout=60)


class TestAPathIsShownTheWayTheToolMatchesIt(unittest.TestCase):
    def test_a_path_is_relative_to_the_start_and_uses_forward_slashes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(core_platform.shown(os.path.join(d, "src", "new.ts"), d), "src/new.ts")

    def test_a_backslash_separator_is_shown_as_a_slash(self):
        with unittest.mock.patch("os.path.relpath", return_value="src\\new.ts"), unittest.mock.patch("os.sep", "\\"):
            self.assertEqual(core_platform.shown("anything"), "src/new.ts")

    def test_a_path_on_another_drive_is_shown_in_full_instead_of_raising(self):
        error = ValueError("path is on mount 'C:', start on mount 'D:'")
        with unittest.mock.patch("os.path.relpath", side_effect=error):
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(core_platform.shown(os.path.join(d, "a.ts")), os.path.join(d, "a.ts").replace(os.sep, "/"))


class _WindowsConsoleIO:
    """Stands in for the reader Python opens on a real Windows console (the code tells it by this name)."""


class _Stream:
    def __init__(self, tty, raw=None):
        self.tty = tty
        self.buffer = types.SimpleNamespace(raw=raw) if raw is not None else None

    def isatty(self):
        return self.tty


class TestWhoIsAtTheTerminal(unittest.TestCase):
    def test_a_stream_that_is_not_a_terminal_is_not_one(self):
        self.assertFalse(core_platform.is_terminal(_Stream(False, _WindowsConsoleIO())))

    def test_a_stand_in_stream_that_says_it_is_a_terminal_is_taken_at_its_word(self):
        self.assertTrue(core_platform.is_terminal(_Stream(True)))

    def test_on_windows_the_null_device_is_not_a_terminal_though_isatty_says_so_and_a_console_is(self):
        with unittest.mock.patch.object(sys, "platform", "win32"):
            self.assertFalse(core_platform.is_terminal(_Stream(True, io.FileIO(os.devnull))))
            self.assertTrue(core_platform.is_terminal(_Stream(True, _WindowsConsoleIO())))


if __name__ == "__main__":
    unittest.main()
