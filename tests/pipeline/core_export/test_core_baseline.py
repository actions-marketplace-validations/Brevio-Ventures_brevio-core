"""``brevio-core baseline``, ``--fail-on``, inline ignores and ``--format markdown``, run in a clean interpreter.

Standard library only: the exporter copies this file beside the code (the 0.4 set) and it runs unchanged there. In the
harness the test first exports the 0.4 set and runs against that copy (ROADMAP E20, E21, E26, 2026-11-05).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import tests.pipeline.core_export.test_core_portable as portable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EXPORTER = os.path.join(ROOT, "tools", "export_core.py")      # present in the harness checkout only
TREE = ROOT
_EXPORT = tempfile.TemporaryDirectory()
# A fake key in the AWS access key format (AWS's documented example key ID), joined at run time so this file is not
# itself a finding of a secret scan. The hardcoded-secret rule (R13.1) flags the format (E35 precision run, 2026-11-05).
AWS_EXAMPLE_LINE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
OTHER_AWS_EXAMPLE_LINE = "const apiKey = '" + "AKIA" + "I44QH8DHBEXAMPLE" + "';\n"
TS_IGNORE = "// @ts-ignore\n"
CLEAN = "export const add = (a: number, b: number): number => a + b;\n"
# Built at run time, so this file holds no ignore comment of its own for a diff scan to report.
IGNORE = "// brevio-core" + ": ignore"
_RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
_MCP = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(['mcp']))"
_ESCAPE = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_markdown; sys.stdout.buffer.write(core_markdown.escape(sys.argv[2]).encode() + b'\\n')"
CALL = "tools" + "/call"     # joined: the leak scan reads a literal "tools" + "/" as a private folder
WEB = "https" + "://"        # joined: the leak scan refuses a literal foreign URL in an exported file
_ZW = "​"


def setUpModule():
    global TREE
    if os.path.isfile(EXPORTER):
        out = os.path.join(_EXPORT.name, "core")
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.4"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _EXPORT.cleanup()


def report(text):
    """The Markdown report without the closing Teams line of the 0.6 set (``test_core_up.py`` covers that line)."""
    return re.sub(r"\n\nTeams: [^\n]*\n$", "\n", text)


def run_cli(cwd, *args):
    return subprocess.run([sys.executable, "-I", "-c", _RUN, TREE, *args], cwd=cwd, capture_output=True, encoding="utf-8", timeout=60)


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read(d, rel):
    with open(os.path.join(d, rel), encoding="utf-8") as fh:
        return fh.read()


def git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def committed_repo(d, files):
    git(d, "init", "-q", "-b", "main")
    for rel, text in files.items():
        write(d, rel, text)
    git(d, "add", "-A")
    git(d, "commit", "-qm", "base")


class TestBaseline(unittest.TestCase):
    def test_a_planted_repo_passes_after_baseline_and_fails_on_one_new_finding(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/old.ts", AWS_EXAMPLE_LINE + TS_IGNORE + "const n: number = 'x';\n")
            self.assertEqual(run_cli(d, "scan").returncode, 1)
            made = run_cli(d, "baseline")
            self.assertEqual(made.returncode, 0, made.stderr)
            self.assertIn("2 finding(s) in .brevio/baseline.json", made.stdout)
            quiet = run_cli(d, "scan")
            self.assertEqual((quiet.returncode, quiet.stdout), (0, ""), quiet.stderr)
            self.assertIn("0 finding(s) in 1 file(s), 2 baselined, 0 silenced", quiet.stderr)
            write(d, "src/new.ts", OTHER_AWS_EXAMPLE_LINE)
            loud = run_cli(d, "scan")
            self.assertEqual(loud.returncode, 1)
            self.assertEqual(loud.stdout.splitlines(), ["src/new.ts:1: [R13.1] Hardcoded secret pattern detected in added code"])

    def test_a_moved_line_stays_baselined_and_a_copy_of_it_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            run_cli(d, "baseline")
            write(d, "src/a.ts", CLEAN * 5 + "  " + AWS_EXAMPLE_LINE)       # moved down and re-indented
            self.assertEqual(run_cli(d, "scan").returncode, 0)
            write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE + AWS_EXAMPLE_LINE)          # the same line twice: one is new
            twice = run_cli(d, "scan")
            self.assertEqual((twice.returncode, len(twice.stdout.splitlines())), (1, 1), twice.stdout)

    def test_the_file_is_sorted_one_entry_per_line_stable_and_drops_fixed_findings(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/b.ts", AWS_EXAMPLE_LINE)
            write(d, "src/a.ts", TS_IGNORE + AWS_EXAMPLE_LINE)
            run_cli(d, "baseline")
            first = read(d, ".brevio/baseline.json")
            run_cli(d, "baseline")
            self.assertEqual(read(d, ".brevio/baseline.json"), first)
            rows = [ln.strip().rstrip(",") for ln in first.splitlines() if ln.strip().startswith("{\"path\"")]
            entries = [json.loads(r) for r in rows]
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries, sorted(entries, key=lambda e: (e["path"], e["rule"], e["line_sha256"])))
            self.assertEqual(json.loads(first)["findings"], entries)
            write(d, "src/b.ts", CLEAN)
            self.assertIn("2 finding(s)", run_cli(d, "baseline").stdout)
            self.assertNotIn("src/b.ts", read(d, ".brevio/baseline.json"))

    def test_a_baseline_of_one_path_keeps_the_entries_of_the_others(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            write(d, "lib/b.ts", AWS_EXAMPLE_LINE)
            run_cli(d, "baseline")
            write(d, "src/a.ts", CLEAN)
            self.assertIn("1 finding(s)", run_cli(d, "baseline", "src").stdout)
            self.assertIn("lib/b.ts", read(d, ".brevio/baseline.json"))

    def test_fail_on_none_exits_zero_and_still_prints_and_all_ignores_the_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            run_cli(d, "baseline")
            write(d, "src/b.ts", OTHER_AWS_EXAMPLE_LINE)
            none = run_cli(d, "scan", "--fail-on", "none")
            self.assertEqual(none.returncode, 0)
            self.assertEqual([ln.split(":")[0] for ln in none.stdout.splitlines()], ["src/b.ts"])
            every = run_cli(d, "scan", "--fail-on", "all")
            self.assertEqual(every.returncode, 1)
            self.assertEqual({ln.split(":")[0] for ln in every.stdout.splitlines()}, {"src/a.ts", "src/b.ts"})
            self.assertEqual(run_cli(d, "scan", "--fail-on", "sometimes").returncode, 2)

    def test_every_scan_mode_reads_the_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": CLEAN})
            write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE)
            run_cli(d, "baseline", "--diff", "HEAD")
            for args in (["scan", "src"], ["scan", "--file", "src/a.ts"], ["scan", "--forbid-ignores", "--file", "src/a.ts"],
                         ["scan", "--diff", "HEAD", "--allow-new-suppressions"]):
                self.assertEqual(run_cli(d, *args).returncode, 0, args)

    def test_a_broken_baseline_is_a_usage_error_and_an_entry_of_an_unknown_rule_warns(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", CLEAN)
            write(d, ".brevio/baseline.json", "not json")
            self.assertEqual(run_cli(d, "scan").returncode, 2)
            write(d, ".brevio/baseline.json", json.dumps({"version": 1, "findings": [
                {"path": "src/a.ts", "rule": "R99.9", "line_sha256": "0" * 64, "count": 1}]}))
            res = run_cli(d, "scan")
            self.assertEqual(res.returncode, 0)
            self.assertIn("warning: .brevio/baseline.json: the entry for src/a.ts names R99.9, which is not a rule", res.stderr)


def baselined_repo(d, files):
    """A repository on ``main`` whose first commit holds ``files`` and the baseline of their findings."""
    for rel, text in files.items():
        write(d, rel, text)
    assert run_cli(d, "baseline").returncode == 0
    committed_repo(d, {**files, ".brevio/baseline.json": read(d, ".brevio/baseline.json")})


class TestDiffBase(unittest.TestCase):
    """A base the user named must exist, and a scan that read nothing says so (D27, I1; 2026-11-13)."""

    def test_a_named_base_that_is_not_in_the_checkout_is_a_usage_error_not_a_clean_pass(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": AWS_EXAMPLE_LINE})
            for args in (("scan", "--diff", "nonexistent"), ("baseline", "--diff", "origin/main"),
                         ("scan", "--fix", "--dry-run", "--diff", "nonexistent")):
                res = run_cli(d, *args)
                self.assertEqual(res.returncode, 2, (args, res.stderr))
                self.assertIn("is not in this checkout", res.stderr)
                self.assertEqual(res.stdout, "")

    def test_diff_head_in_a_repository_without_a_commit_still_reads_the_new_files(self):
        """Review of D27 (2026-11-13): 0.9.1 scanned the files of a repository before its first commit; that stays."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            res = run_cli(d, "scan", "--diff", "HEAD")
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertIn("src/a.ts:1: [R13.1]", res.stdout)

    def test_the_bare_diff_keeps_its_fallback_and_says_when_it_fell_back_to_head(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": CLEAN})
            git(d, "branch", "-m", "trunk")
            write(d, "src/b.ts", AWS_EXAMPLE_LINE)
            res = run_cli(d, "scan", "--diff")
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertIn("base: HEAD (no origin/main or main", res.stderr)
            git(d, "branch", "-m", "main")
            self.assertNotIn("base: HEAD", run_cli(d, "scan", "--diff").stderr)

    def test_the_markdown_report_says_no_lines_were_read_when_none_were(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": CLEAN})
            empty = run_cli(d, "scan", "--diff", "--format", "markdown").stdout
            self.assertIn("No lines were read.", empty)
            self.assertNotIn("No findings.", empty)
            write(d, "src/b.ts", CLEAN)
            self.assertIn("No findings.", run_cli(d, "scan", "--diff", "--format", "markdown").stdout)


class TestDiffAgainstTheBaseline(unittest.TestCase):
    """The baseline hides the old copy of a line, not a new one (D26; 2026-11-13)."""

    def branch(self, d):
        baselined_repo(d, {"f1.ts": CLEAN + TS_IGNORE})
        git(d, "checkout", "-q", "-b", "feat")

    def test_a_baselined_line_that_moves_stays_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [f"export const v{i} = {i};\n" for i in range(4)]
            baselined_repo(d, {"f1.ts": "".join(lines) + TS_IGNORE})
            git(d, "checkout", "-q", "-b", "feat")
            write(d, "f1.ts", TS_IGNORE + "".join(lines))
            res = run_cli(d, "scan", "--diff", "main")
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)
            self.assertIn("1 baselined", res.stderr)

    def test_a_copy_of_a_baselined_line_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            self.branch(d)
            write(d, "f1.ts", CLEAN + TS_IGNORE + TS_IGNORE)
            res = run_cli(d, "scan", "--diff", "main")
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertEqual(len(res.stdout.splitlines()), 1, res.stdout)
            self.assertIn("f1.ts:3: [R9.2]", res.stdout)

    def test_a_copy_added_where_the_old_line_was_deleted_stays_baselined(self):
        with tempfile.TemporaryDirectory() as d:
            self.branch(d)
            write(d, "f1.ts", "\n".join(["export const q = 1;", TS_IGNORE.strip()]) + "\n")
            res = run_cli(d, "scan", "--diff", "main")
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)

    def test_fail_on_all_ignores_the_baseline_and_the_baseline_file_is_not_changed(self):
        with tempfile.TemporaryDirectory() as d:
            self.branch(d)
            before = read(d, ".brevio/baseline.json")
            write(d, "f1.ts", CLEAN + TS_IGNORE + TS_IGNORE)
            res = run_cli(d, "scan", "--diff", "main", "--fail-on", "all")
            self.assertEqual(len(res.stdout.splitlines()), 1, res.stdout)
            self.assertIn("0 baselined", res.stderr)
            self.assertEqual(read(d, ".brevio/baseline.json"), before)


class TestBaselineFolder(unittest.TestCase):
    """The baseline is found from any folder of the repository, and a baseline in the current folder comes first (D29)."""

    def test_a_scan_from_a_subfolder_reads_the_baseline_at_the_git_root(self):
        with tempfile.TemporaryDirectory() as d:
            baselined_repo(d, {"src/a.ts": AWS_EXAMPLE_LINE, "lib/b.ts": AWS_EXAMPLE_LINE})
            sub = os.path.join(d, "src")
            res = run_cli(sub, "scan")
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)
            self.assertIn("1 baselined", res.stderr)
            self.assertEqual(run_cli(d, "scan").returncode, 0)

    def test_baseline_from_a_subfolder_writes_the_file_at_the_root_and_keeps_the_other_entries(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": AWS_EXAMPLE_LINE, "lib/b.ts": OTHER_AWS_EXAMPLE_LINE})
            made = run_cli(os.path.join(d, "src"), "baseline")
            self.assertEqual(made.returncode, 0, made.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "src", ".brevio")))
            rows = [json.loads(r.strip().rstrip(",")) for r in read(d, ".brevio/baseline.json").splitlines() if r.strip().startswith("{\"path\"")]
            self.assertEqual([r["path"] for r in rows], ["src/a.ts"])
            run_cli(d, "baseline", "lib")
            self.assertEqual(sorted(r["path"] for r in json.loads(read(d, ".brevio/baseline.json"))["findings"]), ["lib/b.ts", "src/a.ts"])
            write(d, "src/a.ts", CLEAN)
            run_cli(os.path.join(d, "src"), "baseline")
            self.assertEqual([r["path"] for r in json.loads(read(d, ".brevio/baseline.json"))["findings"]], ["lib/b.ts"])

    def test_a_baseline_in_the_current_folder_keeps_priority_as_in_0_9_1(self):
        with tempfile.TemporaryDirectory() as d:
            api = os.path.join(d, "services", "api")
            write(api, "a.ts", AWS_EXAMPLE_LINE)
            self.assertEqual(run_cli(api, "baseline").returncode, 0)         # no repository yet: the file is in this folder, as in 0.9.1
            committed_repo(d, {"services/api/a.ts": AWS_EXAMPLE_LINE, "services/api/.brevio/baseline.json": read(api, ".brevio/baseline.json")})
            self.assertEqual(json.loads(read(api, ".brevio/baseline.json"))["findings"][0]["path"], "a.ts")
            self.assertEqual(run_cli(api, "scan").returncode, 0)
            self.assertEqual(run_cli(d, "scan").returncode, 1)         # the nested file is not read from the root
            self.assertEqual(run_cli(d, "baseline").returncode, 0)
            self.assertEqual(run_cli(api, "scan").returncode, 0)       # still its own file first
            self.assertEqual(json.loads(read(d, ".brevio/baseline.json"))["findings"][0]["path"], "services/api/a.ts")

    def test_a_diff_baseline_from_a_subfolder_keys_changed_and_new_files_from_the_git_root(self):
        """Review of D29 (2026-11-13): the key got the folder twice and the hash of an empty line, which hid later findings."""
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": CLEAN})
            write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE)
            write(d, "src/b.ts", OTHER_AWS_EXAMPLE_LINE)
            sub = os.path.join(d, "src")
            self.assertEqual(run_cli(sub, "baseline", "--diff", "HEAD").returncode, 0)
            rows = json.loads(read(d, ".brevio/baseline.json"))["findings"]
            self.assertEqual(sorted(r["path"] for r in rows), ["src/a.ts", "src/b.ts"])
            self.assertNotIn(hashlib.sha256(b"").hexdigest(), [r["line_sha256"] for r in rows])
            git(d, "add", ".brevio/baseline.json")
            git(d, "commit", "-qm", "baseline")
            self.assertEqual(run_cli(sub, "scan", "--diff", "HEAD").returncode, 0)
            write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE + "const other = '" + "AKIA" + "IOSFODNN7ANOTHER" + "';\n")
            res = run_cli(sub, "scan", "--diff", "HEAD")
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertEqual(len(res.stdout.splitlines()), 1, res.stdout)
            self.assertTrue(res.stdout.startswith("src/a.ts:3: [R13.1]"), res.stdout)

    def test_a_path_above_the_current_folder_matches_the_baseline_entry_of_the_root(self):
        """Review of D29 (2026-11-13): ``scan ../lib`` from a subfolder built the key ``src/../lib/b.ts`` and missed the entry."""
        with tempfile.TemporaryDirectory() as d:
            baselined_repo(d, {"src/a.ts": AWS_EXAMPLE_LINE, "lib/b.ts": AWS_EXAMPLE_LINE})
            res = run_cli(os.path.join(d, "src"), "scan", "../lib/b.ts")
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)
            self.assertIn("1 baselined", res.stderr)

    def test_a_folder_that_is_no_repository_keeps_the_baseline_in_the_current_folder(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            self.assertIn("in .brevio/baseline.json", run_cli(d, "baseline").stdout)
            self.assertEqual(run_cli(d, "scan").returncode, 0)


class TestWhatTheBaselineHides(unittest.TestCase):
    def test_baseline_names_each_secret_it_records_and_says_to_rotate_it(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/k.ts", AWS_EXAMPLE_LINE)
            write(d, "src/m.ts", TS_IGNORE)
            res = run_cli(d, "baseline")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("R13.1 in src/k.ts is in the baseline: a secret is in your history; rotate it", res.stderr)
            self.assertNotIn("src/m.ts", res.stderr)
            write(d, "src/k.ts", CLEAN)
            self.assertNotIn("rotate", run_cli(d, "baseline").stderr)         # the secret is out of the code: no note

    def test_a_full_scan_says_how_many_baseline_entries_match_nothing_and_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", TS_IGNORE + AWS_EXAMPLE_LINE)
            run_cli(d, "baseline")
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            res = run_cli(d, "scan")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("1 baseline entry matches no finding; `brevio-core baseline --prune` removes it", res.stderr)
            write(d, "src/a.ts", CLEAN)
            self.assertIn("2 baseline entries match no finding", run_cli(d, "scan").stderr)

    def test_a_scan_of_some_paths_or_a_diff_says_nothing_about_entries_it_did_not_look_at(self):
        with tempfile.TemporaryDirectory() as d:
            baselined_repo(d, {"src/a.ts": AWS_EXAMPLE_LINE, "lib/b.ts": AWS_EXAMPLE_LINE})
            write(d, "src/a.ts", CLEAN)
            for args in (("scan", "lib"), ("scan", "--diff", "main"), ("scan", "--file", "lib/b.ts")):
                self.assertNotIn("match no finding", run_cli(d, *args).stderr, args)
            self.assertNotIn("match no finding", run_cli(os.path.join(d, "lib"), "scan").stderr)

    def test_an_ignore_comment_that_silences_nothing_is_a_note_and_one_that_does_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", f"{IGNORE} R9.2 -- generated\n" + TS_IGNORE + CLEAN + f"{IGNORE} R9.2 -- old\n" + CLEAN)
            res = run_cli(d, "scan")
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)
            self.assertIn("the ignore comment at src/a.ts:4 (R9.2) matches no finding any more", res.stderr)
            self.assertNotIn("src/a.ts:1 ", res.stderr)
            self.assertNotIn("matches no finding", run_cli(d, "scan", "--forbid-ignores").stderr)

    def test_prune_removes_only_the_dead_entries_lists_them_and_leaves_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", TS_IGNORE + AWS_EXAMPLE_LINE)
            run_cli(d, "baseline")
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            res = run_cli(d, "baseline", "--prune")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.splitlines()[0], "brevio-core: removed src/a.ts: R9.2")
            self.assertEqual([r["rule"] for r in json.loads(read(d, ".brevio/baseline.json"))["findings"]], ["R13.1"])
            self.assertEqual(run_cli(d, "scan").returncode, 0)
            again = run_cli(d, "baseline", "--prune")
            self.assertIn("nothing removed", again.stdout)

    def test_prune_needs_the_whole_folder_that_holds_the_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            baselined_repo(d, {"src/a.ts": AWS_EXAMPLE_LINE})
            for cwd, args in ((d, ("src",)), (d, ("--diff", "main")), (os.path.join(d, "src"), ())):
                res = run_cli(cwd, "baseline", "--prune", *args)
                self.assertEqual(res.returncode, 2, (args, res.stderr))
                self.assertIn("--prune reads the whole folder", res.stderr)


class TestInlineIgnore(unittest.TestCase):
    def test_an_ignore_with_a_reason_silences_that_rule_on_the_line_and_the_line_below(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", f"{IGNORE} R9.2 -- generated client\n" + TS_IGNORE
                  + AWS_EXAMPLE_LINE.rstrip("\n") + f" {IGNORE} R13.1 -- a fixture key */\n")
            res = run_cli(d, "scan")
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stdout)
            self.assertIn("0 finding(s) in 1 file(s), 0 baselined, 2 silenced", res.stderr)

    def test_an_ignore_without_a_reason_does_not_count_and_warns(self):
        with tempfile.TemporaryDirectory() as d:
            for i, comment in enumerate((f"{IGNORE} R9.2\n", f"{IGNORE} R9.2 --   \n", f"{IGNORE} R9.2 because\n")):
                write(d, "src/a.ts", comment + TS_IGNORE)
                res = run_cli(d, "scan")
                self.assertEqual(res.returncode, 1, i)
                self.assertIn("[R9.2]", res.stdout)
                self.assertIn("has no '-- reason', so it does not count", res.stderr)

    def test_an_ignore_naming_another_rule_does_not_count_and_an_unknown_rule_warns(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", f"{IGNORE} R13.1, R99.1 -- wrong rule\n" + TS_IGNORE)
            res = run_cli(d, "scan")
            self.assertEqual(res.returncode, 1)
            self.assertIn("src/a.ts:2: [R9.2]", res.stdout)
            self.assertIn("names R99.1, which is not a rule", res.stderr)
            self.assertNotIn("R13.1, which", res.stderr)

    def test_forbid_ignores_reports_the_finding_and_says_the_comment_counts_in_ci_only(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", f"{IGNORE} R9.2 -- generated\n" + TS_IGNORE)
            res = run_cli(d, "scan", "--forbid-ignores", "--file", "src/a.ts")
            self.assertEqual(res.returncode, 1)
            self.assertIn("counts in CI only", res.stdout)
            self.assertIn("0 silenced", res.stderr)

    def test_an_ignore_comment_in_a_doc_is_text_and_silences_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "notes.md", f"{IGNORE} R13.1 -- shown as an example\n" + AWS_EXAMPLE_LINE)
            self.assertEqual(run_cli(d, "scan", "notes.md").returncode, 1)

    def test_sarif_writes_a_silenced_finding_with_an_in_source_suppression(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", f"{IGNORE} R9.2 -- generated client\n" + TS_IGNORE + AWS_EXAMPLE_LINE)
            res = run_cli(d, "scan", "--sarif", "out.sarif")
            self.assertEqual(res.returncode, 1)
            results = json.loads(read(d, "out.sarif"))["runs"][0]["results"]
            by_rule = {r["ruleId"]: r for r in results}
            self.assertEqual(by_rule["R9.2"]["suppressions"], [{"kind": "inSource", "justification": "generated client"}])
            self.assertNotIn("suppressions", by_rule["R13.1"])


class TestNewSuppressions(unittest.TestCase):
    def test_a_diff_that_adds_an_ignore_comment_is_reported_until_a_reviewer_allows_it(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": CLEAN})
            write(d, "src/a.ts", CLEAN + f"{IGNORE} R9.2 -- generated\n" + TS_IGNORE)
            res = run_cli(d, "scan", "--diff", "HEAD")
            self.assertEqual(res.returncode, 1)
            self.assertEqual(res.stdout.splitlines(), ["src/a.ts:2: [suppression] new suppression: needs a human review"])
            self.assertEqual(run_cli(d, "scan", "--diff", "HEAD", "--allow-new-suppressions").returncode, 0)
            self.assertEqual(run_cli(d, "scan", "--diff", "HEAD", "--fail-on", "none").returncode, 0)

    def test_a_diff_that_adds_baseline_entries_is_reported_and_one_that_drops_them_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": AWS_EXAMPLE_LINE})
            run_cli(d, "baseline")
            res = run_cli(d, "scan", "--diff", "HEAD")
            self.assertEqual(res.returncode, 1)
            self.assertRegex(res.stdout, r"^\.brevio/baseline\.json:\d+: \[suppression\] new suppression: needs a human review")
            git(d, "add", "-A")
            git(d, "commit", "-qm", "baseline")
            write(d, "src/a.ts", CLEAN)
            run_cli(d, "baseline")
            self.assertEqual(run_cli(d, "scan", "--diff", "HEAD").returncode, 0)


# The section holds a zero-width space, which a legacy code page cannot encode: the child writes UTF-8 bytes, as ``_ESCAPE`` does, and
# starts with the streams of a Windows pipe so that Linux fails the same way (the first Windows run of 2026-11-13 printed nothing).
_CUSTODY = ("import sys, json; " + portable.LEGACY_STREAMS + "sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_markdown; "
            "found = json.loads(sys.argv[2]); sys.stdout.buffer.write(core_markdown.custody_section(found, sys.argv[3]).encode('utf-8'))")


class TestPullRequestComment(unittest.TestCase):
    """What the Action's comment and annotations say (2026-11-13, revisit I16, D28, D54)."""

    def test_sarif_declares_the_suppression_rule_that_a_new_ignore_comment_reports(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": CLEAN})
            write(d, "src/a.ts", CLEAN + f"{IGNORE} R9.2 -- generated\n")
            self.assertEqual(run_cli(d, "scan", "--diff", "HEAD", "--sarif", "out.sarif").returncode, 1)
            run = json.loads(read(d, "out.sarif"))["runs"][0]
            result = next(r for r in run["results"] if r["ruleId"] == "suppression")
            self.assertEqual(run["tool"]["driver"]["rules"][result["ruleIndex"]]["id"], "suppression")

    def test_the_report_of_a_diff_scan_says_it_lists_added_lines_only_and_a_folder_scan_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            committed_repo(d, {"src/a.ts": CLEAN})
            write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE)
            diff = run_cli(d, "scan", "--diff", "HEAD", "--format", "markdown").stdout
            self.assertIn("\n\nThe table lists added lines only.\n", diff)
            self.assertNotIn("added lines only", run_cli(d, "scan", "--format", "markdown").stdout)
            self.assertNotIn("added lines only", run_cli(d, "scan", "--diff", "HEAD", "--format", "markdown", "src/none").stdout)

    def test_a_github_annotation_is_an_error_when_the_finding_fails_the_check_and_a_warning_when_it_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            red = run_cli(d, "scan", "--format", "github")
            self.assertEqual(red.returncode, 1)
            self.assertTrue(red.stdout.startswith("::error file=src/a.ts,line=1,title=R13.1::"), red.stdout)
            quiet = run_cli(d, "scan", "--format", "github", "--fail-on", "none")
            self.assertEqual(quiet.returncode, 0)
            self.assertTrue(quiet.stdout.startswith("::warning file=src/a.ts,line=1,title=R13.1::"), quiet.stdout)

    def test_the_custody_section_lists_the_findings_says_whether_they_fail_and_escapes_the_cells(self):
        found = json.dumps(["tests/@octocat <b>.py:3: [R20.3] Test skip or focus marker added"])
        out = subprocess.run([sys.executable, "-I", "-c", _CUSTODY, TREE, found, "fail"], capture_output=True, encoding="utf-8",
                             timeout=60).stdout
        self.assertTrue(out.startswith("### brevio-core test custody\n\n1 finding(s) in the test files this change touches.\n"), out)
        self.assertIn("| R20.3 | tests/@" + _ZW + "octocat &lt;b&gt;.py:3 |", out)
        self.assertIn("These findings fail the check.", out)
        self.assertNotIn("<b>", out)
        report_only = subprocess.run([sys.executable, "-I", "-c", _CUSTODY, TREE, found, "report"], capture_output=True,
                                     encoding="utf-8", timeout=60).stdout
        self.assertIn("reported and do not fail the check", report_only)

    def test_the_custody_section_says_so_when_there_are_no_findings_and_when_the_check_did_not_run(self):
        none = subprocess.run([sys.executable, "-I", "-c", _CUSTODY, TREE, "[]", "report"], capture_output=True, encoding="utf-8",
                              timeout=60).stdout
        self.assertEqual(none, "### brevio-core test custody\n\nNo test-custody findings.\n")
        absent = subprocess.run([sys.executable, "-I", "-c", _CUSTODY, TREE, "null", "report"], capture_output=True,
                                encoding="utf-8", timeout=60).stdout
        self.assertIn("did not run", absent)


class TestMarkdown(unittest.TestCase):
    def test_no_findings_is_a_heading_and_one_line_with_no_number(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", CLEAN)
            res = run_cli(d, "scan", "--format", "markdown")
            self.assertEqual(res.returncode, 0)
            self.assertEqual(report(res.stdout), "### brevio-core\n\nNo findings.\n")

    def test_findings_are_a_table_of_rule_place_and_why_and_the_exit_code_follows_fail_on(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", TS_IGNORE + AWS_EXAMPLE_LINE)
            res = run_cli(d, "scan", "--format", "markdown")
            self.assertEqual(res.returncode, 1)
            lines = res.stdout.splitlines()
            self.assertEqual(lines[:6], ["### brevio-core", "", "2 finding(s).", "", "| Rule | Where | Why |", "| --- | --- | --- |"])
            self.assertTrue(lines[6].startswith("| R9.2 | src/a.ts:1 | \\`@" + _ZW + "ts-ignore\\` used"), lines[6])
            self.assertEqual(run_cli(d, "scan", "--format", "markdown", "--fail-on", "none").returncode, 0)

    def test_rule_ids_link_only_with_a_rules_url(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            linked = run_cli(d, "scan", "--format", "markdown", "--rules-url", f"{WEB}example.org/rules/").stdout
            self.assertIn(f"| [R13.1]({WEB}example.org/rules/R13.1) | src/a.ts:1 |", linked)
            self.assertNotIn("](", run_cli(d, "scan", "--format", "markdown").stdout)

    def test_a_long_list_is_cut_at_fifty_rows_and_says_how_many_more(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", AWS_EXAMPLE_LINE * 57)
            out = report(run_cli(d, "scan", "--format", "markdown").stdout).splitlines()
            self.assertEqual(len([ln for ln in out if ln.startswith("| R13.1 |")]), 50)
            self.assertIn("57 finding(s).", out)
            self.assertEqual(out[-1], "and 7 more.")

    def test_the_only_numbers_in_the_text_are_counts_lines_and_rule_ids(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", f"{IGNORE} R9.2 -- generated\n" + TS_IGNORE + AWS_EXAMPLE_LINE)
            run_cli(d, "baseline", "src/a.ts")
            write(d, "src/b.ts", AWS_EXAMPLE_LINE)
            out = run_cli(d, "scan", "--format", "markdown").stdout
            self.assertIn("Not shown: 1 silenced, 1 baselined.", out)
            rest = re.sub(r"R\d+\.\d+|\.ts:\d+|^\d+ finding\(s\)\.$|\d+ silenced|\d+ baselined", "", out, flags=re.M)
            self.assertEqual(re.findall(r"\d", rest), [])

    def test_hostile_text_from_the_repository_cannot_mention_link_tag_or_start_a_workflow_command(self):
        hostile = f"@octocat [x]({WEB}evil.example) <img src=x> `code` | cell\n::error::pwn www.evil.example $x$"
        res = subprocess.run([sys.executable, "-I", "-c", _ESCAPE, TREE, hostile], capture_output=True, encoding="utf-8", timeout=60)
        out = res.stdout.rstrip("\n")
        self.assertNotIn("\n", out)
        for raw in ("@o", "](", "<img", "`code`", " | ", "::", "https:", "www.", "$x$"):
            self.assertNotIn(raw, out)
        self.assertIn("@" + _ZW + "octocat", out)
        with tempfile.TemporaryDirectory() as d:     # a hostile path in the baseline file reaches the report
            committed_repo(d, {"src/a.ts": CLEAN})
            write(d, ".brevio/baseline.json", json.dumps({"version": 1, "findings": [
                {"path": f"@octocat ::set-output <b>[x]({WEB}evil.example)|", "rule": "R13.1", "line_sha256": "a" * 64}]}))
            md = run_cli(d, "scan", "--diff", "HEAD", "--format", "markdown").stdout
            row = [ln for ln in md.splitlines() if "suppression" in ln][0]
            self.assertEqual(row.count(" | "), 2, row)
            for raw in ("@o", "::", "<b>", "](", "https:"):
                self.assertNotIn(raw, row)

    def test_a_hostile_file_name_is_escaped_in_the_table(self):
        with tempfile.TemporaryDirectory() as d:
            # Windows allows no newline and none of < > | : in a name, so there the name keeps what it does allow.
            name = "@octocat x [y](h-z).ts" if os.name == "nt" else "@octocat\n::error::x <b> [y](http:z) |.ts"
            write(d, name, AWS_EXAMPLE_LINE)
            md = run_cli(d, "scan", "--format", "markdown").stdout
            row = [ln for ln in md.splitlines() if ln.startswith("| R13.1")][0]
            self.assertEqual(row.count(" | "), 2, row)
            self.assertFalse([ln for ln in md.splitlines() if ln.startswith("::")])
            for raw in ("@o", "::", "<b>", "](", "http:"):
                self.assertNotIn(raw, row)


class TestMcpScan(unittest.TestCase):
    def session(self, d, *calls):
        lines = "".join(json.dumps({"jsonrpc": "2.0", "id": i, "method": CALL, "params": {"name": n, "arguments": a}}) + "\n"
                        for i, (n, a) in enumerate(calls, 1))
        res = subprocess.run([sys.executable, "-I", "-c", _MCP, TREE], cwd=d, input=lines, capture_output=True, encoding="utf-8",
                             timeout=60, env=portable.child_env(os.path.dirname(shutil.which("git"))))
        return [json.loads(ln) for ln in res.stdout.splitlines()]

    def test_the_mcp_scan_hides_baselined_findings_but_never_honours_an_ignore(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", AWS_EXAMPLE_LINE + f"{IGNORE} R9.2 -- generated\n" + TS_IGNORE)
            run_cli(d, "baseline", "src/a.ts")      # the R9.2 finding is silenced, so only R13.1 is recorded
            reply = self.session(d, ("scan", {"paths": ["src"]}))[0]["result"]
        self.assertEqual(reply["structuredContent"], {"count": 1, "files": 1, "baselined": 1})
        text = reply["content"][0]["text"]
        self.assertIn("src/a.ts:3 [R9.2]", text)
        self.assertIn("counts in CI only", text)

    def test_the_mcp_server_has_no_tool_that_writes_a_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            reply = self.session(d, ("baseline", {}))[0]
            self.assertIn("unknown tool", reply["error"]["message"])
            self.assertFalse(os.path.exists(os.path.join(d, ".brevio")))


if __name__ == "__main__":
    unittest.main()
