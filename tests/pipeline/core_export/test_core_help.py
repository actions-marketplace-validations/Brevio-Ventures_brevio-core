"""The short help of a bare ``brevio-core``, of ``--help`` and of ``help``, and ``help --all`` (2026-11-12, improvement plan).

Run in a clean interpreter with only the exported tree on its path. Standard library only: the exporter copies this file beside
the code and it runs unchanged in the exported tree."""
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
_RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"


def run_cli(*args):
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run([sys.executable, "-I", "-c", _RUN, ROOT, *args], cwd=d, capture_output=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL, timeout=60)


# The same, with a person at the keyboard: the terminal check says yes. A test cannot allocate a terminal on every system.
_AT_A_TERMINAL = ("import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli, core_platform; "
                  "core_platform.is_terminal = lambda stream: True; raise SystemExit(core_cli.main(sys.argv[2:]))")


def listed(text):
    """The command names of the ``{a,b,c}`` list in the usage of the full help."""
    return re.search(r"\{([^}]*)\}", " ".join(text.split()).replace(" ", "")).group(1).split(",")


class TestTheShortHelp(unittest.TestCase):
    def test_a_bare_run_and_help_and_the_long_flags_print_the_same_short_list_and_exit_zero(self):
        bare = run_cli()
        self.assertEqual(bare.returncode, 0, bare.stderr)
        for args in (["--help"], ["-h"], ["help"]):
            res = run_cli(*args)
            self.assertEqual((res.returncode, res.stdout), (0, bare.stdout), args)
        self.assertIn("brevio-core help --all", bare.stdout)
        self.assertLess(len(bare.stdout.splitlines()), 16)

    def test_a_bare_run_at_a_terminal_prints_the_short_help_and_writes_nothing(self):
        """A bare run at a terminal used to open a menu, or, in a folder that was not set up, a wizard whose Enter wrote the
        workflow, the agent rules and the agent hook. The decision is that a bare run shows the short list (review of the
        first-five stage, 2026-11-12)."""
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run([sys.executable, "-I", "-c", _AT_A_TERMINAL, ROOT], cwd=d, capture_output=True, encoding="utf-8",
                                 input="\n", timeout=60)
            self.assertEqual(os.listdir(d), [])
        self.assertEqual((res.returncode, res.stdout), (0, run_cli().stdout), res.stderr)
        self.assertNotIn("Setup profile", res.stdout)

    def test_it_names_only_commands_that_this_build_has_and_the_two_every_build_has(self):
        names = listed(run_cli("help", "--all").stdout)
        rows = re.findall(r"(?m)^  (\w+)\s{2,}", run_cli().stdout)
        self.assertIn("scan", rows)
        self.assertIn("explain", rows)
        self.assertEqual(sorted(set(rows) - set(names)), [], "a row of the short help names a command the build lacks")
        self.assertLessEqual(len(rows), 6)

    def test_the_full_list_keeps_every_command_and_the_short_help_is_shorter(self):
        full = run_cli("help", "--all")
        self.assertEqual(full.returncode, 0, full.stderr)
        names = listed(full.stdout)
        for name in ("scan", "explain", "rules", "leaks", "help"):
            self.assertIn(name, names)
        self.assertLess(len(run_cli().stdout), len(full.stdout))

    def test_help_with_a_command_prints_that_commands_help_and_one_with_more_words_is_a_usage_error(self):
        """``help scan`` was a usage error ("help takes only --all"). It now prints what ``scan --help`` prints (2026-11-13)."""
        for name in ("scan", "explain", "rules", "help"):
            res = run_cli("help", name)
            direct = run_cli(name, "--help")
            self.assertEqual((res.returncode, res.stdout), (0, direct.stdout if name != "help" else run_cli("help", "--help").stdout), name)
        self.assertIn("--diff", run_cli("help", "scan").stdout)
        for args in (["scan", "extra"], ["--all", "scan"]):
            res = run_cli("help", *args)
            self.assertEqual((res.returncode, res.stdout), (2, ""), args)
            self.assertIn("brevio-core <command> --help", res.stderr)

    def test_help_with_an_unknown_command_names_the_commands_there_are(self):
        res = run_cli("help", "nosuch")
        self.assertEqual((res.returncode, res.stdout), (2, ""))
        self.assertIn("invalid choice: 'nosuch'", res.stderr)

    @unittest.skipUnless(os.path.isfile(os.path.join(ROOT, "brevio", "operator", "agent_cmds.py")), "add ships from the 0.2 set")
    def test_help_of_a_line_command_forwards_to_its_own_help(self):
        res = run_cli("help", "add")
        self.assertEqual((res.returncode, res.stdout), (0, run_cli("add", "--help").stdout))

    def test_the_help_of_one_command_is_unchanged_by_the_short_list(self):
        res = run_cli("scan", "--help")
        self.assertEqual(res.returncode, 0)
        self.assertIn("--diff", res.stdout)


if __name__ == "__main__":
    unittest.main()
