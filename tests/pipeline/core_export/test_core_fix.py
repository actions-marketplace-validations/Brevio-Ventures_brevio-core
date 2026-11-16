"""``brevio-core scan --fix`` and the MCP ``fix`` tool, run in a clean interpreter with only the tree on its path.

Standard library only: the exporter copies this file beside the code (the 0.3 set) and it runs unchanged there. In the
harness the test first exports the 0.3 set and runs against that copy.
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
# A fake key in the AWS access key format (AWS's documented example key ID), joined at run time so this file is not
# itself a finding of a secret scan. The hardcoded-secret rule (R13.1) flags the format (E35 precision run, 2026-11-05).
AWS_EXAMPLE_LINE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
TS_IGNORE = "// @ts-ignore\nconst n: number = 'x';\n"
BARE_EXCEPT = "try:\n    run()\nexcept:\n    errors = 1\n"         # a handler that does something: --fix can rewrite it
PASS_EXCEPT = "try:\n    run()\nexcept:\n    pass\n"           # a handler that only passes: the rewrite would stay a finding
ELLIPSIS_EXCEPT = "try:\n    run()\nexcept:\n    ...\n"
ONE_LINE_PASS_EXCEPT = "try:\n    run()\nexcept: pass\n"
# One sample per fixer, by rule id: a file name and a line that triggers the rule.
SAMPLES = {
    "R9.2": ("src/a.ts", "  /* @ts-ignore */ const n: number = 'x';"),
    "R5.4": ("a.py", "    except :"),
    "R10.1": ("a.py", "resp = requests.get(url)"),
    "R18.2": ("src/Link.tsx", '  <a href="/docs" target="_blank">Docs</a>'),
}
_RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
_MCP = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(['mcp']))"
_IN_TREE = ("import sys, json; sys.path.insert(0, sys.argv[1]); from brevio.pipeline.code_rules import fixers; "
            "print(json.dumps({r: [fixers.fix_line(p, t), sorted(fixers.rules_on(p, fixers.fix_line(p, t)[0])), sorted(fixers.FIXERS)] "
            "for r, (p, t) in json.loads(sys.argv[2]).items()}))")


def setUpModule():
    global TREE
    if os.path.isfile(EXPORTER):
        out = os.path.join(_EXPORT.name, "core")
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.3"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _EXPORT.cleanup()


def run_cli(cwd, *args):
    return subprocess.run([sys.executable, "-I", "-c", _RUN, TREE, *args], cwd=cwd, capture_output=True, encoding="utf-8", timeout=60)


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read(d, rel):
    with open(os.path.join(d, rel), encoding="utf-8", newline="") as fh:
        return fh.read()


def git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


class TestFixers(unittest.TestCase):
    def test_every_fixer_output_no_longer_triggers_its_rule_and_every_fixer_has_a_sample(self):
        res = subprocess.run([sys.executable, "-I", "-c", _IN_TREE, TREE, json.dumps(SAMPLES)], capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        for rule, ((fixed, rules), after, declared) in json.loads(res.stdout).items():
            self.assertEqual(sorted(SAMPLES), declared)
            self.assertEqual(rules, [rule], fixed)
            self.assertNotIn(rule, after, fixed)
            self.assertNotEqual(fixed, SAMPLES[rule][1])


class TestFixCommand(unittest.TestCase):
    def test_fix_rewrites_in_place_keeps_the_secret_and_reports_what_remains(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", TS_IGNORE + AWS_EXAMPLE_LINE)
            write(d, "a.py", BARE_EXCEPT.replace("\n", "\r\n"))
            res = run_cli(d, "scan", "--fix")
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertIn("2 fixed, 1 remaining", res.stderr)
            self.assertIn("src/a.ts:3: [R13.1]", res.stdout)
            self.assertEqual(read(d, "src/a.ts"), TS_IGNORE.replace("@ts-ignore", "@ts-expect-error") + AWS_EXAMPLE_LINE)
            self.assertEqual(read(d, "a.py"), BARE_EXCEPT.replace("except:", "except Exception:").replace("\n", "\r\n"))

    def test_a_bare_except_that_re_raises_is_not_rewritten(self):
        """Found in review of D30: the scan leaves ``except:`` + ``raise`` alone, but --fix judged the line alone and
        rewrote it, and counted a fix for a finding that was never reported."""
        keep = "try:\n    run()\nexcept:\n    log()\n    raise\n"
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", keep + BARE_EXCEPT)
            res = run_cli(d, "scan", "--fix")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("1 fixed, 0 remaining", res.stderr)
            self.assertEqual(read(d, "a.py"), keep + BARE_EXCEPT.replace("except:", "except Exception:"))

    def test_fixing_twice_changes_nothing_the_second_time(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", TS_IGNORE)
            write(d, "a.py", BARE_EXCEPT)
            self.assertEqual(run_cli(d, "scan", "--fix").returncode, 0)
            once = (read(d, "src/a.ts"), read(d, "a.py"))
            again = run_cli(d, "scan", "--fix")
            self.assertEqual(again.returncode, 0)
            self.assertIn("0 fixed, 0 remaining", again.stderr)
            self.assertEqual((read(d, "src/a.ts"), read(d, "a.py")), once)
            self.assertEqual(run_cli(d, "scan", "--fix", "--dry-run").stdout, "")

    def test_a_bare_except_that_only_passes_is_left_unchanged_and_stays_reported(self):
        """The rewrite of ``except:`` + ``pass`` gave ``except Exception:`` + ``pass``, which R5.4 reports again: a fix must
        remove the finding it claims, so a person decides what the handler should do (2026-11-13)."""
        for name, text in (("pass", PASS_EXCEPT), ("ellipsis", ELLIPSIS_EXCEPT), ("one line", ONE_LINE_PASS_EXCEPT),
                           ("crlf", PASS_EXCEPT.replace("\n", "\r\n"))):
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                write(d, "a.py", text)
                res = run_cli(d, "scan", "--fix")
                self.assertEqual(res.returncode, 1, res.stderr)
                self.assertIn("a.py:3: [R5.4] Bare `except:` detected", res.stdout)
                self.assertIn("0 fixed, 1 remaining", res.stderr)
                self.assertEqual(read(d, "a.py"), text)
                self.assertEqual(run_cli(d, "scan", "--fix", "--dry-run").stdout, "")

    def test_a_pass_only_bare_except_is_not_counted_as_fixable(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", PASS_EXCEPT)
            self.assertNotIn("fixable", run_cli(d, "scan").stderr)
            write(d, "a.py", PASS_EXCEPT + BARE_EXCEPT)
            plain = run_cli(d, "scan")
            self.assertIn("1 fixable:", plain.stderr)
            fixed = run_cli(d, "scan", "--fix")
            self.assertIn("1 fixed, 1 remaining", fixed.stderr)
            self.assertIn("a.py:3: [R5.4]", fixed.stdout)
            self.assertEqual(read(d, "a.py"), PASS_EXCEPT + BARE_EXCEPT.replace("except:", "except Exception:"))

    def test_fixing_twice_leaves_a_pass_only_bare_except_alone_both_times(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", PASS_EXCEPT + BARE_EXCEPT)
            self.assertEqual(run_cli(d, "scan", "--fix").returncode, 1)
            once = read(d, "a.py")
            again = run_cli(d, "scan", "--fix")
            self.assertEqual(again.returncode, 1)
            self.assertIn("0 fixed, 1 remaining", again.stderr)
            self.assertEqual(read(d, "a.py"), once)

    def test_the_fix_tool_counts_a_pass_only_bare_except_as_remaining(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", PASS_EXCEPT)
            (res,) = TestFixTool.session(self, d, {"write": True})
            self.assertEqual((res["structuredContent"]["fixed"], res["structuredContent"]["remaining"]), (0, 1))
            self.assertEqual(read(d, "a.py"), PASS_EXCEPT)

    def test_a_dry_run_prints_a_unified_diff_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", TS_IGNORE)
            res = run_cli(d, "scan", "src", "--fix", "--dry-run")
            self.assertEqual(res.returncode, 1)
            self.assertIn("--- a/src/a.ts\n+++ b/src/a.ts\n", res.stdout)
            self.assertIn("-// @ts-ignore\n+// @ts-expect-error\n", res.stdout)
            self.assertEqual(read(d, "src/a.ts"), TS_IGNORE)

    def test_dry_run_without_fix_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli(d, "scan", "--dry-run").returncode, 2)

    def test_with_a_diff_base_only_the_lines_the_change_adds_are_fixed(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            write(d, "src/a.ts", TS_IGNORE)
            git(d, "add", ".")
            git(d, "commit", "-q", "-m", "base")
            write(d, "src/a.ts", TS_IGNORE + "// @ts-ignore\n")
            res = run_cli(d, "scan", "--fix", "--diff", "main")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(read(d, "src/a.ts"), TS_IGNORE + "// @ts-expect-error\n")

    def test_r182_fixer_adds_noopener_noreferrer_when_rel_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/Link.tsx", '<a href="/docs" target="_blank">Docs</a>\n')
            res = run_cli(d, "scan", "--fix")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(read(d, "src/Link.tsx"), '<a href="/docs" target="_blank" rel="noopener noreferrer">Docs</a>\n')

    def test_r101_fixer_adds_timeout_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "client.py", "resp = requests.get(url)\n")
            res = run_cli(d, "scan", "--fix")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(read(d, "client.py"), "resp = requests.get(url, timeout=30)\n")


class TestFixTool(unittest.TestCase):
    def session(self, d, *calls):
        msgs = [{"jsonrpc": "2.0", "id": i, "method": "tools" + "/call", "params": {"name": "fix", "arguments": a}}
                for i, a in enumerate(calls, 1)]
        res = subprocess.run([sys.executable, "-I", "-c", _MCP, TREE], cwd=d, input="".join(json.dumps(m) + "\n" for m in msgs),
                             capture_output=True, encoding="utf-8", timeout=60)
        return [json.loads(ln)["result"] for ln in res.stdout.splitlines()]

    def test_the_fix_tool_returns_the_diff_and_writes_only_when_asked(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", TS_IGNORE + AWS_EXAMPLE_LINE)
            dry, wrote = self.session(d, {"paths": ["src"]}, {"paths": ["src"], "write": True})
            self.assertFalse(dry["isError"])
            self.assertIn("+// @ts-expect-error", dry["structuredContent"]["diff"])
            self.assertEqual((dry["structuredContent"]["fixed"], dry["structuredContent"]["remaining"]), (1, 1))
            self.assertFalse(dry["structuredContent"]["written"])
            self.assertTrue(wrote["structuredContent"]["written"])
            self.assertEqual(wrote["structuredContent"]["remaining"], 1)
            self.assertEqual(read(d, "src/a.ts"), TS_IGNORE.replace("@ts-ignore", "@ts-expect-error") + AWS_EXAMPLE_LINE)

    def test_the_fix_tool_refuses_a_path_outside_the_folder_and_a_bad_write_flag(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            outside, bad = self.session(d, {"paths": [other]}, {"write": "yes"})
        self.assertTrue(outside["isError"])
        self.assertTrue(bad["isError"])


if __name__ == "__main__":
    unittest.main()
