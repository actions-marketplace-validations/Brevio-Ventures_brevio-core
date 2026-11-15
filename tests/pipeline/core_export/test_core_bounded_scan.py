"""A scan of a folder holds one file's lines at a time, not every file's (2026-11-13, bounded memory).

Standard library only: the exporter copies this file beside the code and it runs unchanged in the exported tree."""
import contextlib
import io
import os
import shutil
import tempfile
import tracemalloc
import unittest
from unittest import mock

from brevio.pipeline import core_cli, core_diff


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _eager(root):
    """What ``collect_files`` returned before it read lazily: every file's lines, read when the folder is walked."""
    out = {}
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = core_cli.prune_dirs(dirpath, dirs)
        for n in sorted(names):
            if (n.endswith(core_cli.CODE_SUFFIXES) or n == "package.json") and not core_cli._BUNDLE_NAME.search(n):
                lines = core_diff.read(os.path.join(dirpath, n))
                if not any(lno <= 2 and len(text) > core_cli.LONG_LINE for lno, text in lines[:2]):
                    out[core_cli.core_platform.shown(os.path.join(dirpath, n))] = lines
    return out


class BoundedScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _scan_peak(self, name, files):
        root = os.path.join(self.tmp, name)
        for i in range(files):
            _write(root, f"pkg{i % 5}/m{i}.py", "".join(f"value_{j} = {j}\n" for j in range(1500)))
        tracemalloc.start()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = core_cli.main(["scan", root])
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(code, 0)
        return peak

    def test_peak_memory_does_not_grow_with_the_number_of_files(self):
        self._scan_peak("warm", 2)      # the first scan loads the optional modules; that is not the memory of a scan
        small = self._scan_peak("small", 15)
        large = self._scan_peak("large", 90)
        # Keeping every file's lines would make the second peak about six times the first; one file at a time makes
        # them equal. On the code that kept every file, this ratio was close to six on the machine that wrote the test.
        self.assertLess(large, small * 1.5, f"peak {small} bytes for 15 files, {large} bytes for 90 files")

    def test_the_folder_reads_as_the_eager_walk_read_it(self):
        root = os.path.join(self.tmp, "t")
        _write(root, "a.py", "x = 1\r\ny = 2\n")
        _write(root, "sub/b.ts", "const b = 1;\n")
        _write(root, "sub/app.min.js", "var a=1;\n")
        _write(root, "sub/long.js", "x" * 600 + "\nvar a = 1;\n")
        _write(root, "sub/long2.js", "var a = 1;\n" + "x" * 600 + "\n")
        _write(root, "sub/edge.js", "x" * 500 + "\r\n" + "y" * 500 + "\r\nz\n")
        _write(root, "sub/sep.js", "x" * 300 + " " + "y" * 300 + "\nz\n")
        _write(root, "sub/big.py", "#" * 10 + "\n" * (core_cli.MAX_BYTES // 1 + 1))
        _write(root, "vendor/v.py", "x = 1\n")
        _write(root, "node_modules/n.js", "x = 1\n")
        _write(root, "nested/.git/HEAD", "ref\n")
        _write(root, "nested/n.py", "x = 1\n")
        _write(root, "package.json", "{}\n")
        _write(root, "bom.py", "\ufeffx = 1\r\ny = 2\r\n")
        _write(root, "bad.py", "x = 1\n")
        with open(os.path.join(root, "bad.py"), "ab") as fh:
            fh.write(b"\xff\xfe\x80 junk\n")
        got = core_cli.collect_files([root])
        want = _eager(root)
        self.assertEqual(list(got), list(want))
        self.assertEqual({k: got[k] for k in got}, want)
        self.assertEqual(got[core_cli.core_platform.shown(os.path.join(root, "sub", "big.py"))], [])

    def test_a_file_named_on_the_command_line_is_read_as_asked(self):
        path = os.path.join(self.tmp, "named.min.js")
        _write(self.tmp, "named.min.js", "var a = 1;\n")
        got = core_cli.collect_files([path])
        self.assertEqual(list(got.values()), [[(1, "var a = 1;")]])

    def test_a_file_that_is_missing_asks_git_once_and_not_on_every_pass(self):
        calls = []

        def fake_git(*args):
            calls.append(args)
            return os.path.join(self.tmp, "top") + "\n"
        _write(self.tmp, "top/gone.py", "x = 1\n")
        with mock.patch.object(core_diff, "git", fake_git):
            files = core_diff.FileLines({"gone.py": os.path.join("no", "such", "dir", "gone.py")})
            for _ in range(3):
                self.assertEqual(files["gone.py"], [])
            self.assertEqual(len(calls), 1)
            self.assertEqual(core_diff.FileLines({"gone.py": "gone.py"})["gone.py"], [(1, "x = 1")])
            self.assertEqual(len(calls), 2)
            self.assertEqual(files.where(lambda _k: True)["gone.py"], [])
            self.assertEqual(len(calls), 2)

    @unittest.skipUnless(hasattr(os, "symlink") and os.path.exists("/dev/zero"), "needs a symlink to a device")
    def test_a_file_that_reports_no_size_and_never_ends_reads_as_empty(self):
        link = os.path.join(self.tmp, "zero.py")
        try:
            os.symlink("/dev/zero", link)
        except OSError:
            self.skipTest("this machine does not allow symlinks")
        self.assertEqual(core_diff.read(link), [])
        self.assertIsNone(core_cli._read_raw(link))

    def test_asking_whether_a_file_is_named_does_not_read_it(self):
        _write(self.tmp, "a.py", "x = 1\n")
        files = core_diff.FileLines({"a.py": os.path.join(self.tmp, "a.py")})
        with mock.patch.object(core_diff, "_read_resolved", side_effect=AssertionError("read")):
            self.assertIn("a.py", files)
            self.assertNotIn("b.py", files)

    def test_the_lines_are_not_kept_between_reads(self):
        root = os.path.join(self.tmp, "k")
        _write(root, "a.py", "x = 1\n")
        got = core_cli.collect_files([root])
        key = next(iter(got))
        self.assertIsNot(got[key], got[key])

    def test_the_text_of_a_finding_line_reads_the_file_on_disk(self):
        _write(self.tmp, "a.py", "one\ntwo\n")
        _write(self.tmp, "b.py", "three\n")
        text_of = core_cli.line_reader()
        a, b = os.path.join(self.tmp, "a.py"), os.path.join(self.tmp, "b.py")
        self.assertEqual((text_of(a, 2), text_of(b, 1), text_of(a, 1), text_of(a, 9)), ("two", "three", "one", ""))

    def test_an_empty_or_missing_file_is_read_once_for_all_the_findings_in_it(self):
        reads = []
        real = core_cli._read

        def counting(path):
            reads.append(path)
            return real(path)
        _write(self.tmp, "empty.py", "")
        text_of = core_cli.line_reader()
        with mock.patch.object(core_cli, "_read", counting):
            for lno in (1, 2, 3):
                self.assertEqual(text_of(os.path.join(self.tmp, "empty.py"), lno), "")
        self.assertEqual(len(reads), 1)


if __name__ == "__main__":
    unittest.main()
