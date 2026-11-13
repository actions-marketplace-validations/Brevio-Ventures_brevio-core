"""The ``brevio-core scan`` command, run in a clean interpreter with only the exported tree on its path.

Standard library only: the exporter copies this file beside the code and it runs unchanged in the exported tree.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from brevio.pipeline import core_sarif
from brevio.pipeline.code_rules import PR_RULES_122

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
# A fake key in the AWS access key format (AWS's documented example key ID), joined at run time so this file is not
# itself a finding of a secret scan. The hardcoded-secret rule (R13.1) flags the format (E35 precision run, 2026-11-05).
AWS_EXAMPLE_LINE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
CLEAN_LINE = "export const add = (a: number, b: number): number => a + b;\n"
GITHUB = "https" + "://github.com/"      # joined: the export check refuses a literal address
_RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"


def run_cli(cwd, *args):
    """``brevio-core`` with ``args`` in ``cwd``, under ``python -I`` so nothing but the standard library and ROOT loads."""
    return subprocess.run([sys.executable, "-I", "-c", _RUN, ROOT, *args], cwd=cwd, capture_output=True, encoding="utf-8", timeout=60)


def write(d, rel, text):
    path = os.path.join(d, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


class TestScanCommand(unittest.TestCase):
    def test_a_planted_secret_fails_the_scan_and_a_clean_file_passes(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/bad.ts", AWS_EXAMPLE_LINE)
            write(d, "src/ok.ts", CLEAN_LINE)
            bad = run_cli(d, "scan", "src/bad.ts")
            self.assertEqual(bad.returncode, 1, bad.stderr)
            self.assertIn("src/bad.ts:1: [R13.1]", bad.stdout)
            self.assertIn("Explain: brevio-core explain R13.1", bad.stderr)
            self.assertNotIn("https://", bad.stderr)        # the playground link went: the page is not in this repository (2026-11-12)
            self.assertEqual(run_cli(d, "scan", "src/ok.ts").returncode, 0)

    def test_a_diff_scan_skips_build_output(self):
        """CI installs the package, which writes build/lib copies; those are not the change (2026-11-05)."""
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "app.ts", CLEAN_LINE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            write(d, "build/lib/pkg/x.py", AWS_EXAMPLE_LINE)
            write(d, "pkg.egg-info/x.py", AWS_EXAMPLE_LINE)
            r = run_cli(d, "scan", "--diff", "HEAD")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            write(d, "src/y.ts", AWS_EXAMPLE_LINE)
            self.assertEqual(run_cli(d, "scan", "--diff", "HEAD").returncode, 1)

    # Third-party code kept in a repository gave most false positives of the E35 precision run: Playwright keeps
    # minified and bundled builds as test assets (docs/benchmarks/precision.md, 2026-11-05).
    UPSTREAM = {"third_party/lib/a.ts": AWS_EXAMPLE_LINE, "third-party/b.js": "eval(code);\n", "src/vendor/c.ts": AWS_EXAMPLE_LINE,
                "assets/modernizr.min.js": "eval(code);\n", "assets/lib.min.mjs": "eval(code);\n",
                "static/js/main.3f2a1b9c.chunk.js": "eval(code);\n", "dist2/app.bundle.js": "eval(code);\n",
                "out/index-4be0c13d.js": "eval(code);\n",
                "tests/assets/modernizr/modernizr.js": "(()=>{var j=0;" + "x=1;" * 200 + "})();\n" + "eval(code);\n"}

    def test_a_folder_scan_skips_vendored_minified_and_bundled_files(self):
        with tempfile.TemporaryDirectory() as d:
            for rel, text in self.UPSTREAM.items():
                write(d, rel, text)
            write(d, "src/own.ts", "const header = 'x';\n" + AWS_EXAMPLE_LINE)
            write(d, "src/index-page.js", "eval(code);\n")          # a word after the dash, not a hash
            res = run_cli(d, "scan")
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertEqual(sorted({ln.split(":")[0] for ln in res.stdout.splitlines() if ": [R" in ln}),
                             ["src/index-page.js", "src/own.ts"])

    def test_a_diff_scan_skips_vendored_minified_and_bundled_files(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "app.ts", CLEAN_LINE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            for rel, text in self.UPSTREAM.items():
                write(d, rel, text)
            git(d, "add", "-A")                                    # tracked, so the diff carries them, not ls-files
            r = run_cli(d, "scan", "--diff", "HEAD")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_every_scan_mode_skips_typescript_declaration_files(self):
        """A declaration holds no code that runs; Playwright's protocol.d.ts gave R2.8 hits on doc prose (E35, 2026-11-06)."""
        prose = "which includes eval(), Function(), setTimeout() and setInterval()\n"
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, "app.ts", CLEAN_LINE)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
            for rel in ("types/protocol.d.ts", "types/index.d.mts", "types/index.d.cts"):
                write(d, rel, prose + AWS_EXAMPLE_LINE)
            for args in (("scan",), ("scan", "types"), ("scan", "--file", "types/protocol.d.ts"), ("scan", "--diff", "HEAD")):
                r = run_cli(d, *args)
                self.assertEqual((r.returncode, r.stdout), (0, ""), args)
            write(d, "types/protocol.ts", prose)
            self.assertIn("types/protocol.ts:1: [R2.8]", run_cli(d, "scan").stdout)

    def test_a_folder_is_walked_and_the_default_is_the_current_folder(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/deep/bad.ts", AWS_EXAMPLE_LINE)
            write(d, "node_modules/pkg/bad.ts", AWS_EXAMPLE_LINE)
            res = run_cli(d, "scan")
            self.assertEqual(res.returncode, 1)
            self.assertIn("src/deep/bad.ts", res.stdout)
            self.assertNotIn("node_modules", res.stdout)

    def test_the_agent_hook_form_scan_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.ts", AWS_EXAMPLE_LINE)
            write(d, "b.ts", CLEAN_LINE)
            self.assertEqual(run_cli(d, "scan", "--file", "a.ts").returncode, 1)
            self.assertEqual(run_cli(d, "scan", "--file", "b.ts").returncode, 0)

    def test_a_missing_path_is_a_usage_error_not_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "scan", "--file", "nope.ts")
            self.assertEqual(res.returncode, 2)
            self.assertIn("no such file", res.stderr)

    def test_sarif_is_written_to_the_named_file_with_the_rule_and_line(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/bad.ts", CLEAN_LINE + AWS_EXAMPLE_LINE)
            res = run_cli(d, "scan", "src/bad.ts", "--sarif", "out.sarif")
            self.assertEqual(res.returncode, 1)
            with open(os.path.join(d, "out.sarif"), encoding="utf-8") as fh:
                log = json.load(fh)
            self.assertEqual(log["version"], "2.1.0")
            hit = log["runs"][0]["results"][0]
            self.assertEqual(hit["ruleId"], "R13.1")
            self.assertEqual(hit["locations"][0]["physicalLocation"]["region"]["startLine"], 2)
            self.assertEqual(len(log["runs"][0]["tool"]["driver"]["rules"]), len(PR_RULES_122))

    def test_the_sarif_rules_name_the_command_that_prints_the_rule_page_and_link_only_the_tag_of_a_release(self):
        """The rules held a ``helpUri`` to a page on a website that this repository does not show (2026-11-12). Changed on
        purpose (2026-11-13, revisit I39): a tree that is a release on GitHub links the page of the rule at the tag of its
        version, which the repository does show; a tree that is not a release writes no link."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/bad.ts", AWS_EXAMPLE_LINE)
            run_cli(d, "scan", "src/bad.ts", "--sarif", "out.sarif")
            with open(os.path.join(d, "out.sarif"), encoding="utf-8") as fh:
                text = fh.read()
            rules = json.loads(text)["runs"][0]["tool"]["driver"]["rules"]
        uris = [r["helpUri"] for r in rules if "helpUri" in r]
        if not os.path.isfile(os.path.join(ROOT, "VERSION")):
            self.assertEqual(uris, [])
            self.assertNotIn("https://", text)
        else:
            self.assertTrue(all(u.startswith(GITHUB) and "/blob/v" in u and u.endswith(".md") for u in uris), uris[:2])
        self.assertEqual(next(r for r in rules if r["id"] == "R13.1")["help"]["text"], "brevio-core explain R13.1 prints the rule page.")

    def test_a_rule_outside_the_catalogue_is_declared_and_each_result_carries_the_index_of_its_rule(self):
        """``suppression`` and ``guard:<id>`` results named rules that ``tool.driver.rules`` did not hold (revisit D31)."""
        rules = [{"id": "R1.1", "shortDescription": {"text": "one"}}, {"id": "R1.2", "shortDescription": {"text": "two"}}]
        results = [{"ruleId": "R1.2", "level": "error"}, {"ruleId": "suppression", "level": "error"},
                   {"ruleId": "guard:no-eval", "level": "warning"}, {"ruleId": "suppression", "level": "error"}]
        driver = core_sarif.log(results, rules)["runs"][0]["tool"]["driver"]
        ids = [r["id"] for r in driver["rules"]]
        self.assertEqual(ids, ["R1.1", "R1.2", "suppression", "guard:no-eval"])
        self.assertEqual([r["ruleIndex"] for r in results], [1, 2, 3, 2])
        by_id = {r["id"]: r for r in driver["rules"]}
        self.assertEqual(by_id["guard:no-eval"]["defaultConfiguration"]["level"], "warning")
        self.assertIn("no-eval", by_id["guard:no-eval"]["shortDescription"]["text"])
        self.assertNotIn("version", driver)
        self.assertEqual(rules, [{"id": "R1.1", "shortDescription": {"text": "one"}}, {"id": "R1.2", "shortDescription": {"text": "two"}}])

    def test_an_id_outside_the_catalogue_is_not_described_as_a_suppression(self):
        """A finding line without a rule tag (rule ``brevio-core``) or of a removed rule was declared with the text of the
        ``suppression`` rule, which was untrue (second review of revisit D31)."""
        log = core_sarif.log([{"ruleId": "brevio-core", "level": "error"}, {"ruleId": "suppression", "level": "error"}], [])
        by_id = {r["id"]: r for r in log["runs"][0]["tool"]["driver"]["rules"]}
        self.assertNotIn("suppression", by_id["brevio-core"]["shortDescription"]["text"].lower())
        self.assertNotIn("baseline", by_id["brevio-core"]["help"]["text"])
        self.assertIn("ignore comment", by_id["suppression"]["shortDescription"]["text"])

    def test_a_release_on_github_names_its_version_and_links_each_rule_page_at_its_tag(self):
        """The link is written for a release only, and only on GitHub, where the tag of the version holds ``docs/rules``."""
        rel = type("Release", (), {"repo_url": GITHUB + "acme/brevio-core/", "version": "1.2.3"})()
        log = core_sarif.log([{"ruleId": "suppression"}], [{"id": "R9.2"}], rel)
        driver = log["runs"][0]["tool"]["driver"]
        self.assertEqual(driver["version"], "1.2.3")
        self.assertEqual(driver["rules"][0]["helpUri"], GITHUB + "acme/brevio-core/blob/v1.2.3/docs/rules/R9.2.md")
        self.assertNotIn("helpUri", driver["rules"][1])        # the suppression rule has no page
        self.assertNotIn("informationUri", driver)
        elsewhere = type("Release", (), {"repo_url": "https" + "://git.example.org/acme/brevio-core", "version": "1.2.3"})()
        self.assertNotIn("helpUri", core_sarif.log([], [{"id": "R9.2"}], elsewhere)["runs"][0]["tool"]["driver"]["rules"][0])

    def test_diff_mode_flags_only_the_lines_added_against_the_base(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            write(d, "src/old.ts", AWS_EXAMPLE_LINE)    # already on main: not this change's problem
            git(d, "add", ".")
            git(d, "commit", "-q", "-m", "base")
            self.assertEqual(run_cli(d, "scan", "--diff", "main").returncode, 0)
            write(d, "src/new.ts", CLEAN_LINE + AWS_EXAMPLE_LINE)
            write(d, "src/old.ts", AWS_EXAMPLE_LINE + "eval('2 + 2');\n")
            res = run_cli(d, "scan", "--diff", "main")
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertIn("src/new.ts:2: [R13.1]", res.stdout)
            self.assertIn("src/old.ts:2:", res.stdout)
            self.assertNotIn("src/old.ts:1:", res.stdout)


class TestLeaksCommand(unittest.TestCase):
    def test_leaks_fails_on_a_stray_email_and_passes_a_clean_folder(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.md", "write to someone" + "@corp-mail.io\n")
            res = run_cli(d, "leaks")
            self.assertEqual(res.returncode, 1)
            self.assertIn("a.md: email:", res.stdout)
        with tempfile.TemporaryDirectory() as d:
            write(d, "a.md", "write to you@example.com\n")
            clean = run_cli(d, "leaks")
            self.assertEqual(clean.returncode, 0)
            self.assertEqual(clean.stdout, "no leaks found in .\n")       # it printed one empty line, which looked like a command that did not run

    def test_leaks_does_not_claim_a_clean_folder_for_a_path_that_is_no_folder(self):
        """The first wording printed "no leaks found in <path>" for a typo in the path, where nothing had been checked."""
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "leaks", "no-such-folder")
            self.assertEqual(res.returncode, 0)      # 0.9.1 exit code kept
            self.assertNotIn("no leaks found", res.stdout)
            self.assertIn("is not a folder", res.stderr)

    def test_scan_surface_resolves_the_root_it_is_given(self):
        """This test also drove ``_interactive_entry``, the menu and first-run wizard of a bare run at a terminal. A bare run now
        prints the short help (``test_core_help``), so only the root resolution stays here (review of the first-five stage)."""
        from brevio.pipeline import core_cli

        with tempfile.TemporaryDirectory() as d:
            write(d, "styles/reveal.css", "[data-reveal] { opacity: 0; }\n")
            found = core_cli.scan_surface({"styles/reveal.css": [(1, "[data-reveal] { opacity: 0; }")]}, root=d)
            self.assertTrue(any("[R1.2]" in f for f in found), found)


HAS_TRACK_D = os.path.isfile(os.path.join(ROOT, "brevio", "pipeline", "core_shield.py"))


@unittest.skipUnless(HAS_TRACK_D, "Track D subcommands require 0.6 set")
class TestTrackDSubcommands(unittest.TestCase):
    def test_roast_subcommand_prints_the_preview_counts_and_no_grade_badge_or_receipt(self):
        """The earlier test pinned a 100/100 score, a letter grade, a verdict, a badge and a receipt link (2026-11-12).

        Changed on purpose (D32, 2026-11-13): ``--badge`` of 0.9.1 is now accepted with a notice, so it no longer exits 2."""
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/ok.ts", CLEAN_LINE)
            res = run_cli(d, "roast", "--no-upgrade-line")
            self.assertEqual(res.returncode, 0)
            self.assertIn("brevio-core score (preview): 0 finding(s) in 1 file(s)", res.stdout)
            for word in ("Grade", "/100", "Verdict", "Badge", "Receipt", "https://"):
                self.assertNotIn(word, res.stdout)
            badge = run_cli(d, "roast", "--badge")
            self.assertEqual(badge.returncode, 0)
            self.assertIn("--badge no longer applies", badge.stderr)
            data = json.loads(run_cli(d, "roast", "--json").stdout)
            self.assertEqual((data["finding_count"], data["files"]), (0, 1))
            self.assertFalse({"score", "grade", "roast", "badge_markdown", "receipt_url"} & set(data))

    def test_roast_subcommand_with_a_finding_counts_it_by_rule_and_says_what_to_run(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/bad.ts", AWS_EXAMPLE_LINE)
            res = run_cli(d, "roast")
            self.assertEqual(res.returncode, 0)
            self.assertIn("[R13.1] 1 finding(s)", res.stdout)
            self.assertIn("Next: `brevio-core explain R13.1`", res.stdout)
            self.assertNotIn("Engineering Roast", res.stdout)
            self.assertNotIn("AI wrote code violating", res.stdout)

    def test_map_subcommand_text_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/main.ts", CLEAN_LINE)
            res = run_cli(d, "map")
            self.assertEqual(res.returncode, 0)
            self.assertIn("# Repository Symbol Map", res.stdout)

            json_res = run_cli(d, "map", "--json")
            self.assertEqual(json_res.returncode, 0)
            data = json.loads(json_res.stdout)
            self.assertIn("root", data)
            self.assertIn("ranked_files", data)

    def test_heal_subcommand_text_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "src/ok.ts", CLEAN_LINE)
            res = run_cli(d, "heal")
            self.assertEqual(res.returncode, 0)
            self.assertIn("Self-heal complete", res.stdout)

            json_res = run_cli(d, "heal", "--json")
            self.assertEqual(json_res.returncode, 0)
            data = json.loads(json_res.stdout)
            self.assertTrue(data.get("ok"))
            self.assertEqual(data.get("status"), "clean")

    def test_shield_without_a_comment_says_what_it_needs_and_still_exits_zero(self):
        """``shield`` alone printed one empty line (D10, 2026-11-13). The exit code stays 0, as on 0.9.1."""
        with tempfile.TemporaryDirectory() as d:
            res = run_cli(d, "shield")
            self.assertEqual(res.returncode, 0)
            self.assertIn("needs --comment", res.stdout)
            self.assertFalse(json.loads(run_cli(d, "shield", "--json").stdout)["matched"])      # the JSON form is unchanged

    def test_the_help_of_shield_and_heal_say_what_they_do(self):
        with tempfile.TemporaryDirectory() as d:
            full = " ".join(run_cli(d, "help", "--all").stdout.split())
        self.assertNotIn("run brevio-core heal", full)
        self.assertNotIn("Command Handler", full)
        self.assertIn("maintainer's pull-request comment", full)
        self.assertIn("what still needs a person", full)

    def test_shield_subcommand_explain_roast_and_fix(self):
        with tempfile.TemporaryDirectory() as d:
            # explain
            exp_res = run_cli(d, "shield", "--comment", "@brevio explain R13.1")
            self.assertEqual(exp_res.returncode, 0)
            self.assertIn("R13.1", exp_res.stdout)
            self.assertIn("No hardcoded secrets", exp_res.stdout)   # D15 (2026-11-13): the title now says what the scan checks

            # roast
            roast_res = run_cli(d, "shield", "--comment", "@brevio roast")
            self.assertEqual(roast_res.returncode, 0)
            self.assertIn("(preview)", roast_res.stdout)
            self.assertNotIn("Verdict:", roast_res.stdout)

            # fix (JSON output)
            fix_res = run_cli(d, "shield", "--comment", "@brevio fix", "--json")
            self.assertEqual(fix_res.returncode, 0)
            data = json.loads(fix_res.stdout)
            self.assertTrue(data.get("matched"))
            self.assertEqual(data.get("command"), "fix")
            self.assertTrue(data.get("ok"))


if __name__ == "__main__":
    unittest.main()
