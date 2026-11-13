#!/usr/bin/env python3
"""Run the README's marked shell blocks as a test, the way a reader would follow them.

A line ``<!-- readme-test -->`` directly before a fenced ``sh`` (or ``bash``) block marks it. The marked blocks run
in order with ``bash -e -o pipefail``, each in the same folder, in two fresh git repositories, each holding one empty
commit that ``origin/main`` names (``scan --diff origin/main`` stops when its base is not in the checkout, 0.10.0):

- ``empty``: a new repository with no file. Every block must exit 0.
- ``planted``: the same, but just before the first block that runs ``brevio-core scan`` a file with a hardcoded
  secret appears in it (``src/config.ts``, not committed: it is the change under review). A block that runs
  ``brevio-core scan`` may exit 1 there, and must report that file with rule R13.1. Every other block must exit 0.

It fails when nothing is marked, when a marker is not followed by a shell block, when no marked block runs
``brevio-core scan``, when a block fails, or when the planted finding is not reported.

``brevio-core`` must be on PATH (CI installs it from the checkout). Standard library only, and portable: on Windows it
needs the ``bash`` of Git for Windows on PATH, which ``shell: bash`` in a workflow provides.

    python scripts/readme_test.py [README.md] [--bash PATH]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import NamedTuple

MARKER = "<!-- readme-test -->"
FENCE = re.compile(r"^(?P<indent>\s*)```(?:sh|bash)\s*$")
SCAN = re.compile(r"\bbrevio-core\s+scan\b")
PLANTED_PATH = "src/config.ts"
PLANTED_RULE = "R13.1"
# The same synthetic pattern the exported CLI tests plant: AWS's documented example key ID, a fake in a credential
# format the hardcoded-secret rule matches. It is joined at run time so this file is not itself a finding when the
# gate scans a change to it.
PLANTED_R131_LINE = "const apiKey = '" + "AKIA" + "IOSFODNN7EXAMPLE" + "';\n"
TIMEOUT = 600


class Block(NamedTuple):
    line: int       # 1-based line of the opening fence
    code: str


def marked_blocks(text: str) -> tuple[list[Block], list[str]]:
    """The marked shell blocks of ``text`` and a problem line for each marker that is not followed by one."""
    lines = text.splitlines()
    blocks: list[Block] = []
    problems: list[str] = []
    for i, line in enumerate(lines):
        if line.strip() != MARKER:
            continue
        fence = FENCE.match(lines[i + 1]) if i + 1 < len(lines) else None
        if not fence:
            problems.append(f"line {i + 1}: {MARKER} is not directly before a ```sh block")
            continue
        indent = len(fence.group("indent"))
        body: list[str] = []
        for j in range(i + 2, len(lines)):
            if lines[j].strip() == "```":
                blocks.append(Block(i + 2, "\n".join(ln[indent:] if ln[:indent].isspace() else ln for ln in body) + "\n"))
                break
            body.append(lines[j])
        else:
            problems.append(f"line {i + 2}: the marked block is never closed")
    return blocks, problems


def _git(repo: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=60)


def new_repo(parent: str, name: str) -> str:
    """A fresh repository on ``main`` with a local identity, so a block may commit, and one empty commit that ``origin/main``
    names, so a block may diff against it; no file is committed."""
    repo = os.path.join(parent, name)
    os.makedirs(repo)
    _git(repo, "init", "-q")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    for key, value in (("user.name", "readme-test"), ("user.email", "readme-test@example.com"),
                       ("commit.gpgsign", "false"), ("core.autocrlf", "false")):
        _git(repo, "config", key, value)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "start")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def plant(repo: str) -> None:
    path = os.path.join(repo, *PLANTED_PATH.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(PLANTED_R131_LINE)


def run_block(bash: str, repo: str, block: Block, scratch: str) -> subprocess.CompletedProcess:
    """Run one block with bash in ``repo``; the script file uses LF endings and a forward-slash path on every system."""
    script = os.path.join(scratch, f"block-{block.line}.sh")
    with open(script, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(block.code)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([bash, "--noprofile", "--norc", "-e", "-o", "pipefail", pathlib.Path(script).as_posix()],
                          cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT)


def reports_planted(output: str) -> bool:
    return any(PLANTED_PATH in ln and PLANTED_RULE in ln for ln in output.splitlines())


def check(setup: str, block: Block, res: subprocess.CompletedProcess) -> str | None:
    """Why this block's result fails the test in ``setup``, or None when it passes."""
    output = res.stdout + res.stderr
    if setup == "planted" and SCAN.search(block.code):
        if res.returncode not in (0, 1):
            return f"exit {res.returncode}"
        return None if reports_planted(output) else f"{PLANTED_PATH} [{PLANTED_RULE}] not reported"
    return None if res.returncode == 0 else f"exit {res.returncode}"


def run_setup(setup: str, blocks: list[Block], bash: str, parent: str) -> list[str]:
    repo = new_repo(parent, setup)
    problems: list[str] = []
    planted = False
    for block in blocks:
        if setup == "planted" and not planted and SCAN.search(block.code):
            plant(repo)
            planted = True
        res = run_block(bash, repo, block, parent)
        why = check(setup, block, res)
        print(f"[readme-test] {setup}: block at line {block.line}: {why or 'ok'}")
        if why:
            problems.append(f"{setup}: block at line {block.line}: {why}\n--- block\n{block.code}--- output\n"
                            f"{res.stdout}{res.stderr}---")
    return problems


def run(readme: str, bash: str) -> list[str]:
    """Every problem with the README's marked blocks; an empty list means the README works as written."""
    with open(readme, encoding="utf-8") as fh:
        blocks, problems = marked_blocks(fh.read())
    if not blocks:
        return problems + [f"{readme}: no block is marked with {MARKER}"]
    if not any(SCAN.search(b.code) for b in blocks):
        problems.append(f"{readme}: no marked block runs brevio-core scan")
    with tempfile.TemporaryDirectory(prefix="readme-test-", ignore_cleanup_errors=True) as parent:
        for setup in ("empty", "planted"):
            problems += run_setup(setup, blocks, bash, parent)
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("readme", nargs="?", default="README.md")
    ap.add_argument("--bash", help="the bash to run the blocks with (default: the first bash on PATH)")
    args = ap.parse_args(argv)
    bash = args.bash or shutil.which("bash")
    if not bash:
        print("[readme-test] no bash on PATH", file=sys.stderr)
        return 2
    problems = run(args.readme, bash)
    for p in problems:
        print(f"[readme-test] FAIL {p}", file=sys.stderr)
    print(f"[readme-test] {'FAIL' if problems else 'OK'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
