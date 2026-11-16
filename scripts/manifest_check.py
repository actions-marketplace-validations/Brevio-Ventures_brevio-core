#!/usr/bin/env python3
"""Fail a pull request that changes a file the release does not export (CONTRIBUTING.md, "How this repo works").

This repository is an export of a private harness. Each release replaces every file listed in ``MANIFEST.json``, and
a merged change is brought back into the harness through that list. A file outside it has no way back, so a change to
it would be lost at the next release. The files the release writes itself (``RELEASE_ONLY``) are never edited in a
pull request. ``ALLOWED`` lists the few paths that belong to this repository alone.

The manifest is read from the base commit, so a pull request cannot widen it. Before the first release there is no
manifest and nothing is checked. Standard library only.

    python scripts/manifest_check.py --base BASE_SHA [--head HEAD]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

# Paths a pull request may add or change although no release writes them: issue and discussion forms, and pictures
# for the docs. A maintainer still brings each one back by hand.
ALLOWED = (".github/ISSUE_TEMPLATE/", ".github/DISCUSSION_TEMPLATE/", "docs/img/")
RELEASE_ONLY = ("MANIFEST.json", "VERSION", "RELEASE.md", "IMPORTED", "CONTRIBUTORS.md")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                           "-c", "protocol.ext.allow=never", *args],
                          capture_output=True, text=True, timeout=120)


def manifest_at(base: str) -> set[str] | None:
    """The files ``MANIFEST.json`` lists at commit ``base``, or None when that commit has none."""
    r = _git("show", f"{base}:MANIFEST.json")
    if r.returncode:
        return None
    return set(json.loads(r.stdout)["files"])


def changed_files(base: str, head: str) -> list[str]:
    """Every path ``head`` adds, changes or deletes against ``base``; a rename counts as both of its paths."""
    r = _git("diff", "--name-only", "--no-renames", "-z", base, head)
    if r.returncode:
        raise SystemExit(f"git diff {base} {head} failed: {r.stderr.strip()}")
    return sorted(filter(None, r.stdout.split("\0")))


def problems(changed: list[str], listed: set[str]) -> list[str]:
    """One line per changed path that the release writes itself or that is neither listed nor allowed."""
    out = []
    for path in changed:
        if path in RELEASE_ONLY:
            out.append(f"{path}: written by the release; a pull request does not change it")
        elif path not in listed and not path.startswith(ALLOWED):
            out.append(f"{path}: not in MANIFEST.json, so a release would drop it; a maintainer decides where it goes")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True, help="the commit the pull request is compared with")
    ap.add_argument("--head", default="HEAD", help="the pull request's commit (default HEAD)")
    args = ap.parse_args(argv)
    listed = manifest_at(args.base)
    if listed is None:
        print("[manifest] no MANIFEST.json at the base commit: nothing to check before the first release")
        return 0
    changed = changed_files(args.base, args.head)
    found = problems(changed, listed)
    for line in found:
        print(f"[manifest] {line}")
    if found:
        print("[manifest] See CONTRIBUTING.md, \"How this repo works\".")
        return 1
    print(f"[manifest] OK: {len(changed)} changed files, all exported by the release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
