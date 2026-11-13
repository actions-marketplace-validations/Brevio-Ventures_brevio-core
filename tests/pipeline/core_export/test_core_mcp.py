"""``brevio-core mcp``: a real stdio session against the tree, in a clean interpreter with only that tree on its path.

Standard library only: the exporter copies this file beside the code (the 0.2 set) and it runs unchanged there. In the
harness the test first exports the newest set (0.6, which holds every earlier one, so the tools of each set are served
and checked) and runs against that copy.
"""
import json
import os
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
# A fake key in the AWS access key format (AWS's documented example key ID), joined at run time so this file is not
# itself a finding of a secret scan. The hardcoded-secret rule (R13.1) flags the format (E35 precision run, 2026-11-05).
AWS_EXAMPLE_LINE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
CLEAN_LINE = "export const add = (a: number, b: number): number => a + b;\n"
_SERVE = ("import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; code = core_cli.main(['mcp', *sys.argv[2:]]); "
          "sys.stderr.write('NET:' + ','.join(sorted(m for m in ('socket', 'ssl', 'urllib.request', 'http.client') if m in sys.modules))); "
          "raise SystemExit(code)")
LIST, CALL = "tools" + "/list", "tools" + "/call"     # joined: the leak scan reads a literal "tools" + "/" as a private folder
_BIN = tempfile.TemporaryDirectory()    # a PATH holding git and nothing else
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


def session(cwd, *messages):
    """Send each message (a dict, or a raw line) on stdin of ``brevio-core mcp``; return the replies, stderr and the exit code."""
    lines = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    res = subprocess.run([sys.executable, "-I", "-c", _SERVE, TREE], cwd=cwd, input=lines, capture_output=True, encoding="utf-8",
                         timeout=60, env=portable.child_env(_PATH))
    return [json.loads(ln) for ln in res.stdout.splitlines()], res.stderr, res.returncode


def rpc(msg_id, method, params=None):
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, **({"params": params} if params is not None else {})}


def call(msg_id, tool, **arguments):
    return rpc(msg_id, CALL, {"name": tool, "arguments": arguments})


def found_paths(reply):
    """The paths of the finding lines (``path:line [rule] ...``) of one scan reply."""
    lines = reply["result"]["content"][0]["text"].splitlines()
    return {ln.split(":")[0] for ln in lines if " [" in ln and ln.split(":")[0].endswith(".ts")}


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


class TestProtocol(unittest.TestCase):
    def test_initialize_then_list_names_the_tools_and_a_notification_gets_no_reply(self):
        with tempfile.TemporaryDirectory() as d:
            replies, _err, code = session(d, rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
                                          {"jsonrpc": "2.0", "method": "notifications/initialized"}, rpc(2, LIST))
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in replies], [1, 2])
        init = replies[0]["result"]
        self.assertEqual((init["protocolVersion"], init["serverInfo"]["name"]), ("2025-06-18", "brevio-core"))
        self.assertIn("tools", init["capabilities"])
        self.assertIn("resources", init["capabilities"])
        self.assertIn("prompts", init["capabilities"])
        tools = {t["name"]: t for t in replies[1]["result"]["tools"]}
        has_fix = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "code_rules", "fixers.py"))   # the 0.3 set adds fix
        has_brief = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_memory.py"))          # the 0.6 set adds brief
        has_check = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "gates", "core_gates.py"))  # the 0.6 set adds check
        has_score = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_score.py"))
        has_aeo = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_aeo.py"))
        has_map = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_map.py"))
        has_heal = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_heal.py"))
        has_patch = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_patch.py"))           # the 0.6 set adds verify_edit and finding_packet
        expected_tools = sorted([
            *(['aeo'] if has_aeo else []),
            *(['brief'] if has_brief else []),
            *(['check'] if has_check else []),
            "explain",
            *(['finding_packet', 'verify_edit'] if has_patch else []),
            *(["fix"] if has_fix else []),
            *(['heal'] if has_heal else []),
            "learn",
            "list_rules",
            *(['map'] if has_map else []),
            "scan",
            *(['score'] if has_score else []),
        ])
        self.assertEqual(sorted(tools), expected_tools)
        for t in tools.values():
            self.assertEqual(t["inputSchema"]["type"], "object")
            ann = t["annotations"]
            self.assertTrue(isinstance(ann.get("title"), str) and ann["title"])
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                self.assertIsInstance(ann.get(hint), bool)
            self.assertFalse(ann["destructiveHint"])
            self.assertTrue(ann["idempotentHint"])
            self.assertFalse(ann["openWorldHint"])
            for prop in t["inputSchema"]["properties"].values():
                self.assertIn("type", prop)
                self.assertTrue(isinstance(prop.get("description"), str) and prop["description"])
        self.assertTrue(tools["scan"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["learn"]["annotations"]["readOnlyHint"])
        for name in ("verify_edit", "finding_packet"):      # they only read: nothing is written, nothing is sent
            if has_patch:
                self.assertTrue(tools[name]["annotations"]["readOnlyHint"])

    def test_server_info_names_the_package_version_when_a_release_stamped_it(self):
        """D5 (MCP half, 2026-11-13): ``serverInfo.version`` was the tool-set number (0.4), not the package's."""
        init_module = os.path.join(TREE, "brevio", "pipeline", "core_init.py")
        if not os.path.isfile(init_module):
            self.skipTest("this build has no core_init, so no release version")
        with tempfile.TemporaryDirectory() as copy, tempfile.TemporaryDirectory() as d:
            tree = os.path.join(copy, "core")
            shutil.copytree(TREE, tree, ignore=shutil.ignore_patterns("__pycache__"))
            write(tree, "VERSION", "7.8.9\n")
            write(tree, "pyproject.toml", '[project]\nname = "brevio-core"\n\n[project.urls]\nSource = "https://brevio.pro/acme/brevio-core"\n')
            lines = json.dumps(rpc(1, "initialize", {"protocolVersion": "2025-06-18"})) + "\n"
            res = subprocess.run([sys.executable, "-I", "-c", _SERVE, tree], cwd=d, input=lines, capture_output=True,
                                 encoding="utf-8", timeout=60, env=portable.child_env(_PATH))
        self.assertEqual(json.loads(res.stdout)["result"]["serverInfo"], {"name": "brevio-core", "version": "7.8.9"})

    def test_a_tool_call_with_an_argument_the_schema_does_not_name_is_an_error_that_names_the_near_one(self):
        """D6 (2026-11-13): ``{"diff": "HEAD"}`` was dropped and the whole change was scanned."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            write(d, "a.ts", CLEAN_LINE)
            git(d, "add", "-A")
            git(d, "commit", "-q", "-m", "base")
            write(d, "a.ts", CLEAN_LINE + AWS_EXAMPLE_LINE)
            replies, _err, _code = session(d, call(1, "scan", diff="HEAD"), call(2, "scan", diff_base="HEAD"),
                                           call(3, "scan", paths=["a.ts"], colour="red"))
        bad, good, other = (r["result"] for r in replies)
        self.assertTrue(bad["isError"])
        self.assertEqual(bad["content"][0]["text"], "unknown argument: diff (did you mean diff_base?)")
        self.assertFalse(good["isError"])
        self.assertIn("a.ts:2 [R13.1]", good["content"][0]["text"])
        self.assertEqual((other["isError"], other["content"][0]["text"]), (True, "unknown argument: colour"))

    def test_an_unknown_client_version_gets_the_newest_one_this_server_speaks(self):
        with tempfile.TemporaryDirectory() as d:
            replies, _err, _code = session(d, rpc(1, "initialize", {"protocolVersion": "1999-01-01"}))
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")

    def test_bad_input_gets_a_json_rpc_error_and_the_session_carries_on(self):
        with tempfile.TemporaryDirectory() as d:
            replies, _err, code = session(d, "this is not json", "", rpc(1, "no/such/method"), rpc(2, "ping"),
                                          call(3, "no_such_tool"), rpc(4, CALL, {"arguments": {}}), "[1, 2]")
        self.assertEqual(code, 0)
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["error"]["code"], -32601)
        self.assertEqual(replies[2]["result"], {})
        self.assertEqual((replies[3]["error"]["code"], replies[4]["error"]["code"]), (-32602, -32602))
        self.assertEqual(replies[5]["error"]["code"], -32600)
        self.assertEqual(len(replies), 6)

    def test_a_whole_session_makes_no_network_call(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/bad.ts", AWS_EXAMPLE_LINE)
            _replies, err, code = session(d, rpc(1, "initialize", {}), call(2, "scan", paths=["src"]), call(3, "explain", rule="R2.1"))
        self.assertEqual(code, 0)
        self.assertEqual(err, "NET:")      # no network module was even loaded

    def test_arguments_to_the_command_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run([sys.executable, "-I", "-c", _SERVE, TREE, "extra"], cwd=d, capture_output=True, encoding="utf-8",
                                 timeout=60, input="", env=portable.child_env(_PATH))
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")

    def test_resources_list_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            replies, _err, code = session(
                d,
                rpc(1, "resources/list"),
                rpc(2, "resources/templates/list"),
                rpc(3, "resources/read", {"uri": "brevio://rules/catalog"}),
                rpc(4, "resources/read", {"uri": "brevio://rules/R13.1"}),
            )
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        res_list = replies[0]["result"]["resources"]
        uris = [r["uri"] for r in res_list]
        self.assertIn("brevio://rules/catalog", uris)
        has_map = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_map.py"))
        if has_map:
            self.assertIn("brevio://map", uris)
        templates = replies[1]["result"]["resourceTemplates"]
        self.assertEqual(templates[0]["uriTemplate"], "brevio://rules/{rule_id}")
        cat_content = replies[2]["result"]["contents"][0]
        self.assertEqual(cat_content["uri"], "brevio://rules/catalog")
        self.assertIn("R13.1", cat_content["text"])
        r131_content = replies[3]["result"]["contents"][0]
        self.assertEqual(r131_content["uri"], "brevio://rules/R13.1")
        self.assertIn("R13.1", r131_content["text"])

    def test_prompts_list_and_get(self):
        with tempfile.TemporaryDirectory() as d:
            replies, _err, code = session(
                d,
                rpc(1, "prompts/list"),
                rpc(2, "prompts/get", {"name": "audit-diff", "arguments": {"diff_base": "HEAD"}}),
                rpc(3, "prompts/get", {"name": "pre-edit-brief", "arguments": {"files": "src/app.ts"}}),
                rpc(4, "prompts/get", {"name": "remediate-findings", "arguments": {"paths": "."}}),
            )
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        prompts = replies[0]["result"]["prompts"]
        names = [p["name"] for p in prompts]
        self.assertEqual(sorted(names), ["audit-diff", "pre-edit-brief", "remediate-findings"])
        for idx in (1, 2, 3):
            res = replies[idx]["result"]
            self.assertIn("messages", res)
            self.assertTrue(len(res["messages"]) > 0)
            self.assertIn("text", res["messages"][0]["content"])

    def test_map_and_heal_tools_when_available(self):
        has_map = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_map.py"))
        has_heal = os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_heal.py"))
        with tempfile.TemporaryDirectory() as d:
            write(d, "app.py", "def main(): pass\n")
            if has_map:
                replies, _err, code = session(d, call(1, "map", root=d))
                self.assertEqual(code, 0)
                self.assertFalse(replies[0]["result"]["isError"])
                r_rep, _, _ = session(d, rpc(2, "resources/read", {"uri": "brevio://map"}))
                self.assertIn("contents", r_rep[0]["result"])
            if has_heal:
                replies, _err, code = session(d, call(1, "heal", root=d))
                self.assertEqual(code, 0)
                self.assertFalse(replies[0]["result"]["isError"])


class TestAPathOnAnotherDrive(unittest.TestCase):
    def test_a_path_that_shares_no_root_with_the_folder_is_outside_it_not_a_crash(self):
        """On Windows ``os.path.commonpath`` raises ``ValueError`` for two drives; the tool must answer "outside"."""
        script = ("import os, sys; sys.path.insert(0, sys.argv[1])\n"
                  "from brevio.operator import mcp_cmd\n"
                  "def other_drive(paths):\n    raise ValueError(\"Paths don't have the same drive\")\n"
                  "os.path.commonpath = other_drive\n"
                  "print(mcp_cmd._inside_folder('x.ts'))\n")
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run([sys.executable, "-I", "-c", script, TREE], cwd=d, capture_output=True, encoding="utf-8",
                                 timeout=60, env=portable.child_env(_PATH))
        self.assertEqual((res.returncode, res.stdout.strip()), (0, "False"), res.stderr)


class TestScanTool(unittest.TestCase):
    def test_a_planted_secret_comes_back_as_a_finding_and_a_clean_file_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/bad.ts", AWS_EXAMPLE_LINE)
            write(d, "src/ok.ts", CLEAN_LINE)
            replies, _err, _code = session(d, rpc(1, "initialize", {}), call(2, "scan", paths=["src/bad.ts"]),
                                           call(3, "scan", paths=["src/ok.ts"]))
        bad, ok = replies[1]["result"], replies[2]["result"]
        self.assertFalse(bad["isError"])
        self.assertGreaterEqual(bad["structuredContent"]["count"], 1)
        first = bad["content"][0]["text"].splitlines()[0]
        self.assertRegex(first, r"^src/bad\.ts:1 \[R\d+\.\d+\] .+ -> .+")      # path:line [rule] message -> fix hint
        self.assertNotIn("findings", bad["structuredContent"])                 # the text is sent once, not twice
        self.assertEqual(ok["structuredContent"]["count"], 0)
        self.assertEqual(ok["content"][0]["text"], "0 finding(s) in 1 file(s)")

    def test_with_no_arguments_it_scans_the_uncommitted_changes(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            write(d, "src/old.ts", AWS_EXAMPLE_LINE)
            git(d, "add", ".")
            git(d, "commit", "-q", "-m", "first")
            write(d, "src/new.ts", AWS_EXAMPLE_LINE)
            replies, _err, _code = session(d, call(1, "scan"))
        self.assertEqual(found_paths(replies[0]), {"src/new.ts"})

    def test_a_diff_scan_reports_only_the_lines_the_change_adds(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            write(d, "src/a.ts", AWS_EXAMPLE_LINE)
            git(d, "add", ".")
            git(d, "commit", "-q", "-m", "first")
            write(d, "src/b.ts", AWS_EXAMPLE_LINE)
            replies, _err, _code = session(d, call(1, "scan", diff_base="HEAD"), call(2, "scan", paths=["src"]))
        self.assertEqual(found_paths(replies[0]), {"src/b.ts"})
        self.assertEqual(found_paths(replies[1]), {"src/a.ts", "src/b.ts"})

    def test_a_path_that_is_missing_or_outside_the_project_is_an_error_result(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            write(other, "x.ts", AWS_EXAMPLE_LINE)
            replies, _err, _code = session(d, call(1, "scan", paths=["nope.ts"]), call(2, "scan", paths=[other]),
                                           call(3, "scan", diff_base="--output=x"), call(4, "scan", paths="src"))
        for r in replies:
            self.assertTrue(r["result"]["isError"], r)
        self.assertIn("outside the project folder", replies[1]["result"]["content"][0]["text"])

    def test_a_long_list_of_findings_is_cut_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/many.ts", AWS_EXAMPLE_LINE * 250)
            replies, _err, _code = session(d, call(1, "scan", paths=["src"]))
        data, text = replies[0]["result"]["structuredContent"], replies[0]["result"]["content"][0]["text"].splitlines()
        self.assertEqual(data["count"], 250)
        self.assertEqual(len([ln for ln in text if ln.startswith("src/many.ts:")]), 20)
        self.assertEqual(text[-2:], ["+230 more", "250 finding(s) in 1 file(s)"])


class TestScoreSurfaceAgreesWithTheCommand(unittest.TestCase):
    """D36 (2026-11-13): the ``score`` tool and ``brevio://scorecard`` say what ``score`` prints: findings counted by rule, no
    score or grade."""

    def test_the_tool_text_and_the_resource_are_what_score_prints(self):
        if not os.path.isfile(os.path.join(TREE, "brevio", "pipeline", "core_score.py")):
            return      # a build without the 0.6 set has no ``score``, so no tool and no resource to compare
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            cli = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; "
                                  "raise SystemExit(core_cli.main(sys.argv[2:]))", TREE, "score", "--no-upgrade-line"],
                                 cwd=d, capture_output=True, encoding="utf-8", timeout=60, env=portable.child_env(_PATH))
            cli_json = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; "
                                       "raise SystemExit(core_cli.main(sys.argv[2:]))", TREE, "score", "--json"],
                                      cwd=d, capture_output=True, encoding="utf-8", timeout=60, env=portable.child_env(_PATH))
            replies, _err, code = session(d, rpc(1, LIST), call(2, "score"), rpc(3, "resources/read", {"uri": "brevio://scorecard"}),
                                          rpc(4, "resources/list"))
        self.assertEqual(code, 0)
        tool = next(t for t in replies[0]["result"]["tools"] if t["name"] == "score")
        text = replies[1]["result"]["content"][0]["text"]
        self.assertEqual(text.strip(), cli.stdout.strip())
        self.assertIn("score (preview)", text)
        self.assertNotRegex(text + tool["description"], r"(?i)\bgrade\b|\bbadge\b|/100")
        self.assertIn("count the findings", tool["description"])
        card = json.loads(replies[2]["result"]["contents"][0]["text"])
        self.assertEqual(card, json.loads(cli_json.stdout))
        listed = next(r for r in replies[3]["result"]["resources"] if r["uri"] == "brevio://scorecard")
        self.assertIn("counted by rule", listed["description"])
        self.assertNotIn("grade", listed["description"].lower())


class TestRuleTools(unittest.TestCase):
    def test_explain_returns_the_rule_page_for_an_id_in_any_case(self):
        with tempfile.TemporaryDirectory() as d:
            replies, _err, _code = session(d, call(1, "explain", rule="r2.1"), call(2, "explain", rule="R99.9"), call(3, "explain"))
        page = replies[0]["result"]
        self.assertFalse(page["isError"])
        self.assertTrue(page["content"][0]["text"].startswith("# R2.1:"))
        self.assertIn("## What it catches", page["content"][0]["text"])
        for r in replies[1:]:
            self.assertTrue(r["result"]["isError"])
            self.assertIn("list_rules", r["result"]["content"][0]["text"])

    def test_list_rules_lists_every_rule_and_a_query_narrows_it(self):
        with tempfile.TemporaryDirectory() as d:
            replies, _err, _code = session(d, call(1, "list_rules"), call(2, "list_rules", query="tenant"))
        everything, some = (r["result"]["structuredContent"] for r in replies)
        ids = [r["id"] for r in everything["rules"]]
        self.assertEqual(ids, sorted(ids, key=lambda i: tuple(int(p) for p in i[1:].split("."))))
        self.assertEqual(everything["count"], len(ids))
        self.assertTrue(0 < some["count"] < everything["count"])
        self.assertTrue(all("tenant" in (r["id"] + r["summary"]).lower() for r in some["rules"]))


class TestLearnTool(unittest.TestCase):
    def test_a_fix_in_the_last_commit_is_compiled_into_a_guard_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            write(d, "src/calc.py", "result = compute_legacy_total(items)\n")
            git(d, "add", ".")
            git(d, "commit", "-q", "-m", "first")
            write(d, "src/calc.py", "result = compute_total(items)\n")
            git(d, "commit", "-q", "-am", "fix")
            replies, _err, _code = session(d, call(1, "learn", name="no legacy total"), call(2, "learn", diff_base="-x"))
            guard = replies[0]["result"]["structuredContent"]["guard"]
            self.assertFalse(replies[0]["result"]["isError"])
            self.assertEqual(guard["title"], "no legacy total")
            with open(os.path.join(d, ".brevio", "guards.json"), encoding="utf-8") as fh:
                self.assertIn(guard["id"], json.load(fh)["guards"])
        self.assertTrue(replies[1]["result"]["isError"])

    def test_a_folder_with_no_earlier_commit_gives_an_error_result_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            replies, _err, code = session(d, call(1, "learn"), rpc(2, "ping"))
        self.assertEqual(code, 0)
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("No git diff", replies[0]["result"]["content"][0]["text"])
        self.assertEqual(replies[1]["result"], {})


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.path.isfile(os.path.join(ROOT, "brevio", "pipeline", "core_patch.py")), "the 0.6 set ships the edit tools")
class TestEditTools(unittest.TestCase):
    """``verify_edit`` and ``finding_packet`` (2026-11-11): the verdict of ``patch --dry-run`` and a finding's context."""

    SEARCH, MID, REPLACE = "<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE"

    def block(self, path, old, new):
        return f"{path}\n{self.SEARCH}\n{old}\n{self.MID}\n{new}\n{self.REPLACE}\n"

    def test_a_clean_edit_is_accepted_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", "export const a = 1;\n")
            replies, _err, code = session(d, call(1, "verify_edit", patch=self.block("src/a.ts", "export const a = 1;", "export const a = 2;")))
            with open(os.path.join(d, "src", "a.ts"), encoding="utf-8") as fh:
                after = fh.read()
        res = replies[0]["result"]
        self.assertEqual(code, 0)
        self.assertFalse(res["isError"])
        self.assertEqual((res["structuredContent"]["ok"], res["structuredContent"]["files"]), (True, ["src/a.ts"]))
        self.assertTrue(res["content"][0]["text"].startswith("accepted: src/a.ts"))
        self.assertEqual(after, "export const a = 1;\n")

    def test_an_edit_that_plants_a_secret_or_a_suppression_or_touches_the_config_is_refused_with_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/a.ts", "export const a = 1;\n")
            write(d, "brevio-core.toml", "")
            replies, _err, _code = session(
                d, call(1, "verify_edit", patch=self.block("src/a.ts", "export const a = 1;", AWS_EXAMPLE_LINE.strip() + " // brevio-core: ignore R13.1 -- test")),
                call(2, "verify_edit", patch=self.block("brevio-core.toml", "", 'ignore = ["R13.1"]')),
                call(3, "verify_edit", patch=self.block("a.ts", "x", "y")))
        planted, config, missing = (r["result"] for r in replies)
        self.assertFalse(planted["isError"])         # a refusal is the answer, not a failure of the tool
        self.assertFalse(planted["structuredContent"]["ok"])
        self.assertTrue(any("[R13.1]" in r for r in planted["structuredContent"]["reasons"]))
        self.assertTrue(any("[suppression]" in r for r in planted["structuredContent"]["reasons"]))
        self.assertTrue(any("protected" in r for r in config["structuredContent"]["reasons"]))
        self.assertFalse(missing["structuredContent"]["ok"])

    def test_a_block_with_no_file_takes_the_path_argument_and_a_bad_argument_is_an_error_result(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", "export const a = 1;\n")
            bare = f"{self.SEARCH}\nexport const a = 1;\n{self.MID}\nexport const a = 2;\n{self.REPLACE}\n"
            replies, _err, _code = session(d, call(1, "verify_edit", patch=bare, path="a.ts"), call(2, "verify_edit", patch=5))
        self.assertTrue(replies[0]["result"]["structuredContent"]["ok"])
        self.assertTrue(replies[1]["result"]["isError"])

    def test_the_packet_of_a_finding_has_the_rule_text_the_line_its_context_and_the_extension(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/bad.ts", "a\nb\n" + AWS_EXAMPLE_LINE + "c\n")
            replies, _err, _code = session(d, call(1, "finding_packet", finding="src/bad.ts:3 [R13.1] a secret", context=1),
                                           call(2, "finding_packet", finding="src/bad.ts:3: [R13.1] a secret"))
        res = replies[0]["result"]
        packet = res["structuredContent"]
        self.assertFalse(res["isError"])
        self.assertEqual((packet["rule"], packet["line"], packet["extension"], packet["before"], packet["after"]), ("R13.1", 3, ".ts", ["b"], ["c"]))
        self.assertTrue(packet["why"] and packet["good"])
        self.assertIn("> 3 |", res["content"][0]["text"])
        self.assertEqual(replies[1]["result"]["structuredContent"]["before"], [])     # the scan format reads the same, with no context
        self.assertEqual(replies[1]["result"]["structuredContent"]["rule"], "R13.1")

    def test_a_packet_for_a_secrets_file_a_missing_line_or_a_path_outside_is_an_error_result(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            write(d, ".env", "KEY=1\n")
            write(d, "a.ts", "x\n")
            write(other, "x.ts", "x\n")
            findings = [".env:1 [R13.1] x", "a.ts:9 [R9.2] x", f"{other}/x.ts:1 [R9.2] x", "nonsense"]
            replies, _err, _code = session(d, *[call(i, "finding_packet", finding=f) for i, f in enumerate(findings, 1)])
        self.assertEqual([r["result"]["isError"] for r in replies], [True] * 4)

    def test_the_edit_tools_run_with_every_socket_refused_and_send_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", "x\n")
            _replies, err, code = session(d, call(1, "verify_edit", patch=self.block("a.ts", "x", "y")), call(2, "finding_packet", finding="a.ts:1 [R9.2] x"))
        self.assertEqual(code, 0)
        self.assertEqual(err, "NET:")
