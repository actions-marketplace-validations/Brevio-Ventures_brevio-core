"""What the guard tests share: an exported tree for one set, a clean child interpreter, and temporary git repositories.

Standard library only: the exporter copies this file beside the tests (the 0.2 set) and it runs unchanged there. In the
harness a test first exports its own set and runs the commands against that copy; in the exported tree the tree itself is
used. Children start with ``sys.executable -I`` and a PATH that finds git and nothing else (2026-11-12, improvement plan).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import tests.pipeline.core_export.test_core_portable as portable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EXPORTER = os.path.join(ROOT, "tools", "export_core.py")      # present in the harness checkout only
RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
# The same, with a person at the keyboard: the terminal check says yes. A test cannot allocate a terminal on every system.
AT_A_TERMINAL = ("import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli, core_platform; "
                 "core_platform.is_terminal = lambda stream: True; raise SystemExit(core_cli.main(sys.argv[2:]))")
FIXED = "result = compute_legacy_total(items)\n"
FIXING = "result = compute_total(items)\n"
# A fake key in the AWS access key format (the documented example), joined at run time so this file is not a finding.
AWS_EXAMPLE_LINE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
GUARD_ID = "guard-no-legacy-total"


class Tree:
    """The exported tree of one set (or this tree, outside the harness), and the commands run against it."""

    def __init__(self, set_name):
        self.set_name = set_name
        self.path = ROOT
        self._export = None
        self._bin = tempfile.TemporaryDirectory()    # a PATH holding git and nothing else, so no coding client is found on it
        self.env_path = portable.git_only_path(self._bin.name)

    def open(self):
        if os.path.isfile(EXPORTER):
            self._export = tempfile.TemporaryDirectory()
            self.path = os.path.join(self._export.name, "core")
            subprocess.run([sys.executable, EXPORTER, "--out", self.path, "--set", self.set_name], check=True,
                           capture_output=True, timeout=120)

    def close(self):
        self._bin.cleanup()
        if self._export is not None:
            self._export.cleanup()

    def run(self, cwd, *args, code=RUN, stdin=""):
        """``brevio-core`` with ``args`` in ``cwd``."""
        return subprocess.run([sys.executable, "-I", "-c", code, self.path, *args], cwd=cwd, input=stdin, capture_output=True,
                              encoding="utf-8", timeout=120, env=portable.child_env(self.env_path, cwd))


def git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                   timeout=60, env=portable.git_env())


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def read(d, rel):
    with open(os.path.join(d, rel), encoding="utf-8") as fh:
        return fh.read()


def read_json(d, rel):
    return json.loads(read(d, rel))


def guards(d):
    return read_json(d, ".brevio/guards.json")["guards"]


def edit_guard(d, guard_id, **changes):
    """Set fields of one guard in the file, as a person editing it by hand would."""
    doc = read_json(d, ".brevio/guards.json")
    doc["guards"][guard_id].update(changes)
    write(d, ".brevio/guards.json", json.dumps(doc, indent=2))


def repo_with_a_fix():
    """A repository whose last commit replaces ``compute_legacy_total`` with ``compute_total`` in ``src/calc.py``."""
    d = tempfile.TemporaryDirectory()
    git(d.name, "init", "-q", "-b", "main")
    write(d.name, "src/calc.py", FIXED)
    git(d.name, "add", ".")
    git(d.name, "commit", "-q", "-m", "first")
    write(d.name, "src/calc.py", FIXING)
    git(d.name, "commit", "-q", "-am", "fix")
    return d


def learned(tree):
    """A repository with a fix whose guard (``GUARD_ID``) is kept, and the guards committed."""
    d = repo_with_a_fix()
    res = tree.run(d.name, "learn", "--name", "no legacy total")
    assert res.returncode == 0, res.stderr
    git(d.name, "add", "-A")
    git(d.name, "commit", "-q", "-m", "add the guard")
    return d


class TestTheFixtures(unittest.TestCase):
    """The fixtures are tests of the guard tests' own set-up: a tree runs a command, and a person's terminal is simulated."""

    def test_a_tree_runs_a_command_in_a_clean_interpreter_whose_path_finds_git_only(self):
        tree = Tree("0.2")
        tree.open()
        try:
            with tempfile.TemporaryDirectory() as d:
                res = tree.run(d, "learn", "--help")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("--replay", res.stdout)
        finally:
            tree.close()

    def test_a_learned_repository_holds_the_fix_the_guard_and_a_commit_of_both(self):
        tree = Tree("0.2")
        tree.open()
        try:
            with learned(tree) as d:
                self.assertEqual(list(guards(d)), [GUARD_ID])
                self.assertTrue(os.path.isfile(os.path.join(d, ".agents", "rules", f"{GUARD_ID}.md")))
        finally:
            tree.close()


if __name__ == "__main__":
    unittest.main()
