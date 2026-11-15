"""The loader of the optional parts of ``brevio-core`` (split from ``core_cli``, 2026-11-13).

Standard library only: the exporter copies this file beside the code and it runs unchanged in the exported tree."""
import argparse
import unittest

from brevio.pipeline import core_cli, core_sets


class CoreSetsTests(unittest.TestCase):
    def test_a_module_the_build_does_not_ship_loads_as_none(self):
        self.assertIsNone(core_sets.set_module("pipeline", "core_no_such_module"))

    def test_a_missing_package_is_not_hidden_when_the_error_is_another_import(self):
        self.assertIs(core_sets.set_module("pipeline", "core_sets"), core_sets)

    def test_the_names_other_modules_call_stay_importable_from_core_cli(self):
        for name in ("set_module", "line_commands", "up_tools", "upgrade_line", "ai_command", "AI_HELP", "AGENT_MAX"):
            self.assertIs(getattr(core_cli, name), getattr(core_sets, name), name)

    def test_the_command_table_pairs_every_command_with_a_help_line(self):
        tools, helps = core_sets.line_commands()
        self.assertEqual(sorted(tools), sorted(helps))

    def test_an_absent_command_gives_two_empty_tables(self):
        self.assertEqual(core_sets._opt_cmd("x", "pipeline", "core_no_such_module"), ({}, {}))

    def test_the_scan_flags_of_the_baseline_are_added_to_the_scan_parser(self):
        if core_sets.baseline_tools() is None:
            self.skipTest("a build without the 0.4 set has no baseline flags")
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="cmd")
        sc = sub.add_parser("scan")
        core_sets.add_baseline_arguments(sub, sc)
        args = ap.parse_args(["scan", "--fail-on", "all"])
        self.assertEqual(args.fail_on, "all")
        self.assertIn("baseline", sub.choices)


if __name__ == "__main__":
    unittest.main()
