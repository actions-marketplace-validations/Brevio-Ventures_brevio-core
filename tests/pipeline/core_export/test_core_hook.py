"""The agent hook (``hook install`` and ``hook run``) as a coding client runs it, and ``scan --format agent`` (ROADMAP E34).

Standard library only: the exporter copies this file beside the code (the 0.4 set) and it runs unchanged there. In the
harness the test first exports the 0.4 set and runs against that copy. Each case installs the hooks into a temporary
git repository, takes the command the client config names and feeds it the client's event JSON on stdin, in a clean
``-I`` interpreter with a PATH that finds git only.
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
# A fake key in the AWS access key format (AWS's documented example key ID), joined at run time so this file is not
# itself a finding of a secret scan. The hardcoded-secret rule (R13.1) flags the format (E35 precision run, 2026-11-05).
AWS_EXAMPLE_LINE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
CLEAN = "export const add = (a: number, b: number): number => a + b;\n"
IGNORED = "// brevio-core: ignore R13.1 -- a test key\n" + AWS_EXAMPLE_LINE
CURSOR_VERSION = "9.9.9"
LATENCY_LIMIT = 1.5     # CPU seconds for a whole post-edit hook run on a 2,000-line file, interpreter start included
_BIN = tempfile.TemporaryDirectory()
_PATH = portable.git_only_path(_BIN.name)
_EXPORT = tempfile.TemporaryDirectory()


def setUpModule():
    global TREE
    if os.path.isfile(EXPORTER):
        out = os.path.join(_EXPORT.name, "core")
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.4"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _BIN.cleanup()
    _EXPORT.cleanup()


def run_cli(cwd, *args, stdin=""):
    return subprocess.run([sys.executable, "-I", "-c", _RUN, TREE, *args], cwd=cwd, input=stdin, capture_output=True,
                          encoding="utf-8", timeout=60, env=portable.child_env(_PATH, cwd))


def git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], check=True,
                   capture_output=True)


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def read_json(d, rel):
    with open(os.path.join(d, rel), encoding="utf-8") as fh:
        return json.load(fh)


def repo(d, **files):
    """A git repository in ``d`` with ``files`` (``src__a_ts`` names ``src/a.ts``) committed."""
    git(d, "init", "-q", "-b", "main")
    for name, text in files.items():
        write(d, name.replace("__", "/").replace("_ts", ".ts"), text)
    git(d, "add", "-A")
    git(d, "commit", "-q", "--allow-empty", "-m", "base")


EDIT_MATCHERS = {"claude_code": "Edit|Write|MultiEdit", "cursor": "Write"}
SHELL_MATCHERS = {"claude_code": "Bash", "cursor": "Shell"}


def unpinned(command):
    """``command`` without the ``==version`` pin a build that knows its release version writes (D12, 2026-11-13); the
    tests of this file name the command the same way in a source tree, which has no version, and in a release."""
    return re.sub(r"brevio-core==\d+\.\d+\.\d+", "brevio-core", command)


def installed_command(d, client, event, shell=False):
    """The argument list after ``uvx brevio-core`` of the hook ``hook install`` wrote for ``client`` and ``event``: the
    entry on the edit tools, or with ``shell`` the one on the shell tool (a stop entry has no matcher)."""
    matcher = (SHELL_MATCHERS if shell else EDIT_MATCHERS)[client]
    if client == "claude_code":
        groups = read_json(d, ".claude/settings.json")["hooks"][event]
        group = next(g for g in groups if g.get("matcher", matcher) == matcher)
        command = group["hooks"][-1]["command"]
    else:
        entries = read_json(d, ".cursor/hooks.json")["hooks"][event]
        command = next(e for e in entries if e.get("matcher", matcher) == matcher)["command"]
    words = unpinned(command).split()
    assert words[:2] == ["uvx", "brevio-core"], command
    return words[2:]


def fire(d, client, event, payload, shell=False):
    """Install the hooks for ``client`` (a second install changes nothing), then run its ``event`` hook on ``payload``."""
    assert run_cli(d, "hook", "install", "--target", client).returncode == 0
    return run_cli(d, *installed_command(d, client, event, shell), stdin=json.dumps(payload))


def claude_edit(d, rel, old, new):
    """Claude Code's PostToolUse event for an Edit of ``rel`` (the file is rewritten first, as the tool did)."""
    path = os.path.join(d, rel)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    write(d, rel, text.replace(old, new, 1))
    return {"session_id": "s1", "transcript_path": os.path.join(d, "t.jsonl"), "cwd": d, "permission_mode": "default",
            "hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_use_id": "toolu_1",
            "tool_input": {"file_path": path, "old_string": old, "new_string": new, "replace_all": False},
            "tool_response": {"filePath": path, "success": True}}


def claude_stop(d, active=False):
    return {"session_id": "s1", "transcript_path": os.path.join(d, "t.jsonl"), "cwd": d, "permission_mode": "default",
            "hook_event_name": "Stop", "stop_hook_active": active, "last_assistant_message": "Done."}


def cursor_event(d, name, **fields):
    return {"conversation_id": "c1", "generation_id": "g1", "hook_event_name": name, "cursor_version": CURSOR_VERSION,
            "workspace_roots": [d], **fields}


class TestTheInstalledHooks(unittest.TestCase):
    def test_claude_code_gets_a_post_edit_group_on_the_edit_tools_and_a_stop_group_and_a_second_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli(d, "hook", "install", "--target", "claude_code").returncode, 0)
            first = read_json(d, ".claude/settings.json")
            post, stop = first["hooks"]["PostToolUse"], first["hooks"]["Stop"]
            self.assertEqual([g["matcher"] for g in post], ["Edit|Write|MultiEdit", "Bash"])
            self.assertEqual([h["type"] for h in post[0]["hooks"]], ["command"])
            self.assertEqual(unpinned(post[0]["hooks"][0]["command"]), "uvx brevio-core hook run --event post-edit")
            self.assertEqual(unpinned(post[1]["hooks"][0]["command"]), "uvx brevio-core hook run --event post-command")
            self.assertNotIn("matcher", stop[0])
            self.assertEqual(unpinned(stop[0]["hooks"][0]["command"]), "uvx brevio-core hook run --event stop")
            self.assertNotIn("$FILE", json.dumps(first))
            self.assertEqual(run_cli(d, "hook", "install", "--target", "claude_code").returncode, 0)
            self.assertEqual(read_json(d, ".claude/settings.json"), first)

    def test_the_old_file_variable_hook_is_replaced_and_other_hooks_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "./audit.sh"}]}
            old = {"command": "uvx brevio-core scan --forbid-ignores --file $FILE", "tools": ["Write", "Edit", "MultiEdit"]}
            write(d, ".claude/settings.json", json.dumps({"model": "x", "hooks": {"PostToolUse": [other, old]}}))
            self.assertEqual(run_cli(d, "hook", "install", "--target", "claude_code").returncode, 0)
            data = read_json(d, ".claude/settings.json")
            self.assertEqual(data["model"], "x")
            self.assertEqual(data["hooks"]["PostToolUse"][0], other)
            self.assertEqual(len(data["hooks"]["PostToolUse"]), 3)      # theirs, then our edit and shell groups
            self.assertNotIn("$FILE", json.dumps(data))

    def test_cursor_gets_its_own_hooks_file_with_one_stop_follow_up_at_most(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli(d, "hook", "install", "--target", "cursor").returncode, 0)
            data = read_json(d, ".cursor/hooks.json")
            self.assertEqual(data["version"], 1)
            post, stop = data["hooks"]["postToolUse"], data["hooks"]["stop"]
            self.assertEqual([(e["matcher"], unpinned(e["command"])) for e in post],
                             [("Write", "uvx brevio-core hook run --event post-edit --client cursor"),
                              ("Shell", "uvx brevio-core hook run --event post-command --client cursor")])
            self.assertEqual((stop[0]["loop_limit"], unpinned(stop[0]["command"])),
                             (1, "uvx brevio-core hook run --event stop --client cursor"))
            status = json.loads(run_cli(d, "hook", "status", "--json").stdout)
            self.assertEqual((status["cursor"], status["claude_code"]), (True, False))


class TestClaudeCode(unittest.TestCase):
    def test_an_edit_that_adds_a_secret_is_blocked_with_the_finding_and_its_fix_hint(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            res = fire(d, "claude_code", "PostToolUse", claude_edit(d, "src/a.ts", CLEAN, CLEAN + AWS_EXAMPLE_LINE))
            self.assertEqual(res.returncode, 0, res.stderr)
            out = json.loads(res.stdout)
            self.assertEqual(out["decision"], "block")
            self.assertIn("src/a.ts:2 [R13.1] ", out["reason"])
            self.assertIn(" -> ", out["reason"])

    def test_a_clean_edit_exits_0_and_says_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            res = fire(d, "claude_code", "PostToolUse", claude_edit(d, "src/a.ts", CLEAN, CLEAN + CLEAN))
            self.assertEqual((res.returncode, res.stdout, res.stderr), (0, "", ""))

    def test_an_edit_reports_only_the_lines_it_changed(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=AWS_EXAMPLE_LINE + CLEAN)
            res = fire(d, "claude_code", "PostToolUse", claude_edit(d, "src/a.ts", CLEAN, CLEAN + CLEAN))
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)

    def test_an_ignore_comment_the_agent_adds_does_not_silence_the_finding(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            res = fire(d, "claude_code", "PostToolUse", claude_edit(d, "src/a.ts", CLEAN, CLEAN + IGNORED))
            reason = json.loads(res.stdout)["reason"]
            self.assertIn("src/a.ts:3 [R13.1]", reason)
            self.assertIn("counts in CI only", reason)

    def test_a_new_file_outside_git_is_scanned_whole(self):
        with tempfile.TemporaryDirectory() as d:
            path = write(d, "new.ts", CLEAN + AWS_EXAMPLE_LINE)
            event = {**claude_edit(d, "new.ts", CLEAN, CLEAN), "tool_name": "Write",
                     "tool_input": {"file_path": path, "content": CLEAN + AWS_EXAMPLE_LINE}}
            res = fire(d, "claude_code", "PostToolUse", event)
            self.assertIn("new.ts:2 [R13.1]", json.loads(res.stdout)["reason"])

    def test_an_edit_of_a_vendored_minified_or_bundled_file_is_not_scanned(self):
        """Code the project keeps but did not write is not the agent's to fix (E35 precision run, 2026-11-05)."""
        with tempfile.TemporaryDirectory() as d:
            files = {"vendor/lib.ts": CLEAN, "app.min.js": CLEAN, "app.ts": "/*" + "*" * 600 + "*/\n" + CLEAN}
            for rel, text in files.items():
                write(d, rel, text)
            repo(d)
            for rel in files:
                res = fire(d, "claude_code", "PostToolUse", claude_edit(d, rel, CLEAN, CLEAN + AWS_EXAMPLE_LINE))
                self.assertEqual((res.returncode, res.stdout), (0, ""), rel)

    def test_an_edit_outside_the_project_is_not_scanned(self):
        """An absolute path or a symlink that resolves outside the project says nothing (ported from a parallel fix)."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as elsewhere:
            repo(d, src__a_ts=CLEAN)
            outside = write(elsewhere, "probe.ts", AWS_EXAMPLE_LINE)
            os.symlink(outside, os.path.join(d, "src", "link.ts"))
            for path in (outside, os.path.join(d, "src", "link.ts"), os.path.join(d, "src", "..", "..", "x.ts")):
                event = {**claude_edit(d, "src/a.ts", CLEAN, CLEAN), "tool_name": "Write",
                         "tool_input": {"file_path": path, "content": AWS_EXAMPLE_LINE}}
                res = fire(d, "claude_code", "PostToolUse", event)
                self.assertEqual((res.returncode, res.stdout), (0, ""), path)

    def test_an_edit_is_scanned_when_the_agent_runs_from_a_subfolder(self):
        """Claude Code runs the hook in the session's current folder; the hook finds the project root itself."""
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN, docs__keep_ts=CLEAN)
            event = {**claude_edit(d, "src/a.ts", CLEAN, CLEAN + AWS_EXAMPLE_LINE), "cwd": os.path.join(d, "docs")}
            assert run_cli(d, "hook", "install", "--target", "claude_code").returncode == 0
            res = run_cli(os.path.join(d, "docs"), *installed_command(d, "claude_code", "PostToolUse"),
                          stdin=json.dumps(event))
            self.assertIn("src/a.ts:2 [R13.1]", json.loads(res.stdout)["reason"], res.stderr)

    def test_stop_blocks_once_on_the_uncommitted_changes_and_never_when_a_stop_hook_already_continued(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "src/b.ts", AWS_EXAMPLE_LINE)
            first = fire(d, "claude_code", "Stop", claude_stop(d))
            out = json.loads(first.stdout)
            self.assertEqual(out["decision"], "block")
            self.assertIn("src/b.ts:1 [R13.1]", out["reason"])
            again = fire(d, "claude_code", "Stop", claude_stop(d, active=True))
            self.assertEqual((again.returncode, again.stdout, again.stderr), (0, "", ""))

    def test_a_blocking_stop_also_writes_the_reason_to_stderr_and_still_exits_2(self):
        """D23 (2026-11-13): the reason was only on stdout, which Claude Code does not read on exit 2."""
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "src/b.ts", AWS_EXAMPLE_LINE)
            res = fire(d, "claude_code", "Stop", claude_stop(d))
            self.assertEqual(res.returncode, 2)
            self.assertEqual(res.stderr.strip(), json.loads(res.stdout)["reason"])
            self.assertIn("src/b.ts:1 [R13.1]", res.stderr)
            self.assertIn("Exits 0, and 2 at stop", run_cli(d, "hook", "run", "--help").stdout.replace("\n", " ").replace("  ", " "))

    def test_a_cursor_stop_writes_its_follow_up_to_stderr_too(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "src/b.ts", AWS_EXAMPLE_LINE)
            res = fire(d, "cursor", "stop", cursor_event(d, "stop", status="completed", loop_count=0))
            self.assertEqual(res.stderr.strip(), json.loads(res.stdout)["followup_message"])

    def test_the_agent_cannot_silence_the_stop_hook_by_running_baseline(self):
        """D21 and I26 (2026-11-13): after ``baseline`` the stop hook printed nothing while ``scan --diff`` reported it."""
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "src/b.py", "try:\n    save()\nexcept:\n    pass\n")
            self.assertEqual(json.loads(fire(d, "claude_code", "Stop", claude_stop(d)).stdout)["decision"], "block")
            self.assertEqual(run_cli(d, "baseline").returncode, 0)
            self.assertTrue(os.path.isfile(os.path.join(d, ".brevio", "baseline.json")))
            res = fire(d, "claude_code", "Stop", claude_stop(d))
            self.assertEqual(res.returncode, 2, res.stderr)
            reason = json.loads(res.stdout)["reason"]
            self.assertIn(".brevio/baseline.json:", reason)
            self.assertIn("[suppression]", reason)
            self.assertIn("src/b.py", reason)

    def test_an_edit_of_the_baseline_file_is_reported_at_once_and_a_committed_baseline_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "src/b.py", "try:\n    save()\nexcept:\n    pass\n")
            self.assertEqual(run_cli(d, "baseline").returncode, 0)
            path = os.path.join(d, ".brevio", "baseline.json")
            event = {"cwd": d, "tool_input": {"file_path": path}}
            res = fire(d, "claude_code", "PostToolUse", event)
            self.assertIn("[suppression]", json.loads(res.stdout)["reason"])
            git(d, "add", "-A")
            git(d, "commit", "-q", "-m", "a person reviewed the baseline")
            quiet = fire(d, "claude_code", "Stop", claude_stop(d))
            self.assertEqual((quiet.returncode, quiet.stdout), (0, ""), quiet.stderr)

    def test_a_clean_stop_lets_the_agent_finish(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "src/a.ts", CLEAN + CLEAN)
            res = fire(d, "claude_code", "Stop", claude_stop(d))
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)

    def test_stop_runs_core_gates_for_imports_migrations_and_agent_files_when_available(self):
        from brevio.pipeline import core_cli, core_hook
        if core_cli.set_module("pipeline.gates", "core_gates") is None:
            self.skipTest("this set ships no core_gates")

        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "package.json", '{"name": "pkg"}\n')
            write(d, "src/a.ts", "import { x } from './nonexistent';\n")
            write(d, "migrations/002_drop.sql", "DROP TABLE users;\n")
            write(d, "CLAUDE.md", "Hidden \u202e injection\n")
            cwd = os.getcwd()
            try:
                out = core_hook.answer(json.dumps(claude_stop(d)), "stop", "claude_code")
            finally:
                os.chdir(cwd)
            parsed = json.loads(out)
            self.assertEqual(parsed["decision"], "block")
            self.assertIn("[R9.4]", parsed["reason"])
            self.assertIn("[R4.6]", parsed["reason"])
            self.assertIn("[R2.8]", parsed["reason"])


class TestTheFixHintFitsTheLanguage(unittest.TestCase):
    def test_a_python_file_gets_python_hints_for_a_sql_string_and_a_bare_except(self):
        """D20 and I25 (2026-11-13): both were answered with a TypeScript example."""
        with tempfile.TemporaryDirectory() as d:
            repo(d, app__old_py="x = 1\n")
            body = 'def save(cur, n):\n    cur.execute(f"SELECT * FROM t WHERE n = {n}")\n    try:\n        run()\n    except:\n        pass\n'
            write(d, "app/new.py", body)
            event = {"cwd": d, "tool_input": {"file_path": os.path.join(d, "app", "new.py")}}
            reason = json.loads(fire(d, "claude_code", "PostToolUse", event).stdout)["reason"]
            sql = next(ln for ln in reason.splitlines() if "[R2.8]" in ln)
            bare = next(ln for ln in reason.splitlines() if "[R5.4]" in ln)
            self.assertIn("%s", sql)
            self.assertIn("-> except ValueError", bare)
            self.assertNotIn("JSON.parse", reason)
            self.assertNotIn("catch", reason)

    def test_a_finding_that_has_no_example_in_the_files_language_carries_no_arrow(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.py", "def f():\n    eval(x)\n    return 1\n")
            lines = run_cli(d, "scan", "--format", "agent", "a.py").stdout
            self.assertNotIn(" -> ", lines)


class TestCursor(unittest.TestCase):
    def test_a_write_that_adds_a_secret_comes_back_as_additional_context(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            path = write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE)
            res = fire(d, "cursor", "postToolUse", cursor_event(d, "postToolUse", tool_name="Write", cwd=d,
                                                                  tool_input={"file_path": path}, tool_output="{}"))
            self.assertIn("src/a.ts:2 [R13.1]", json.loads(res.stdout)["additional_context"], res.stderr)

    def test_stop_asks_for_one_follow_up_and_then_lets_the_agent_finish(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE)
            first = fire(d, "cursor", "stop", cursor_event(d, "stop", status="completed", loop_count=0))
            self.assertIn("[R13.1]", json.loads(first.stdout)["followup_message"])
            again = fire(d, "cursor", "stop", cursor_event(d, "stop", status="completed", loop_count=1))
            self.assertEqual((again.returncode, again.stdout), (0, ""))

    def test_claude_codes_hook_run_by_cursor_stays_quiet_when_cursors_own_hook_is_installed(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            path = write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE)
            event = cursor_event(d, "postToolUse", tool_name="Write", tool_input={"file_path": path})
            alone = fire(d, "claude_code", "PostToolUse", event)
            self.assertIn("[R13.1]", json.loads(alone.stdout)["additional_context"])
            self.assertEqual(run_cli(d, "hook", "install", "--target", "cursor").returncode, 0)
            self.assertEqual(fire(d, "claude_code", "PostToolUse", event).stdout, "")


def claude_bash(d, command):
    """Claude Code's PostToolUse event after its Bash tool ran ``command``."""
    return {"session_id": "s1", "transcript_path": os.path.join(d, "t.jsonl"), "cwd": d, "permission_mode": "default",
            "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_2",
            "tool_input": {"command": command, "description": "Commit"},
            "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}


HEREDOC = "git commit -m \"$(cat <<'EOF'\nRevert the cache change\n\nIt broke the build.\nEOF\n)\""


class TestLearnAfterAFixCommit(unittest.TestCase):
    """A fix commit made by the agent gets one line suggesting ``learn`` (ROADMAP E40): never a block, never a run."""

    def test_a_fix_commit_gets_one_line_of_context_suggesting_learn_and_nothing_is_run(self):
        for command in ("git commit -m 'Fix the null check in totals'", "git add -A && git commit -am \"bugfix: off by one\"",
                        "git -C . commit -m 'regression in the parser'", HEREDOC, "git commit -m 'BUG: wrong total'"):
            with tempfile.TemporaryDirectory() as d:
                repo(d, src__a_ts=CLEAN)
                res = fire(d, "claude_code", "PostToolUse", claude_bash(d, command), shell=True)
                self.assertEqual(res.returncode, 0, res.stderr)
                out = json.loads(res.stdout)
                self.assertNotIn("decision", out, command)
                text = out["hookSpecificOutput"]["additionalContext"]
                self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
                self.assertEqual(len(text.splitlines()), 1)
                self.assertLess(text.index("`brevio-core learn --dry-run`"), text.index("then `brevio-core learn`"))
                self.assertFalse(os.path.exists(os.path.join(d, ".brevio")), command)
                self.assertFalse(os.path.exists(os.path.join(d, ".agents")), command)

    def test_other_commands_and_other_commits_say_nothing(self):
        for command in ("git commit -m 'Add the totals page'", "git commit -m 'add a fixture for totals'",
                        "./fix.sh && git commit -m 'tidy up'", "git status", "npm test -- --grep fix", "git log --grep fix"):
            with tempfile.TemporaryDirectory() as d:
                repo(d, src__a_ts=CLEAN)
                res = fire(d, "claude_code", "PostToolUse", claude_bash(d, command), shell=True)
                self.assertEqual((res.returncode, res.stdout, res.stderr), (0, "", ""), command)

    def test_the_shell_hook_never_scans(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, "src/a.ts", CLEAN + AWS_EXAMPLE_LINE)
            res = fire(d, "claude_code", "PostToolUse", claude_bash(d, "git commit -am 'Fix totals'"), shell=True)
            self.assertNotIn("R13.1", res.stdout)
            self.assertIn("learn", json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_cursor_gets_the_suggestion_as_additional_context_from_its_shell_tool(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            event = cursor_event(d, "postToolUse", tool_name="Shell", cwd=d,
                                 tool_input={"command": "git commit -m 'fix: totals'", "working_directory": d})
            res = fire(d, "cursor", "postToolUse", event, shell=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("brevio-core learn --dry-run", json.loads(res.stdout)["additional_context"])
            quiet = cursor_event(d, "postToolUse", tool_name="Shell", tool_input={"command": "ls"})
            self.assertEqual(fire(d, "cursor", "postToolUse", quiet, shell=True).stdout, "")


class TestFailOpen(unittest.TestCase):
    def test_input_that_is_not_json_exits_0_with_one_line(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "hook", "run", "--event", "post-edit", stdin="not json")
            self.assertEqual((res.returncode, res.stdout), (0, ""))
            self.assertEqual(len(res.stderr.splitlines()), 1, res.stderr)
            self.assertTrue(res.stderr.startswith("brevio-core hook: skipped after an internal error"), res.stderr)

    def test_a_scan_past_its_budget_exits_0_with_one_line(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d)
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            res = run_cli(d, "hook", "run", "--event", "stop", "--budget", "0", stdin=json.dumps(claude_stop(d)))
            self.assertEqual((res.returncode, res.stdout), (0, ""))
            self.assertEqual(res.stderr, "brevio-core hook: skipped, the scan took longer than 0 s\n")

    def test_a_scan_that_overran_its_budget_is_dropped_even_when_the_hook_wakes_after_it_finished(self):
        """Under CPU load the waiting thread can wake after a late scan is done; the late answer must still be dropped.

        The test above failed 2 times in 30 under load that way (2026-11-05). Here the wait always outlasts the scan."""
        late_wake = ("import sys, threading; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_hook; "
                     "join = threading.Thread.join; threading.Thread.join = lambda self, timeout=None: join(self); "
                     "raise SystemExit(core_hook.main(sys.argv[2:]))")
        with tempfile.TemporaryDirectory() as d:
            repo(d)
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            res = subprocess.run([sys.executable, "-I", "-c", late_wake, TREE, "--event", "stop", "--budget", "0"], cwd=d,
                                 input=json.dumps(claude_stop(d)), capture_output=True, encoding="utf-8", timeout=60,
                                 env=portable.child_env(_PATH, d))
            self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)
            self.assertEqual(res.stderr, "brevio-core hook: skipped, the scan took longer than 0 s\n")

    def test_a_broken_baseline_does_not_block_the_agent(self):
        with tempfile.TemporaryDirectory() as d:
            repo(d, src__a_ts=CLEAN)
            write(d, ".brevio/baseline.json", "{ not json")
            res = run_cli(d, "hook", "run", "--event", "post-edit",
                          stdin=json.dumps(claude_edit(d, "src/a.ts", CLEAN, CLEAN + AWS_EXAMPLE_LINE)))
            self.assertEqual((res.returncode, res.stdout), (0, ""))
            self.assertIn("skipped after an internal error", res.stderr)


class TestAnEditedFileOnAnotherDrive(unittest.TestCase):
    def test_a_file_that_has_no_path_relative_to_the_project_is_not_scanned_and_does_not_crash_the_hook(self):
        """On Windows ``os.path.relpath`` raises ``ValueError`` for two drives; the file is outside the project."""
        script = ("import os, sys; sys.path.insert(0, sys.argv[1])\n"
                  "from brevio.pipeline import core_hook\n"
                  "def other_drive(*args, **kwargs):\n    raise ValueError(\"path is on mount 'C:', start on mount 'D:'\")\n"
                  "os.path.relpath = other_drive\n"
                  "event = core_hook.Event('claude_code', 'post-edit', os.path.join(os.getcwd(), 'a.ts'), os.getcwd(), False)\n"
                  "print(core_hook.changed_lines(event))\n")
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            res = subprocess.run([sys.executable, "-I", "-c", script, TREE], cwd=d, capture_output=True, encoding="utf-8",
                                 timeout=60, env=portable.child_env(_PATH, d))
        self.assertEqual((res.returncode, res.stdout.strip()), (0, "{}"), res.stderr)


class TestLatency(unittest.TestCase):
    def test_a_post_edit_hook_on_a_2000_line_file_finishes_well_inside_its_budget(self):
        with tempfile.TemporaryDirectory() as d:
            body = "".join(f"export const v{i} = {i};\n" for i in range(2000))
            repo(d, src__big_ts=body)
            edited = body.replace(" = ", " =  ")      # every line changed
            path = write(d, "src/big.ts", edited[:-len("export const v1999 =  1999;\n")] + AWS_EXAMPLE_LINE)
            event = {**claude_edit(d, "src/big.ts", "\0", "\0"), "tool_input": {"file_path": path}}
            self.assertEqual(run_cli(d, "hook", "install", "--target", "claude_code").returncode, 0)
            argv = installed_command(d, "claude_code", "PostToolUse")
            # The CPU time of the hook process (and, on POSIX, the git it runs), not the wall clock: on a loaded machine
            # the wall clock measured the other processes (1.68 s under the parallel suite). The hook's own
            # wall-clock budget is lifted for the same reason; its fail-open has a test of its own.
            res, took = portable.run_with_cpu([sys.executable, "-I", "-c", _RUN, TREE, *argv, "--budget", "60"], cwd=d,
                                              input=json.dumps(event), capture_output=True, encoding="utf-8", timeout=60,
                                              env=portable.child_env(_PATH, d))
            self.assertIn("src/big.ts:2000 [R13.1]", json.loads(res.stdout)["reason"], res.stderr)
            self.assertLess(took, LATENCY_LIMIT)


class TestAgentFormat(unittest.TestCase):
    def test_agent_lines_are_grouped_by_rule_with_a_fix_hint_and_cut_at_20(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", AWS_EXAMPLE_LINE * 15 + "// @ts-ignore\n" * 10)
            res = run_cli(d, "scan", "--format", "agent", "a.ts")
            lines = res.stdout.splitlines()
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertEqual(len(lines), 21)
            self.assertEqual(lines[-1], "+5 more")
            rules = [ln.split(" ")[1] for ln in lines[:-1]]
            self.assertEqual(rules, sorted(rules, key=rules.index))     # each rule's lines together
            self.assertTrue(all(" -> " in ln for ln in lines[:-1]), lines)
            self.assertRegex(lines[0], r"^a\.ts:\d+ \[R\d+\.\d+\] ")

    def test_a_clean_scan_prints_no_agent_lines(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", CLEAN)
            res = run_cli(d, "scan", "--format", "agent", "a.ts")
            self.assertEqual((res.returncode, res.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
