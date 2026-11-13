"""The 0.2 agent tools (learn, hook install, gain, add), run in a clean interpreter with only the exported tree on its path.

Standard library only: the exporter copies this file beside the code (the 0.2 set) and it runs unchanged there.
Every command runs on a temporary git repository, never on the tree itself. In the harness the tools are bound to
the harness's own workspace, so there the test first exports the 0.2 set and runs against that copy.
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
TREE = ROOT                                                    # the tree the commands run from
_RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
_MODULES_AFTER = ("import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; core_cli.main(sys.argv[2:]); "
                  "print(sorted(m for m in ('socket', 'ssl', 'urllib.request', 'http.client') if m in sys.modules))")
FIXED = "result = compute_legacy_total(items)\n"
FIXING = "result = compute_total(items)\n"


_BIN = tempfile.TemporaryDirectory()    # a PATH holding git and nothing else, so no coding client is found on it
_PATH = portable.git_only_path(_BIN.name)
_EXPORT = tempfile.TemporaryDirectory()


def setUpModule():
    global TREE
    if os.path.isfile(EXPORTER):
        out = os.path.join(_EXPORT.name, "core")
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.2"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _BIN.cleanup()
    _EXPORT.cleanup()


def run_cli(cwd, *args, code=_RUN):
    """``brevio-core`` with ``args`` in ``cwd`` under ``python -I``, with a PATH that finds git only."""
    return subprocess.run([sys.executable, "-I", "-c", code, TREE, *args], cwd=cwd, capture_output=True, encoding="utf-8",
                          timeout=60, env=portable.child_env(_PATH))


def git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], check=True,
                   capture_output=True)


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read_json(d, rel):
    with open(os.path.join(d, rel), encoding="utf-8") as fh:
        return json.load(fh)


def repo_with_a_fix():
    """A git repository whose last commit replaces ``compute_legacy_total`` with ``compute_total``."""
    d = tempfile.TemporaryDirectory()
    git(d.name, "init", "-q", "-b", "main")
    write(d.name, "src/calc.py", FIXED)
    git(d.name, "add", ".")
    git(d.name, "commit", "-q", "-m", "first")
    write(d.name, "src/calc.py", FIXING)
    git(d.name, "commit", "-q", "-am", "fix")
    return d


class TestLearn(unittest.TestCase):
    def test_the_last_fix_becomes_a_guard_and_an_agent_rule(self):
        with repo_with_a_fix() as d:
            res = run_cli(d, "learn", "--name", "no legacy total", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            guard = json.loads(res.stdout)
            self.assertEqual((guard["id"], guard["status"]), ("guard-no-legacy-total", "trial"))
            self.assertIn("compute_legacy_total", guard["patterns"][0])
            self.assertEqual(list(read_json(d, ".brevio/guards.json")["guards"]), ["guard-no-legacy-total"])
            self.assertTrue(os.path.isfile(os.path.join(d, ".agents", "rules", "guard-no-legacy-total.md")))

    def test_the_learned_guard_catches_the_defect_when_it_comes_back(self):
        with repo_with_a_fix() as d:
            self.assertEqual(run_cli(d, "learn").returncode, 0)
            write(d, "src/other.py", FIXED)
            write(d, "src/clean.py", FIXING)
            hit = run_cli(d, "learn", "--check", "src/other.py")
            self.assertEqual(hit.returncode, 1, hit.stderr)
            self.assertIn("src/other.py", hit.stdout)
            self.assertEqual(run_cli(d, "learn", "--check", "src/clean.py").returncode, 0)

    def test_a_repository_with_no_earlier_commit_says_so_and_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            res = run_cli(d, "learn")
            self.assertEqual(res.returncode, 1)
            self.assertIn("No git diff", res.stderr)


def repo_with_an_uncommitted_fix():
    """A git repository holding the defect in two files, one of them fixed in the working tree but not committed."""
    d = tempfile.TemporaryDirectory()
    git(d.name, "init", "-q", "-b", "main")
    write(d.name, "src/calc.py", FIXED)
    write(d.name, "src/report.py", "def report(items):\n    " + FIXED)
    git(d.name, "add", ".")
    git(d.name, "commit", "-q", "-m", "first")
    write(d.name, "src/calc.py", FIXING)
    return d


class TestLearnBeforeCommitting(unittest.TestCase):
    def test_dry_run_prints_the_guard_and_its_hits_in_the_repository_and_writes_nothing(self):
        with repo_with_an_uncommitted_fix() as d:
            res = run_cli(d, "learn", "--worktree", "--dry-run", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            out = json.loads(res.stdout)
            self.assertIn("compute_legacy_total", out["patterns"][0])
            self.assertEqual((out["repo_hits"], out["written"]), (1, False))    # the copy still in src/report.py
            self.assertFalse(os.path.exists(os.path.join(d, ".brevio")))
            self.assertFalse(os.path.exists(os.path.join(d, ".agents")))
            text = run_cli(d, "learn", "--worktree", "--dry-run")
            self.assertIn("matches 1 line(s) in this repository today", text.stdout)
            self.assertIn("nothing written", text.stdout)

    def test_worktree_compiles_the_uncommitted_fix(self):
        with repo_with_an_uncommitted_fix() as d:
            res = run_cli(d, "learn", "--worktree", "--name", "no legacy total", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(list(read_json(d, ".brevio/guards.json")["guards"]), ["guard-no-legacy-total"])

    def test_staged_compiles_only_the_staged_fix(self):
        with repo_with_an_uncommitted_fix() as d:
            unstaged = run_cli(d, "learn", "--staged", "--dry-run")
            self.assertEqual(unstaged.returncode, 1)
            self.assertIn("No git diff changes detected for the staged changes", unstaged.stderr)
            git(d, "add", "src/calc.py")
            staged = run_cli(d, "learn", "--staged", "--dry-run", "--json")
            self.assertEqual(staged.returncode, 0, staged.stderr)
            self.assertEqual(json.loads(staged.stdout)["source_file"], "src/calc.py")

    def test_staged_and_worktree_cannot_be_given_together(self):
        with repo_with_an_uncommitted_fix() as d:
            self.assertEqual(run_cli(d, "learn", "--staged", "--worktree").returncode, 2)


def unpinned(command):
    """``command`` without the ``==version`` pin that a build knowing its release version writes (D12, 2026-11-13)."""
    return re.sub(r"brevio-core==\d+\.\d+\.\d+", "brevio-core", command)


class TestHookInstall(unittest.TestCase):
    def test_a_named_agent_gets_the_hook_run_command_and_a_second_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli(d, "hook", "install", "--target", "claude_code", "--json").returncode, 0)
            first = read_json(d, ".claude/settings.json")
            hooks = first["hooks"]["PostToolUse"]
            self.assertEqual([(g["matcher"], [unpinned(h["command"]) for h in g["hooks"]]) for g in hooks],
                             [("Edit|Write|MultiEdit", ["uvx brevio-core hook run --event post-edit"]),
                              ("Bash", ["uvx brevio-core hook run --event post-command"])])
            self.assertEqual(run_cli(d, "hook", "install", "--target", "claude_code").returncode, 0)
            self.assertEqual(read_json(d, ".claude/settings.json"), first)

    def test_an_older_file_variable_hook_is_replaced_by_the_hook_run_command(self):
        with tempfile.TemporaryDirectory() as d:
            old = {"hooks": {"PostToolUse": [{"command": "uvx brevio-core scan --file $FILE", "tools": ["Write"]}]}}
            os.makedirs(os.path.join(d, ".claude"))
            with open(os.path.join(d, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
                json.dump(old, fh)
            self.assertEqual(run_cli(d, "hook", "install", "--target", "claude_code").returncode, 0)
            hooks = read_json(d, ".claude/settings.json")["hooks"]["PostToolUse"]
            self.assertEqual([unpinned(h["command"]) for g in hooks for h in g["hooks"]],
                             ["uvx brevio-core hook run --event post-edit", "uvx brevio-core hook run --event post-command"])

    def test_hook_run_in_a_build_without_the_runner_lets_the_agent_go_on(self):
        if os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_hook.py")):
            self.skipTest("this build ships the hook runner (tests/pipeline/core_export/test_core_hook.py covers it)")
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run([sys.executable, "-I", "-c", _RUN, TREE, "hook", "run", "--event", "stop"], cwd=d, input="{}",
                                 capture_output=True, encoding="utf-8", timeout=60, env=portable.child_env(_PATH))
            self.assertEqual((res.returncode, res.stdout), (0, ""))
            self.assertIn("no hook runner", res.stderr)

    def test_all_installs_every_supported_agent_and_status_reports_them(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run_cli(d, "hook", "install", "--all").returncode, 0)
            for rel in (".claude/settings.json", ".cursor/rules/brevio-core.mdc", ".hermes/skills/brevio-core/SKILL.md",
                        "skills/brevio-core/SKILL.md"):
                self.assertTrue(os.path.isfile(os.path.join(d, rel)), rel)
            status = json.loads(run_cli(d, "hook", "status", "--json").stdout)
            self.assertEqual(sorted(status), ["claude_code", "cursor", "hermes", "openclaw"])
            self.assertTrue(all(status.values()))

    def test_the_box_lines_it_prints_go_out_as_utf_8_through_a_pipe_with_a_legacy_code_page(self):
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run([sys.executable, "-I", "-c", "import sys; " + portable.LEGACY_STREAMS + _RUN, TREE,
                                  "hook", "install", "--all"], cwd=d, capture_output=True, timeout=60, env=portable.child_env(_PATH, d))
            self.assertEqual((res.returncode, res.stderr), (0, b""))
            self.assertIn("\u2550".encode("utf-8"), res.stdout)

    def test_with_no_target_only_the_agents_found_here_are_wired(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".cursor"))
            res = run_cli(d, "hook", "install", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(json.loads(res.stdout), {"cursor": True})
            self.assertFalse(os.path.exists(os.path.join(d, ".claude")))

    def test_detect_lists_the_clients_it_found(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".hermes"))
            found = json.loads(run_cli(d, "hook", "detect", "--json").stdout)
            self.assertEqual(found["active_agents"], ["hermes"])

    def test_nothing_it_prints_or_writes_makes_a_speed_claim(self):
        claim = re.compile(r"\d\s*ms\b", re.I)
        with tempfile.TemporaryDirectory() as d:
            texts = [run_cli(d, "hook", "install", "--all").stdout, run_cli(d, "hook", "--help").stdout]
            for rel in (".claude/settings.json", ".cursor/rules/brevio-core.mdc", ".hermes/skills/brevio-core/SKILL.md"):
                with open(os.path.join(d, rel), encoding="utf-8") as fh:
                    texts.append(fh.read())
            self.assertEqual([t for t in texts if claim.search(t)], [])


class TestGain(unittest.TestCase):
    def test_a_new_project_shows_counts_of_zero_and_no_savings(self):
        with tempfile.TemporaryDirectory() as d:
            summary = json.loads(run_cli(d, "gain", "--json").stdout)
            self.assertEqual((summary["total_interceptions"], summary["savings_measured"], summary["tokens_saved"]),
                             (0, False, 0))
            text = run_cli(d, "gain").stdout
            self.assertIn("Learned-guard hits recorded: 0", text)
            self.assertIn("`learn --check` finds a guard's line", text)
            self.assertIn("Learned guards: 0 active, 0 on trial", text)
            self.assertNotIn("saved (measured)", text)

    def test_a_caught_defect_is_counted_and_never_priced(self):
        with repo_with_a_fix() as d:
            run_cli(d, "learn")
            write(d, "src/other.py", FIXED)
            self.assertEqual(run_cli(d, "learn", "--check", "src/other.py").returncode, 1)
            summary = json.loads(run_cli(d, "gain", "--json").stdout)
            # A kept guard is on trial until a person promotes it: gain no longer counts it as active (2026-11-12).
            self.assertEqual((summary["total_interceptions"], summary["active_learned_guards"], summary["trial_learned_guards"]),
                             (1, 0, 1))
            self.assertEqual((summary["savings_measured"], summary["tokens_saved"], summary["cost_saved_usd"]), (False, 0, 0.0))
            self.assertNotIn("saved (measured)", run_cli(d, "gain").stdout)

    def test_a_hit_that_scan_reports_is_not_in_the_ledger_and_the_card_says_only_learn_check_records(self):
        """The card said every learned-guard hit was recorded; a hit of `scan` is not (2026-11-12, review of the first-five stage)."""
        with repo_with_a_fix() as d:
            run_cli(d, "learn")
            write(d, "src/other.py", FIXED)
            self.assertIn("guard:", run_cli(d, "scan", "src/other.py").stdout)
            card = run_cli(d, "gain")
            summary = json.loads(run_cli(d, "gain", "--json").stdout)
            self.assertEqual(summary["total_interceptions"], 0)
            self.assertIn("a hit that `scan` or the agent hook reports", card.stdout)

    def test_an_old_ledger_that_held_an_estimate_is_not_shown_as_savings(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, ".brevio/stats.json", json.dumps({"version": 1, "total_interceptions": 3, "tokens_saved": 135000,
                                                      "cost_saved_usd": 1.35, "events": []}))
            summary = json.loads(run_cli(d, "gain", "--json").stdout)
            self.assertEqual((summary["total_interceptions"], summary["tokens_saved"], summary["savings_measured"]),
                             (3, 0, False))


class TestAdd(unittest.TestCase):
    def test_a_pack_is_added_with_its_rules_and_listed_without_a_name(self):
        with tempfile.TemporaryDirectory() as d:
            listing = run_cli(d, "add", "--json")
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertIn("fastapi-async", [p["id"] for p in json.loads(listing.stdout)])
            res = run_cli(d, "add", "fastapi-async", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(json.loads(res.stdout)["installed_guards"], ["pack-fastapi-no-blocking-io", "pack-fastapi-lifespan"])
            guards = read_json(d, ".brevio/guards.json")["guards"]
            self.assertEqual({g["status"] for g in guards.values()}, {"active"})
            self.assertTrue(os.path.isfile(os.path.join(d, ".agents", "rules", "pack-fastapi-lifespan.md")))
            # Verify regex pattern scanning catches blocking requests.get and time.sleep
            write(d, "api/routes.py", "async def get_items(url):\n    res = requests.get(url)\n    time.sleep(1)\n")
            check_res = run_cli(d, "learn", "--check", "api/routes.py")
            self.assertEqual(check_res.returncode, 1, check_res.stderr)
            self.assertIn("pack-fastapi-no-blocking-io", check_res.stdout)

    def test_multi_hunk_plan_guard_and_title_escaping(self):
        from brevio.pipeline.gates import defect_compiler

        diff = (
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "+Added a documentation line only\n"
            "--- a/src/service.py\n"
            "+++ b/src/service.py\n"
            "-legacy_unsafe_call(user_input)\n"
            "+safe_call(user_input)\n"
        )
        with tempfile.TemporaryDirectory() as d:
            g = defect_compiler.compile_guard_from_diff(diff, name='Bad "quote"\nnewline', cwd=d)
            self.assertIsNotNone(g)
            self.assertEqual(g["source_file"], "src/service.py")
            self.assertEqual(g["patterns"], ["legacy_unsafe_call(user_input)"])
            with open(os.path.join(d, g["rule_file"]), encoding="utf-8") as fh:
                rule_md = fh.read()
            self.assertIn("description: \"Learned Guard: Bad 'quote' newline\"", rule_md)

    def test_add_help_shows_the_usage_of_add_and_says_that_no_name_lists_the_packs(self):
        """``add --help`` printed the usage of ``list`` (D47, 2026-11-13); ``add NAME --help`` was already right."""
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "add", "--help")
            self.assertEqual(res.returncode, 0)
            self.assertIn("usage: brevio-core add [-h] [--json] pack", res.stdout)
            self.assertIn("with no name list the packs", " ".join(res.stdout.split()))
            self.assertIn("with no name list the packs", " ".join(run_cli(d, "pack", "add", "--help").stdout.split()))      # no "pack add" claim
            self.assertIn("usage: brevio-core add", run_cli(d, "add", "some-pack", "-h").stdout)
            self.assertNotIn("usage: brevio-core list", run_cli(d, "add", "-h").stdout)
            pack_help = run_cli(d, "pack", "--help").stdout
            self.assertIn("{list,add}", pack_help)
            self.assertIn("usage: brevio-core pack [-h]", pack_help)      # not "brevio-core [-h] {list,add}", which names no command
            self.assertEqual(run_cli(d, "add", "--json").returncode, 0)        # no name and no help flag still lists

    def test_an_unknown_pack_exits_non_zero_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "add", "no-such-pack")
            self.assertEqual(res.returncode, 1)
            self.assertIn("Unknown community guard pack", res.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, ".brevio")))


class TestTheToolsStayLocal(unittest.TestCase):
    def test_no_command_loads_a_network_module(self):
        with repo_with_a_fix() as d:
            for args in (("learn",), ("gain",), ("add", "supabase-rls"), ("hook", "install", "--all")):
                res = run_cli(d, *args, code=_MODULES_AFTER)
                self.assertEqual(res.returncode, 0, (args, res.stderr))
                self.assertEqual(res.stdout.strip().splitlines()[-1], "[]", args)

    def test_the_cloud_bridge_is_never_in_the_package_and_up_is_a_command_only_from_the_0_6_set(self):
        """Before the 0.6 set there is no ``up``; from it on, ``up`` is the preview of ``core_up`` (ROADMAP E37), which
        uploads nothing. The sending bridge stays in the harness in every set."""
        ships_up = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_up.py"))
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "up")
            self.assertEqual(res.returncode, 0 if ships_up else 2, res.stderr)
            self.assertEqual(os.listdir(d), [])
        self.assertFalse(os.path.exists(os.path.join(TREE, "brevio", "operator", "up_cmd.py")))
        self.assertFalse(os.path.exists(os.path.join(TREE, "brevio", "pipeline", "gates", "cloud_bridge.py")))

    def test_the_scan_command_still_works_beside_them(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "ok.ts", "export const a = 1;\n")
            self.assertEqual(run_cli(d, "scan", "ok.ts").returncode, 0)


if __name__ == "__main__":
    unittest.main()
