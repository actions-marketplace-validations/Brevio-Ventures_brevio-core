"""``check`` and ``heal`` hold one file's lines at a time, not every file's (2026-11-14, bounded memory).

Standard library only: the exporter copies this file beside the code and it runs unchanged in the exported tree."""
import os
import shutil
import subprocess
import tempfile
import tracemalloc
import unittest
from unittest import mock

from brevio.pipeline import core_diff
from brevio.pipeline.gates import core_gate_lines, core_gates


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _repo(root):
    os.makedirs(root)
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-q",
                    "--allow-empty", "-m", "start"], check=True)


class BoundedCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _peak(self, name, files, lines_of=None):
        root = os.path.join(self.tmp, name)
        _repo(root)
        for i in range(files):
            _write(root, f"pkg{i % 5}/mod{i}x.py", "".join(f"value_{j} = {j}\n" for j in range(1500)))
        tracemalloc.start()
        try:
            lines = lines_of(root) if lines_of else None
            result = core_gates.run_check(root=root, base="HEAD", lines=lines)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertTrue(result["passed"], result["findings"])
        return peak

    def test_the_peak_memory_of_check_does_not_grow_with_the_number_of_files(self):
        self._peak("warm", 2)       # the first check loads optional modules; that is not the memory of a check
        small = self._peak("small", 15)
        large = self._peak("large", 90)
        # Measured on 2026-11-14 on the machine that wrote this test, a new repository of 1,500-line files (all untracked):
        # the code that read every file when it collected the lines peaked at 9,101,642 bytes for 15 files and 25,348,702
        # for 90 (ratio 2.79); reading one file at a time peaked at 6,301,761 and 6,307,931 (ratio 1.00). The bound sits
        # between the two.
        self.assertLess(large, small * 1.5, f"peak {small} bytes for 15 files, {large} bytes for 90 files")

    def test_the_peak_memory_of_check_on_the_lines_heal_selects_does_not_grow_either(self):
        def heal_lines(root):
            return core_diff.FileLines({f: os.path.join(root, f) for f in _py_files(root)}, resolved=True)     # as ``select_lines`` returns them
        self._peak("hwarm", 2, heal_lines)
        small = self._peak("hsmall", 15, heal_lines)
        large = self._peak("hlarge", 90, heal_lines)
        self.assertLess(large, small * 1.5, f"peak {small} bytes for 15 files, {large} bytes for 90 files")

    def test_the_lines_of_a_new_file_are_read_when_asked_and_not_kept(self):
        root = os.path.join(self.tmp, "r")
        _repo(root)
        _write(root, "a.py", "x = 1\r\ny = 2\n")
        lines = core_gate_lines.collect_diff_lines(root, "HEAD")
        self.assertIsInstance(lines, core_gate_lines.DiffLines)
        self.assertEqual(list(lines), ["a.py"])
        self.assertEqual(lines["a.py"], [(1, "x = 1"), (2, "y = 2")])
        self.assertIsNot(lines["a.py"], lines["a.py"])
        with mock.patch.object(core_diff, "_read_resolved", side_effect=AssertionError("read")):
            self.assertIn("a.py", lines)
            self.assertNotIn("b.py", lines)
            self.assertEqual(len(lines), 1)

    def test_the_changed_lines_of_a_tracked_file_are_the_added_lines_and_come_before_the_new_files(self):
        root = os.path.join(self.tmp, "t")
        _repo(root)
        _write(root, "old.py", "a = 1\nb = 2\n")
        subprocess.run(["git", "-C", root, "add", "old.py"], check=True)
        subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "one"], check=True)
        _write(root, "old.py", "a = 1\nb = 3\nc = 4\n")
        _write(root, "new.py", "z = 0\n")
        _write(root, "vendor/v.py", "x = 1\n")
        lines = core_gate_lines.collect_diff_lines(root, "HEAD")
        self.assertEqual(list(lines), ["old.py", "new.py"])
        self.assertEqual(lines["old.py"], [(2, "b = 3"), (3, "c = 4")])
        self.assertEqual(lines["new.py"], [(1, "z = 0")])
        self.assertEqual(core_gate_lines.collect_diff_lines(root, "HEAD", ["new.py"]).keys(), {"new.py"})

    def test_a_folder_that_is_no_checkout_reads_its_code_files_when_asked(self):
        root = os.path.join(self.tmp, "n")
        _write(root, "src/a.ts", "const a = 1;\n")
        _write(root, "node_modules/b.ts", "const b = 1;\n")
        lines = core_gate_lines.collect_non_git_code_lines(root, None)
        self.assertEqual(list(lines), ["src/a.ts"])
        self.assertEqual(lines["src/a.ts"], [(1, "const a = 1;")])

    def test_the_names_the_commands_and_tests_use_are_still_found_in_core_gates(self):
        for name in ("_git", "_git_out", "_resolve_base", "_matches_filter", "_collect_non_git_code_lines", "collect_diff_lines"):
            self.assertTrue(callable(getattr(core_gates, name)), name)
        self.assertTrue(core_gates._matches_filter("a/b.py", ["a"]))
        self.assertFalse(core_gates._matches_filter("c/b.py", ["a"]))


def _py_files(root):
    return [os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/")
            for d, _dirs, names in os.walk(root) if ".git" not in d.split(os.sep) for n in names if n.endswith(".py")]


if __name__ == "__main__":
    unittest.main()
