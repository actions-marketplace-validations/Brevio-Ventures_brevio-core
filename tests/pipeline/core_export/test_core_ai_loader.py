"""The gate never loads ``brevio-core ai``: what the loader of ``core_cli`` does at run time (the claims check, 2026-11-12).

``ai`` is registered by name, so ``--help`` can list it, and its module is imported only when the command being run is
``ai``. This file runs each other command in a fresh interpreter, on a small repository, and asks which modules that process
holds when it ends: no module of the ``ai`` group and no network module. The import-graph and name checks of the security
test cover the same claim from the source; this is the run-time half.

Standard library only: the exporter copies this file beside the code (the 0.7 set) and it runs unchanged there. Nothing here
reaches another machine.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

import tests.pipeline.core_export.test_core_portable as portable
from brevio.pipeline import core_cli

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
HAS_AI = os.path.isfile(os.path.join(ROOT, "brevio", "pipeline", "core_ai.py"))
# A trimmed release leaves some commands out with their modules; a command that the build lacks is not run (2026-11-12).
MODULE_OF = {"heal": "core_heal", "map": "core_map", "roast": "core_roast", "shield": "core_shield"}


def has_command(argv):
    module = MODULE_OF.get(argv[0])
    return module is None or os.path.isfile(os.path.join(ROOT, "brevio", "pipeline", f"{module}.py"))

WATCH = ("urllib.request", "http.client", "ssl")        # what the group's transport would bring in
SCRIPT = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from brevio.pipeline import core_cli\n"
    "code = 0\n"
    "try:\n"
    "    code = core_cli.main(sys.argv[2:])\n"
    "except SystemExit as stop:\n"
    "    code = stop.code\n"
    f"held = sorted(m for m in sys.modules if m.startswith('brevio.pipeline.core_ai') or m in {WATCH!r})\n"
    "print('HELD:' + json.dumps(held), file=sys.stderr)\n"
    "raise SystemExit(code)\n")
EDIT = {"tool_name": "Edit", "tool_input": {"file_path": "a.ts"}, "cwd": "."}
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}
# Each command that is not ``ai``, as a person or an agent runs it: (label, argv, what goes on standard input).
COMMANDS = (
    ("scan", ["scan", "a.ts"], ""),
    ("check", ["check"], ""),
    ("baseline", ["baseline"], ""),
    ("init", ["init", "--yes"], ""),
    ("heal", ["heal"], ""),
    ("patch", ["patch"], ""),
    ("up", ["up"], ""),
    ("rules", ["rules"], ""),
    ("explain (the rule explainer)", ["explain", "R9.1"], ""),
    ("hook run", ["hook", "run", "--event", "post-edit"], json.dumps(EDIT)),
    ("mcp (started, asked once, stopped by the end of its input)", ["mcp"], json.dumps(INITIALIZE) + "\n"),
    ("learn", ["learn"], ""),
    ("the short help", ["--help"], ""),
    ("the help that lists every command", ["help", "--all"], ""),
)


def held_by(argv, repo, stdin=""):
    """``(exit code, the ai and network modules the process held at its end, stdout, stderr)`` of ``brevio-core argv``."""
    res = subprocess.run([sys.executable, "-I", "-c", SCRIPT, ROOT, *argv], cwd=repo, input=stdin, capture_output=True, encoding="utf-8",
                         timeout=120, env=portable.child_env(os.path.dirname(shutil.which("git") or "/usr/bin/git"), repo,
                                                         BREVIO_AI_CONFIG=os.path.join(repo, "ai-settings", "ai.toml")))
    marks = [ln for ln in res.stderr.splitlines() if ln.startswith("HELD:")]
    return res.returncode, (json.loads(marks[-1][5:]) if marks else None), res.stdout, res.stderr


@unittest.skipUnless(HAS_AI, "this set ships no ai")
class TestNoOtherCommandLoadsTheAiGroup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.realpath(self._tmp.name)
        with open(os.path.join(self.repo, "a.ts"), "w", encoding="utf-8") as fh:
            fh.write("const x = y as any;\n")
        for argv in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                                                     "commit", "-q", "-m", "first"]):
            subprocess.run(["git", "-C", self.repo, *argv], check=True, capture_output=True, timeout=30)

    def test_each_other_command_ends_holding_no_module_of_the_ai_group_and_no_network_module(self):
        for label, argv, stdin in COMMANDS:
            if not has_command(argv):
                continue
            _code, held, _out, err = held_by(argv, self.repo, stdin)
            self.assertEqual(held, [], f"{label}: {err[-300:]}")
            self.assertNotIn("invalid choice", err, label)        # the command ran: it was not an unknown word

    def test_the_help_lists_ai_with_its_one_line_description_without_loading_it(self):
        _code, held, out, _err = held_by(["help", "--all"], self.repo)    # ``--help`` is the short list since 2026-11-12
        self.assertEqual(held, [])
        flat = " ".join(out.split())
        self.assertIn("ai", re.search(r"\{([^}]*)\}", flat).group(1).replace(" ", "").split(","))
        self.assertIn(core_cli.AI_HELP, flat)

    def test_ai_itself_loads_its_modules_and_the_transport_so_the_other_runs_above_are_not_passing_by_accident(self):
        code, held, out, _err = held_by(["ai", "status"], self.repo)
        self.assertEqual(code, 0)
        self.assertIn("nothing; see `brevio-core ai connect --help`", out)
        for name in ("core_ai", "core_ai_net", "core_ai_state", "core_ai_verify"):
            self.assertIn(f"brevio.pipeline.{name}", held)
        self.assertIn("urllib.request", held)

    def test_no_command_of_the_gate_loads_the_command_runner_or_the_two_later_commands_and_ai_loads_them_only_when_they_run(self):
        """The command runner starts a program: it is the module no gate command may load, named here as the loader test's own
        watch (2026-11-12, improvement plan)."""
        later = ("core_ai_exec", "core_ai_summarize", "core_ai_learn", "core_ai_regex")
        for label, argv, stdin in COMMANDS:
            if has_command(argv):
                _code, held, _out, _err = held_by(argv, self.repo, stdin)
                self.assertEqual([m for m in held if m.rsplit(".", 1)[-1] in later], [], label)
        _code, held, _out, _err = held_by(["ai", "status"], self.repo)
        self.assertEqual([m for m in held if m.rsplit(".", 1)[-1] in later], [])

    def test_a_command_connector_loads_the_runner_and_not_the_transport_or_any_network_module(self):
        prog = os.path.abspath(sys.executable)
        code, held, _out, err = held_by(["ai", "connect", "--program", prog, "--arg=x"], self.repo)
        self.assertEqual(code, 0, err)
        self.assertIn("brevio.pipeline.core_ai_exec", held)
        self.assertEqual([m for m in held if m in WATCH or m.endswith("core_ai_net")], [])

    def test_building_the_command_table_registers_ai_by_name_and_a_build_without_the_module_has_no_ai(self):
        script = ("import json, sys\nsys.path.insert(0, sys.argv[1])\nfrom brevio.pipeline import core_cli\n"
                  "tools, helps = core_cli.line_commands()\n"
                  "print(json.dumps(['ai' in tools, helps.get('ai'), sorted(m for m in sys.modules if m.startswith('brevio.pipeline.core_ai'))]))\n")
        res = subprocess.run([sys.executable, "-I", "-c", script, ROOT], cwd=self.repo, capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(json.loads(res.stdout), [True, core_cli.AI_HELP, []], res.stderr)
        with unittest.mock.patch.object(core_cli.importlib.util, "find_spec", return_value=None):
            tools, helps = core_cli.line_commands()
        self.assertEqual(("ai" in tools, "ai" in helps), (False, False))

    def test_a_missing_module_is_not_reported_as_a_registered_command_that_then_fails(self):
        with unittest.mock.patch.object(core_cli.importlib.util, "find_spec", return_value=None):
            self.assertEqual(core_cli.ai_command(), ({}, {}))


if __name__ == "__main__":
    unittest.main()
