"""The command connector of ``brevio-core ai``: a program you name receives the request (the 0.7 set, improvement plan,
2026-11-12).

Standard library only: the exporter copies this file beside the code and it runs unchanged there. The "program" of each test
is ``sys.executable`` running a small script written into a temporary folder, so nothing here depends on a shell, a model or
a program that is not already running the test, and it runs on Windows as well. Nothing here reaches another machine.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock

from brevio.pipeline import core_ai_exec, core_ai_net
from brevio.pipeline import core_ai_state as st
from tests.pipeline.core_export import test_core_security as security
from tests.pipeline.core_export.test_core_ai import (
    FINDING,
    GOOD_REPLY,
    HTTPS,
    Answers,
    Project,
)

TELL = (
    "import json, os, sys\n"
    "sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8')\n"
    "data = sys.stdin.read()\n"
    "print(json.dumps({'cwd': os.getcwd(), 'listing': os.listdir('.'), 'env': sorted(os.environ), 'argv': sys.argv[1:], 'stdin': data}))\n")
REPLY = (        # writes what it read on standard input to the file named second, then the contents of the file named first
    "import sys\n"
    "sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8')\n"
    "data = sys.stdin.read()\n"
    "open(sys.argv[2], 'w', encoding='utf-8', newline='').write(data)\n"
    "sys.stdout.write(open(sys.argv[1], encoding='utf-8').read())\n")
SLEEP = "import time\ntime.sleep(30)\n"
LOUD = "import sys\nsys.stdout.write('x' * 5000)\n"
FAIL = "import sys\nsys.stderr.write('it broke here')\nsys.exit(3)\n"
SILENT = "import sys\nsys.stdin.read()\n"
DEAF = "import sys\nsys.stdout.write('ok')\n"        # never reads its input
START_NAMES = ("TEMP", "TMP", "LC_CTYPE", "PYTHONUTF8", "PYTHONCOERCECLOCALE", "__PYVENV_LAUNCHER__")   # a Python start-up adds some


class Scripts(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = os.path.realpath(self._dir.name)

    def script(self, text, name="prog.py"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path

    def connector(self, text, *args, pass_env=(), name="default"):
        return st.Connector(name, "command", argv=(os.path.abspath(sys.executable), self.script(text), *args), env_pass=tuple(pass_env))

    def env(self, **more):
        """What a person's shell holds, as far as the runner reads it: the names it passes on, and whatever a test adds."""
        base = {n: os.environ[n] for n in core_ai_exec.platform_names() if n in os.environ}
        return {**base, **more}


class TestTheRunner(Scripts):
    def tell(self, *args, env=None, pass_env=()):
        text, usage = core_ai_exec.complete(self.connector(TELL, *args, pass_env=pass_env), "the request text", env=env or self.env())
        self.assertIsNone(usage)
        return json.loads(text)

    def test_the_request_goes_on_standard_input_and_not_into_the_arguments(self):
        told = self.tell("first", "second")
        self.assertEqual(told["stdin"], core_ai_exec.request_text("the request text"))
        self.assertEqual(told["argv"], ["first", "second"])
        self.assertNotIn("the request text", " ".join(told["argv"]))

    def test_the_system_line_is_the_one_of_the_endpoint_transport_so_the_two_connectors_ask_the_same(self):
        self.assertEqual(core_ai_exec.SYSTEM, core_ai_net.SYSTEM)

    def test_an_argument_with_shell_syntax_arrives_as_text_because_no_shell_reads_it(self):
        marker = os.path.join(self.dir, "made-by-a-shell")
        hostile = f"$(echo x > {marker}); echo y > {marker} && `echo z`"
        self.assertEqual(self.tell(hostile)["argv"], [hostile])
        self.assertFalse(os.path.exists(marker))

    def test_the_program_starts_in_a_new_empty_folder_that_is_gone_afterwards(self):
        told = self.tell()
        self.assertEqual(told["listing"], [])
        self.assertNotEqual(os.path.realpath(told["cwd"]), os.path.realpath(os.getcwd()))
        self.assertFalse(os.path.exists(told["cwd"]))

    def test_the_environment_holds_the_platform_names_and_the_listed_ones_and_no_other_variable_of_the_caller(self):
        env = self.env(HELD_BACK="a-value", LISTED="the-listed-value")
        told = self.tell(env=env, pass_env=("LISTED", "NOT_SET_AT_ALL"))
        self.assertIn("LISTED", told["env"])
        self.assertNotIn("HELD_BACK", told["env"])
        self.assertNotIn("NOT_SET_AT_ALL", told["env"])
        allowed = {*core_ai_exec.platform_names(), "LISTED", *START_NAMES}
        self.assertEqual([n for n in told["env"] if n.upper() not in {a.upper() for a in allowed}], [])

    def test_the_environment_function_gives_exactly_the_names_asked_for_where_they_are_set(self):
        c = self.connector(TELL, pass_env=("A_NAME", "B_NAME"))
        env = {"A_NAME": "1", "OTHER": "2", "PATH": "/x"}
        got = core_ai_exec.child_env(c, env)
        self.assertEqual(got, {"A_NAME": "1", "PATH": "/x"} if "PATH" in core_ai_exec.platform_names() else {"A_NAME": "1"})

    def test_a_program_that_does_not_finish_is_stopped_at_the_timeout(self):
        began = time.monotonic()
        with self.assertRaises(core_ai_exec.ExecError) as cm:
            core_ai_exec.complete(self.connector(SLEEP), "x", env=self.env(), timeout=1)
        self.assertIn("did not finish within 1 seconds and was stopped", str(cm.exception))
        self.assertLess(time.monotonic() - began, 15)

    def test_a_program_that_writes_more_than_the_size_cap_is_stopped_and_gives_no_reply(self):
        with unittest.mock.patch.object(core_ai_exec, "MAX_REPLY_BYTES", 1000):
            with self.assertRaises(core_ai_exec.ExecError) as cm:
                core_ai_exec.complete(self.connector(LOUD), "x", env=self.env())
        self.assertIn("size cap", str(cm.exception))

    def test_a_program_that_fails_gives_its_exit_code_and_the_start_of_its_error_output(self):
        with self.assertRaises(core_ai_exec.ExecError) as cm:
            core_ai_exec.complete(self.connector(FAIL), "x", env=self.env())
        self.assertIn("exited with code 3", str(cm.exception))
        self.assertIn("it broke here", str(cm.exception))

    def test_a_program_that_writes_nothing_is_an_error_and_is_not_started_again(self):
        with self.assertRaises(core_ai_exec.ExecError) as cm:
            core_ai_exec.complete(self.connector(SILENT), "x", env=self.env())
        self.assertIn("wrote nothing", str(cm.exception))

    def test_a_program_that_never_reads_its_input_does_not_hang_the_call(self):
        began = time.monotonic()
        text, _ = core_ai_exec.complete(self.connector(DEAF), "y" * 2_000_000, env=self.env(), timeout=20)
        self.assertEqual(text, "ok")
        self.assertLess(time.monotonic() - began, 20)

    def test_a_program_that_cannot_be_started_is_an_error_that_names_the_reason(self):
        c = st.Connector("default", "command", argv=(self.script("not a program"), "x"))
        with self.assertRaises(core_ai_exec.ExecError):
            core_ai_exec.complete(c, "x", env=self.env())


class TestStoppingAndPlatforms(Scripts):
    @unittest.skipUnless(hasattr(os, "killpg"), "process groups are a POSIX notion; on Windows only the program itself is stopped")
    def test_at_the_timeout_what_the_program_started_is_stopped_with_it_where_the_system_has_process_groups(self):
        pid_file = os.path.join(self.dir, "child.pid")
        code = ("import subprocess, sys, time\n"
                f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({pid_file!r}, 'w').write(str(child.pid))\n"
                "time.sleep(60)\n")
        with self.assertRaises(core_ai_exec.ExecError):
            core_ai_exec.complete(self.connector(code), "x", env=self.env(), timeout=2)
        with open(pid_file, encoding="utf-8") as fh:
            child = int(fh.read())
        for _ in range(100):
            try:
                os.kill(child, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            self.fail("the program's own child was left running")

    def test_a_process_the_program_leaves_running_does_not_hold_the_call_open_after_the_program_ended(self):
        # review 2026-11-12: the child keeps the output pipe open, so the call waited for it (it hung past the timeout)
        pid_file = os.path.join(self.dir, "child.pid")
        code = ("import subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])\n"
                f"open({pid_file!r}, 'w').write(str(child.pid))\n"
                "print('the reply')\n")
        began = time.monotonic()
        try:
            text, _ = core_ai_exec.complete(self.connector(code), "x", env=self.env(), timeout=30)
        finally:
            with open(pid_file, encoding="utf-8") as fh:
                left = int(fh.read())
        waited = time.monotonic() - began
        if not hasattr(os, "killpg"):       # Windows: nothing stops it, so the test does
            os.kill(left, 15)
        self.assertEqual(text, "the reply")
        self.assertLess(waited, 20)
        if hasattr(os, "killpg"):
            for _ in range(100):
                try:
                    os.kill(left, 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:
                self.fail("what the program left running was not stopped")

    def test_a_reply_written_with_windows_line_ends_is_read_as_lines_ending_in_a_line_feed(self):
        # 2026-11-13, Windows CI: a program's text-mode output ends its lines with CRLF, which broke the fence of a fix
        code = "import sys\nsys.stdout.buffer.write(b'```ts\\r\\nx\\r\\n```\\r\\n')\n"
        text, _ = core_ai_exec.complete(self.connector(code), "x", env=self.env())
        self.assertEqual(text, "```ts\nx\n```")

    def test_a_folder_the_system_will_not_remove_does_not_fail_a_call_that_has_its_reply(self):
        # 2026-11-13, Windows CI: a process left in the folder holds it; on Python 3.10 the cleanup of a temporary folder
        # then recursed until RecursionError (simulated here: its cleanup raises that, and the folder cannot be removed)
        refuse = PermissionError(13, "The process cannot access the file because it is being used by another process")
        real = shutil.rmtree

        def stuck(path, ignore_errors=False, **kw):
            real(path, ignore_errors=True)      # the test's own tidying: the system refuses the runner, not the test
            if not ignore_errors:
                raise refuse

        with unittest.mock.patch.object(tempfile.TemporaryDirectory, "cleanup", side_effect=RecursionError), \
                unittest.mock.patch.object(core_ai_exec.shutil, "rmtree", side_effect=stuck) as rmtree:
            try:
                text, _ = core_ai_exec.complete(self.connector("print('the reply')\n"), "x", env=self.env())
            except PermissionError:
                self.fail("a folder that could not be removed failed the call")
        self.assertEqual(text, "the reply")
        self.assertEqual(rmtree.call_count, 1)

    def test_the_names_a_program_starts_with_are_the_platforms_and_a_batch_file_is_refused_on_windows(self):
        with unittest.mock.patch.object(core_ai_exec.sys, "platform", "win32"):
            self.assertEqual(core_ai_exec.platform_names(), core_ai_exec.BASE_ENV["windows"])
            batch = self.script("@echo off\n", "run.bat")
            os.chmod(batch, 0o700)
            found = core_ai_exec.problems(st.Connector("default", "command", argv=(batch,)), {})
            self.assertTrue(any("batch file" in p for p in found), found)
            self.assertEqual(core_ai_exec.problems(self.connector(TELL), {}), [])
        with unittest.mock.patch.object(core_ai_exec.sys, "platform", "linux"):
            self.assertEqual(core_ai_exec.platform_names(), core_ai_exec.BASE_ENV["posix"])

    def test_a_program_is_started_once_and_a_failure_is_not_retried(self):
        counter = os.path.join(self.dir, "starts")
        code = f"open({counter!r}, 'a').write('x')\nimport sys\nsys.exit(1)\n"
        with self.assertRaises(core_ai_exec.ExecError):
            core_ai_exec.complete(self.connector(code), "x", env=self.env())
        with open(counter, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "x")


class TestWhatIsRefusedBeforeAnythingStarts(Scripts):
    def test_a_ci_variable_stops_it_before_the_program_is_started(self):
        marker = os.path.join(self.dir, "started")
        c = self.connector(f"open({marker!r}, 'w').close()\nprint('x')\n")
        for name in core_ai_exec.CI_VARIABLES:
            with self.assertRaises(core_ai_exec.ExecError) as cm:
                core_ai_exec.complete(c, "x", env=self.env(**{name: "true"}))
            self.assertIn(name, str(cm.exception))
        self.assertFalse(os.path.exists(marker))

    def test_a_ci_variable_with_any_value_stops_it_and_one_set_to_nothing_does_not(self):
        # review 2026-11-12: the old test let "0" and "false" through, while every text says a variable "is set"
        self.assertEqual(core_ai_exec.ci_reason({"CI": ""}), "")
        self.assertEqual(core_ai_exec.ci_reason({"CI": "  "}), "")
        for value in ("1", "true", "0", "false", "False", "no"):
            self.assertIn("CI", core_ai_exec.ci_reason({"CI": value}), value)

    def test_a_program_that_is_gone_or_not_executable_or_not_absolute_is_a_problem_with_a_plain_reason(self):
        gone = st.Connector("default", "command", argv=(os.path.join(self.dir, "gone"), "x"))
        self.assertTrue(any("no longer an executable" in p for p in core_ai_exec.problems(gone, self.env())))
        self.assertTrue(core_ai_exec.problems(st.Connector("default", "command", argv=("prog",)), self.env()))
        self.assertTrue(core_ai_exec.problems(st.Connector("default", "command"), self.env()))

    @unittest.skipIf(os.name == "nt", "the executable bit is a POSIX notion")
    def test_a_file_that_is_not_executable_is_refused_by_resolve(self):
        path = self.script("x")
        os.chmod(path, 0o600)
        with self.assertRaises(core_ai_exec.ExecError):
            core_ai_exec.resolve(path)

    def test_resolve_gives_an_absolute_path_for_a_path_and_for_a_name_on_path_and_refuses_a_name_that_is_not(self):
        self.assertEqual(core_ai_exec.resolve(sys.executable), os.path.abspath(sys.executable))
        folder, name = os.path.split(os.path.abspath(sys.executable))
        found = core_ai_exec.resolve(name, {"PATH": folder})
        self.assertTrue(os.path.isabs(found))
        self.assertEqual(os.path.normcase(os.path.dirname(found)), os.path.normcase(folder))
        with self.assertRaises(core_ai_exec.ExecError):
            core_ai_exec.resolve("no-such-program-on-this-machine", {"PATH": folder})

    def test_a_name_found_only_in_the_current_folder_is_refused_because_a_repository_can_put_a_program_there(self):
        # review 2026-11-12: on Windows a lookup by name looks in the current folder first, so connect, run inside a repository,
        # would have stored the repository's program under the name of the one meant. The lookup is stood in for here.
        folder, name = os.path.split(os.path.abspath(sys.executable))
        here = os.path.realpath(folder)
        found_here = os.path.join(here, name)
        with unittest.mock.patch.object(core_ai_exec.os, "getcwd", return_value=here), \
                unittest.mock.patch.object(core_ai_exec.shutil, "which", return_value=found_here):
            with self.assertRaises(core_ai_exec.ExecError) as cm:
                core_ai_exec.resolve(name, {"PATH": os.path.join(self.dir, "elsewhere")})
            self.assertIn("full path", str(cm.exception))
            self.assertTrue(core_ai_exec.resolve(name, {"PATH": folder}))                    # the folder is on PATH: the user chose it
            self.assertTrue(core_ai_exec.resolve(name, {"PATH": os.pathsep.join(["", folder])}))
            self.assertTrue(core_ai_exec.resolve(os.path.join(folder, name), {"PATH": ""}))  # a path was given: nothing is looked up


def shell_problems(tree):
    """Calls of ``subprocess`` in ``tree`` that could start a shell: a command given as one string, ``shell`` other than a
    constant ``False``, or ``**`` keywords that could carry one."""
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"):
            continue
        first = node.args[0] if node.args else None
        out += ["a command that is one string"] if isinstance(first, ast.Constant) and isinstance(first.value, str) else []
        out += ["keywords from ** may hold shell=True"] if any(k.arg is None for k in node.keywords) else []
        out += ["shell is not False" for k in node.keywords
                if k.arg == "shell" and not (isinstance(k.value, ast.Constant) and k.value.value is False)]
    return out


class TestTheExceptionInTheSecurityTest(unittest.TestCase):
    """The command runner is the one module allowed to start a program other than git, named in the security test."""

    def found(self, rel, src):
        with unittest.mock.patch(f"{security.__name__}._modules", return_value=[security._Module(rel, ast.parse(src), {})]):
            return security.problems()

    def test_the_runner_may_start_a_program_and_no_other_module_may(self):
        rel, call = security._rel("pipeline", "core_ai_exec"), "import subprocess\nsubprocess.Popen(['prog', 'x'])\n"
        self.assertEqual(self.found(rel, call), [])
        for other in ("core_ai", "core_ai_net", "core_cli", "core_ai_exec_copy"):
            self.assertTrue(self.found(security._rel("pipeline", other), call), other)

    def test_the_runner_is_held_to_the_other_rules_no_os_system_no_network_no_dynamic_code(self):
        rel = security._rel("pipeline", "core_ai_exec")
        for bad in ("import os\nos.system('ls')\n", "import socket\n", "import urllib.request\n", "eval('1')\n",
                    "import subprocess as sp\nrun = sp.run\n"):
            self.assertTrue(self.found(rel, bad), bad)

    def test_the_runner_starts_no_shell_whatever_its_calls_look_like(self):
        # review 2026-11-12: the exception skips the check of the call, so nothing stopped ``shell=True`` in this module
        with open(core_ai_exec.__file__, encoding="utf-8") as fh:
            self.assertEqual(shell_problems(ast.parse(fh.read())), [])
        for bad in ("subprocess.Popen('ls | wc', stdin=None)", "subprocess.Popen(['ls'], shell=True)", "subprocess.run(argv, shell=flag)",
                    "subprocess.Popen(argv, **options)", "subprocess.check_output('ls')"):
            self.assertTrue(shell_problems(ast.parse("import subprocess\n" + bad + "\n")), bad)
        self.assertEqual(shell_problems(ast.parse("import subprocess\nsubprocess.Popen(argv, shell=False)\n")), [])

    def test_the_exception_names_the_runner_only(self):
        self.assertEqual(security.PROCESS_EXCEPTIONS, frozenset({security._rel("pipeline", "core_ai_exec")}))

    def test_the_runner_does_not_import_the_transport_or_the_network_so_loading_it_opens_no_socket(self):
        with open(core_ai_exec.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual({m for m in imported if m.startswith(("urllib", "http", "socket", "ssl")) or m.endswith("core_ai_net")}, set())


class TestTheCommandWithAProgram(Project):
    def setUp(self):
        super().setUp()
        self.env = {**self.env, **{n: os.environ[n] for n in core_ai_exec.platform_names() if n in os.environ}}
        self.reply_file = os.path.join(self.dir, "reply.txt")
        self.seen_file = os.path.join(self.dir, "seen.txt")
        self.program = os.path.join(self.dir, "reply.py")
        with open(self.program, "w", encoding="utf-8") as fh:
            fh.write(REPLY)
        self.say("It uses a cast that turns the type checker off.")

    def say(self, text):
        with open(self.reply_file, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def seen(self):
        try:
            with open(self.seen_file, encoding="utf-8", newline="") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def connect_program(self, *extra):
        code, out, err = self.run_ai("connect", "--program", sys.executable, f"--arg={self.program}", f"--arg={self.reply_file}",
                                     f"--arg={self.seen_file}", *extra)
        self.assertEqual(code, 0, err)
        return out

    def test_connect_stores_the_absolute_path_the_arguments_and_the_variable_names_and_no_model(self):
        out = self.connect_program("--pass-env", "MY_OWN_VARIABLE")
        c = st.load(self.cfg_path).connectors["default"]
        self.assertEqual((c.kind, c.model, c.url, c.key_env), ("command", "", "", ""))
        self.assertEqual(c.argv[0], os.path.abspath(sys.executable))
        self.assertEqual(c.argv[1:], (self.program, self.reply_file, self.seen_file))
        self.assertEqual(c.env_pass, ("MY_OWN_VARIABLE",))
        self.assertIn("may itself send it elsewhere: brevio-core cannot see what it does", out)
        with open(self.cfg_path, encoding="utf-8") as fh:
            self.assertIn('kind = "command"', fh.read())
        self.assertIsNone(self.seen())          # connecting starts nothing

    def test_a_bare_name_is_found_on_path_when_you_connect_and_the_absolute_path_is_what_is_stored(self):
        folder, name = os.path.split(os.path.abspath(sys.executable))
        code, _out, err = self.run_ai("connect", "--program", name, env={**self.env, "PATH": folder})
        self.assertEqual(code, 0, err)
        stored = st.load(self.cfg_path).connectors["default"].argv[0]
        self.assertTrue(os.path.isabs(stored))
        self.assertEqual(os.path.normcase(os.path.dirname(stored)), os.path.normcase(folder))

    def test_a_program_that_is_not_there_an_address_beside_it_and_a_key_in_an_argument_are_refused(self):
        code, _out, err = self.run_ai("connect", "--program", os.path.join(self.dir, "nothing-here"))
        self.assertEqual(code, 2)
        self.assertIn("not an executable file", err)
        code, _out, err = self.run_ai("connect", "--program", sys.executable, "--url", HTTPS + "x.example.test/v1")
        self.assertEqual((code, "takes no --url" in err), (2, True))
        code, _out, err = self.run_ai("connect", "--program", sys.executable, "--arg=sk_" + "a1b2c3d4e5f6a7b8c9d0")
        self.assertEqual((code, "starts like a key" in err), (2, True))
        code, _out, err = self.run_ai("connect", "--arg=x")
        self.assertEqual((code, "go with --program" in err), (2, True))
        self.assertFalse(os.path.exists(self.cfg_path))

    def test_status_names_the_program_and_says_the_token_limit_is_an_endpoint_setting(self):
        self.connect_program()
        code, out, _err = self.run_ai("status")
        self.assertEqual(code, 0)
        self.assertIn(f"runs {os.path.abspath(sys.executable)} with 3 argument(s) (a program you named", out)
        self.assertIn("a program is not told it", out)

    def test_status_of_an_endpoint_alone_does_not_mention_programs_in_the_limits_line(self):
        # review 2026-11-12: the limits line of a user who has no program changed for them, with a sentence that is not theirs
        code, _out, err = self.run_ai("connect", "--local", "9", "--model", "m")
        self.assertEqual(code, 0, err)
        _code, out, _err = self.run_ai("status")
        limits = next(line for line in out.splitlines() if line.startswith("limits:"))
        self.assertNotIn("program", limits)
        self.assertIn("tokens out,", limits)

    def test_a_program_is_started_only_for_a_person_at_a_terminal_and_yes_does_not_stand_in_for_one(self):
        self.connect_program()
        self.agree()
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("only for a person at a terminal", err)
        self.assertIsNone(self.seen())

    def test_it_prints_the_request_and_the_program_first_and_the_program_reads_exactly_that_text(self):
        self.connect_program()
        self.agree()
        code, out, err = self.run_ai("explain", FINDING, "--context", "1", ask=Answers("y"))
        self.assertEqual(code, 0, err)
        seen = self.seen()
        self.assertIn(f"RUN the program {os.path.abspath(sys.executable)}", out)
        self.assertIn("no shell, started once", out)
        self.assertIn(seen.rstrip("\n"), out)
        self.assertTrue(seen.startswith(core_ai_exec.SYSTEM + "\n\n"))
        self.assertIn("as any", seen)

    def test_the_reply_is_printed_inside_the_closed_block_with_every_line_behind_the_gutter(self):
        self.connect_program()
        self.agree()
        self.say("first line\n\x1b[2Jsecond line\nend of the answer: whatever follows is brevio-core's own")
        code, out, err = self.run_ai("explain", FINDING, ask=Answers("y"))
        self.assertEqual(code, 0, err)
        self.assertIn("(written by the program of default; brevio-core did not write it):", out)
        self.assertIn("| first line", out)
        self.assertIn("| \\x1b[2Jsecond line", out)
        self.assertNotIn("\x1b", out)
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("end of the answer: every line above starts with"))
        self.assertIn("and is the program's;", out.rstrip().splitlines()[-1])     # review 2026-11-12: it said "the model's"

    def test_the_first_use_text_says_a_program_receives_the_request_and_brevio_core_cannot_see_what_it_does(self):
        self.connect_program()
        ask = Answers("y", "y")
        code, out, err = self.run_ai("explain", FINDING, ask=ask)
        self.assertEqual(code, 0, err)
        self.assertIn("standard input of the program you named", out)
        self.assertIn("may itself send it elsewhere: brevio-core cannot see what the program does with it", out)
        self.assertIn("not when a CI variable is set", out)
        self.assertEqual(ask.asked[0], "Agree to ai explain in this repository? [y/N] ")

    def test_a_ci_variable_stops_the_call_and_nothing_is_started(self):
        self.connect_program()
        self.agree()
        code, _out, err = self.run_ai("explain", FINDING, env={**self.env, "CI": "true"}, ask=Answers("y"))
        self.assertEqual(code, 3)
        self.assertIn("the variable CI is set", err)
        self.assertIsNone(self.seen())

    def test_a_dry_run_shows_the_request_and_starts_nothing(self):
        self.connect_program()
        code, out, _err = self.run_ai("explain", FINDING, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("RUN the program", out)
        self.assertIn("--dry-run: nothing was sent.", out)
        self.assertIsNone(self.seen())

    def test_a_line_that_looks_like_a_secret_stops_the_call_before_the_program_is_started(self):
        self.connect_program()
        self.agree()
        self.put("s.ts", ["const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';", "const v = x as any;"])
        code, _out, err = self.run_ai("explain", "s.ts:2: [R9.1] x", "--context", "1", ask=Answers("y"))
        self.assertEqual(code, 3)
        self.assertIn("looks like a secret", err)
        self.assertIsNone(self.seen())

    def test_a_fix_from_a_program_is_checked_by_the_rules_like_one_from_an_endpoint(self):
        self.connect_program()
        self.agree()
        self.say(GOOD_REPLY)
        code, out, err = self.run_ai("fix", FINDING, ask=Answers("y"))
        self.assertEqual(code, 0, err)
        self.assertIn("verified:", out)
        self.assertIn("(written by the program of default; brevio-core did not write it):", out)
        self.say("```ts\n  const data = JSON.parse(raw) as any; // @ts-ignore\n```")
        code, out, _err = self.run_ai("fix", FINDING, ask=Answers("y"))
        self.assertEqual(code, 1)
        self.assertIn("refused by the rules", out)

    def test_a_fix_from_a_program_that_ends_its_lines_with_windows_line_ends_is_still_verified(self):
        self.connect_program()
        self.agree()
        with open(self.program, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.stdin.read()\n"
                     f"sys.stdout.buffer.write(open({self.reply_file!r}, 'rb').read().replace(b'\\n', b'\\r\\n'))\n")
        self.say(GOOD_REPLY)
        code, out, err = self.run_ai("fix", FINDING, ask=Answers("y"))
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("verified:", out)

    def test_a_program_that_fails_is_reported_and_counted_and_not_started_again(self):
        self.connect_program()
        self.agree()
        with open(self.program, "w", encoding="utf-8") as fh:
            fh.write(FAIL)
        code, _out, err = self.run_ai("explain", FINDING, ask=Answers("y"))
        self.assertEqual(code, 1)
        self.assertIn("exited with code 3", err)
        self.assertEqual(self.ledger().calls, 1)

    def test_a_repository_cannot_name_a_program_for_ai_and_the_error_says_routing_is_user_level_only(self):
        self.connect_program()
        self.put("brevio-core.toml", ["[ai]", 'argv = ["/bin/sh", "-c", "x"]'])
        code, _out, err = self.run_ai("explain", FINDING, "--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("routing is user-level only", err)
        self.assertIsNone(self.seen())

    def test_the_settings_file_with_a_command_connector_is_read_back_and_a_bad_one_is_refused(self):
        self.connect_program("--pass-env", "A_NAME")
        again = st.parse_config(st.render(st.load(self.cfg_path)), self.cfg_path)
        self.assertEqual(again.connectors["default"].argv, st.load(self.cfg_path).connectors["default"].argv)
        for body, why in (('kind = "command"\nargv = ["relative"]', "absolute path"), ('kind = "command"\nargv = []', "argv is"),
                          (f'kind = "command"\nargv = ["/x"]\nurl = "{HTTPS}a.example.test/"', "not an address"),
                          ('kind = "command"\nargv = ["/x"]\nenv_pass = ["A", "A"]', "different variable names"),
                          ('kind = "command"\nargv = "/x"', "list of strings"),
                          (f'kind = "endpoint"\nurl = "{HTTPS}a.example.test/x"\nmodel = "m"\nargv = ["/x"]', "belong to a command")):
            with self.assertRaises(st.AiConfigError, msg=body) as cm:
                st.parse_config(f"[connector.w]\n{body}\n")
            self.assertIn(why, str(cm.exception), body)


if __name__ == "__main__":
    unittest.main()
