"""The shipped agent skill: its frontmatter parses, every command it names exists, and ``hook install`` writes it.

Standard library only: the exporter copies this file beside the code (the 0.2 set) and it runs unchanged there. In the
harness the test first exports the 0.2 set and runs against that copy.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
EXPORTER = os.path.join(ROOT, "tools", "export_core.py")      # present in the harness checkout only
TREE = ROOT
SKILL = "skills/brevio-core/SKILL.md"
_RUN = "import sys; sys.path.insert(0, sys.argv[1]); from brevio.pipeline import core_cli; raise SystemExit(core_cli.main(sys.argv[2:]))"
_COMMAND = re.compile(r"(?:^|[\s`$(])brevio-core\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?", re.M)
_EXPORT = tempfile.TemporaryDirectory()


def setUpModule():
    global TREE
    if os.path.isfile(EXPORTER):
        out = os.path.join(_EXPORT.name, "core")
        subprocess.run([sys.executable, EXPORTER, "--out", out, "--set", "0.2"], check=True, capture_output=True, timeout=120)
        TREE = out


def tearDownModule():
    _EXPORT.cleanup()


def run_cli(cwd, *args):
    return subprocess.run([sys.executable, "-I", "-c", _RUN, TREE, *args], cwd=cwd, capture_output=True, encoding="utf-8", timeout=60)


def read(rel):
    with open(os.path.join(TREE, rel), encoding="utf-8") as fh:
        return fh.read()


def split_frontmatter(text):
    """``(fields, body)`` of a file that opens with a ``---`` block of ``key: value`` lines; the block must close."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("no frontmatter block")
    end = lines.index("---", 1)
    fields = {}
    for ln in lines[1:end]:
        key, sep, value = ln.partition(":")
        if not sep or not key.strip() or key != key.strip():
            raise ValueError(f"not a key: value line: {ln!r}")
        fields[key] = value.strip().strip("'\"")
    return fields, "\n".join(lines[end + 1:])


class TestSkillFile(unittest.TestCase):
    def test_the_frontmatter_has_a_name_and_a_description(self):
        fields, body = split_frontmatter(read(SKILL))
        self.assertEqual(fields["name"], "brevio-core")
        self.assertRegex(fields["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertTrue(20 < len(fields["description"]) <= 1024)
        self.assertEqual(sorted(fields), ["compatibility", "description", "license", "metadata", "name"])   # the portable keys only
        self.assertTrue(body.lstrip().startswith("# brevio-core"))

    def test_the_frontmatter_is_portable_name_is_the_folder_and_the_binary_it_needs_is_the_command(self):
        fields, _body = split_frontmatter(read(SKILL))
        self.assertEqual(fields["name"], os.path.basename(os.path.dirname(SKILL)))
        self.assertLessEqual(len(fields["description"]), 1024)
        self.assertLessEqual(len(fields["compatibility"]), 500)
        self.assertEqual(fields["license"], "Apache-2.0")
        meta = json.loads(fields["metadata"])
        self.assertEqual(meta["openclaw"]["requires"]["bins"], ["brevio-core"])
        self.assertTrue(meta["version"] and meta["openclaw"]["homepage"].startswith("https://"))
        self.assertLess(len(read(SKILL).splitlines()), 500)

    def test_a_broken_frontmatter_is_refused_by_the_parser(self):
        for bad in ("# no block", "---\nname: x\n", "---\nnot a pair\n---\n", "---\n  name: x\n---\n"):
            with self.assertRaises(ValueError, msg=bad):
                split_frontmatter(bad)

    def test_the_skill_has_the_three_parts_an_agent_needs(self):
        body = split_frontmatter(read(SKILL))[1]
        for heading in ("## When to run it", "## Commands", "## How to read findings"):
            self.assertIn(heading, body)

    def test_every_command_it_names_exists_and_answers_help(self):
        body = split_frontmatter(read(SKILL))[1]
        named = {(m.group(1), m.group(2)) for m in _COMMAND.finditer(body)}
        self.assertTrue({"scan", "learn", "hook", "mcp"} <= {c for c, _ in named}, named)
        with tempfile.TemporaryDirectory() as d:
            for command, sub in sorted(named, key=str):
                helped = run_cli(d, command, "--help")
                self.assertEqual(helped.returncode, 0, f"brevio-core {command}: {helped.stderr}")
                if command == "hook" and sub:
                    self.assertIn(sub, helped.stdout + helped.stderr, f"brevio-core hook {sub}")

    def test_every_flag_it_shows_for_scan_exists(self):
        body = split_frontmatter(read(SKILL))[1]
        flags = set(re.findall(r"brevio-core scan[^\n#]*?(--[a-z-]+)", body))
        with tempfile.TemporaryDirectory() as d:
            helped = run_cli(d, "scan", "--help").stdout
        self.assertTrue(flags)
        for flag in flags:
            self.assertIn(flag, helped)

    def test_the_skill_makes_no_numeric_claim(self):
        text = read(SKILL)
        self.assertIsNone(re.search(r"\d[\d.,]*\s*%|\d\s*(?:ms|milliseconds?|seconds?|tokens?)\b|[$€£]\s*\d", text, re.I))


class TestHookInstallWritesIt(unittest.TestCase):
    def test_install_writes_the_shipped_text_for_each_agent_and_replaces_older_skill_text(self):
        with tempfile.TemporaryDirectory() as d:
            for rel in (".hermes/skills/brevio-core/SKILL.md", "skills/brevio-core/SKILL.md"):
                os.makedirs(os.path.join(d, os.path.dirname(rel)))
                with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
                    fh.write("---\nname: brevio-core\ncommands:\n  - scan: old\n---\nolder text\n")
            self.assertEqual(run_cli(d, "hook", "install", "--all").returncode, 0)
            for rel in (".claude/skills/brevio-core/SKILL.md", ".hermes/skills/brevio-core/SKILL.md", "skills/brevio-core/SKILL.md"):
                with open(os.path.join(d, rel), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), read(SKILL), rel)
            self.assertEqual(run_cli(d, "hook", "install", "--all").returncode, 0)
            with open(os.path.join(d, "skills/brevio-core/SKILL.md"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), read(SKILL))

    def test_an_agent_that_is_not_found_gets_no_skill(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".cursor"))
            self.assertEqual(run_cli(d, "hook", "install", "--target", "cursor").returncode, 0)
            self.assertFalse(os.path.exists(os.path.join(d, ".claude")))
            self.assertFalse(os.path.exists(os.path.join(d, "skills")))


if __name__ == "__main__":
    unittest.main()
