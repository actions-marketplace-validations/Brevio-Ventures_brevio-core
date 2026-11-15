"""What the ``brevio-core`` command line says about itself: its version, the rule list and the notes around a scan (2026-11-13).

``core_cli`` is over its size limit and may not grow, so the small pieces of the command-line surface live here: ``--version``,
the ``rules`` and ``explain`` text with the note on which rules ``scan`` runs, the hint that names ``scan --fix``, the warning
for a ``select`` that names no rule the scanner checks, and the guard that ends a command quietly when the reader of a pipe
has gone. Standard library and the rule catalogue only, so it ships in every set.
"""
from __future__ import annotations

import errno
import json
import os
import re
import shlex
import sys
from collections.abc import Callable, Iterable
from typing import Any

from brevio.pipeline.code_rules import PR_RULES_122, rule_status
from brevio.pipeline.code_rules import docs as rule_docs
from brevio.pipeline.code_rules.details import scanned_by
from brevio.pipeline.core_diff import git, resolve
from brevio.pipeline.core_sets import set_module

TREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # the folder that holds ``brevio/``
NAME = "brevio-core"
_VERSION = re.compile(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]*")
BROKEN_PIPE = 141       # what a shell reports for a command that a closed pipe ended (128 + SIGPIPE)


def _version_file(tree: str) -> str:
    """The text of ``VERSION`` in ``tree``, or ``""``. In the ``.pyz`` the tree is the archive, which ``open`` cannot enter, so the
    module's own loader reads the file there (2026-11-13: ``python3 brevio-core.pyz --version`` said the version was unknown)."""
    path = os.path.join(tree, "VERSION")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        pass
    loader = getattr(sys.modules[__name__], "__loader__", None) if tree == TREE else None
    get_data = getattr(loader, "get_data", None)
    # The importer names the archive as it was given (``../dist/brevio-core.pyz``), and ``TREE`` is made absolute and
    # normalised, so the two spellings differ and the importer would find no such entry: ask it with its own spelling.
    archive = getattr(loader, "archive", None)
    try:
        return get_data(os.path.join(archive or tree, "VERSION")).decode("utf-8").strip() if get_data is not None else ""
    except (OSError, UnicodeDecodeError):
        return ""


def package_version(tree: str | None = None, installed: Callable[[str], dict[str, list[str]] | None] | None = None) -> str | None:
    """The version of this package: the ``VERSION`` file of a release tree, else the installed metadata, else ``None``.

    ``installed`` reads the metadata header by hand (``core_init.installed_metadata``): ``importlib.metadata`` would load a
    network module on some Python versions, which the core must not do."""
    text = _version_file(TREE if tree is None else tree)
    if _VERSION.fullmatch(text):
        return text
    fields = installed(NAME) if installed is not None else None
    found = ((fields or {}).get("Version") or [""])[0]
    return found if _VERSION.fullmatch(found) else None


def print_version(installed: Callable[[str], dict[str, list[str]] | None] | None = None, tree: str | None = None) -> int:
    """``brevio-core --version``: one line on standard output, or an exit code 1 and a reason when no version is known."""
    version = package_version(tree, installed)
    if version is None:
        print(f"{NAME}: the version is not known here (no VERSION file and no installed package metadata)", file=sys.stderr)
        return 1
    print(f"{NAME} {version}")
    return 0


def _reader_has_gone(exc: OSError) -> bool:
    """Whether ``exc`` is a write to a pipe whose reader has closed. POSIX raises ``BrokenPipeError``; Windows raises a plain
    ``OSError`` with ``EINVAL`` (``ERROR_NO_DATA``) and no file name, which a bad path in the command's own work would carry."""
    return isinstance(exc, BrokenPipeError) or (sys.platform == "win32" and exc.errno == errno.EINVAL and exc.filename is None)


def guarded(run: Callable[[], int]) -> int:
    """Run a command and end it quietly when the reader of standard output has closed the pipe (``... | head``).

    The unwritten output goes to the null device, so the flush at exit cannot fail again. A command that was cut short returns
    141, as a shell does for a command a closed pipe ended."""
    try:
        code = run()
        if sys.stdout is not None:      # a command started with standard output closed has none; print then does nothing
            sys.stdout.flush()
        return code
    except OSError as exc:
        if not _reader_has_gone(exc):
            raise
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):       # a stream that has no file descriptor (a test's) has nothing to redirect
            pass
        return BROKEN_PIPE


def no_leaks(root: str) -> str:
    """The line ``leaks`` prints for a folder with nothing to report; for a path that is no folder it says so on standard
    error and gives the empty line 0.9.1 printed, since nothing was checked and "no leaks found" would not be true."""
    if os.path.isdir(root):
        return f"no leaks found in {root}"
    print(f"{NAME}: leaks: {root} is not a folder; nothing was checked", file=sys.stderr)
    return ""


def _marker(rule_id: str, scanned: frozenset[str], surface: frozenset[str]) -> str:
    return "  [scan]" if rule_id in scanned else "  [scan --surface]" if rule_id in surface else ""


def rule_rows(query: str | None = None, only_scanned: bool = False) -> list[dict[str, Any]]:
    """The documented rules that match ``query``, each with whether ``scan`` runs it and its status."""
    scanned, surface = frozenset(scanned_by("code")), frozenset(scanned_by("dev"))
    q = (query or "").strip().lower()
    return [{"id": rid, "summary": PR_RULES_122[rid], "scanned": rid in scanned, "surface": rid in surface, "status": rule_status(rid)}
            for rid in rule_docs.rule_ids()
            if (not q or q in rid.lower() or q in PR_RULES_122[rid].lower()) and (rid in scanned or not only_scanned)]


def run_rules(as_json: bool, query: str | None, only_scanned: bool = False) -> int:
    """``brevio-core rules``: the list, marked with the rules ``scan`` runs. The note on the others goes to standard error."""
    rules = rule_rows(query, only_scanned)
    if as_json:
        print(json.dumps({"rules": rules, "count": len(rules)}, indent=2))
        return 0
    scanned, surface = frozenset(scanned_by("code")), frozenset(scanned_by("dev"))
    for r in rules:
        print(f"{r['id']}: {r['summary']}{_marker(r['id'], scanned, surface)}")
    if not only_scanned:
        print(f"brevio-core: {len(scanned)} of {len(PR_RULES_122)} documented rules are checked by `scan` ([scan]), {len(surface)} more "
              "by `scan --surface`; the others are documented only. `rules --scanned` lists what `scan` checks.", file=sys.stderr)
    return 0


def explain_text(rule_id: str) -> str:
    """The page of a rule, with one line under its title when ``brevio-core scan`` does not run the rule."""
    page = rule_docs.render(rule_id)
    if rule_id in scanned_by("code"):
        return page
    note = ("`brevio-core scan --surface` runs this rule; a plain `brevio-core scan` does not."
            if rule_id in scanned_by("dev") else "`brevio-core scan` does not run this rule.")
    title, sep, rest = page.partition("\n")
    return f"{title}\n\n{note}{sep}{rest}"


def _around(text_of: Callable[[str, int], str], path: str, lno: int, span: int = 40) -> list[tuple[int, str]]:
    """The line ``lno`` of ``path`` and the ``span`` lines below it, so a fixer can read the handler that starts there."""
    return [(n, text_of(path, n)) for n in range(lno, lno + span)]


def print_fixable(shown: Iterable[str], fix_module: Any, named: list[str], diff: str | None,
                  fields: Callable[[str], Any], text_of: Callable[[str, int], str]) -> None:
    """On standard error, ``N fixable: brevio-core scan --fix --dry-run`` for the findings the fixers would change.

    A finding counts only when its line, as it is on disk, is rewritten for its own rule: a rule with a fixer also fires on lines
    its fixer leaves alone (an empty ``catch`` in a ``.ts`` file). ``fix_module`` is the fixer module of the 0.3 set, or ``None``
    in a build without it (then nothing is said)."""
    if fix_module is None:
        return
    count = 0
    for finding in shown:
        got = fields(finding)
        if got and got["rule"] in fix_module.FIXERS and got["rule"] in fix_module.fix_line(
                got["path"], text_of(got["path"], got["line"]), _around(text_of, got["path"], got["line"]), got["line"])[1]:
            count += 1
    if count:
        parts = [NAME, "scan", *(shlex.quote(p) for p in named)]
        if diff is not None:
            parts += ["--diff", *([shlex.quote(diff)] if diff else [])]
        print(f"{count} fixable: {' '.join([*parts, '--fix', '--dry-run'])}", file=sys.stderr)


def warn_select(project: Any, paths: Iterable[str], surface: bool, guard_prefix: str) -> None:
    """Warn on standard error when the configuration of every scanned file leaves no rule that the scanner checks."""
    rules = [*scanned_by("code"), *(scanned_by("dev") if surface else []), f"{guard_prefix}x"]
    files = list(paths)
    if project is not None and files and not any(project.for_path(p).runs(r) for p in files for r in rules):
        print("brevio-core: warning: the select and ignore settings leave no rule that scan checks, so no finding can come from "
              "this scan; `brevio-core rules --scanned` lists them", file=sys.stderr)


def scan_facts(files: Iterable[str], project: Any = None, surface: bool = False) -> dict[str, Any]:
    """The facts of a scan run that the report states: the rules that ran for the files read, and how many files that is.

    A rule ran when ``brevio-core.toml`` leaves it on for at least one file that was not excluded; with no config every
    rule of the scan runs. Learned guards are not counted: they are the repository's own, not the tool's rules (stage C5)."""
    paths = list(files)
    rules = [*scanned_by("code"), *(scanned_by("dev") if surface else [])]
    live = [p for p in paths if project is None or not project.for_path(p).excluded]
    ran = [r for r in rules if live and (project is None or any(project.for_path(p).runs(r) for p in live))]
    return {"rules_ran": len(ran), "files": len(paths)}


def _short(ref: str) -> str:
    """The 12-digit id git gives ``ref``, or ``""`` when git does not know it (hex only, so safe in a command)."""
    return git("rev-parse", "--short=12", ref).strip() if ref else ""


def facts_for(args: Any, files: Iterable[str], project: Any, seconds: float,
              installed: Callable[[str], dict[str, list[str]] | None] | None = None, named: Iterable[str] = ()) -> str:
    """The facts line of this scan run, or ``""`` without ``--facts`` (``scan --format markdown --facts``, stage C5, 2026-11-14).

    The base is the one ``--diff`` resolved; a scan of paths has none. The command that repeats the scan is left out when the
    version or the base is unknown, or with ``--no-reproduce`` (the Action sets it when ``source`` installs something else).
    It is also left out when paths were named or ``--allow-new-suppressions`` was given: the command would run another scan."""
    if not getattr(args, "facts", False):
        return ""
    version = package_version(None, installed)
    base = _short(resolve(args.diff)) if args.diff is not None else ""
    head = _short("HEAD")
    facts = scan_facts(files, project, getattr(args, "surface", False))
    command = ""
    same_scan = not list(named) and not getattr(args, "allow_new_suppressions", False)
    if version and base and same_scan and not getattr(args, "no_reproduce", False):
        command = f"pip install {NAME}=={version} && {NAME} scan --diff {base}" + (" --surface" if getattr(args, "surface", False) else "")
    return str(set_module("pipeline", "core_markdown").facts_line(version, facts["rules_ran"], facts["files"], seconds, command, base, head))
