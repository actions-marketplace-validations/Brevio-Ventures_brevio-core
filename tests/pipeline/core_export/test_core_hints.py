"""The fix hint of a finding fits the language of its file (D20 and I25, 2026-11-13).

Ships with the portable rule engines: it imports only the standard library and the exported modules.
"""
import unittest

from brevio.pipeline.code_rules import docs, hints, scan_added_lines_for_rules
from brevio.pipeline.code_rules.details import DETAILS

PATHS = {"py": "src/app.py", "ts": "src/app.ts"}


class TestTheHintFitsTheFile(unittest.TestCase):
    def test_a_python_sql_finding_gets_a_python_hint_and_not_the_script_example(self):
        text = hints.fix_text("R2.8", "app/new.py", "Raw SQL string interpolation detected; use parameterized queries")
        self.assertIn("execute(", text)
        self.assertNotIn("JSON.parse", text)

    def test_a_bare_except_gets_a_python_hint_and_an_empty_catch_a_script_one(self):
        self.assertTrue(hints.fix_text("R5.4", "a.py", "Bare `except:` detected; specify explicit exception tuple").startswith("except "))
        self.assertIn("catch (e)", hints.fix_text("R5.4", "a.ts", "Empty catch block detected; handle or log the error"))

    def test_a_finding_with_no_example_in_its_files_language_gets_no_hint(self):
        self.assertEqual(hints.fix_text("R5.4", "a.rs", "Bare `except:` detected"), "")
        self.assertEqual(hints.fix_text("R2.8", "a.py", "Unsafe `eval` or dynamic `Function` constructor detected"), "")
        self.assertEqual(hints.fix_text("R2.8", "a.ts", "Hidden directional or Unicode tag control character detected"), "")
        self.assertEqual(hints.hint("R9.1", "a.py", "Untyped escape hatch"), "")      # a script rule's example, a Python file

    def test_a_vue_or_svelte_file_keeps_the_script_hint_it_had_before(self):
        """Found on review: the language check dropped the hint in the two component formats the scan reads."""
        for path in ("ui/Card.vue", "ui/Card.svelte"):
            with self.subTest(path=path):
                self.assertIn("catch (e)", hints.fix_text("R5.4", path, "Empty catch block detected; handle or log the error"))
                self.assertEqual(hints.fix_text("R9.1", path, "x"), DETAILS["R9.1"].good.strip().splitlines()[0])

    def test_a_rule_with_one_example_keeps_it_in_a_file_of_its_language(self):
        first = DETAILS["R9.1"].good.strip().splitlines()[0]
        self.assertEqual(hints.fix_text("R9.1", "src/a.tsx", "x"), first)
        self.assertEqual(hints.fix_text("R5.5", "tool.py", "x"), DETAILS["R5.5"].good.strip().splitlines()[0])
        self.assertEqual(hints.fix_text("R5.5", "tool.ts", "x"), "")

    def test_every_variant_is_a_line_its_own_rule_does_not_flag(self):
        for v in hints.VARIANTS:
            with self.subTest(rule=v.rule, lang=v.lang, kind=v.kind):
                found = scan_added_lines_for_rules({PATHS[v.lang]: [v.text]})
                self.assertEqual([f for f in found if f"[{v.rule}]" in f], [], v.text)
                self.assertLessEqual(len(v.text), hints.HINT_CHARS)

    def test_every_variant_names_a_finding_its_scanner_makes(self):
        bad = {("R2.8", "py", "SQL string interpolation"): 'cur.execute(f"SELECT * FROM t WHERE n = {n}")',
               ("R2.8", "ts", "SQL string interpolation"): "await db.$queryRawUnsafe(`SELECT * FROM t WHERE n = ${n}`);",
               ("R2.8", "ts", "dangerouslySetInnerHTML"): "<div dangerouslySetInnerHTML={{ __html: html }} />",
               ("R2.8", "ts", "`eval`"): "eval(userInput);",
               ("R5.4", "py", "Bare `except:`"): "except:",
               ("R5.4", "ts", "Empty catch"): "try { save(); } catch (e) {}"}
        for v in hints.VARIANTS:
            with self.subTest(rule=v.rule, lang=v.lang, kind=v.kind):
                path = "src/app.tsx" if "Inner" in v.marker or "dangerously" in v.marker else PATHS[v.lang]
                found = [f for f in scan_added_lines_for_rules({path: [bad[(v.rule, v.lang, v.marker)]]}) if f"[{v.rule}]" in f]
                self.assertEqual(len(found), 1, found)
                self.assertEqual(hints.fix_text(v.rule, path, found[0]), v.text)


class TestTheRulePageAgrees(unittest.TestCase):
    def test_the_page_of_a_rule_with_several_hints_lists_them_and_other_pages_do_not(self):
        page = docs.render("R2.8")
        for v in hints.VARIANTS:
            if v.rule == "R2.8":
                self.assertIn(v.text, page)
        self.assertIn("cur.execute", page)
        self.assertNotIn("The fix depends on", docs.render("R9.1"))


if __name__ == "__main__":
    unittest.main()
