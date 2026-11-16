"""``brevio-core up`` and the Teams line, as the 0.6 set ships them (ROADMAP E37, 2026-11-05).

``up`` is a preview: it lists the files a team sync would take, with their sizes and fields, and it uploads nothing. The
Teams line ends the Markdown report and ``gain``, leaves with ``--no-upgrade-line``, and never reaches an agent (the hook and
the MCP server). Neither prints a link since 2026-11-12: the waitlist page is not shown in this repository (improvement plan).

Standard library only: the exporter copies this file beside the code (the 0.6 set) and it runs unchanged there. In the
harness the test first exports the 0.6 set and runs against that copy, in a clean ``-I`` interpreter with a PATH that
finds git only.
"""
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
_RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
_MODULES_AFTER = ("import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; core_cli.main(sys.argv[2:]); "
                  "print(sorted(m for m in ('socket', 'ssl', 'urllib.request', 'http.client') if m in sys.modules))")
LINE = ("Teams: central sharing of learned guards and suppression approvals across an organisation is not built yet; "
        "`brevio-core up` lists what it would take.")
# A fake key in the AWS access key format (AWS's documented example key ID), joined at run time so this file is not
# itself a finding of a secret scan. The hardcoded-secret rule (R13.1) flags the format (E35 precision run, 2026-11-05).
AWS_EXAMPLE_LINE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
# The absolute claims the public text may not make outside its scoped form (ROADMAP E39), and words that promise.
ABSOLUTE = re.compile(r"\bno network\b|\bnothing\b|\bnever\b|\balways\b|\bevery\b|\ball\b|\bguarantee|\bzero\b|\bonly\b",
                      re.I)
_BIN = tempfile.TemporaryDirectory()
_PATH = portable.git_only_path(_BIN.name)
_EXPORT = tempfile.TemporaryDirectory()


def setUpModule():
    global TREE
    if os.path.isfile(EXPORTER):
        out = os.path.join(_EXPORT.name, "core")
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.6"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _BIN.cleanup()
    _EXPORT.cleanup()


def run_cli(cwd, *args, stdin="", code=_RUN):
    return subprocess.run([sys.executable, "-I", "-c", code, TREE, *args], cwd=cwd, input=stdin, capture_output=True,
                          encoding="utf-8", timeout=60, env=portable.child_env(_PATH, cwd))


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def git_cmd(d, *args):
    subprocess.run(["git", "-C", d, *args], check=True, capture_output=True, timeout=30)


def tree_of(d):
    return sorted(os.path.relpath(os.path.join(p, n), d) for p, _dirs, names in os.walk(d) for n in names)


class TestUp(unittest.TestCase):
    def test_up_with_nothing_learned_lists_no_file_prints_no_link_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "up")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("uploads nothing and makes no link", res.stdout)
            self.assertIn("none yet", res.stdout)
            self.assertNotIn("https://", res.stdout)
            self.assertNotIn("waitlist", res.stdout)
            self.assertEqual(tree_of(d), [])

    def test_up_lists_each_file_a_sync_would_take_with_its_size_and_fields(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli(d, "add", "fastapi-async").returncode, 0)
            write(d, ".brevio/stats.json", json.dumps({"version": 2, "total_interceptions": 3, "events": []}))
            before = tree_of(d)
            res = run_cli(d, "up")
            self.assertEqual(res.returncode, 0, res.stderr)
            size = os.path.getsize(os.path.join(d, ".brevio", "guards.json"))
            self.assertIn(f"  .brevio/guards.json ({size:,} bytes): each learned guard as stored: ", res.stdout)
            self.assertIn("file_patterns", res.stdout)
            self.assertIn("): total_interceptions, savings_measured, tokens_saved, cost_saved_usd", res.stdout)
            self.assertIn(".agents/rules/pack-fastapi-lifespan.md (", res.stdout)
            self.assertIn("): the whole text", res.stdout)
            self.assertIn("repository name only as a salted hash", res.stdout)
            self.assertEqual(tree_of(d), before)

    def test_up_json_says_the_sync_is_not_available_and_names_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli(d, "add", "fastapi-async").returncode, 0)
            out = json.loads(run_cli(d, "up", "--json").stdout)
            self.assertEqual(out["available"], False)
            self.assertNotIn("waitlist", out)
            self.assertEqual(out["files"][0]["path"], ".brevio/guards.json")
            self.assertEqual(set(out["files"][0]), {"path", "bytes", "fields"})

    def test_up_loads_no_network_module(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli(d, "add", "fastapi-async").returncode, 0)
            res = run_cli(d, "up", code=_MODULES_AFTER)
            self.assertEqual(res.stdout.strip().splitlines()[-1], "[]", res.stderr)

    def test_up_is_listed_with_the_other_commands(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("up", re.search(r"\{([^}]*)\}", run_cli(d, "help", "--all").stdout).group(1).split(","))

    def test_up_lists_file_memory_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, ".brevio/file_memory.json", json.dumps({"version": 1, "episodes": []}))
            res = run_cli(d, "up")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn(".brevio/file_memory.json (", res.stdout)
            self.assertIn("each recorded file trap-to-fix episode", res.stdout)
            out = json.loads(run_cli(d, "up", "--json").stdout)
            self.assertTrue(any(f["path"] == ".brevio/file_memory.json" for f in out["files"]))

    def test_up_notes_recent_authors_when_multiple(self):
        with tempfile.TemporaryDirectory() as d:
            git_cmd(d, "init", "-q")
            git_cmd(d, "-c", "user.name=u1", "-c", "user.email=u1@example.com", "commit", "-q", "--allow-empty", "-m", "c1")
            git_cmd(d, "-c", "user.name=u2", "-c", "user.email=u2@example.com", "commit", "-q", "--allow-empty", "-m", "c2")
            res = run_cli(d, "up")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("2 recent contributors are active on this repository without shared guard sync", res.stdout)


class TestTheTeamsLine(unittest.TestCase):
    def test_the_markdown_report_ends_with_the_line_and_the_flag_leaves_it_out(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            on = run_cli(d, "scan", "--format", "markdown", "a.ts")
            self.assertEqual(on.returncode, 1, on.stderr)
            self.assertEqual(on.stdout.splitlines()[-1], LINE)
            self.assertEqual(on.stdout.count("Teams:"), 1)
            off = run_cli(d, "scan", "--format", "markdown", "--no-upgrade-line", "a.ts")
            self.assertNotIn("Teams:", off.stdout)
            write(d, "b.ts", "export const a = 1;\n")
            self.assertEqual(run_cli(d, "scan", "--format", "markdown", "b.ts").stdout.splitlines()[-1], LINE)

    def test_gain_ends_with_the_line_unless_turned_off_and_its_json_carries_none(self):
        with tempfile.TemporaryDirectory() as d:
            on = run_cli(d, "gain")
            self.assertEqual(on.stdout.splitlines()[-1], LINE, on.stderr)
            self.assertNotIn("Teams:", run_cli(d, "gain", "--no-upgrade-line").stdout)
            self.assertNotIn("Teams:", run_cli(d, "gain", "--json").stdout)

    def test_no_text_or_agent_output_carries_the_line(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            for fmt in ("text", "agent"):
                self.assertNotIn("Teams:", run_cli(d, "scan", "--format", fmt, "a.ts").stdout, fmt)

    def test_the_agent_hook_never_carries_the_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = write(d, "a.ts", AWS_EXAMPLE_LINE)
            edit = {"cwd": d, "hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": path}}
            res = run_cli(d, "hook", "run", "--event", "post-edit", stdin=json.dumps(edit))
            self.assertIn("[R13.1]", json.loads(res.stdout)["reason"], res.stderr)
            self.assertNotIn("Teams:", res.stdout)
            commit = {"cwd": d, "tool_name": "Bash", "tool_input": {"command": "git commit -m 'Fix the key'"}}
            res = run_cli(d, "hook", "run", "--event", "post-command", stdin=json.dumps(commit))
            self.assertIn("learn --dry-run", res.stdout, res.stderr)
            self.assertNotIn("Teams:", res.stdout)

    def test_the_mcp_server_never_carries_the_line(self):
        call = "tools" + "/call"        # joined: the leak scan reads a literal "tools" + "/" as a private folder
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": call, "params": {"name": "scan", "arguments": {"paths": ["a.ts"]}}}]
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            res = run_cli(d, "mcp", stdin="".join(json.dumps(m) + "\n" for m in msgs))
        self.assertIn("R13.1", res.stdout, res.stderr)
        self.assertNotIn("Teams:", res.stdout)

    def test_the_line_holds_no_number_and_no_absolute_claim(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [run_cli(d, "gain").stdout.splitlines()[-1]]
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            lines.append(run_cli(d, "scan", "--format", "markdown", "a.ts").stdout.splitlines()[-1])
        for line in lines:
            self.assertTrue(line.startswith("Teams: "), line)
            self.assertEqual(re.findall(r"\d", line), [], line)
            self.assertEqual(ABSOLUTE.findall(line), [], line)
        self.assertTrue(ABSOLUTE.search("Teams: never lose a guard"))


if __name__ == "__main__":
    unittest.main()
