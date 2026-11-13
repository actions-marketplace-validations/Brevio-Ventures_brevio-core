"""The regular-expression subset a model may propose for a guard (``brevio-core ai learn``, the 0.7 set, improvement plan,
2026-11-12), tried with patterns that take exponential or polynomial time.

Standard library only. The slow patterns are never matched against a line here unless the check accepted them, so the test
itself cannot hang on one; the accepted ones are matched against the worst line the scan reads, with a time limit.
"""
from __future__ import annotations

import re
import time
import unittest

from brevio.pipeline import core_ai_regex as rx
from brevio.pipeline.gates import guard_scan

EXPONENTIAL = ("(a+)+$", "(a|aa)+b$", "(.*a){20}", r"(\w+\s?)*end_of_it", "(a*)*abcdefg", "(abc|abd)+defghi", "((ab)+c)+defghi",
               "(?:a+)+abcdefg", "(x+x+)+abcdefg", r"(\d+,?)+abcdefg")
BACKREFERENCES = (r"foo(bar)\1baz", r"(abc)(def)\2ghij", "(?P<n>abc)defg(?P=n)", r"abcdefg\1", r"\1abcdefg")
OTHER_FORMS = ("(?=foo)barbazqux", "(?!foo)barbazqux", "(?<=foo)barbazqux", "(?i)barbazqux", "(?P<n>abc)defghi", "(?#note)abcdefg",
               "(?(1)a|b)abcdefg", r"abc\qdef", r"abcdef\Zghi", "abcdefg+?", "abcdefg**", "abcdefg*+", "*abcdefg", "(*abcdefg)")
SHAPE = ("", "   ", " abcdefgh", "abcdefgh ", "ab", "a.b.c.d", "[abc", "abcdefg)", "(abcdefg", "abcdefgh\\", "x" * 121,
         "abcdefgh\x00", "abcdefgh\n", "x{1,500}abcdefg", "a{51}bcdefgh")
UNBOUNDED = ("a*a*a*abcdefg", "a+b+c+d+abcdefg", ".*.*.*.*abcdefg", r"\w+\s+\w+\s+\w+\s+abcdefg", "x{1,}y{2,}z{3,}abcdefg")
MANY_WAYS = ("x.{0,50}x.{0,50}x.{0,50}x.{0,50}x.{0,50}y", "x[a-z]{0,50}x[a-z]{0,50}x[a-z]{0,50}x[a-z]{0,50}x[a-z]{0,50}y",
             "(x|x)" * 16 + "y", "x?" * 16 + "xxxxxxxxy", "x.*x.*x.{0,50}xyzxyz", "(x|xx|xxx|xxxx)" * 12 + "y")
JUST_UNDER = ("(x|x)" * 15 + "y", "x?" * 15 + "xxxxxxxxy", "x.*x.*xyzxyz", "x.{0,50}x.{0,50}xyzxyz", "(x|xx|xxx|xxxx)" * 7 + "y")
ACCEPTED = (r"gateway\.submit\(card, amount, \w+\)", "foo.*barbaz.*qux", "abcdef(?:ghi|jkl)", "abcdefg{,}", r"\bretry\(\w+, 0\)",
            "[A-Z]{3}_KEY = abcdef", "console\\.log\\(.{0,40}\\)", "^import abcdefg$", r"a*a*abcdefg")


class TestWhatIsRefused(unittest.TestCase):
    def refused(self, patterns):
        for pattern in patterns:
            self.assertTrue(rx.problem(pattern), pattern)

    def test_a_repeated_group_that_holds_a_repeat_or_an_alternative_is_refused(self):
        self.refused(EXPONENTIAL)

    def test_a_backreference_is_refused(self):
        self.refused(BACKREFERENCES)

    def test_lookaround_named_groups_flags_conditionals_and_unknown_escapes_are_refused(self):
        self.refused(OTHER_FORMS)

    def test_a_pattern_of_the_wrong_shape_too_long_too_short_unbalanced_or_with_an_oversized_count_is_refused(self):
        self.refused(SHAPE)

    def test_more_than_two_repeats_without_an_upper_end_are_refused_where_the_scan_would_allow_three(self):
        self.refused(UNBOUNDED)
        self.assertEqual(guard_scan.risky("a*a*a*abcdefg"), "")      # the scan's own limit is three: this check is stricter
        self.assertEqual(rx.MAX_UNBOUNDED, 2)

    def test_repeats_and_alternatives_that_multiply_into_too_many_ways_to_match_are_refused_though_each_is_bounded(self):
        # review 2026-11-12: the first of these took more than twenty seconds on one line of 200 characters and was accepted
        self.refused(MANY_WAYS)
        self.assertEqual(guard_scan.risky(MANY_WAYS[0]), "")        # the scan's own check lets it through
        self.assertEqual(rx.MAX_PATHS, guard_scan.REGEX_MAX_LINE ** 2)

    def test_a_reason_never_holds_the_pattern(self):
        for pattern in (*EXPONENTIAL, *BACKREFERENCES, *OTHER_FORMS):
            why = rx.problem(pattern)
            self.assertNotIn(pattern, why)

    def test_the_length_cap_is_the_one_of_the_constant(self):
        self.assertEqual(rx.problem("a" * rx.MAX_LENGTH), "")
        self.assertIn(str(rx.MAX_LENGTH), rx.problem("a" * (rx.MAX_LENGTH + 1)))


class TestWhatIsAccepted(unittest.TestCase):
    def test_plain_patterns_with_a_few_repeats_classes_and_alternatives_pass(self):
        for pattern in ACCEPTED:
            self.assertEqual(rx.problem(pattern), "", pattern)
            re.compile(pattern)

    def test_the_patterns_the_check_accepts_match_the_worst_line_the_scan_reads_in_a_fraction_of_a_second(self):
        hostile = ["a" * guard_scan.REGEX_MAX_LINE, "a" * (guard_scan.REGEX_MAX_LINE - 1) + "!", ("ab " * 70)[:guard_scan.REGEX_MAX_LINE],
                   "x" * guard_scan.REGEX_MAX_LINE, "{" * guard_scan.REGEX_MAX_LINE]
        for pattern in (*ACCEPTED, *JUST_UNDER):
            self.assertEqual(rx.problem(pattern), "", pattern)
            compiled = re.compile(pattern)
            for line in hostile:
                began = time.perf_counter()
                compiled.search(line)
                self.assertLess(time.perf_counter() - began, 1.0, pattern)

    def test_python_reads_a_brace_with_no_number_as_text_and_one_with_a_comma_as_a_repeat_and_so_does_the_check(self):
        self.assertEqual(rx.problem("abcdefg{}"), "")                  # ``{}`` is text
        self.assertTrue(rx.problem("(abcdefg+){,5}"))                  # ``{,5}`` repeats a group that holds a repeat
        self.assertTrue(rx.problem("abcdefg{,}{,}"))
        self.assertEqual(re.compile("abcdefg{,}").pattern, "abcdefg{,}")


class TestTheScanStillHoldsItsOwnLimit(unittest.TestCase):
    def test_a_pattern_this_check_accepts_is_one_the_scan_runs_as_a_regular_expression(self):
        for pattern in ACCEPTED:
            self.assertEqual(guard_scan.regex_problem("re:" + pattern), "", pattern)


if __name__ == "__main__":
    unittest.main()
