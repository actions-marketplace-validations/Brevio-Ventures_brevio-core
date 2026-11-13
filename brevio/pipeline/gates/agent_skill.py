"""The agent skill text of the ``brevio-core`` package (ROADMAP E16, the 0.2 set, 2026-11-05).

One source for the skill file: ``hook install`` writes it into each coding agent's skills folder, and the exporter
ships the same text as ``skills/brevio-core/SKILL.md`` (the core exporter). The format is the common
agent-skills one: YAML frontmatter with ``name`` and ``description``, then plain Markdown. Standard library only.
The frontmatter keeps to the portable keys (name, description, license, compatibility, metadata). A test
(``tests/pipeline/core_export/test_core_skill.py``) checks that every command the text names exists. ``AGENT_RULES``
is also the block ``brevio-core init`` writes into ``AGENTS.md`` and a Cursor rule, so the three cannot drift (ROADMAP
E22, 2026-11-05).
"""
from __future__ import annotations

import importlib
import json
import os
import re
from typing import Any

SKILL_PATH = "skills/brevio-core/SKILL.md"

LEARNED_START = "<!-- BREVIO_LEARNED_GUARDS_START -->"
LEARNED_END = "<!-- BREVIO_LEARNED_GUARDS_END -->"

_UNSAFE_PATTERNS = [
    r"(?:curl|wget)\s+[^\n|]+\|\s*(?:ba)?sh\b",
    r"rm\s+-(?:r[fF]|fr)\s+/",
    r"\beval\s*\(",
    r"`[^`\n]+`",
    r"\$\([^)\n]+\)",
    r"\bsk-[A-Za-z0-9]{16,}\b",
    r"\bghp_[A-Za-z0-9]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"ignore\s+(?:all\s+)?previous\s+instructions",
    r"disregard\s+(?:the\s+)?system\s+prompt",
    r"you\s+are\s+now\s+in\s+developer\s+mode",
]
_UNSAFE_RE = re.compile("|".join(f"(?:{p})" for p in _UNSAFE_PATTERNS), re.IGNORECASE)


def _get_memory_sanitizer() -> Any:
    top = __name__.split(".")[0]
    try:
        mod = importlib.import_module(f"{top}.pipeline.core_memory")
        return getattr(mod, "sanitize_memory_text", None)
    except (ImportError, AttributeError):
        return None


def _sanitize(text: str) -> tuple[bool, str]:
    fn = _get_memory_sanitizer()
    if fn is not None:
        return fn(text)
    if not isinstance(text, str):
        return True, ""
    match = _UNSAFE_RE.search(text)
    if match:
        cleaned = _UNSAFE_RE.sub("", text)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        return False, cleaned
    return True, text.strip()


AGENT_RULES = """- After you edit code, scan the files you changed: `brevio-core scan <files>`.
- Fix a finding in the code itself, also one the agent hook reports after an edit or before you stop.
- Never add an ignore comment and never edit `.brevio/baseline.json` to make a finding go away.
- When you cannot fix a finding, stop and tell the human the rule and the line.
- After you fix a bug, run `brevio-core learn` so the fix becomes a guard.
"""

SKILL_MD = f"""---
name: brevio-core
description: Check code you wrote or changed against deterministic code rules, with no model call; `scan`, the agent hook and the MCP server use no network and send nothing. Use it after editing files, before running tests or opening a pull request, and after fixing a bug you want to stop coming back.
license: Apache-2.0
compatibility: Needs Python 3.10 or newer and the brevio-core package (pip install brevio-core, or run it with uvx).
metadata: {{"version": "0.2", "openclaw": {{"homepage": "https://brevio.pro", "requires": {{"bins": ["brevio-core"]}}}}}}
---

# brevio-core

A local rule gate for the mistakes coding agents keep repeating. It reads the lines a change adds and checks them
against fixed code rules. It makes no model call and needs no API key; `scan`, the agent hook and the MCP server use
no network and send nothing.

## When to run it

- After you edit or create code files: scan what you touched.
- Before you run the test suite or open a pull request: scan the whole change.
- After you fix a bug that escaped: compile the fix into a guard, so a scan reports the line if it comes back.

## Commands

```
brevio-core scan src/                  # files or folders
brevio-core scan --diff origin/main    # only the lines added against a base
brevio-core scan --diff --sarif out.sarif
brevio-core learn                      # compile the last commit's fix into a guard
brevio-core learn --check src/a.py     # run the learned guards over whole files (scan reports them too)
brevio-core add                        # list the curated guard packs
brevio-core hook install               # wire the scan into the coding agents found here
brevio-core gain                       # print the counts the local ledger recorded
brevio-core mcp                        # serve these tools over MCP on stdin and stdout
```

If the command is not on the path, put `uvx` in front of it.

The MCP server offers `scan`, `explain`, `list_rules` and `learn` as tools, so an agent can call them without a shell.

## How to read findings

Each finding is one line: `path:line: [RULE] message`. The exit code is 0 when nothing is found, 1 when there are
findings and 2 for a usage error.

- Fix the code the line points at. An agent cannot silence a rule: the hook and the MCP tools do not honour an ignore
  comment, and one added to a pull request is reported until a human reviews it. A human can, with a reason.
- Run `brevio-core mcp` and call `explain` with the rule id, or open `docs/rules/<RULE>.md`, to see what the rule
  catches, why, and a bad and a good example.
- Scan again after the fix. Stop when the exit code is 0.
- A finding in a line you did not add is not yours to fix in this change: scan with `--diff` to see only your lines.

## Rules

{AGENT_RULES}"""


def sync_learned_guards_to_skill(root: str = ".") -> dict[str, Any]:
    """Sync active guards and recent safe lessons to SKILL.md and cursor rules."""
    guards_file = os.path.join(root, ".brevio", "guards.json")
    active_items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    if os.path.isfile(guards_file):
        try:
            with open(guards_file, encoding="utf-8") as fh:
                gdata = json.load(fh)
            raw = gdata.get("guards", {})
            items = raw if isinstance(raw, list) else list(raw.values()) if isinstance(raw, dict) else []
            for g in items:
                if isinstance(g, dict) and g.get("status", "active") in ("active", "trial"):
                    gid = g.get("id") or "guard"
                    desc = g.get("title") or g.get("description") or (", ".join(g.get("patterns", []))) or "Active invariant guard"
                    pair = (str(gid), str(desc))
                    if pair not in seen:
                        seen.add(pair)
                        active_items.append({"id": str(gid), "desc": str(desc)})
        except Exception:
            pass

    memory_file = os.path.join(root, ".brevio", "memory.jsonl")
    if os.path.isfile(memory_file):
        try:
            with open(memory_file, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = entry.get("text") or (f"{entry.get('trap', '')} -> {entry.get('fix', '')}" if entry.get("trap") else "")
                    is_safe, cleaned = _sanitize(text)
                    if not is_safe or not cleaned:
                        continue
                    lid = entry.get("rule_id") or entry.get("id") or entry.get("file") or "lesson"
                    pair = (str(lid), str(cleaned))
                    if pair not in seen:
                        seen.add(pair)
                        active_items.append({"id": str(lid), "desc": str(cleaned)})
        except Exception:
            pass

    section_lines = [
        LEARNED_START,
        "## Repository-Specific Learned Invariants (Auto-Evolved)",
    ]
    for item in active_items:
        section_lines.append(f"- **[{item['id']}]**: {item['desc']}")
    section_lines.append(LEARNED_END)
    section_block = "\n".join(section_lines)

    targets = [
        os.path.join("skills", "brevio-core", "SKILL.md"),
        os.path.join(".cursor", "rules", "brevio-core.mdc"),
    ]
    updated_files: list[str] = []
    pattern = re.compile(
        re.escape(LEARNED_START) + r".*?" + re.escape(LEARNED_END),
        re.DOTALL,
    )

    for rel in targets:
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8") as fh:
                content = fh.read()
            if LEARNED_START in content and LEARNED_END in content:
                new_content = pattern.sub(section_block, content)
            else:
                new_content = content.rstrip() + "\n\n" + section_block + "\n"
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(new_content)
            updated_files.append(rel.replace(os.sep, "/"))
        except OSError:
            pass

    return {
        "updated_files": updated_files,
        "guards_synced": len(active_items),
    }
