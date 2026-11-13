"""The starter specs ``brevio-core init --specs`` writes, and the stack they are written for (ROADMAP E22).

They were part of ``core_init``, which is over the size a file may have; they are a module of their own so that the text can
depend on what the build ships. The single-task workflow section of ``CLAUDE.md`` names ``connect``, ``plan``, ``build``,
``qa``, ``pr`` and ``task``, so it is written only in a build that has them: a trimmed release leaves those commands out and
the file then names none of them (2026-11-12, improvement plan). The pull-request workflow ``init`` writes is here too, with
the one checkout pin that the README's first screen shares (2026-11-13, revisit D7). Standard library only.
"""
from __future__ import annotations

import json
import os
import re

from brevio.pipeline import core_cli

# Third-party actions are pinned to a commit, not a movable tag (ADOPTION.md rule "everything is pinned", 2026-11-05).
# The release tool reads this pin for the README's first screen, so init and the README name one checkout.
CHECKOUT_PIN = "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5.1.0"
TASK_MODULE = "core_task"       # the module of the single-task commands; absent from a trimmed release
WORKFLOW = (
    "## Single-Task Local Workflow\n"
    "Follow the deterministic task progression in order:\n"
    "1. `brevio-core connect`: Detect stack and coding clients and scaffold specs.\n"
    "2. `brevio-core plan [task]`: Create single-task plan (.brevio/task_plan.json & .brevio/TASK_PLAN.md).\n"
    "3. `brevio-core build`: Execute build preparation and deterministic auto-fixes.\n"
    "4. `brevio-core qa`: Run pre-PR flight gates and count the findings (.brevio/qa_report.json).\n"
    "5. `brevio-core pr`: Verify QA gates and synthesize PR body (.brevio/PR_BODY.md).\n\n"
    "Alternatively, run the entire loop in one step:\n"
    "`brevio-core task [task]`\n\n"
)


def workflow_text(action: str, version: str) -> str:
    """The pull-request workflow: read-only by default, the write scopes on the job alone, pinned exactly.

    ``pull-requests: write`` is for the comment, ``security-events: write`` for the upload to code scanning that
    ``sarif: true`` turns on: the two scopes the README's workflow names (2026-11-13, revisit D7)."""
    return f"""# Written by `brevio-core init`; `brevio-core init --undo` removes it while it is unchanged.
name: brevio-core
on:
  pull_request:
  merge_group:
permissions:
  contents: read
jobs:
  brevio-core:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
      security-events: write
    steps:
      - uses: actions/checkout@{CHECKOUT_PIN}
        with:
          fetch-depth: 0
      - uses: {action}@v{version}
        with:
          package-version: {version}
          sarif: true
"""


def _has_fastapi(root: str) -> bool:
    for rel in ("pyproject.toml", "requirements.txt"):
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    if "fastapi" in fh.read().lower():
                        return True
            except OSError:
                pass
    return False


_CODE_SUFFIXES = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "Node/TS", ".jsx": "Node/TS"}
_SKIPPED_DIRS = frozenset({"node_modules", "venv", "env", "dist", "build", "__pycache__", "site-packages"})
MAX_FILES_LOOKED_AT = 5000


def _languages_in_files(root: str) -> list[str]:
    """The stack labels of the code files under ``root``, from their suffixes: hidden and dependency folders are not read."""
    found: dict[str, None] = {}
    seen = 0
    for _dir, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIPPED_DIRS)
        for name in sorted(names):
            seen += 1
            label = _CODE_SUFFIXES.get(os.path.splitext(name)[1].lower())
            if label:
                found.setdefault(label)
        if seen >= MAX_FILES_LOOKED_AT:
            break
    return list(found)


def detect_stack(root: str) -> list[str]:
    """Detect technologies present in this repository by checking marker files."""
    checks = (
        ("Python", ("pyproject.toml", "requirements.txt")),
        ("Node/TS", ("package.json",)),
        ("TypeScript", ("tsconfig.json",)),
        ("Docker", ("Dockerfile", "docker-compose.yml", "docker-compose.yaml")),
        ("Database/Migrations", ("prisma/schema.prisma", "migrations")),
        ("Next.js", ("next.config.js", "next.config.ts", "next.config.mjs")),
        ("Rust", ("Cargo.toml",)),
        ("Go", ("go.mod",)),
    )
    detected = [label for label, rels in checks if any(os.path.exists(os.path.join(root, r)) for r in rels)]
    if _has_fastapi(root) and "FastAPI" not in detected:
        detected.append("FastAPI")
    for label in _languages_in_files(root):     # a repository with no marker file is not "generic" when it holds code (2026-11-13)
        if label not in detected and not (label == "TypeScript" and "Node/TS" in detected):
            detected.append(label)
    return detected


def render_specs(root: str, stack: list[str] | None = None) -> dict[str, str]:
    """Generate deterministic starter content for CONSTITUTION.md, CLAUDE.md, and roadmap.json."""
    if stack is None:
        stack = detect_stack(root)
    raw_slug = os.path.basename(os.path.abspath(root))
    project_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_slug).strip("-") or "project"
    stack_str = ", ".join(stack) if stack else "generic"
    chain = core_cli.set_module("pipeline", TASK_MODULE) is not None      # the single-task commands: absent from a trimmed release

    constitution_text = (
        f"# Engineering Constitution: {project_slug}\n\n"
        "## 1. Architecture Invariants\n"
        f"- Stack: {stack_str}\n"
        "- Clean boundaries: Modules declare explicit public interfaces and preserve loose coupling.\n"
        "- Standard library and deterministic execution: zero external network dependencies in flight checks.\n\n"
        "## 2. File Custody & Preservation Rules\n"
        "- Do not remove existing tests or loosen assertions without explicit design authorization.\n"
        "- Preserve existing docstrings, license headers, and architecture comments.\n"
        "- Task isolation: scope file modifications strictly to the acceptance criteria.\n\n"
        "## 3. Zero-Regression Test Requirements\n"
        "- Every defect fix and feature must ship with deterministic regression tests.\n"
        "- Test suites must execute locally and in CI with clean zero exits.\n\n"
        "## 4. Brevio-Core Flight Gate Invariants\n"
        "- `brevio-core scan --diff HEAD`: Aim for no code rule findings in modified lines.\n"
        "- `brevio-core check`: Conflict markers, test custody, AST complexity, and orphan files must pass.\n"
        "- `brevio-core score` (preview): counts the findings by rule; fix the most frequent rule first.\n"
    )

    claude_text = (
        f"# {project_slug}\n\n"
        "## Project Overview\n"
        f"Engineering workspace for {project_slug} checked by brevio-core.\n\n"
        + (WORKFLOW if chain else "")
        + "## Repository Invariants & Rules\n"
        "@AGENTS.md\n"
        "Refer to CONSTITUTION.md for architecture invariants, custody rules, and flight gate definitions.\n"
    )

    roadmap_data = {
        "project": project_slug,
        "edition": "core",
        "items": [
            {
                "id": "TASK-001",
                "title": "Pass single-task quality gates and eliminate code findings",
                "priority": "P0",
                "type": "quality",
                "acceptance_criteria": [
                    "Pass brevio-core scan --diff HEAD with 0 findings",
                    "Pass brevio-core check pre-PR flight gates",
                    "Pass brevio-core score with 0 findings",
                ],
                "status": "todo",
            },
            {
                "id": "TASK-002",
                "title": "Establish test custody and baseline coverage invariants",
                "priority": "P1",
                "type": "testing",
                "acceptance_criteria": [
                    "Verify existing tests pass without regressions",
                    "Ensure every new module has corresponding test custody",
                ],
                "status": "todo",
            },
            *([{
                "id": "TASK-003",
                "title": "Automate single-task PR receipt generation in local workflow",
                "priority": "P2",
                "type": "workflow",
                "acceptance_criteria": [
                    "Generate .brevio/PR_BODY.md with the QA finding count",
                    "Confirm roadmap item status transitions to done upon QA pass",
                ],
                "status": "todo",
            }] if chain else []),
        ],
    }
    roadmap_text = json.dumps(roadmap_data, indent=2) + "\n"

    return {
        "CONSTITUTION.md": constitution_text,
        "CLAUDE.md": claude_text,
        "roadmap.json": roadmap_text,
    }
