"""The agent rules block of ``init``: short, and with one pinned command when the release is known (2026-11-12, improvement plan).

Standard library only: the exporter copies this file beside the code and it runs unchanged there."""
import unittest

from brevio.pipeline import core_agent_block
from brevio.pipeline.gates import agent_skill


class TestTheAgentBlock(unittest.TestCase):
    def test_a_known_release_adds_one_pinned_command_to_the_first_rule_and_nothing_else(self):
        text = core_agent_block.rules("brevio-core", "1.2.3")
        self.assertEqual(text.count("uvx"), 1)
        self.assertIn("`uvx brevio-core==1.2.3 scan <files>`", text)
        self.assertEqual(text.replace(" If it is not installed, run `uvx brevio-core==1.2.3 scan <files>`.", ""), agent_skill.AGENT_RULES)

    def test_an_unknown_release_gets_the_rules_unchanged_because_there_is_nothing_to_pin(self):
        self.assertEqual(core_agent_block.rules("brevio-core", None), agent_skill.AGENT_RULES)

    def test_the_rules_stay_short_and_hold_no_word_that_promises_a_result(self):
        text = core_agent_block.rules("brevio-core", "1.2.3")
        self.assertLessEqual(len(text.splitlines()), 5)
        self.assertLess(len(text), 600)
        for word in ("guarantee", "always", "every", "all ", "100%"):
            self.assertNotIn(word, text.lower())


if __name__ == "__main__":
    unittest.main()
