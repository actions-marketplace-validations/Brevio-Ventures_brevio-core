"""The rule pages match the rule definitions (ROADMAP 8.2 item 11, 2026-11-03).

Ships with the portable rule engines: it imports only the standard library and the exported modules, and
finds the pages in ``docs/rules`` beside the code, in the harness and in the exported tree alike.
"""
import json
import os
import re
import shutil
import tempfile
import unittest

from brevio.pipeline import dev_rules
from brevio.pipeline.code_rules import PR_RULES_122, docs, scan_added_lines_for_rules
from brevio.pipeline.code_rules.details import DETAILS, SCANS

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "docs", "rules")


def _findings(rule_id: str, text: str) -> list[str]:
    engine, path = SCANS[rule_id]
    lines = text.splitlines()
    if engine == "dev":
        found = dev_rules._scan_single_file(path, text, list(enumerate(lines, 1)))
    else:
        found = scan_added_lines_for_rules({path: lines})
    return [f for f in found if re.search(rf"\[{re.escape(rule_id)}[ \]]", f)]


class TestEveryRuleHasItsFields(unittest.TestCase):
    def test_the_details_name_exactly_the_rules_in_the_catalogue(self):
        self.assertEqual(sorted(set(PR_RULES_122) - set(DETAILS)), [], "a rule has no why/bad/good")
        self.assertEqual(sorted(set(DETAILS) - set(PR_RULES_122)), [], "details for a rule that does not exist")

    def test_each_rule_says_why_and_shows_a_bad_and_a_good_example(self):
        for rid, d in DETAILS.items():
            with self.subTest(rule=rid):
                self.assertTrue(d.why.strip() and d.bad.strip() and d.good.strip())
                self.assertNotEqual(d.bad, d.good)

    def test_every_rule_of_the_dev_scan_catalogue_is_in_the_full_catalogue(self):
        self.assertEqual(sorted(set(dev_rules.CATALOG_RULES) - set(PR_RULES_122)), [])

    def test_a_scanned_rule_names_a_real_rule(self):
        self.assertEqual(sorted(set(SCANS) - set(PR_RULES_122)), [])


class TestExamplesOfScannedRules(unittest.TestCase):
    def test_the_bad_example_is_flagged_and_the_good_one_is_not(self):
        for rid in sorted(SCANS):
            with self.subTest(rule=rid):
                self.assertTrue(_findings(rid, DETAILS[rid].bad), f"{rid}: the bad example is not flagged")
                self.assertEqual(_findings(rid, DETAILS[rid].good), [], f"{rid}: the good example is flagged")


class TestThePagesMatchTheDefinitions(unittest.TestCase):
    def test_the_committed_pages_are_complete_current_and_have_no_orphan(self):
        self.assertEqual(docs.problems(PAGES), [], "regenerate: python -m brevio.pipeline.code_rules.docs --out docs/rules")

    def test_a_missing_page_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(PAGES, os.path.join(d, "r"))
            os.remove(os.path.join(d, "r", "R5.4.md"))
            self.assertEqual(docs.problems(os.path.join(d, "r")), ["missing: R5.4.md"])

    def test_a_page_for_a_rule_that_does_not_exist_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(PAGES, os.path.join(d, "r"))
            with open(os.path.join(d, "r", "R99.1.md"), "w", encoding="utf-8") as f:
                f.write("# R99.1\n")
            self.assertEqual(docs.problems(os.path.join(d, "r")), ["orphan: R99.1.md names no rule"])

    def test_an_edited_page_is_reported_as_stale(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(PAGES, os.path.join(d, "r"))
            with open(os.path.join(d, "r", "R5.4.md"), "a", encoding="utf-8") as f:
                f.write("extra\n")
            self.assertEqual(len(docs.problems(os.path.join(d, "r"))), 1)

    def test_a_page_holds_every_field_the_roadmap_asks_for(self):
        page = docs.render("R5.4")
        for head in ("## What it catches", "## Why", "## A bad example", "## A good example", "## How to silence it"):
            self.assertIn(head, page)
        self.assertNotIn("brevio.pro", page)       # the playground link was removed: that page is not shown in this repository (2026-11-12)
        self.assertNotIn("Playground", page)

    def test_a_page_is_a_search_landing_page_with_the_answer_first(self):
        text = docs.page("R9.2")
        front, _sep, body = text[4:].partition("\n---\n")
        self.assertTrue(text.startswith("---\n"))
        meta = dict(line.split(": ", 1) for line in front.splitlines())
        self.assertEqual(json.loads(meta["title"]), "Silenced type errors in TypeScript: why and how to fix")
        self.assertEqual(json.loads(meta["description"]), docs.answer("R9.2"))
        self.assertTrue(body.startswith(docs.render("R9.2")))
        self.assertLess(body.index("**Short answer:**"), body.index("## What it catches"))
        self.assertEqual(docs.search_title("R1.1"), "Mobile GPU budget in React and TypeScript: why and how to fix")

    def test_related_rules_are_other_rules_of_the_same_group_with_a_page(self):
        for rid in docs.rule_ids():
            with self.subTest(rule=rid):
                rel = docs.related(rid)
                self.assertNotIn(rid, rel)
                self.assertLessEqual(len(rel), docs.RELATED)
                self.assertTrue(all(r.split(".")[0] == rid.split(".")[0] for r in rel))
                for r in rel:
                    self.assertIn(f"- [{r}]({docs.filename(r)})", docs.render(rid))
                    self.assertTrue(os.path.isfile(os.path.join(PAGES, docs.filename(r))))

    def test_the_json_ld_is_a_tech_article_that_cannot_close_its_script_early(self):
        for rid in docs.rule_ids():
            block = docs.json_ld(rid)
            body = block.split("\n")[1]
            doc = json.loads(body)
            self.assertEqual((doc["@type"], doc["headline"]), ("TechArticle", docs.search_title(rid)))
            self.assertNotIn("</", body)
            self.assertTrue(docs.page(rid).endswith(block))

    def test_the_slice_cards_are_read_from_the_code(self):
        cards = docs.card_ids()
        self.assertTrue({"R1.1", "R1.7", "R2.4", "R3.4", "R4.5", "R6.4", "R7.5", "R9.3"} <= cards)
        self.assertNotIn("R10.1", cards)


if __name__ == "__main__":
    unittest.main()
