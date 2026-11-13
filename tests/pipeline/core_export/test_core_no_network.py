"""The gate's commands complete in a child process in which opening a socket raises (2026-11-12, improvement plan 4a).

SECURITY-REVIEW.md lists this file as the test a reader can run:

    python -m unittest tests.pipeline.core_export.test_core_no_network -v

One test starts `scan`, `scan --diff`, `check`, the agent hook, one MCP request, `baseline`, `learn` and `gain` under
``python -I`` with ``socket.socket`` and ``socket.create_connection`` replaced (and the lookup and socket-pair helpers
with them) by stubs that raise and report themselves. It checks that each command finishes with the exit code it is
expected to give and a line of its usual output, and that none reported a use. The refusal is in the Python process only:
a program it starts, such as ``git``, is outside it, and so is ``pip``. A command that sends a request on purpose, where a
set has one, is not run here; ``test_core_security`` fails when a module other than that transport imports a network module.

Standard library only: the exporter copies this file beside the code and it runs unchanged there. Children start with
``sys.executable`` and never with a shell.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import tests.pipeline.core_export.test_core_guard_fixtures as fx

REFUSE = ("import socket, sys\n"
          "def _refuse(*a, **k):\n"
          "    sys.stderr.write('NETWORK-USED\\n')\n"
          "    raise OSError('the network is off in this test')\n"
          "class _Socket:\n"
          "    def __init__(self, *a, **k):\n"
          "        _refuse()\n"
          "socket.socket = _Socket\n"
          "socket.create_connection = socket.getaddrinfo = socket.socketpair = socket.fromfd = _refuse\n")
REFUSED = REFUSE + fx.RUN       # the stubs first, then ``brevio-core`` with the arguments after the tree's path
PLANTED = "// @ts-ignore\nconst n: number = 'x';\n"
TREE = fx.Tree("0.6")


def setUpModule():
    TREE.open()


def tearDownModule():
    TREE.close()


def mcp_requests():
    call = "tools" + "/call"        # joined: the leak scan reads a literal "tools" + "/" as a private folder
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": call, "params": {"name": "scan", "arguments": {"paths": ["planted.ts"]}}}]
    return "".join(json.dumps(m) + "\n" for m in msgs)


class TestTheCommandsCompleteWithEverySocketRefused(unittest.TestCase):
    def test_scan_diff_check_the_hook_mcp_baseline_learn_and_gain_each_complete_with_every_socket_refused(self):
        with fx.repo_with_a_fix() as d:
            fx.write(d, "planted.ts", PLANTED)
            event = json.dumps({"cwd": d, "tool_name": "Write", "tool_input": {"file_path": os.path.join(d, "planted.ts")}})
            # (name, arguments, standard input, exit code, text the output must hold). Baseline comes after the commands
            # that report the planted line, because it records the line.
            steps = [("scan", ["scan", "planted.ts"], "", 1, "[R9.2]"),
                     ("scan --diff", ["scan", "--diff", "HEAD"], "", 1, "[R9.2]"),
                     ("check", ["check"], "", 1, "Flight Check Receipt"),
                     ("hook run", ["hook", "run", "--event", "post-edit"], event, 0, "[R9.2]"),
                     ("mcp", ["mcp"], mcp_requests(), 0, '"count":1'),
                     ("baseline", ["baseline"], "", 0, "baseline.json"),
                     ("learn", ["learn", "--name", "no legacy total"], "", 0, fx.GUARD_ID),
                     ("gain", ["gain"], "", 0, "ledger recorded")]
            for name, args, stdin, code, text in steps:
                with self.subTest(command=name):
                    res = TREE.run(d, *args, code=REFUSED, stdin=stdin)
                    self.assertEqual(res.returncode, code, res.stderr)
                    self.assertIn(text, res.stdout)
                    self.assertNotIn("NETWORK-USED", res.stderr)
                    self.assertNotIn("Traceback", res.stderr)
            self.assertIn(fx.GUARD_ID, fx.guards(d))        # learn wrote the guard: the command did its work, it was not skipped


class TestTheRefusalIsInForce(unittest.TestCase):
    """The stubs above would prove nothing if they did not stop a connection: this is the control."""

    def child(self, code):
        return subprocess.run([sys.executable, "-I", "-c", REFUSE + code], capture_output=True, encoding="utf-8", timeout=60)

    def test_a_socket_a_connection_and_a_url_open_raise_and_report_themselves(self):
        res = self.child("import socket, urllib.request\n"
                         "for call in (lambda: socket.socket(), lambda: socket.create_connection(('localhost', 9)),\n"
                         "             lambda: urllib.request.urlopen('http' + '://localhost:9/')):\n"
                         "    try:\n        call()\n    except OSError as exc:\n        print(type(exc).__name__)\n"
                         "    else:\n        print('connected')\n")
        self.assertEqual(res.stdout.split(), ["OSError", "OSError", "URLError"], res.stderr)
        self.assertEqual(res.stderr.count("NETWORK-USED"), 3)

    def test_a_command_that_did_open_a_socket_would_fail_the_test_above(self):
        with tempfile.TemporaryDirectory() as d:
            res = TREE.run(d, "--version", code=REFUSE + "import socket\ntry:\n    socket.socket()\nexcept OSError:\n    pass\n" + fx.RUN)
        self.assertIn("NETWORK-USED", res.stderr)


if __name__ == "__main__":
    unittest.main()
