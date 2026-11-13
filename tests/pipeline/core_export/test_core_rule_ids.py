"""Rule IDs are permanent, and a rule's status decides whether it fails a scan (ROADMAP E33).

Standard library only: the exporter copies this file beside the code (the 0.6 set) and it runs unchanged there. In the
harness the test first exports the 0.6 set and runs against that copy. ``rule_ids.json`` holds the IDs of the last
release; this test fails when an ID in it has disappeared, comes back with another title or has left ``deprecated``.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EXPORTER = os.path.join(ROOT, "tools", "export_core.py")      # present in the harness checkout only
TREE = ROOT
_EXPORT = tempfile.TemporaryDirectory()
TS_IGNORE = "// @ts-ignore\n"
# The start of a snippet run in the exported tree; ``s`` is the stability module.
_PY = ("import json, sys\n"
       "sys.path.insert(0, sys.argv[1])\n"
       "from brevio.pipeline.code_rules import stability as s\n")
_CLI = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
# The CLI with one rule's status changed first, as a later release would change it.
_STATUS = ("import sys\n"
           "sys.path.insert(0, sys.argv[1])\n"
           "from brevio.pipeline import code_rules, core_cli\n"
           "code_rules.RULE_STATUS[sys.argv[2]] = sys.argv[3]\n"
           "raise SystemExit(core_cli.main(sys.argv[4:]))\n")


def setUpModule():
    global TREE
    if os.path.isfile(EXPORTER):
        out = os.path.join(_EXPORT.name, "core")
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.6"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _EXPORT.cleanup()


def py(code):
    res = subprocess.run([sys.executable, "-I", "-c", _PY + code, TREE], capture_output=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def with_status(cwd, rule, status, *args):
    return subprocess.run([sys.executable, "-I", "-c", _STATUS, TREE, rule, status, *args], cwd=cwd, capture_output=True,
                          encoding="utf-8", timeout=60)


def rules(**entries):
    return {rid.replace("_", "."): {"title": title, "status": status} for rid, (title, status) in entries.items()}


class TestTheRecordedIds(unittest.TestCase):
    def test_every_recorded_id_is_still_in_the_catalogue_with_its_title(self):
        got = py("print(json.dumps({'problems': s.problems(s.load(), s.current()), 'recorded': len(s.load()),"
                 " 'statuses': sorted({e['status'] for e in s.current().values()})}))")
        self.assertEqual(got["problems"], [])
        self.assertGreater(got["recorded"], 1)
        self.assertTrue(set(got["statuses"]) <= {"stable", "preview", "deprecated"})

    def test_the_id_file_is_one_rule_per_line_and_reads_back(self):
        got = py("import os, tempfile\n"
                 "p = os.path.join(tempfile.mkdtemp(), 'ids.json')\n"
                 "open(p, 'w').write(s.render(s.current()))\n"
                 "text = open(p).read()\n"
                 "print(json.dumps({'same': s.load(p) == s.current(), 'lines': text.count('\\n'), 'n': len(s.current()),"
                 " 'committed': s.render(s.load()) == open(s.IDS_FILE).read()}))")
        self.assertTrue(got["same"])
        self.assertEqual(got["lines"], got["n"] + 5)            # the braces, the format line and one line per rule
        self.assertTrue(got["committed"], "rule_ids.json is not in the form render() writes")

    def test_the_id_file_holds_the_catalogue_but_for_a_title_that_was_reworded_and_a_rule_added_since_the_release(self):
        """Changed on purpose (2026-11-13): the file was compared with the catalogue whole. A title reworded to what its
        scan checks (``REWORDED``) stays as the last release recorded it until ``--write``, so only that line may differ.
        Changed again for B2: the file holds what the last release held, so a rule added since is not in it. It is a preview
        rule, and the release notes list it under Added; the catalogue holds no rule that the file has and it lacks."""
        got = py("rec, cur = s.load(), s.current()\n"
                 "added = s.notes(rec, cur).split('Added:\\n\\n')[1].split('\\n\\n')[0] if set(cur) - set(rec) else ''\n"
                 "print(json.dumps({'gone': sorted(set(rec) - set(cur)),"
                 " 'status': [r for r in cur if r in rec and rec[r]['status'] != cur[r]['status']],"
                 " 'title': [r for r in cur if r in rec and rec[r]['title'] != cur[r]['title'] and s.REWORDED.get(r) != rec[r]['title']],"
                 " 'unlisted': [r for r in cur if r not in rec and (cur[r]['status'] != 'preview' or '- ' + r + ' ' not in added)],"
                 " 'reworded': sorted(r for r in s.REWORDED if r in rec and rec[r]['title'] == cur[r]['title'])}))")
        self.assertEqual(got["gone"], [], "an ID of rule_ids.json has left the catalogue")
        self.assertEqual(got["status"], [])
        self.assertEqual(got["title"], [])
        self.assertEqual(got["unlisted"], [], "a rule of the catalogue that the file lacks must be preview and listed as added")
        self.assertEqual(got["reworded"], [], "REWORDED names a rule whose title was not reworded: empty it after --write")

    def test_every_title_change_since_the_release_is_a_declared_reword_against_the_recorded_line(self):
        """D15 (2026-11-13). Here and not in test_core_rules: that file runs in every export set, and the stability
        module ships in the 0.6 set only."""
        got = py("rec, cur = s.load(), s.current()\n"
                 "changed = [r for r in cur if r in rec and rec[r]['title'] != cur[r]['title']]\n"
                 "print(json.dumps({'undeclared': [r for r in changed if s.REWORDED.get(r) != rec[r]['title']],"
                 " 'problems': s.problems(rec, cur)}))")
        self.assertEqual(got["undeclared"], [])
        self.assertEqual(got["problems"], [])

    def test_the_release_notes_list_the_reworded_titles_and_the_new_rule(self):
        got = py("print(json.dumps(s.notes(s.load(), s.current())))")
        self.assertIn("Reworded (the ID and the status are unchanged; a note below says where the findings changed):", got)
        self.assertIn("R10.1 Explicit HTTP request timeouts: Python requests calls pass timeout=", got)
        self.assertIn("R10.9 Timeouts on fetch and axios", got)

    def test_a_reworded_title_is_accepted_only_against_the_line_the_release_recorded(self):
        got = py("old = 'Old: promises more'\n"
                 "s.REWORDED['R1.1'] = old\n"
                 "ok = s.problems({'R1.1': {'title': old, 'status': 'stable'}}, {'R1.1': {'title': 'New: what it checks', 'status': 'stable'}})\n"
                 "other = s.problems({'R1.1': {'title': 'Third: another line', 'status': 'stable'}}, {'R1.1': {'title': 'New: what it checks', 'status': 'stable'}})\n"
                 "print(json.dumps({'ok': ok, 'other': other}))")
        self.assertEqual(got["ok"], [])
        self.assertEqual(len(got["other"]), 1)
        self.assertIn("reused with another meaning", got["other"][0])

    def test_a_broken_id_file_is_refused(self):
        got = py("import os, tempfile\n"
                 "p = os.path.join(tempfile.mkdtemp(), 'ids.json')\n"
                 "open(p, 'w').write(json.dumps({'format': 1, 'rules': {'R1.1': {'title': 'x', 'status': 'gone'}}}))\n"
                 "try:\n    s.load(p)\n    print(json.dumps('read'))\n"
                 "except ValueError as exc:\n    print(json.dumps(str(exc)))")
        self.assertIn("not a format 1 rule ID file", got)


class TestThePolicy(unittest.TestCase):
    def test_a_removed_a_reused_and_a_revived_id_and_a_new_stable_rule_are_refused(self):
        before = rules(R1_1=("Old: a", "stable"), R1_2=("Two: b", "stable"), R1_3=("Three: c", "deprecated"),
                       R1_4=("Four: d", "stable"))
        now = rules(R1_2=("Other: z", "stable"), R1_3=("Three: c", "stable"), R1_4=("Four: d", "stable"),
                    R1_5=("New: e", "stable"), R1_6=("Newer: f", "preview"))
        got = py(f"print(json.dumps(s.problems({before!r}, {now!r})))")
        self.assertEqual(len(got), 4, got)
        self.assertTrue(got[0].startswith("R1.1: removed"))
        self.assertTrue(got[1].startswith("R1.2: reused with another meaning"))
        self.assertTrue(got[2].startswith("R1.3: was deprecated"))
        self.assertTrue(got[3].startswith("R1.5: a new rule ships as preview first"))

    def test_retiring_a_rule_and_promoting_a_preview_rule_are_allowed(self):
        before = rules(R1_1=("One: a", "stable"), R1_2=("Two: b", "preview"))
        now = rules(R1_1=("One: a", "deprecated"), R1_2=("Two: b", "stable"))
        self.assertEqual(py(f"print(json.dumps(s.problems({before!r}, {now!r})))"), [])

    def test_the_release_notes_list_the_rules_added_changed_and_retired(self):
        before = rules(R1_1=("One: a", "stable"), R1_2=("Two: b", "preview"), R1_3=("Three: c", "stable"))
        now = rules(R1_1=("One: a", "deprecated"), R1_2=("Two: b", "stable"), R1_3=("Three: c", "stable"),
                    R1_4=("Four: d", "preview"))
        notes = py(f"print(json.dumps(s.notes({before!r}, {now!r})))")
        self.assertIn("Added:\n\n- R1.4 Four: d (preview)", notes)
        self.assertIn("Changed:\n\n- R1.2 Two: b (preview -> stable)", notes)
        self.assertIn("Retired:\n\n- R1.1 One: a (deprecated; the ID stays reserved)", notes)
        self.assertIn("No rule was added", py(f"print(json.dumps(s.notes({now!r}, {now!r})))"))

    def test_the_release_notes_carry_a_note_for_a_rule_whose_findings_moved_to_it(self):
        now = rules(R1_1=("One: a", "stable"), R1_2=("Two: b", "stable"))
        notes = py(f"print(json.dumps(s.notes({now!r}, {now!r}, {{'R1.2': 'was R1.1', 'R9.9': 'gone'}})))")
        self.assertIn("No rule was added, changed or retired since the last release.\n\nNotes:\n\n- R1.2 Two: b: was R1.1\n",
                      notes)
        self.assertNotIn("R9.9", notes)
        self.assertIn("- R2.8 No unsafe code or HTML sinks", py("print(json.dumps(s.notes(s.current(), s.current())))"))


class TestStatusAtScanTime(unittest.TestCase):
    def test_a_preview_rule_reports_its_finding_but_never_fails_the_scan(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.ts"), "w", encoding="utf-8") as fh:
                fh.write(TS_IGNORE)
            runs = [with_status(d, "R9.2", "preview", "scan", "--fail-on", mode) for mode in ("new", "all", "error")]
            stable = with_status(d, "R9.2", "stable", "scan")
        for res in runs:
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("[R9.2]", res.stdout)
            self.assertIn("1 at warn level", res.stderr)
        self.assertEqual(stable.returncode, 1)

    def test_a_deprecated_rule_reports_nothing_and_is_not_listed_by_config(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.ts"), "w", encoding="utf-8") as fh:
                fh.write(TS_IGNORE)
            res = with_status(d, "R9.2", "deprecated", "scan")
            cfg = with_status(d, "R9.2", "deprecated", "config", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("[R9.2]", res.stdout)
        self.assertNotIn("R9.2", json.loads(cfg.stdout)["rules"])

    def test_each_rule_page_states_the_rule_status(self):
        page = os.path.join(TREE, "docs", "rules", "R9.2.md")
        with open(page, encoding="utf-8") as fh:
            self.assertIn("Status: stable.", fh.read())

    def test_a_sleep_in_a_test_is_reported_but_never_fails_the_scan_and_its_page_says_why(self):
        """R6.3 is preview since the E35 precision run: most waits it flagged were the test itself (2026-11-05)."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "popup.test.ts"), "w", encoding="utf-8") as fh:
                fh.write("await page.waitForTimeout(500);\nexpect(popups).toHaveLength(0);\n")
            res = subprocess.run([sys.executable, "-I", "-c", _CLI, TREE, "scan", "--fail-on", "all"], cwd=d,
                                 capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("popup.test.ts:1: [R6.3]", res.stdout)
        self.assertEqual(py("print(json.dumps(s.current()['R6.3']['status']))"), "preview")
        with open(os.path.join(TREE, "docs", "rules", "R6.3.md"), encoding="utf-8") as fh:
            self.assertIn("Status: preview. In a precision run on open-source repositories", fh.read())


if __name__ == "__main__":
    unittest.main()
