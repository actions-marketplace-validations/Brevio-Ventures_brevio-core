"""``brevio-core.toml``, ``brevio-core config`` and ``--fail-on error``, run in a clean interpreter (ROADMAP E32).

Standard library only: the exporter copies this file beside the code (the 0.6 set) and it runs unchanged there. In the
harness the test first exports the 0.6 set and runs against that copy. The TOML reader for Python 3.10 is checked
against ``tomllib`` on the same text, and a scan is run with it in place of ``tomllib``.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EXPORTER = os.path.join(ROOT, "tools", "export_core.py")      # present in the harness checkout only
TREE = ROOT
_EXPORT = tempfile.TemporaryDirectory()
TWO = "// @ts-ignore\nconst x = y as any;\n"        # one R9.2 and one R9.1 finding
CLEAN = "export const add = (a: number, b: number): number => a + b;\n"
# Every socket call fails and reports itself, so a test sees it if resolving ``extends`` ever reached the network.
_RUN = ("import socket, sys\n"
        "def _refuse(*a, **k):\n"
        "    sys.stderr.write('NETWORK-USED\\n')\n"
        "    raise OSError('the network is off in this test')\n"
        "socket.socket = socket.create_connection = socket.getaddrinfo = _refuse\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from brevio.pipeline import core_cli, core_config\n"
        "if sys.argv[2] == 'py310':\n"
        "    core_config.TOMLLIB = None\n"
        "raise SystemExit(core_cli.main(sys.argv[3:]))\n")
_PARSE = ("import json, sys\n"
          "try:\n"
          "    import tomllib\n"
          "except ModuleNotFoundError:\n"
          "    tomllib = None\n"
          "sys.path.insert(0, sys.argv[1])\n"
          "from brevio.pipeline import core_config\n"
          "text = sys.stdin.read()\n"
          "try:\n"
          "    subset = core_config.parse_subset(text)\n"
          "except core_config.ConfigError as exc:\n"
          "    subset = {'error': str(exc)}\n"
          "full = tomllib is not None and 'error' not in subset\n"
          "print(json.dumps({'subset': subset, 'tomllib': tomllib.loads(text) if full else None}))\n")
FULL = '''# a comment
extends = "policy/brevio-core.toml"
select = ["R9", 'R13.1']    # a literal string too
ignore = [
  "R6.3",   # one per line, with a comment
  "R5.4",
]
exclude = ["vendor/**", "*.min.js", "a \\"quoted\\" name"]
level = "warn"

[rules."R13.1"]
level = "error"

[rules.'R9.2']
level = "error"

[[overrides]]
paths = ["legacy/**"]
ignore = ["R9.1"]

[[overrides]]
paths = ["util/*.ts"]
rules = ["R9"]
level = "warn"
'''


def setUpModule():
    global TREE
    if os.path.isfile(EXPORTER):
        out = os.path.join(_EXPORT.name, "core")
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.6"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _EXPORT.cleanup()


def run_cli(cwd, *args, reader="tomllib"):
    return subprocess.run([sys.executable, "-I", "-c", _RUN, TREE, reader, *args], cwd=cwd, capture_output=True, encoding="utf-8",
                          timeout=60)


def parsed(text):
    res = subprocess.run([sys.executable, "-I", "-c", _PARSE, TREE], input=text, capture_output=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path) or d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(d, rel):
    with open(os.path.join(d, rel), encoding="utf-8") as fh:
        return fh.read()


def git(d, *args):
    return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                           *args], check=True, capture_output=True, encoding="utf-8", timeout=60).stdout.strip()


def config_of(d, path, reader="tomllib"):
    res = run_cli(d, "config", "--json", path, reader=reader)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


class TestTheReaderOfPython310(unittest.TestCase):
    def test_it_reads_every_form_the_settings_use(self):
        self.assertEqual(parsed(FULL)["subset"], {
            "extends": "policy/brevio-core.toml", "select": ["R9", "R13.1"], "ignore": ["R6.3", "R5.4"],
            "exclude": ["vendor/**", "*.min.js", 'a "quoted" name'], "level": "warn",
            "rules": {"R13.1": {"level": "error"}, "R9.2": {"level": "error"}},
            "overrides": [{"paths": ["legacy/**"], "ignore": ["R9.1"]},
                          {"paths": ["util/*.ts"], "rules": ["R9"], "level": "warn"}]})

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib is new in Python 3.11")
    def test_it_reads_every_form_the_settings_use_as_tomllib_does(self):
        for text in (FULL, "", "# only a comment\n", 'select = []\n', '[rules."R9.1"]\n', "level='warn'"):
            got = parsed(text)
            self.assertEqual(got["subset"], got["tomllib"], text)

    def test_it_refuses_toml_it_does_not_read_and_names_the_line(self):
        for text, line, what in (('level = "warn"\nfail = 3\n', 2, "a string or a list of strings"),
                                 ('a = { b = "c" }\n', 1, "a string or a list"),
                                 ('a = """x"""\n', 1, "multi-line string"),
                                 ('a = "x"\na = "y"\n', 2, "defined twice"),
                                 ('[t]\n[t]\n', 2, "defined twice"),
                                 ('a = "x" b\n', 1, "end of the line"),
                                 ('a = [1]\n', 1, "strings only")):
            error = parsed(text)["subset"].get("error", "")
            self.assertIn(f"line {line}:", error, text)
            self.assertIn(what, error, text)
            self.assertIn("Python 3.11", error)

    def test_a_scan_with_the_reader_of_python_310_gives_what_a_scan_with_tomllib_gives(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "brevio-core.toml", FULL.replace('extends = "policy/brevio-core.toml"\n', ""))
            write(d, "legacy/a.ts", TWO)
            write(d, "util/b.ts", TWO)
            for path in ("legacy/a.ts", "util/b.ts", "."):
                self.assertEqual(config_of(d, path, "py310"), config_of(d, path), path)
            new, old = run_cli(d, "scan", reader="py310"), run_cli(d, "scan")
        self.assertEqual((new.returncode, new.stdout, new.stderr), (old.returncode, old.stdout, old.stderr))
        self.assertIn("[R9.2]", old.stdout)


class TestWhatAppliesWhere(unittest.TestCase):
    def test_the_nearest_file_wins_and_exclude_and_overrides_add_up(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "brevio-core.toml", 'ignore = ["R6.3"]\nexclude = ["gen"]\n[rules."R9.1"]\nlevel = "warn"\n')
            write(d, "src/brevio-core.toml", 'ignore = ["R5.4"]\nexclude = ["*.min.ts"]\nlevel = "warn"\n'
                                             '[rules."R9.2"]\nlevel = "error"\n'
                                             '[[overrides]]\npaths = ["legacy/**"]\nignore = ["R9.1"]\n')
            for rel in ("src/a.ts", "src/legacy/b.ts", "src/c.min.ts", "gen/d.ts", "src/gen/e.ts", "top.ts"):
                write(d, rel, TWO)
            top, a, b = config_of(d, "top.ts"), config_of(d, "src/a.ts"), config_of(d, "src/legacy/b.ts")
            self.assertEqual(top["files"], ["brevio-core.toml"])
            self.assertEqual(a["files"], ["brevio-core.toml", "src/brevio-core.toml"])
            self.assertEqual((top["ignore"], a["ignore"], b["ignore"]), (["R6.3"], ["R5.4"], ["R5.4", "R9.1"]))
            self.assertIn("R6.3", a["rules"])                       # the nearer ignore replaced the root's
            self.assertEqual((top["rules"]["R9.1"]["level"], top["rules"]["R9.2"]["level"]), ("warn", "error"))
            self.assertEqual((a["rules"]["R9.1"]["level"], a["rules"]["R9.2"]["level"]), ("warn", "error"))
            self.assertNotIn("R9.1", b["rules"])
            for rel in ("src/c.min.ts", "gen/d.ts", "src/gen/e.ts"):
                self.assertTrue(config_of(d, rel)["excluded"], rel)
                self.assertEqual(config_of(d, rel)["rules"], {}, rel)
            self.assertFalse(a["excluded"])
            self.assertTrue(config_of(d, "src/gen")["excluded"])
            res = run_cli(d, "scan")
        found = sorted(line.split(": [")[0].rsplit(":", 1)[0] + " " + line.split("[")[1].split("]")[0]
                       for line in res.stdout.splitlines())
        self.assertEqual(found, ["src/a.ts R9.1", "src/a.ts R9.2", "src/legacy/b.ts R9.2", "top.ts R9.1", "top.ts R9.2"])

    def test_the_config_command_prints_the_files_and_each_rule_with_its_level_and_status(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "brevio-core.toml", 'select = ["R9"]\n[rules."R9.2"]\nlevel = "warn"\n')
            res = run_cli(d, "config")
            bare = run_cli(d, "config", "brevio-core.toml")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("files: brevio-core.toml", res.stdout)
        self.assertIn("select: R9", res.stdout)
        self.assertRegex(res.stdout, r"R9\.1\s+error\s+stable")
        self.assertRegex(res.stdout, r"R9\.2\s+warn\s+stable")
        self.assertNotIn("R13.1", res.stdout)
        self.assertEqual(bare.returncode, 0, bare.stderr)

    def test_with_no_config_every_scanned_rule_runs_at_error(self):
        with tempfile.TemporaryDirectory() as d:
            got = config_of(d, ".")
        self.assertEqual(got["files"], [])
        self.assertGreater(len(got["rules"]), 1)
        self.assertEqual({v["level"] for v in got["rules"].values()}, {"error"})


class TestLevelsAndFailing(unittest.TestCase):
    def test_fail_on_error_fails_only_on_error_level_findings_and_reports_the_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", TWO)
            write(d, "brevio-core.toml", 'level = "warn"\n')
            warned = run_cli(d, "scan", "--fail-on", "error")
            failed_new = run_cli(d, "scan")
            write(d, "brevio-core.toml", 'level = "warn"\n[rules."R9.2"]\nlevel = "error"\n')
            mixed = run_cli(d, "scan", "--fail-on", "error", "--sarif", "out.sarif")
            with open(os.path.join(d, "out.sarif"), encoding="utf-8") as fh:
                levels = {r["ruleId"]: r["level"] for r in json.load(fh)["runs"][0]["results"]}
        self.assertEqual(warned.returncode, 0, warned.stderr)
        self.assertIn("[R9.1]", warned.stdout)
        self.assertIn("[R9.2]", warned.stdout)
        self.assertIn("2 at warn level", warned.stderr)
        self.assertEqual(failed_new.returncode, 1)          # the default fails on every new finding, whatever its level
        self.assertEqual(mixed.returncode, 1)
        self.assertEqual(levels, {"R9.1": "warning", "R9.2": "error"})

    def test_a_baselined_finding_still_passes_under_fail_on_error(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", TWO)
            self.assertEqual(run_cli(d, "baseline").returncode, 0)
            res = run_cli(d, "scan", "--fail-on", "error")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("2 baselined", res.stderr)

    def test_fix_leaves_an_excluded_file_and_an_ignored_rule_alone(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "vendor/a.ts", "// @ts-ignore\n")
            write(d, "b.ts", "// @ts-ignore\n")
            write(d, "c/d.ts", "// @ts-ignore\n")
            write(d, "brevio-core.toml", 'exclude = ["vendor"]\n[[overrides]]\npaths = ["c/**"]\nignore = ["R9.2"]\n')
            res = run_cli(d, "scan", "--fix")
            texts = {rel: read(d, rel) for rel in ("vendor/a.ts", "b.ts", "c/d.ts")}
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(texts, {"vendor/a.ts": "// @ts-ignore\n", "b.ts": "// @ts-expect-error\n", "c/d.ts": "// @ts-ignore\n"})


class TestABadConfig(unittest.TestCase):
    def test_an_unknown_key_a_wrong_level_and_broken_toml_are_usage_errors_that_name_the_file(self):
        for text, what in (('selct = ["R9"]\n', "unknown key selct"), ('level = "loud"\n', '"warn" or "error"'),
                           ('select = "R9"\n', "list of strings"), ('select = [\n', "brevio-core.toml"),
                           ('[[overrides]]\nignore = ["R9.1"]\n', "needs paths"),
                           ('[rules.R9.1]\nlevel = "warn"\n', 'in quotes')):
            with tempfile.TemporaryDirectory() as d:
                write(d, "src/a.ts", TWO)          # a config is read for the files under its folder
                write(d, "src/brevio-core.toml", text)
                for reader in ("tomllib", "py310"):
                    res = run_cli(d, "scan", reader=reader)
                    self.assertEqual(res.returncode, 2, (text, reader, res.stderr))
                    self.assertIn("src/brevio-core.toml", res.stderr)
                    self.assertIn(what, res.stderr, (text, reader))

    def test_a_rule_pattern_that_matches_no_rule_is_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", CLEAN)
            write(d, "brevio-core.toml", 'ignore = ["R99.1", "R9"]\n')
            res = run_cli(d, "scan")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("warning: brevio-core.toml: R99.1 matches no rule", res.stderr)
        self.assertNotIn("R9 matches", res.stderr)


class TestExtends(unittest.TestCase):
    def test_a_vendored_policy_is_read_first_with_its_paths_relative_to_the_file_that_extends_it(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "policy/brevio-core.toml", 'exclude = ["gen"]\nlevel = "warn"\n')
            write(d, "brevio-core.toml", 'extends = "policy/brevio-core.toml"\n[rules."R9.2"]\nlevel = "error"\n')
            write(d, "gen/a.ts", TWO)
            write(d, "b.ts", TWO)
            got, gen = config_of(d, "b.ts"), config_of(d, "gen/a.ts")
        self.assertEqual(got["files"], ["policy/brevio-core.toml", "brevio-core.toml"])
        self.assertEqual((got["rules"]["R9.1"]["level"], got["rules"]["R9.2"]["level"]), ("warn", "error"))
        self.assertTrue(gen["excluded"])

    def test_a_checkout_pinned_to_a_commit_is_used_only_at_that_commit(self):
        with tempfile.TemporaryDirectory() as d:
            pol = os.path.join(d, "org-policy")
            os.makedirs(pol)
            git(pol, "init", "-q")
            write(pol, "brevio-core.toml", 'ignore = ["R9.1"]\n')
            git(pol, "add", "-A")
            git(pol, "commit", "-q", "-m", "policy")
            sha = git(pol, "rev-parse", "HEAD")
            write(d, "brevio-core.toml", f'extends = "org-policy@{sha}"\n')
            ok = config_of(d, ".")
            write(d, "brevio-core.toml", f'extends = "org-policy@{"0" * 40}"\n')
            wrong = run_cli(d, "scan")
        self.assertNotIn("R9.1", ok["rules"])
        self.assertEqual(ok["files"], ["org-policy/brevio-core.toml", "brevio-core.toml"])
        self.assertEqual(wrong.returncode, 2)
        self.assertIn(f"is at {sha[:12]}, not {'0' * 12}", wrong.stderr)

    def test_a_git_url_uses_its_submodule_checkout_and_is_never_fetched(self):
        url = "ssh" + "://git.example.com/org/policy.git"
        with tempfile.TemporaryDirectory() as d:
            pol = os.path.join(d, "vendor", "policy")
            os.makedirs(pol)
            git(pol, "init", "-q")
            write(pol, "brevio-core.toml", 'select = ["R13"]\n')
            git(pol, "add", "-A")
            git(pol, "commit", "-q", "-m", "policy")
            sha = git(pol, "rev-parse", "HEAD")
            write(d, ".gitmodules", f'[submodule "policy"]\n\tpath = vendor/policy\n\turl = {url}\n')
            write(d, "brevio-core.toml", f'extends = "{url[:-4]}@{sha}"\n')
            ok = config_of(d, ".")
            write(d, "brevio-core.toml", f'extends = "{url.replace("policy", "other")}@{sha}"\n')
            missing = run_cli(d, "scan")
            write(d, "brevio-core.toml", f'extends = "{url}"\n')
            unpinned = run_cli(d, "scan")
        self.assertEqual(list(ok["rules"]), ["R13.1"])
        self.assertEqual(missing.returncode, 2)
        self.assertIn("never fetches", missing.stderr)
        self.assertIn("needs @<commit SHA>", unpinned.stderr)
        for res in (missing, unpinned):
            self.assertNotIn("NETWORK-USED", res.stderr)

    def test_a_loop_and_a_missing_file_are_usage_errors(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "brevio-core.toml", 'extends = "b.toml"\n')
            write(d, "b.toml", 'extends = "brevio-core.toml"\n')
            loop = run_cli(d, "scan")
            write(d, "brevio-core.toml", 'extends = "nowhere.toml"\n')
            missing = run_cli(d, "scan")
        self.assertEqual((loop.returncode, missing.returncode), (2, 2))
        self.assertIn("loop", loop.stderr)
        self.assertIn("no file nowhere.toml", missing.stderr)


class TestAConfigChangeNeedsAHuman(unittest.TestCase):
    def test_a_diff_that_adds_or_edits_a_config_is_a_new_suppression_until_a_reviewer_allows_it(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "a.ts", CLEAN)
            git(d, "add", "-A")
            git(d, "commit", "-q", "-m", "base")
            write(d, "src/brevio-core.toml", '# turn it down\nignore = ["R9"]\n')
            added = run_cli(d, "scan", "--diff", "HEAD")
            allowed = run_cli(d, "scan", "--diff", "HEAD", "--allow-new-suppressions")
            git(d, "add", "-A")
            git(d, "commit", "-q", "-m", "config")
            write(d, "src/brevio-core.toml", '# turn it down\nignore = ["R9", "R13"]\n')
            edited = run_cli(d, "scan", "--diff", "HEAD")
        self.assertEqual(added.returncode, 1, added.stderr)
        self.assertIn("src/brevio-core.toml:2: [suppression] config change", added.stdout)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("src/brevio-core.toml:2: [suppression]", edited.stdout)


if __name__ == "__main__":
    unittest.main()
