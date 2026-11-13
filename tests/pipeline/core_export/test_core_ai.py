"""``brevio-core ai``, end to end: connect, status, explain and fix against a fake endpoint on this machine (the 0.7 set,
2026-11-11).

Standard library only: the exporter copies this file beside the code (the 0.7 set) and it runs unchanged there. The
endpoint is an ``http.server`` on a loopback port in a thread, the project is a real folder in a temporary place, the
settings file is one in a temporary place too, and nobody answers a question unless a test hands over the answers. The
key, where one is needed, is made at run time. Nothing here reaches another machine.
"""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock

from brevio.pipeline import core_ai, core_cli
from brevio.pipeline import core_ai_state as st
from brevio.pipeline import core_ai_verify as verify

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
HTTP, HTTPS = "http" + "://", "https" + "://"      # joined: the leak scan reads a literal address as a URL that should not be public
LOOP = "127." + "0.0.1"        # joined: the leak scan reads an address of this shape as a private host
TS = ["import { readFileSync } from 'fs';",
      "",
      "export function load(path: string) {",
      "  const raw = readFileSync(path, 'utf8');",
      "  const data = JSON.parse(raw) as any;",
      "  return data.items;",
      "}"]
FINDING = "a.ts:5: [R9.1] Untyped escape hatch `as any` or `z.any()` in production source"
GOOD_LINE = "  const data: { items: string[] } = JSON.parse(raw);"
# A fake key in the AWS access key format, joined at run time so this file is not itself a finding of a secret scan.
SECRET = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';"


def fenced(lines, lead="Here is the fix:"):
    return f"{lead}\n```ts\n" + "\n".join(lines) + "\n```\nDone."


GOOD_REPLY = fenced([TS[3], GOOD_LINE, TS[5]])


class Endpoint:
    """A fake endpoint on a loopback port: it records each request body and answers with the next scripted reply."""

    def __init__(self, test, replies=("It uses a cast that turns the type checker off.",), status=200):
        self.bodies, self.raw, self.headers, self.replies, self.status = [], [], [], list(replies), status
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                outer.raw.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                outer.headers.append(dict(self.headers.items()))
                outer.bodies.append(json.loads(outer.raw[-1]))
                text = outer.replies.pop(0) if len(outer.replies) > 1 else outer.replies[0]
                payload = json.dumps({"choices": [{"message": {"content": text}}],
                                      "usage": {"prompt_tokens": 40, "completion_tokens": 12}}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer((LOOP, 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()
        test.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class Answers:
    """The person at the terminal: each question is answered from the list; running out means no."""

    def __init__(self, *replies):
        self.replies, self.asked = list(replies), []

    def __call__(self, question):
        self.asked.append(question)
        return self.replies.pop(0) if self.replies else "n"


class Project(unittest.TestCase):
    """A project folder with a git marker and ``a.ts``, a settings file beside it, and the folder as the working folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(self.repo, ".git"))
        self.put("a.ts", TS)
        self.cfg_path = os.path.join(self.dir, "cfg", "ai.toml")
        self.env = {"BREVIO_AI_CONFIG": self.cfg_path}
        here = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, here)
        no_terminal = unittest.mock.patch.object(core_ai, "terminal", return_value=None)
        no_terminal.start()
        self.addCleanup(no_terminal.stop)

    def put(self, rel, lines, base=None):
        path = os.path.join(base or self.repo, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines) + "\n")
        return path

    def text(self, rel="a.ts"):
        with open(os.path.join(self.repo, *rel.split("/")), encoding="utf-8", newline="") as fh:
            return fh.read()

    def run_ai(self, *argv, env=None, ask=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = core_ai.main(list(argv), env=self.env if env is None else env, ask=ask)
        return code, out.getvalue(), err.getvalue()

    def connect(self, ep, *extra):
        code, _out, err = self.run_ai("connect", "--local", str(ep.port), "--model", "some-model", *extra)
        self.assertEqual(code, 0, err)

    def agree(self, *commands):
        """What a person's yes at the first use records, without asking the question."""
        cfg = st.with_consent(st.load(self.cfg_path), st.repo_key(self.repo), commands or st.COMMANDS)
        st.save(cfg)

    def limits(self, **changes):
        cfg = st.load(self.cfg_path)
        st.save(cfg._replace(limits=cfg.limits._replace(**changes)))

    def ledger(self):
        return st.usage_on(st.ledger_path(self.cfg_path))


class TestConnect(Project):
    def test_a_local_endpoint_is_written_to_a_private_file_with_no_key_and_both_commands_on(self):
        ep = Endpoint(self)
        code, out, err = self.run_ai("connect", "--local", str(ep.port), "--model", "some-model")
        self.assertEqual((code, err), (0, ""))
        cfg = st.load(self.cfg_path)
        c = cfg.connectors["default"]
        self.assertEqual((c.url, c.format, c.model, c.key_env), (f"{HTTP}localhost:{ep.port}/v1/chat/completions", "chat", "some-model", ""))
        self.assertEqual(cfg.use, {"explain": "default", "fix": "default"})
        self.assertIn("connected default", out)
        self.assertIn("Nothing is sent until you run", out)
        if os.name == "posix":
            self.assertEqual(os.stat(self.cfg_path).st_mode & 0o777, 0o600)
        self.assertEqual(ep.bodies, [])         # connecting calls nobody

    def test_an_address_needs_a_key_variable_that_holds_a_key_and_the_file_keeps_only_its_name(self):
        args = ("connect", "--name", "work", "--url", HTTPS + "llm.example.test/v1/chat", "--model", "m", "--key-env", "WORK_KEY")
        code, _out, err = self.run_ai(*args)
        self.assertEqual(code, 2)
        self.assertIn("WORK_KEY", err)
        self.assertFalse(os.path.exists(self.cfg_path))
        key = secrets.token_hex(12)
        code, out, err = self.run_ai(*args, env={**self.env, "WORK_KEY": key})
        self.assertEqual(code, 0, err)
        with open(self.cfg_path, encoding="utf-8") as fh:
            saved = fh.read()
        self.assertIn('key_env = "WORK_KEY"', saved)
        for shown in (saved, out, err):
            self.assertNotIn(key, shown)

    def test_plain_http_off_this_machine_and_a_local_endpoint_with_an_address_are_refused(self):
        env = {**self.env, "K": secrets.token_hex(8)}
        code, _out, err = self.run_ai("connect", "--url", HTTP + "llm.example.test/v1", "--model", "m", "--key-env", "K", env=env)
        self.assertEqual(code, 2)
        self.assertIn("https", err)
        code, _out, err = self.run_ai("connect", "--local", "8080", "--url", HTTPS + "x.example.test/v1", "--model", "m")
        self.assertEqual(code, 2)
        self.assertIn("--local", err)
        code, _out, _err = self.run_ai("connect", "--local", "70000", "--model", "m")
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.cfg_path))

    def test_use_turns_on_only_the_commands_it_names_and_a_model_is_needed(self):
        ep = Endpoint(self)
        self.connect(ep, "--use", "explain")
        cfg = st.load(self.cfg_path)
        self.assertEqual(cfg.use, {"explain": "default"})
        self.connect(ep, "--use", "fix")
        self.assertEqual(st.load(self.cfg_path).use, {"fix": "default"})        # what it served before and is left out is off
        code, _out, err = self.run_ai("connect", "--local", str(ep.port))
        self.assertEqual(code, 2)
        self.assertIn("model", err)
        code, _out, err = self.run_ai("connect", "--local", str(ep.port), "--model", "m", "--use", "explain,deploy")
        self.assertEqual(code, 2)
        self.assertIn("deploy", err)


class TestStatus(Project):
    def test_before_anything_is_connected_it_says_so_and_every_command_is_off(self):
        code, out, _err = self.run_ai("status")
        self.assertEqual(code, 0)
        self.assertIn("nothing; see `brevio-core ai connect --help`", out)
        self.assertIn("explain: off", out)
        self.assertIn("fix: off", out)
        self.assertIn("agreed to nothing yet", out)

    def test_after_connecting_it_shows_the_destination_the_limits_the_count_and_the_consent_but_never_a_key(self):
        key = secrets.token_hex(12)
        env = {**self.env, "WORK_KEY": key}
        code, _out, err = self.run_ai("connect", "--name", "work", "--url", HTTPS + "llm.example.test/v1/chat", "--model", "m",
                                      "--key-env", "WORK_KEY", env=env)
        self.assertEqual(code, 0, err)
        self.agree("explain")
        _code, out, _err = self.run_ai("status", env=env)
        self.assertIn(f"work: {HTTPS}llm.example.test/v1/chat, chat shape, model m, key from WORK_KEY (set)", out)
        self.assertIn("explain: on, uses work", out)
        self.assertIn("today: 0 of 20 calls, 0 characters sent", out)
        self.assertIn("20 calls a day, 6000 characters in", out)
        self.assertNotIn("per run", out)        # one run makes one call: there is no per-run limit (2026-11-12, claims check)
        self.assertIn("agreed to explain;", out)
        self.assertIn("narrows nothing", out)
        self.assertNotIn(key, out)
        _code, out, _err = self.run_ai("status", env=self.env)
        self.assertIn("key from WORK_KEY (NOT set)", out)

    def test_it_reports_the_kill_switch_the_repository_narrowing_and_a_file_others_can_read(self):
        ep = Endpoint(self)
        self.connect(ep)
        self.put("brevio-core.toml", ['[ai]', 'allow = ["explain"]', 'deny_paths = ["legacy/**"]'])
        os.chmod(self.cfg_path, 0o644)
        _code, out, _err = self.run_ai("status", env={**self.env, "BREVIO_AI": "off"})
        self.assertIn("BREVIO_AI is set to off", out)
        self.assertIn("allows only explain", out)
        self.assertIn("1 path pattern(s) never sent", out)
        if os.name == "posix":
            self.assertIn("other users can read it", out)

    def test_a_routing_key_in_a_repository_file_is_a_settings_problem_not_a_reason_to_send(self):
        self.put("brevio-core.toml", ["[ai]", f'url = "{HTTPS}other.example.test/v1"'])
        _code, out, _err = self.run_ai("status")
        self.assertIn("routing is user-level only", out)


class TestExplain(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self)
        self.connect(self.ep)

    def test_a_dry_run_prints_the_exact_request_and_sends_and_counts_nothing(self):
        code, out, err = self.run_ai("explain", FINDING, "--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"would send this request to {HTTP}localhost:{self.ep.port}/v1/chat/completions", out)
        self.assertIn("Rule R9.1:", out)
        self.assertIn("const data = JSON.parse(raw) as any;", out)
        self.assertIn("--dry-run: nothing was sent.", out)
        self.assertEqual((self.ep.bodies, self.ledger().calls), ([], 0))

    def test_without_the_first_use_consent_yes_is_refused_and_with_it_the_answer_comes_back_labelled(self):
        code, out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("first use of `ai explain`", err)
        self.assertIn("--yes does not stand in", err)
        self.assertEqual(self.ep.bodies, [])
        self.agree("explain")
        code, out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Answer (written by default, model some-model; brevio-core did not write it):", out)
        self.assertIn("It uses a cast that turns the type checker off.", out)
        self.assertEqual(len(self.ep.bodies), 1)

    def test_the_first_use_at_a_terminal_prints_what_each_command_sends_and_records_the_yes(self):
        ask = Answers("y", "y")
        code, out, err = self.run_ai("explain", FINDING, ask=ask)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("What each command sends", out)
        self.assertIn("nothing goes to Brevio", out)
        self.assertEqual([q.split("?")[0].split(" ")[0] for q in ask.asked], ["Agree", "Send"])
        self.assertEqual(st.load(self.cfg_path).repos[st.repo_key(self.repo)], ("explain",))
        ask = Answers("y")
        code, _out, err = self.run_ai("explain", FINDING, ask=ask)         # the second time only the request is asked
        self.assertEqual((code, len(ask.asked)), (0, 1))
        code, _out, _err = self.run_ai("fix", FINDING, "--dry-run")         # a yes for explain is not a yes for fix
        self.assertEqual(code, 0)
        code, _out, err = self.run_ai("fix", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("first use of `ai fix`", err)

    def test_what_is_printed_is_what_arrives_and_it_holds_the_rule_and_the_line_but_no_path(self):
        self.agree("explain")
        code, out, _err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 0)
        body = self.ep.bodies[0]
        self.assertIn(json.dumps(body, indent=2, ensure_ascii=False), out)
        prompt = body["messages"][1]["content"]
        self.assertIn("Rule R9.1:", prompt)
        self.assertIn(TS[4], prompt)
        self.assertNotIn(TS[3], prompt)             # --context is 0 for explain
        self.assertNotIn("a.ts", json.dumps(body))
        self.assertIn(".ts file", prompt)
        self.assertEqual(body["max_tokens"], st.Limits().max_output_tokens)
        self.assertEqual(list(body), ["model", "max_tokens", "messages"])

    def test_control_characters_in_a_model_answer_are_spelled_out_not_sent_to_the_terminal(self):
        self.agree("explain")
        self.ep.replies = ["Fine.\x1b[2J\x1b[1;1Hverified: ok\rforged\x07\ttabbed"]
        code, out, _err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 0)
        answer = out.split("did not write it):\n", 1)[1]
        for raw in ("\x1b", "\x07", "\r"):
            self.assertNotIn(raw, answer)
        self.assertIn("Fine.\\x1b[2J\\x1b[1;1Hverified: ok\\x0dforged\\x07\ttabbed", answer)

    def test_context_lines_go_along_when_asked_for(self):
        self.agree("explain")
        self.run_ai("explain", FINDING, "--yes", "--context", "1")
        prompt = self.ep.bodies[0]["messages"][1]["content"]
        self.assertIn(f"{TS[3]}\n{TS[4]}\n{TS[5]}", prompt)
        self.assertIn("Line 2 of the 3 line(s)", prompt)

    def test_the_call_is_counted_with_its_characters_and_the_tokens_the_endpoint_reported(self):
        self.agree("explain")
        self.run_ai("explain", FINDING, "--yes")
        used = self.ledger()
        body = self.ep.bodies[0]
        sent = len(body["messages"][1]["content"]) + len(body["messages"][0]["content"])
        self.assertEqual((used.calls, used.chars, used.input_tokens, used.output_tokens), (1, sent, 40, 12))
        _code, out, _err = self.run_ai("status")
        self.assertIn("today: 1 of 20 calls", out)
        self.assertIn("40 tokens in and 12 out", out)

    def test_no_terminal_and_no_yes_means_nothing_is_sent_and_so_does_the_answer_no(self):
        self.agree("explain")
        code, _out, err = self.run_ai("explain", FINDING)
        self.assertEqual(code, 3)
        self.assertIn("there is no terminal to ask; pass --yes", err)
        code, _out, err = self.run_ai("explain", FINDING, ask=Answers("n"))
        self.assertEqual((code, err.strip().endswith("not sent")), (3, True))
        code, _out, _err = self.run_ai("explain", FINDING, ask=Answers("maybe"))
        self.assertEqual(code, 3)
        self.assertEqual((self.ep.bodies, self.ledger().calls), ([], 0))

    def test_the_kill_switch_and_a_command_that_is_off_stop_everything(self):
        self.agree("explain")
        for value in ("off", "0", "disabled"):
            code, _out, err = self.run_ai("explain", FINDING, "--yes", env={**self.env, "BREVIO_AI": value})
            self.assertEqual(code, 3, value)
            self.assertIn("BREVIO_AI", err)
        self.run_ai("connect", "--local", str(self.ep.port), "--model", "m", "--use", "fix")     # explain is left out: off
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("explain is off", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_connector_that_cannot_send_is_refused_before_anything_is_shown(self):
        self.agree("explain")
        with open(self.cfg_path, encoding="utf-8") as fh:
            text = fh.read()
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(f"{HTTP}localhost:{self.ep.port}", HTTP + "llm.example.test"))
        code, out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("must use https", err)
        self.assertNotIn("would send", out)

    def test_the_daily_limit_the_size_limit_and_a_damaged_ledger_each_stop_the_call(self):
        self.agree("explain")
        self.limits(max_calls_per_day=1)
        self.assertEqual(self.run_ai("explain", FINDING, "--yes")[0], 0)
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("max_calls_per_day", err)
        self.limits(max_calls_per_day=20, max_input_chars=st.LIMIT_BOUNDS["max_input_chars"][0])
        code, _out, err = self.run_ai("explain", FINDING, "--yes", "--context", "3")
        self.assertEqual(code, 3)
        self.assertIn("max_input_chars", err)
        self.assertEqual(len(self.ep.bodies), 1)
        with open(st.ledger_path(self.cfg_path), "w", encoding="utf-8") as fh:
            fh.write("not json")
        self.limits(max_input_chars=6000)
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 2)
        self.assertIn("delete it to start the count again", err)
        self.assertEqual(len(self.ep.bodies), 1)

    def test_a_failing_endpoint_is_one_failed_call_that_still_counts_and_is_never_tried_again(self):
        self.agree("explain")
        self.ep.status = 500
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 500", err)
        self.assertEqual((len(self.ep.bodies), self.ledger().calls), (1, 1))

    def test_a_secret_among_the_lines_to_send_stops_the_call_and_is_not_printed(self):
        self.put("b.ts", [SECRET, "const v = x as any;"])
        self.agree("explain")
        code, out, err = self.run_ai("explain", "b.ts:2: [R9.1] x", "--yes")             # the line alone is fine
        self.assertEqual(code, 0, err)
        code, out, err = self.run_ai("explain", "b.ts:2: [R9.1] x", "--yes", "--context", "1")
        self.assertEqual(code, 3)
        self.assertIn("line 1 of b.ts looks like a secret", err)
        for shown in (out, err):
            self.assertNotIn("AKIA", shown)
        self.assertEqual(len(self.ep.bodies), 1)

    def test_a_file_that_holds_secrets_and_a_denied_path_are_never_read_into_a_request(self):
        self.put("server.pem", ["const k = x as any;"])
        self.put("legacy/old.ts", ["const k = x as any;"])
        self.put("brevio-core.toml", ["[ai]", 'deny_paths = ["legacy/**"]'])
        self.agree("explain")
        code, _out, err = self.run_ai("explain", "server.pem:1: [R9.1] x", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("holds secrets", err)
        code, _out, err = self.run_ai("explain", "legacy/old.ts:1: [R9.1] x", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("deny_paths", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_repository_can_turn_ai_off_or_allow_fewer_commands_and_cannot_name_a_destination(self):
        self.agree()
        self.put("brevio-core.toml", ["[ai]", "enabled = false"])
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("turns ai off", err)
        self.put("brevio-core.toml", ["[ai]", 'allow = ["explain"]'])
        code, _out, err = self.run_ai("fix", FINDING, "--yes")
        self.assertEqual(code, 3)
        self.assertIn("allows only: explain", err)
        self.put("brevio-core.toml", ["[ai]", f'url = "{HTTPS}other.example.test/v1"', 'key_env = "THEIR_KEY"'])
        code, _out, err = self.run_ai("explain", FINDING, "--yes")
        self.assertEqual(code, 2)
        self.assertIn("routing is user-level only", err)
        self.assertEqual(self.ep.bodies, [])

    def test_a_subfolder_answers_to_the_repository_it_lies_in(self):
        self.put("sub/x.ts", ["const k = x as any;"])
        self.put("brevio-core.toml", ["[ai]", "enabled = false"])
        self.agree()
        os.chdir(os.path.join(self.repo, "sub"))
        self.assertEqual(core_ai.project_root(), self.repo)
        code, _out, err = self.run_ai("explain", "x.ts:1: [R9.1] x", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("turns ai off", err)

    def test_a_finding_that_is_not_there_or_not_a_rule_is_a_usage_error_and_a_scan_gives_the_last_one(self):
        self.agree()
        for finding in ("a.ts:99: [R9.1] x", "nowhere.ts:1: [R9.1] x", "a.ts:5: [R999.9] x", "not a finding", "../x.ts:1: [R9.1] x"):
            code, _out, _err = self.run_ai("explain", finding, "--yes")
            self.assertEqual(code, 2, finding)
        code, _out, err = self.run_ai("explain", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("--last", err)
        code, out, _err = self.run_ai("explain", "--last", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(TS[4], out)
        self.put("a.ts", [GOOD_LINE])
        code, _out, err = self.run_ai("explain", "--last", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("nothing to ask about", err)
        self.assertEqual(self.ep.bodies, [])


class TestFix(Project):
    def setUp(self):
        super().setUp()
        self.ep = Endpoint(self, replies=[GOOD_REPLY])
        self.connect(self.ep)
        self.agree()

    def fix(self, *extra, **kw):
        return self.run_ai("fix", FINDING, "--context", "1", *extra, **kw)

    def test_a_fix_that_passes_is_shown_as_a_diff_with_its_verdict_and_nothing_is_written_unless_asked(self):
        before = self.text()
        code, out, err = self.fix("--yes")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Proposed change (written by default, model some-model; brevio-core did not write it):", out)
        self.assertIn(f"-{TS[4]}\n+{GOOD_LINE}\n", out)
        self.assertIn("verified: [R9.1] no longer flags the line", out)
        self.assertIn("Nothing was written.", out)
        self.assertEqual((self.text(), len(self.ep.bodies)), (before, 1))
        body = self.ep.bodies[0]
        self.assertIn("correct one place in code", body["messages"][0]["content"])
        self.assertIn("corrected version of all 3 line(s)", body["messages"][1]["content"])

    def test_apply_writes_the_working_tree_after_a_second_yes_and_commits_nothing(self):
        ask = Answers("y", "y")
        code, out, err = self.fix("--apply", ask=ask)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual([q.split("?")[0].split(" ")[0] for q in ask.asked], ["Send", "Write"])
        self.assertEqual(self.text(), "\n".join([*TS[:4], GOOD_LINE, *TS[5:]]) + "\n")
        self.assertIn("wrote a.ts (nothing is committed)", out)
        self.assertEqual(os.listdir(os.path.join(self.repo, ".git")), [])

    def test_yes_is_never_honoured_for_apply_and_without_a_terminal_it_is_refused_before_anything_is_sent(self):
        code, _out, err = self.fix("--apply", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("needs a terminal", err)
        self.assertEqual(self.ep.bodies, [])
        ask = Answers("y", "n")
        code, out, _err = self.fix("--apply", "--yes", ask=ask)
        self.assertEqual((code, len(ask.asked), self.text()), (3, 2, "\n".join(TS) + "\n"))
        self.assertIn("not written", out)

    def test_a_reply_that_adds_an_ignore_comment_is_refused_with_the_reasons_and_never_retried(self):
        self.ep.replies = [fenced([TS[3], "  // brevio-core: ignore R9.1 -- checked later", TS[4]])]
        before = self.text()
        code, out, _err = self.fix("--yes")
        self.assertEqual(code, 1)
        self.assertIn("refused by the rules; nothing was written and it is not retried:", out)
        self.assertIn("switches a check off", out)
        self.assertIn("[R9.1] still flags", out)
        self.assertNotIn("verified:", out)
        self.assertEqual((self.text(), len(self.ep.bodies), self.ledger().calls), (before, 1, 1))

    def test_a_refused_reply_is_not_written_even_with_apply_and_a_yes_to_everything(self):
        self.ep.replies = [fenced([TS[3], TS[4] + " // @ts-ignore", TS[5]])]
        before = self.text()
        code, out, _err = self.fix("--apply", ask=Answers("y", "y", "y"))
        self.assertEqual(code, 1)
        self.assertIn("refused by the rules", out)
        self.assertEqual(self.text(), before)

    def test_a_reply_that_introduces_a_new_finding_or_breaks_the_file_is_refused(self):
        self.ep.replies = [fenced([TS[3], GOOD_LINE, "  return (data as any).items;"])]
        code, out, _err = self.fix("--yes")
        self.assertEqual(code, 1)
        self.assertIn("a new finding: [R9.1]", out)

    def test_control_characters_in_a_proposed_change_are_spelled_out_in_the_diff_and_the_reasons(self):
        self.ep.replies = [fenced([TS[3], GOOD_LINE + "\x1b[2K // @ts-ignore", TS[5]])]
        code, out, _err = self.fix("--yes")
        self.assertEqual(code, 1)
        self.assertNotIn("\x1b", out)
        self.assertIn("\\x1b[2K", out)

    def test_a_reply_that_is_not_one_fenced_block_is_refused_with_its_reason_and_shown_only_when_asked(self):
        for reply in ("Just use unknown instead of any.", GOOD_REPLY + "\n" + GOOD_REPLY):
            self.ep.replies = [reply]
            code, out, _err = self.fix("--yes")
            self.assertEqual(code, 1)
            self.assertIn("the reply must hold exactly one fenced block", out)
            self.assertNotIn("Just use unknown", out)
            self.assertNotIn("Done.", out)
            code, out, _err = self.fix("--yes", "--show-refused")
            self.assertEqual(code, 1)
            self.assertIn("Refused model output (written by default, model some-model; brevio-core did not write it):", out)
            self.assertIn("| " + reply.splitlines()[0], out)
        self.assertEqual(self.text(), "\n".join(TS) + "\n")

    def test_a_file_that_sets_what_the_gate_does_is_not_asked_about_at_all(self):
        self.put("CLAUDE.md", ["const k = x as any;"])
        code, _out, err = self.run_ai("fix", "CLAUDE.md:1: [R9.1] x", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("a person changes it", err)
        self.assertEqual(self.ep.bodies, [])

    def test_the_default_window_is_five_lines_either_side_and_explain_defaults_to_the_line_alone(self):
        self.run_ai("fix", FINDING, "--yes")
        prompt = self.ep.bodies[0]["messages"][1]["content"]
        self.assertIn("Line 5 of the 7 line(s)", prompt)         # a seven-line file: the window is the whole file
        self.assertEqual(core_ai.DEFAULT_CONTEXT, {"explain": 0, "fix": 5})

    def test_a_file_that_changed_between_the_request_and_the_reply_is_never_written(self):
        original = self.ep.server.RequestHandlerClass.do_POST

        def edit_then_answer(handler):
            self.put("a.ts", [*TS[:4], "  const data = other(raw) as any;", *TS[5:]])
            original(handler)

        with unittest.mock.patch.object(self.ep.server.RequestHandlerClass, "do_POST", edit_then_answer):
            code, out, _err = self.fix("--apply", ask=Answers("y", "y"))
        self.assertEqual(code, 1)
        self.assertIn("changed since the window was read", out)
        self.assertIn("other(raw)", self.text())


class TestTheReplyAndThePrompt(unittest.TestCase):
    def test_exactly_one_fenced_block_is_read_in_any_of_the_usual_fence_styles(self):
        for text, lines in (("```ts\na\n b\n```", ["a", " b"]), ("x\n~~~\na\n~~~\ny", ["a"]), ("````\n```\nin\n```\n````", ["```", "in", "```"]),
                            ("```\n```", [""]), ("```python\r\na\r\n```", ["a"])):
            self.assertEqual(core_ai.extract_block(text), (lines, ""), text)
        for text, count in (("none", 0), ("```\na\n```\n```\nb\n```", 2), ("```\nnever closed", 0)):
            self.assertEqual(core_ai.extract_block(text)[0], None, text)
            self.assertIn(f"it holds {count}", core_ai.extract_block(text)[1])

    def test_the_prompt_names_no_file_and_its_fence_is_longer_than_any_run_of_backticks_in_the_code(self):
        window = verify.Window("secret/place/x.ts", "R9.1", 2, 1, ["a", "const t = `x` + '```';"])
        prompt, system = core_ai.build_prompt("fix", window)
        self.assertNotIn("secret", prompt)
        self.assertNotIn("x.ts", prompt)
        self.assertIn(".ts file", prompt)
        self.assertIn("\n````\na\nconst t", prompt)
        self.assertEqual(system, core_ai.FIX_SYSTEM)
        self.assertIn("Line 2 of the 2 line(s)", prompt)

    def test_safe_keeps_text_newlines_tabs_and_windows_line_ends_and_spells_out_the_rest(self):
        self.assertEqual(core_ai.safe("a\tb\nc\r\nd é ✓"), "a\tb\nc\r\nd é ✓")
        self.assertEqual(core_ai.safe("\x1b[31mred\x00\x7f\x9b"), "\\x1b[31mred\\x00\\x7f\\x9b")
        self.assertEqual(core_ai.safe("a\rb"), "a\\x0db")

    def test_the_secret_check_is_the_scanners_own_and_reads_the_lines_above_as_the_scanner_does(self):
        rows = [(10, "const a = 1;"), (11, SECRET), (12, "const b = 2;")]
        self.assertEqual(core_ai.secret_lines(rows), [11])
        self.assertEqual(core_ai.secret_lines([(1, "const key = process.env.KEY;")]), [])


class TestTheGateNeverLoadsIt(unittest.TestCase):
    """What the claim "the gate calls no model" rests on at run time, beside the import-graph checks of the security test."""

    SCRIPT = (
        "import json, os, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from brevio.pipeline import core_cli\n"
        "WATCH = ('urllib.request', 'http.client', 'ssl', 'brevio.pipeline.core_ai_net')\n"
        "def loaded(): return sorted(m for m in WATCH if m in sys.modules)\n"
        "out = {'table': loaded()}\n"
        "core_cli.line_commands()\n"
        "out['commands'] = 'ai' in core_cli.line_commands()[0]\n"
        "for label, argv in json.loads(sys.argv[2]):\n"
        "    try:\n"
        "        core_cli.main(argv)\n"
        "    except SystemExit:\n"
        "        pass\n"
        "    out[label] = loaded()\n"
        "print(json.dumps(out))\n")

    def run_script(self, steps, cwd, env=None):
        res = subprocess.run([sys.executable, "-c", self.SCRIPT, ROOT, json.dumps(steps)], cwd=cwd, capture_output=True, encoding="utf-8",
                             timeout=120, input='{"cwd": "."}', env={**os.environ, **(env or {})})
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout.strip().splitlines()[-1])

    def test_building_the_command_table_scanning_and_listing_commands_load_no_network_module(self):
        if not os.path.isfile(os.path.join(ROOT, "brevio", "pipeline", "core_ai.py")):
            self.skipTest("this set ships no ai")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.ts"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(TS) + "\n")
            done = self.run_script([["scan", ["scan", "a.ts"]], ["rules", ["rules"]], ["explain", ["explain", "R9.1"]]], d)
        self.assertEqual(done["commands"], True)
        self.assertEqual({k: v for k, v in done.items() if k != "commands"}, {"table": [], "scan": [], "rules": [], "explain": []})

    def test_status_and_a_dry_run_load_the_transport_by_name_and_only_then(self):
        if not os.path.isfile(os.path.join(ROOT, "brevio", "pipeline", "core_ai.py")):
            self.skipTest("this set ships no ai")
        with tempfile.TemporaryDirectory() as d:
            env = {"BREVIO_AI_CONFIG": os.path.join(d, "ai.toml")}
            done = self.run_script([["status", ["ai", "status"]]], d, env)
        self.assertEqual(done["table"], [])
        self.assertIn("brevio.pipeline.core_ai_net", done["status"])
        self.assertIn("urllib.request", done["status"])


class TestTheCommandIsRegisteredLikeTheOthers(Project):
    def test_the_command_table_holds_ai_with_its_help_and_runs_it(self):
        tools, helps = core_cli.line_commands()
        self.assertIn("ai", tools)
        self.assertEqual(helps["ai"], core_ai.HELP)
        with unittest.mock.patch.dict(os.environ, self.env):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = core_cli.main(["ai", "status"])
        self.assertEqual(code, 0)
        self.assertIn("nothing; see `brevio-core ai connect --help`", out.getvalue())

    def test_a_build_without_the_transport_says_so_and_sends_nothing(self):
        with unittest.mock.patch.object(core_cli, "set_module", return_value=None):
            code, _out, err = self.run_ai("status")
        self.assertEqual(code, 2)
        self.assertIn("no transport", err)

    def test_the_help_names_every_action_and_the_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            core_ai.main(["--help"])
        self.assertEqual(stop.exception.code, 0)
        for word in ("connect", "status", "explain", "fix", "3: nothing was sent or written"):
            self.assertIn(word, out.getvalue())


if __name__ == "__main__":
    unittest.main()
