"""Unit and CLI tests for brevio-core single-task workflow (connect -> plan -> build -> qa -> pr).

Standard library only. Tested against exported clean-room invariants.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from brevio.pipeline import core_heal, core_task

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
_RUN = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from brevio.pipeline import core_cli; "
    "raise SystemExit(core_cli.main(sys.argv[2:]))"
)

AWS_KEY_SAMPLE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
REQUESTS_SAMPLE = "import requests\nresp = requests.get(url)\n"
CLEAN_TS_SAMPLE = "export const add = (a: number, b: number): number => a + b;\n"


def run_cli(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run brevio-core with args in cwd under isolated python -I."""
    return subprocess.run(
        [sys.executable, "-I", "-c", _RUN, ROOT, *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,       # no terminal: connect must not stop to ask
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )


def git(d: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


def write(d: str, rel: str, text: str) -> None:
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


SHELL_TRUE = "import subprocess\n\n\ndef go(cmd):\n    subprocess.run(cmd, shell=True)\n"
CLEAN_PY = "def go(cmd):\n    return cmd\n"


def guard_ids(d: str) -> list[str]:
    path = os.path.join(d, ".brevio", "guards.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return sorted(json.load(fh).get("guards", {}))


class TestCoreTaskWorkflow(unittest.TestCase):
    def test_connect_and_init_specs_and_undo(self) -> None:
        """Test brevio-core connect and init --specs generating and undo cleanly removing specs."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")

            # 1. brevio-core connect
            r = run_cli(d, "connect")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isfile(os.path.join(d, "CLAUDE.md")))
            self.assertTrue(os.path.isfile(os.path.join(d, "CONSTITUTION.md")))
            self.assertTrue(os.path.isfile(os.path.join(d, "roadmap.json")))
            self.assertTrue(os.path.isfile(os.path.join(d, ".brevio", "connection.json")))

            with open(os.path.join(d, "roadmap.json"), encoding="utf-8") as fh:
                rd = json.load(fh)
            self.assertEqual(rd.get("edition"), "core")
            self.assertEqual(len(rd.get("items", [])), 3)

            # Clean up spec files for init test
            for fname in ("CLAUDE.md", "CONSTITUTION.md", "roadmap.json"):
                os.remove(os.path.join(d, fname))

            # 2. brevio-core init --specs --yes
            r_init = run_cli(d, "init", "--specs", "--yes")
            self.assertEqual(r_init.returncode, 0, r_init.stderr)
            self.assertTrue(os.path.isfile(os.path.join(d, "CLAUDE.md")))
            self.assertTrue(os.path.isfile(os.path.join(d, "CONSTITUTION.md")))
            self.assertTrue(os.path.isfile(os.path.join(d, "roadmap.json")))

            # 3. brevio-core init --undo --yes removes them cleanly
            r_undo = run_cli(d, "init", "--undo", "--yes")
            self.assertEqual(r_undo.returncode, 0, r_undo.stderr)
            self.assertFalse(os.path.isfile(os.path.join(d, "CLAUDE.md")))
            self.assertFalse(os.path.isfile(os.path.join(d, "CONSTITUTION.md")))
            self.assertFalse(os.path.isfile(os.path.join(d, "roadmap.json")))

    def test_plan_creates_task_plan_json_and_md(self) -> None:
        """Test brevio-core plan creates .brevio/task_plan.json and TASK_PLAN.md in text and json."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "server.py", "def handle(): pass\n")
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")

            # Text mode
            r = run_cli(d, "plan", "Implement tenant rate limiter")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Implement tenant rate limiter", r.stdout)

            plan_json_path = os.path.join(d, ".brevio", "task_plan.json")
            plan_md_path = os.path.join(d, ".brevio", "TASK_PLAN.md")
            self.assertTrue(os.path.isfile(plan_json_path))
            self.assertTrue(os.path.isfile(plan_md_path))

            with open(plan_json_path, encoding="utf-8") as fh:
                pj = json.load(fh)
            self.assertTrue(pj["ok"])
            self.assertEqual(pj["task"], "Implement tenant rate limiter")

            # JSON mode
            r_json = run_cli(d, "plan", "Implement tenant rate limiter", "--json")
            self.assertEqual(r_json.returncode, 0, r_json.stderr)
            data = json.loads(r_json.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["task"], "Implement tenant rate limiter")
            self.assertIn("server.py", data["target_files"])

    def test_build_autoheals_fixable_finding(self) -> None:
        """Test brevio-core build auto-heals fixable finding and writes BUILD_PACKET.md."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "fetcher.py", REQUESTS_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")

            # Text mode
            r = run_cli(d, "build")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Fixed files: 1", r.stdout)

            packet_path = os.path.join(d, ".brevio", "BUILD_PACKET.md")
            self.assertTrue(os.path.isfile(packet_path))

            with open(os.path.join(d, "fetcher.py"), encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("timeout=30", content)

            # JSON mode
            r_json = run_cli(d, "build", "--json")
            self.assertEqual(r_json.returncode, 0, r_json.stderr)
            data = json.loads(r_json.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["status"], "built")

    def test_qa_fails_on_unfixable_and_passes_on_clean(self) -> None:
        """Test brevio-core qa fails on an unfixable finding in the task's change and passes once it is clean."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "src/key.ts", CLEAN_TS_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")

            # The task plants a secret: the change is scored (qa reads the diff, 2026-11-11), so it fails.
            write(d, "src/key.ts", AWS_KEY_SAMPLE)
            r_fail = run_cli(d, "qa")
            self.assertEqual(r_fail.returncode, 1)
            self.assertIn("FAIL", r_fail.stdout)

            report_path = os.path.join(d, ".brevio", "qa_report.json")
            self.assertTrue(os.path.isfile(report_path))
            with open(report_path, encoding="utf-8") as fh:
                rep = json.load(fh)
            self.assertFalse(rep["ok"])
            self.assertGreater(rep["finding_count"], 0)

            # Replace with clean code
            write(d, "src/key.ts", CLEAN_TS_SAMPLE)
            r_pass = run_cli(d, "qa")
            self.assertEqual(r_pass.returncode, 0, r_pass.stderr)
            self.assertIn("PASS", r_pass.stdout)

            with open(report_path, encoding="utf-8") as fh:
                rep_clean = json.load(fh)
            self.assertTrue(rep_clean["ok"])
            self.assertEqual(rep_clean["finding_count"], 0)

    def test_an_old_finding_in_the_repository_does_not_stop_a_clean_task_from_passing(self) -> None:
        """qa scores the task's diff, not the whole repository: one old finding must not block every task (2026-11-11)."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "src/old.ts", AWS_KEY_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "an old finding")
            write(d, "src/new.ts", CLEAN_TS_SAMPLE)

            r = run_cli(d, "qa")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Findings: 0", r.stdout)

    def test_qa_without_a_plan_base_reads_what_scan_diff_reads_and_the_report_names_the_base(self) -> None:
        """qa and pr default to the base of ``scan --diff``, as ``check`` does (I10, 2026-11-13)."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            write(d, "src/a.ts", CLEAN_TS_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            git(d, "checkout", "-q", "-b", "feature")
            write(d, "src/b.ts", AWS_KEY_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "feature")
            r = run_cli(d, "qa")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            with open(os.path.join(d, ".brevio", "qa_report.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["base"], "main")

    def test_plan_records_the_commit_the_task_starts_from_and_qa_scores_what_changed_since(self) -> None:
        """A task committed before qa is still scored: the plan's recorded base is the diff base (2026-11-11)."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "src/ok.ts", CLEAN_TS_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            head = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, encoding="utf-8", check=True).stdout.strip()

            self.assertEqual(run_cli(d, "plan", "Add a key", "--json").returncode, 0)
            with open(os.path.join(d, ".brevio", "task_plan.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["diff_base"], head)

            write(d, "src/key.ts", AWS_KEY_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "the task, committed")
            r = run_cli(d, "qa")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("FAIL", r.stdout)
            # An explicit --diff wins over the recorded base: against HEAD nothing is left to score.
            self.assertEqual(run_cli(d, "qa", "--diff", "HEAD").returncode, 0)

    def test_pr_generates_body_and_marks_roadmap_done(self) -> None:
        """Test brevio-core pr generates PR_BODY.md and updates roadmap.json to done."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "src/math.ts", CLEAN_TS_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "init")

            # Connect to scaffold roadmap.json
            run_cli(d, "connect")
            run_cli(d, "plan", "TASK-001")

            # Run PR command
            r = run_cli(d, "pr", "--title", "feat: clean task")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("PASS", r.stdout)

            body_path = os.path.join(d, ".brevio", "PR_BODY.md")
            self.assertTrue(os.path.isfile(body_path))
            with open(body_path, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("feat: clean task", body)
            self.assertIn("TASK-001", body)
            self.assertIn("Pipeline Receipt", body)

            # Verify roadmap.json updated
            with open(os.path.join(d, "roadmap.json"), encoding="utf-8") as fh:
                rd = json.load(fh)
            self.assertEqual(rd["items"][0]["status"], "done")

    def test_task_full_pipeline_json(self) -> None:
        """Test brevio-core task end-to-end running connect -> plan -> build -> qa -> pr."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "main.py", "def health(): return {'status': 'ok'}\n")
            git(d, "add", "-A")
            git(d, "commit", "-qm", "init")

            r = run_cli(d, "task", "Add healthcheck endpoint", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            data = json.loads(r.stdout)
            self.assertTrue(data["ok"])
            self.assertTrue(data["connect"]["ok"])
            self.assertTrue(data["plan"]["ok"])
            self.assertTrue(data["build"]["ok"])
            self.assertTrue(data["qa"]["ok"])
            self.assertTrue(data["pr"]["ok"])

            self.assertTrue(os.path.isfile(os.path.join(d, "CLAUDE.md")))
            self.assertTrue(os.path.isfile(os.path.join(d, "CONSTITUTION.md")))
            self.assertTrue(os.path.isfile(os.path.join(d, "roadmap.json")))
            self.assertTrue(os.path.isfile(os.path.join(d, ".brevio", "task_plan.json")))
            self.assertTrue(os.path.isfile(os.path.join(d, ".brevio", "qa_report.json")))
            self.assertTrue(os.path.isfile(os.path.join(d, ".brevio", "PR_BODY.md")))


class TestTaskLeavesTheGuardSetAlone(unittest.TestCase):
    """D50 (2026-11-13): ``task`` and ``build`` learned a guard from the working-tree diff, the task's own files included."""

    def test_a_task_over_a_new_finding_adds_no_guard_and_a_second_task_adds_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "old.py", CLEAN_PY)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            write(d, "old.py", SHELL_TRUE)
            self.assertEqual(guard_ids(d), [])
            run_cli(d, "task", "first", "--root", ".")
            self.assertEqual(guard_ids(d), [])
            git(d, "add", "-A")
            git(d, "commit", "-qm", "the task, with its .brevio files")
            run_cli(d, "task", "second", "--root", ".")
            self.assertEqual(guard_ids(d), [])

    def test_heal_still_learns_guards_when_asked_and_ignores_the_tools_own_folders(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "old.py", CLEAN_PY)
            write(d, ".brevio/TASK_PLAN.md", "- ranked symbols: 1\n")
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            write(d, "old.py", SHELL_TRUE)
            write(d, ".brevio/TASK_PLAN.md", "- ranked symbols: 2\n- `return 1` in a plan\n")
            core_heal.run_heal(root=d, apply_fixes=False)
            ids = guard_ids(d)
        self.assertIn("guard-r5-5", ids)
        self.assertFalse([i for i in ids if "task_plan" in i or "ranked" in i], ids)


class TestQaScopeAndOutput(unittest.TestCase):
    """D33 and D11 (2026-11-13): qa says what it counted, has --all, and a failed qa or pr shows its findings."""

    def committed_findings(self, d: str) -> None:
        git(d, "init", "-q")
        for name in ("a", "b", "c"):
            write(d, f"src/{name}.ts", AWS_KEY_SAMPLE)
        git(d, "add", "-A")
        git(d, "commit", "-qm", "three old findings")

    def test_qa_with_no_commit_counts_the_whole_repository_and_says_so(self) -> None:
        for init in (True, False):
            with self.subTest(git_repository=init), tempfile.TemporaryDirectory() as d:
                if init:
                    git(d, "init", "-q")
                write(d, "a.ts", AWS_KEY_SAMPLE)
                r = run_cli(d, "qa", "--json")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                data = json.loads(r.stdout)
                self.assertEqual(data["finding_count"], 1)
                self.assertIn("whole repository", data["scope"])
                self.assertNotIn("HEAD", data["scope"])

    def test_qa_names_what_it_counted_and_all_counts_the_whole_repository(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.committed_findings(d)
            task = run_cli(d, "qa")
            self.assertEqual(task.returncode, 0, task.stdout + task.stderr)
            self.assertIn("Counted: changes since", task.stdout)
            whole = run_cli(d, "qa", "--all", "--json")
            self.assertEqual(whole.returncode, 1, whole.stdout + whole.stderr)
            data = json.loads(whole.stdout)
            self.assertFalse(data["ok"])
            self.assertEqual(data["finding_count"], 3)
            self.assertEqual(data["scope"], "the whole repository")
            self.assertEqual(json.loads(run_cli(d, "qa", "--json").stdout)["finding_count"], 0)
            text = run_cli(d, "qa", "--all")
            self.assertIn("Counted: the whole repository", text.stdout)

    def test_a_failed_qa_prints_its_findings_and_where_the_rest_are(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "src/k.ts", CLEAN_TS_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            write(d, "src/k.ts", AWS_KEY_SAMPLE)
            r = run_cli(d, "qa")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stdout)
        self.assertIn("src/k.ts:1", r.stdout)
        self.assertIn("[R13.1]", r.stdout)
        self.assertIn("Details: .brevio/qa_report.json", r.stdout)

    def test_a_failed_pr_offers_no_pull_request_command_and_a_passed_one_does(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "src/k.ts", CLEAN_TS_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            passed = run_cli(d, "pr", "--title", "ok")
            write(d, "src/k.ts", AWS_KEY_SAMPLE)
            failed = run_cli(d, "pr", "--title", "no")
            as_json = json.loads(run_cli(d, "pr", "--json").stdout)
        self.assertIn("Command: gh pr create", passed.stdout)
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("gh pr create", failed.stdout)
        self.assertIn("src/k.ts:1", failed.stdout)
        self.assertIn("Fix the findings above, run qa, then pr.", failed.stdout)
        self.assertIn("gh_command", as_json)        # the JSON keeps its 0.9.1 fields

    def test_a_failed_task_shows_findings_and_no_command(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "src/k.ts", CLEAN_TS_SAMPLE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            write(d, "src/k.ts", AWS_KEY_SAMPLE)
            r = run_cli(d, "task", "plant a key")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("gh pr create", r.stdout)
        self.assertIn("src/k.ts:1", r.stdout)


class TestConnectAndTaskSayWhatTheyWrite(unittest.TestCase):
    """D11 and D2 (2026-11-13)."""

    def test_connect_reads_the_stack_from_the_code_files_when_there_is_no_marker_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "app/m.py", CLEAN_PY)
            write(d, "web/a.ts", CLEAN_TS_SAMPLE)
            write(d, "node_modules/x/y.rb.js", "x\n")
            r = run_cli(d, "connect", "--json")
        self.assertEqual(json.loads(r.stdout)["stack"], ["Python", "TypeScript"])

    def test_connect_dry_run_writes_nothing_and_lists_the_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "a.py", CLEAN_PY)
            r = run_cli(d, "connect", "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("nothing written", r.stdout)
            self.assertIn("CONSTITUTION.md", r.stdout)
            self.assertEqual(sorted(os.listdir(d)), [".git", "a.py"])

    def test_connect_dry_run_lists_the_connection_file_too_and_a_pipe_prints_one_header(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "a.py", CLEAN_PY)
            dry = run_cli(d, "connect", "--dry-run")
            real = run_cli(d, "connect")
        self.assertIn(".brevio/connection.json", dry.stdout)
        self.assertEqual(real.stdout.count("brevio-core connect:"), 1, real.stdout)
        self.assertNotIn("Would write", real.stdout)

    def _connect_asked(self, answer: str) -> tuple[str, list[str]]:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "a.py", CLEAN_PY)
            out = io.StringIO()
            with (mock.patch.object(core_task.core_platform, "is_terminal", return_value=True),
                  mock.patch("builtins.input", return_value=answer),
                  contextlib.redirect_stdout(out)):
                core_task.main_connect(["--root", d])
            return out.getvalue(), sorted(os.listdir(d))

    def test_connect_on_a_terminal_asks_first_and_writes_nothing_on_no(self) -> None:
        out, names = self._connect_asked("n")
        self.assertEqual(names, [".git", "a.py"])
        self.assertIn("Would write: CONSTITUTION.md", out)
        self.assertIn("nothing written", out)

    def test_connect_on_a_terminal_writes_on_yes(self) -> None:
        out, names = self._connect_asked("y")
        self.assertIn("CLAUDE.md", names)
        self.assertIn("Written specs", out)

    def test_connect_without_a_terminal_writes_as_0_9_1_did_and_names_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "a.py", CLEAN_PY)
            r = run_cli(d, "connect")
            names = sorted(os.listdir(d))
            with open(os.path.join(d, "CONSTITUTION.md"), encoding="utf-8") as fh:
                constitution = fh.read()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLAUDE.md", names)
        self.assertIn("Written specs: CONSTITUTION.md, CLAUDE.md, roadmap.json", r.stdout)
        self.assertIn(".brevio/connection.json", r.stdout)
        self.assertIn("Aim for no code rule findings in modified lines.", constitution)
        self.assertIn("Do not remove existing tests", constitution)
        self.assertNotIn("Zero code rule findings", constitution)
        self.assertNotIn("Never remove", constitution)

    def test_task_lists_what_it_wrote_and_which_source_files_it_rewrote(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "fetcher.py", "x = 1\n")
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            write(d, "fetcher.py", REQUESTS_SAMPLE)
            r = run_cli(d, "task", "tweak")
        self.assertIn("Wrote: ", r.stdout)
        self.assertIn(".brevio/task_plan.json", r.stdout)
        self.assertIn("CONSTITUTION.md", r.stdout)
        self.assertIn("Rewrote source: fetcher.py", r.stdout)

    def test_the_plan_names_no_score_that_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "a.py", CLEAN_PY)
            run_cli(d, "plan", "x")
            with open(os.path.join(d, ".brevio", "TASK_PLAN.md"), encoding="utf-8") as fh:
                text = fh.read()
        self.assertNotIn("Safety Score", text)


if __name__ == "__main__":
    unittest.main()
