"""The ``brevio-core`` command line: ``scan`` runs the scanned code rules on files or on a diff (ROADMAP E16, 2026-11-04).

Standard library only, so it exports with the rule engines (the core exporter) and runs with no model call and
no network. It is what the GitHub Action, the pre-commit hook and the agent hooks call. Exit code 0 means no finding,
1 means findings, 2 means a usage error. The 0.2 export set adds ``learn``, ``hook``, ``gain`` and ``add`` (ROADMAP
E16, 0.2 set); they show up only when the build ships them (``agent_tools``). The 0.3 set adds ``scan --fix``: the
fixers of ``code_rules.fixers`` rewrite a line only where its rule has one correct fix, then the files are scanned again
(ROADMAP E16, 0.3 set, 2026-11-05). The 0.4 set adds ``init`` and ``init --undo`` (``core_init``, ROADMAP E22), and
``baseline``, ``--fail-on``, inline ignores with a reason and ``--format markdown`` (``core_baseline``, ``core_markdown``;
ROADMAP E20, E21, E26). Agents cannot silence the gate: the agent hook never honours an ignore comment, and a diff scan
reports a new suppression as a finding unless ``--allow-new-suppressions`` says a person reviewed it (2026-11-05).
``--format agent`` prints the compact lines the agent hook and the MCP server answer with (ROADMAP E34, 2026-11-05).
The 0.6 set adds ``brevio-core.toml`` (``core_config``, ROADMAP E32): which rules run where and at which level, shown by
``brevio-core config``, with ``--fail-on error`` failing on error-level findings only. A preview rule reports but never
fails, and a deprecated rule reports nothing, in every set (ROADMAP E33, 2026-11-05). The 0.6 set also adds ``up``
(``core_up``, ROADMAP E37): a preview of what a team sync would take, and one Teams line at the end of the Markdown
report, which ``--no-upgrade-line`` leaves out. The agent format (hook, MCP) never carries it. The 0.6 set also adds
``patch`` (``core_patch``, 2026-11-11): an edit is written only when the rules find the changed lines clean.
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from brevio.pipeline import core_help, core_platform, core_sarif, core_surface, dev_rules, leak_scan
from brevio.pipeline.code_rules import DEPRECATED, PR_RULES_122, PREVIEW, rule_status, scan_lines
from brevio.pipeline.code_rules import docs as rule_docs
from brevio.pipeline.code_rules.details import scanned_by
from brevio.pipeline.code_rules.hints import hint as fix_hint
from brevio.pipeline.core_diff import (  # noqa: F401  the names other modules call here (see core_diff)
    _BUNDLE_NAME,
    CODE_SUFFIXES,
    LONG_LINE,
    MAX_BYTES,
    SKIP_DIRS,
    VENDORED_DIRS,
    bundled,
    diff_lines,
    has_head,
    skipped,
)
from brevio.pipeline.core_diff import git as _git
from brevio.pipeline.core_diff import read as _read
from brevio.pipeline.core_diff import resolve as _base

AGENT_MAX = 20          # finding lines an agent is shown at once; a longer list is cut, so one reply stays short
FORMATS = ("text", "markdown", "agent", "json", "github")
# DOTALL: a file name may hold a newline, and its finding must still parse (so the Markdown report escapes it).
_FINDING = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?:\[(?P<rule>[^\]]+)\] )?(?P<msg>.*)$", re.S)

Numbered = dict[str, list[tuple[int, str]]]


def prune_dirs(dirpath: str, dirs: Iterable[str]) -> list[str]:
    """The sorted subfolders of ``dirpath`` a walk goes into: not a skipped, vendored or virtualenv folder, and not a nested
    checkout (a folder holding its own ``.git``: a worktree or a clone, whose files belong to another project; 2026-11-12)."""
    return sorted(d for d in dirs if d not in SKIP_DIRS and d not in VENDORED_DIRS
                  and not os.path.exists(os.path.join(dirpath, d, ".git")))


def collect_files(paths: Iterable[str]) -> Numbered:
    """Every line of each named file, and of each code file under a named folder that is not vendored, minified, bundled
    or inside a nested checkout; a file over ``MAX_BYTES`` reads as empty (a file named on the command line is read as asked)."""
    out: Numbered = {}
    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirs, names in os.walk(p):
                dirs[:] = prune_dirs(dirpath, dirs)
                for n in sorted(names):
                    if (n.endswith(CODE_SUFFIXES) or n == "package.json") and not _BUNDLE_NAME.search(n):
                        lines = _read(os.path.join(dirpath, n))
                        if not _long_head(lines):
                            out[core_platform.shown(os.path.join(dirpath, n))] = lines
        else:
            out[core_platform.shown(p)] = _read(p)
    return out


def _long_head(numbered: list[tuple[int, str]]) -> bool:
    return any(lno <= 2 and len(text) > LONG_LINE for lno, text in numbered[:2])


_DEV_FINDING = re.compile(r"^- (?P<path>.+?):(?P<line>\d+) \[(?P<rule>R\d+\.\d+)\b[^\]]*\]: (?P<msg>.*)$")


def scan(files: Numbered, proj: Any = None) -> list[str]:
    """Findings (``path:line: [rule] message``) for the given numbered lines.

    A deprecated rule reports nothing. With ``brevio-core.toml`` (the 0.6 set, ``proj`` or read here) an excluded file
    is not scanned, and a rule the config turns off for a file reports nothing there."""
    proj = project(files) if proj is None else proj
    found: list[str] = []
    for rel, numbered in sorted(files.items()):
        if proj is None or not proj.for_path(rel).excluded:
            found += scan_lines(rel, numbered)
    guards = set_module("pipeline", GUARDS_MODULE)      # the learned guards run wherever the scan runs (2026-11-12, improvement plan)
    found += guards.findings(files) if guards is not None else []
    return [f for f in found if _runs(f, proj)]


def scan_surface(files: Numbered, proj: Any = None, root: str = ".") -> list[str]:
    """Run both the 8 line-pattern rules (``code_rules``) and the 9 surface rules (``dev_rules``) across ``files``."""
    if isinstance(proj, str):
        root, proj = proj, None
    proj = project(files) if proj is None else proj
    found = list(scan(files, proj))
    seen = {(f["path"], f["line"], f["rule"]) for f in map(finding_fields, found) if f}
    for rel, numbered in sorted(files.items()):
        if proj is not None and proj.for_path(rel).excluded:
            continue
        full_text = ""
        full_path = os.path.join(root, rel)
        if os.path.isfile(full_path):
            try:
                with open(full_path, encoding="utf-8", errors="ignore") as fh:
                    full_text = fh.read()
            except OSError:
                full_text = ""
        if not full_text:
            full_text = "\n".join(t for _, t in numbered)
        for raw in dev_rules._scan_single_file(rel, full_text, numbered):
            m = _DEV_FINDING.match(raw)
            if not m:
                continue
            key = (m["path"], int(m["line"]), m["rule"])
            if key in seen:
                continue
            norm = f"{m['path']}:{m['line']}: [{m['rule']}] {m['msg']}"
            if _runs(norm, proj):
                found.append(norm)
                seen.add(key)
    return found


def _catalogued(finding: str) -> tuple[str, str]:
    """``(path, rule)`` of a finding of a catalogue rule or a guard, or ``("", "")`` (a suppression finding, not a finding)."""
    fields = finding_fields(finding)
    rule = str(fields["rule"]) if fields else ""
    return (str(fields["path"]), rule) if fields and (rule in PR_RULES_122 or rule.startswith(GUARD_PREFIX)) else ("", "")


def _runs(finding: str, proj: Any) -> bool:
    path, rule = _catalogued(finding)
    if not rule:
        return True
    return rule_status(rule) != DEPRECATED and (proj is None or (proj.for_path(path).runs(rule) and not proj.for_path(path).excluded))


def level_of(finding: str, proj: Any) -> str:
    """``warn`` or ``error``: a preview rule's finding is a warning; otherwise the config's level, ``error`` by default."""
    path, rule = _catalogued(finding)
    if not rule:
        return "error"
    if rule.startswith(GUARD_PREFIX):       # report-only on trial; a warning once active, unless the config raises it
        guards = set_module("pipeline", GUARDS_MODULE)
        return guards.level(rule, proj.for_path(path) if proj is not None else None) if guards is not None else "warn"
    if rule_status(rule) == PREVIEW:
        return "warn"
    return proj.for_path(path).level(rule) if proj is not None else "error"


def blocking(shown: list[str], fail_on: str, proj: Any = None) -> list[str]:
    """The findings that fail the scan: none with ``none``; never a preview rule's; with ``error`` only error-level ones;
    a learned guard's only at error level, whatever ``fail_on`` says (one on trial does not fail a scan)."""
    if fail_on == "none":
        return []
    return [f for f in shown if rule_status(_catalogued(f)[1]) != PREVIEW
            and (level_of(f, proj) == "error" or (fail_on != "error" and not _catalogued(f)[1].startswith(GUARD_PREFIX)))]


def finding_fields(finding: str) -> dict[str, str | int] | None:
    """``path``, ``line``, ``rule`` and ``message`` of one finding line, or ``None`` when it is not one."""
    m = _FINDING.match(finding)
    if not m:
        return None
    return {"path": m["path"], "line": int(m["line"]), "rule": m["rule"] or "brevio-core", "message": m["msg"]}


def agent_lines(shown: list[str]) -> list[str]:
    """``path:line [R] message -> fix hint`` per finding, grouped by rule, cut at ``AGENT_MAX`` with "+N more".

    What an agent reads: the agent hook, the MCP ``scan`` and ``scan --format agent`` print these lines."""
    order = {rid: i for i, rid in enumerate(rule_docs.rule_ids())}
    rows = sorted((f for f in map(finding_fields, shown) if f),
                  key=lambda f: (order.get(str(f["rule"]), len(order)), str(f["rule"]), str(f["path"]), int(f["line"])))
    out = [f"{f['path']}:{f['line']} [{f['rule']}] {f['message']}{fix_hint(str(f['rule']), str(f['path']), str(f['message']))}" for f in rows[:AGENT_MAX]]
    return out + ([f"+{len(rows) - AGENT_MAX} more"] if len(rows) > AGENT_MAX else [])


def select_lines(named: list[str], diff: str | None) -> Numbered:
    """The lines to scan: the added lines against ``diff`` (``None``: no diff scan), narrowed to ``named``, or whole files."""
    if diff is None:
        return collect_files(named or ["."])
    lines = diff_lines(diff)
    if named:
        wanted = collect_files(named)
        lines = {k: v for k, v in lines.items() if k in wanted or any(k.startswith(w.rstrip("/") + "/") for w in named)}
    return lines


def sarif(findings: list[str], silenced: Iterable[tuple[str, str]] = (),
          level: Callable[[str], str] = lambda _f: "error") -> dict:
    """The findings as a SARIF 2.1.0 log, for GitHub code scanning; a silenced one carries its reason as a suppression,
    and a warn-level one (``level``) is a SARIF warning."""
    results = []
    guards, text_of = set_module("pipeline", GUARDS_MODULE), line_reader()
    for f, reason in [*((f, None) for f in findings), *silenced]:
        fields = finding_fields(f)
        if not fields:
            continue
        result: dict[str, Any] = {
            "ruleId": fields["rule"], "level": "warning" if level(f) == "warn" else "error",
            "message": {"text": fields["message"]},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": fields["path"]},
                                                "region": {"startLine": fields["line"], "endLine": fields["line"], "startColumn": 1}}}]}
        result.update(guards.fingerprints(fields, text_of) if guards is not None else {})     # a guard's hit keeps its identity when its line moves
        if reason is not None:
            result["suppressions"] = [{"kind": "inSource", "justification": reason}]
        results.append(result)
    rules = [{"id": rid, "shortDescription": {"text": text}, "defaultConfiguration": {"level": "warning" if rule_status(rid) == PREVIEW else "error"},
              "help": {"text": f"brevio-core explain {rid} prints the rule page."}}
             for rid, text in sorted(PR_RULES_122.items())]
    release = init.release_info() if (init := set_module("pipeline", INIT_MODULE)) is not None else None     # the version and the tag of the rule pages
    return core_sarif.log(results, rules, release)


def write_atomic(path: str, text: str) -> None:
    """Write ``text`` to ``path`` through a temporary file, so a crash never leaves half a report."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


FIXERS_MODULE = "fixers"   # the module of the 0.3 set that declares the fixers; a build without it offers no ``--fix``
BASELINE_MODULE, MARKDOWN_MODULE = "core_baseline", "core_markdown"   # the 0.4 set; a build without them has no baseline
CONFIG_MODULE = "core_config"   # the 0.6 set's brevio-core.toml (ROADMAP E32); a build without it reads no config
GUARDS_MODULE, GUARD_PREFIX = "core_guards", "guard:"   # the 0.2 set's learned guards in the scan; a hit's rule ID is ``guard:<id>``
Changes = dict[str, tuple[str, str, list[str]]]   # path -> (old text, new text, rule id of each fix made)
Triaged = tuple[list[str], list[tuple[str, str]], list[str]]   # shown, silenced (finding, reason), baselined


class UsageError(Exception):
    """The command cannot run as asked (exit code 2)."""


def set_module(package: str, name: str) -> Any:
    """The module ``name`` of ``package`` from a later export set, or ``None`` in a build that does not ship it."""
    top = "brevio" if __name__.startswith("__main__") else __name__.split(".")[0]
    try:
        return importlib.import_module(f"{top}.{package}.{name}")   # a name built at run time: absent from earlier sets
    except ModuleNotFoundError as err:
        if not (err.name or "").startswith(f"{top}.{package}"):
            raise
        return None


def fixers() -> Any:
    """The fixer module of the 0.3 set, or ``None`` in a build that does not ship it."""
    return set_module("pipeline.code_rules", FIXERS_MODULE)


def baseline_tools() -> Any:
    """The baseline and ignore module of the 0.4 set, or ``None`` in a build that does not ship it."""
    return set_module("pipeline", BASELINE_MODULE)


def config_tools() -> Any:
    """The ``brevio-core.toml`` module of the 0.6 set, or ``None`` in a build that does not ship it."""
    return set_module("pipeline", CONFIG_MODULE)


def project(paths: Iterable[str] = ()) -> Any:
    """The ``brevio-core.toml`` files of the current folder, read for ``paths`` now, so a broken file is a usage error
    before anything is scanned; ``None`` in a build without config."""
    tools = config_tools()
    if tools is None:
        return None
    proj = tools.Project(".")
    try:
        proj.preload(paths)
    except tools.ConfigError as exc:
        raise UsageError(str(exc)) from exc
    return proj


def scanned_rules() -> list[str]:
    """The rule IDs ``scan`` checks, read from the rule definitions (the code engine of ``details.SCANS``)."""
    return scanned_by("code")


def line_reader() -> Callable[[str, int], str]:
    """``text_of(path, line)``: one line of a file as it is on disk, each file read once."""
    cache: dict[str, dict[int, str]] = {}

    def text_of(path: str, lno: int) -> str:
        if path not in cache:
            cache[path] = dict(_read(path))
        return cache[path].get(lno, "")
    return text_of


def commented(path: str) -> bool:
    """An ignore comment in ``path`` counts: it is a code file, not a doc that shows the comment."""
    return path.endswith(CODE_SUFFIXES)


def _code_only(lines: Numbered) -> Numbered:
    return {p: v for p, v in lines.items() if commented(p)}


def _load_baseline(tools: Any, where: Any = None) -> Any:
    """The baseline of the nearest ``.brevio/baseline.json`` (``where``, from ``tools.locate``; the current folder's first)."""
    try:
        return tools.load((where or tools.locate()).file)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def _need_base(want: str | None) -> None:
    """A named ``--diff BASE`` must exist in this checkout; the bare ``--diff`` falls back to ``main`` or ``HEAD`` (D27, I1)."""
    unborn = want == "HEAD" and _git("rev-parse", "--is-inside-work-tree").strip() == "true"     # a repository before its first commit: its files are all new
    if want and not unborn and not _git("rev-parse", "--verify", "-q", want).strip():
        raise UsageError(f"base '{want}' is not in this checkout (a shallow clone? fetch more history, or try --diff HEAD~3)")


def triage(found: list[str] | None = None, fail_on: Any = "new", honour_ignores: bool = True,
           lines: Any = None, config: Any = None, root: str = ".", findings: list[str] | None = None,
           whole: list[str] | None = None) -> Triaged:
    """``found`` split into shown, silenced and baselined findings; all shown in a build without the 0.4 set.

    ``fail_on`` ``all`` ignores the baseline. ``honour_ignores`` is false on the agent paths (hook, MCP), so an agent
    cannot silence a rule; such a finding says the comment counts in CI only. ``whole`` names the files of a diff scan,
    read whole to see how often a baselined line occurs in them (D26)."""
    if findings is not None:
        found = findings
    if found is None:
        found = []
    if not isinstance(fail_on, str) or fail_on not in ("new", "all", "none"):
        fail_on = "new"
        if not isinstance(honour_ignores, bool):
            honour_ignores = True
    tools = baseline_tools()
    if tools is None:
        return found, [], []
    where = tools.locate(".", whole is not None)
    base = None if fail_on == "all" else _load_baseline(tools, where)
    totals = None
    if whole is not None and base:
        kept = tools.triage(scan(collect_files(whole)), finding_fields, line_reader(), None, honour_ignores, commented).shown
        totals = tools.counts(kept, finding_fields, line_reader(), where.prefix)
    t = tools.triage(found, finding_fields, line_reader(), base, honour_ignores, commented, where.prefix, totals)
    shown = t.shown
    for note in tools.renamed_ignores(t.silenced, finding_fields):      # `scan` and `check` both come through here
        print(f"brevio-core: warning: {note}", file=sys.stderr)
    if hasattr(tools, "partition"):
        shown = tools.partition(shown, root=root)
    return shown, t.silenced, t.baselined


def new_suppressions(lines: Numbered, base: str) -> list[str]:
    """A finding for each ignore comment the change adds and each entry it adds to the baseline file."""
    tools = baseline_tools()
    found = tools.added_ignores(_code_only(lines)) + _config_changes(lines, tools.SUPPRESSION_RULE) + guard_changes(base)
    where = tools.locate(".", True)
    after_text = _read_raw(where.file) or ""
    if not after_text:
        return found
    before_text = _git("show", f"{_base(base)}:./{where.file}")
    record = where.file[:-len(tools.BASELINE_PATH)] + tools.INIT_RECORD       # ``init`` writes its record beside the baseline
    if tools.init_wrote(before_text, _git("show", f"{_base(base)}:./{record}"), _read_raw(record) or "", after_text):
        return found
    try:
        before = tools.parse(before_text) if before_text.strip() else Counter()
    except ValueError:      # a broken baseline on the base holds nothing to compare with: every entry is new
        before = Counter()
    return found + tools.added_entries(before, _load_baseline(tools, where), after_text, where.file)


def guard_changes(base: str) -> list[str]:
    """A finding for each guard the working tree removes, switches off or narrows against ``base`` without the record
    ``learn dismiss`` leaves (2026-11-12, improvement plan)."""
    guards = set_module("pipeline", GUARDS_MODULE)
    if guards is None:
        return []
    return guards.changes(_git("show", f"{_base(base)}:{guards.GUARDS_PATH}"), _read_raw(guards.GUARDS_PATH) or "")


def known_rules() -> frozenset[str]:
    """The rule IDs an ignore comment, a baseline entry or the config may name: the catalogue and the learned guards."""
    guards = set_module("pipeline", GUARDS_MODULE)
    return frozenset(PR_RULES_122) | frozenset(guards.known_rules() if guards is not None else ())


def _config_changes(lines: Numbered, rule: str) -> list[str]:
    """A finding for each ``brevio-core.toml`` a change adds or edits: a config can turn rules off or down."""
    cfg = config_tools()
    if cfg is None:
        return []
    new = _git("ls-files", "--others", "--exclude-standard", "--", f":(glob)**/{cfg.CONFIG_NAME}").splitlines()
    out = []
    for path in sorted({*(p for p in lines if cfg.is_config(p)), *new}):
        rows = lines.get(path) or _read(path)
        first = next((n for n, text in rows if text.strip() and not text.lstrip().startswith("#")), None)
        if first is not None:
            out.append(f"{path}:{first}: [{rule}] {cfg.CHANGE_MESSAGE}")
    return out


def warnings(lines: Numbered) -> list[str]:
    """Ignore comments that name an unknown rule or give no reason, and baseline entries of rules that do not exist."""
    guards, tools = set_module("pipeline", GUARDS_MODULE), baseline_tools()
    notes = guards.problems() if guards is not None else []       # the 0.2 set has no baseline, and reads guards
    if tools is None:
        return notes
    known = known_rules()
    where = tools.locate()
    return tools.ignore_warnings(_code_only(lines), known) + tools.stale_entries(_load_baseline(tools, where), known, where.file) + notes


def _read_raw(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def plan_fixes(lines: Numbered) -> Changes:
    """The rewrite of each file among ``lines`` that a fixer changes; only the selected line numbers are touched.

    A file ``brevio-core.toml`` excludes is left alone, and so is a line whose fix belongs to a rule that does not run
    there."""
    fix_line, proj = fixers().fix_line, project(lines)
    out: Changes = {}
    for rel, numbered in sorted(lines.items()):
        cfg = proj.for_path(rel) if proj is not None else None
        old = None if cfg is not None and cfg.excluded else _read_raw(rel)
        if old is None:
            continue
        parts, made = old.splitlines(keepends=True), []
        for lno in sorted({n for n, _text in numbered}):
            if not 0 < lno <= len(parts):
                continue
            body = parts[lno - 1].rstrip("\r\n")
            new, rules = fix_line(rel, body, numbered, lno)
            if rules and all(rule_status(r) != DEPRECATED and (cfg is None or cfg.runs(r)) for r in rules):
                parts[lno - 1] = new + parts[lno - 1][len(body):]
                made += rules
        if made:
            out[rel] = (old, "".join(parts), made)
    return out


def unified_diff(changes: Changes) -> str:
    """The changes as one unified diff, with ``a/`` and ``b/`` paths so it applies as a patch."""
    return "".join("".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                                f"a/{rel}", f"b/{rel}")) for rel, (old, new, _r) in sorted(changes.items()))


def apply_fixes(changes: Changes) -> None:
    """Write each rewritten file in place, keeping its permission bits."""
    for rel, (_old, new, _rules) in changes.items():
        mode = os.stat(rel).st_mode
        write_atomic(rel, new)
        os.chmod(rel, mode)


def _failed(shown: list[str], fail_on: str, proj: Any = None) -> int:
    return 1 if blocking(shown, fail_on, proj) else 0


def _run_fix(named: list[str], diff: str | None, dry_run: bool, fail_on: str = "new", honour_ignores: bool = True) -> int:
    lines = select_lines(named, diff)
    proj = project(lines)
    changes = plan_fixes(lines)
    fixed = sum(len(r) for _o, _n, r in changes.values())
    if dry_run:     # nothing is written, so every finding remains
        print(unified_diff(changes), end="")
        print(f"brevio-core: {fixed} fix(es) in {len(changes)} file(s) would be made; nothing written", file=sys.stderr)
        return _failed(triage(scan(lines, proj), fail_on, honour_ignores)[0], fail_on, proj)
    apply_fixes(changes)
    lines = select_lines(named, diff)
    remaining = triage(scan(lines, proj), fail_on, honour_ignores)[0]
    for f in remaining:
        print(f)
    print(f"brevio-core: {fixed} fixed, {len(remaining)} remaining in {len(lines)} file(s)", file=sys.stderr)
    return _failed(remaining, fail_on, proj)


def _covered(named: list[str]) -> Callable[[str], bool]:
    """Whether a path lies under one of ``named`` (everything, when nothing or the current folder is named)."""
    roots = [core_platform.shown(p) for p in named]
    if not roots or "." in roots:
        return lambda _path: True
    return lambda path: any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _run_baseline(named: list[str], diff: str | None, prune: bool = False) -> int:
    """Record the findings of the selection in the baseline. A path scan replaces the entries of the paths it covers,
    so fixed findings drop out; a diff scan only adds; ``prune`` only drops the entries no finding matches any more."""
    tools = baseline_tools()
    where = tools.locate(".", diff is not None)
    if prune and (diff is not None or named not in ([], ["."]) or where.prefix):
        raise UsageError("--prune reads the whole folder that holds .brevio: run it there, with no path and no --diff")
    shown, _silenced, _baselined = triage(scan(select_lines(named, diff)), "all")   # a finding silenced in the code needs no entry
    new, old, inside = tools.counts(shown, finding_fields, line_reader(), where.prefix), _load_baseline(tools, where), _covered(named)
    merged, gone = tools.prune(old, new) if prune else (tools.merge(old, new, (lambda _path: False) if diff is not None else (
        (lambda p: p.startswith(where.prefix + "/") and inside(p[len(where.prefix) + 1:])) if where.prefix else inside)), [])
    for g in gone:
        print(f"brevio-core: removed {g}")
    if prune and not gone:
        print("brevio-core: no entry is out of date; nothing removed")
        return 0
    os.makedirs(os.path.dirname(where.file), exist_ok=True)
    write_atomic(where.file, tools.render(merged))
    print(f"brevio-core: {sum(merged.values())} finding(s) in {where.file}")
    for note in tools.secret_notes(merged):
        print(f"brevio-core: {note}", file=sys.stderr)
    return 0


def _stale_notes(lines: Numbered, triaged: Triaged) -> list[str]:
    """What a whole-folder scan can say about the baseline and the ignore comments: entries that match no finding any
    more, and comments that silence nothing (I47, D55). A scan of a diff or of some paths cannot tell, so it says nothing."""
    tools = baseline_tools()
    where, (shown, silenced, baselined) = tools.locate(), triaged
    if where.prefix:
        return []
    dead = tools.dead_entries(_load_baseline(tools, where), tools.counts(shown + baselined, finding_fields, line_reader()))
    out = [f"{dead} baseline {'entry matches' if dead == 1 else 'entries match'} no finding; `brevio-core baseline --prune` removes "
           f"{'it' if dead == 1 else 'them'}"] if dead else []
    return out + tools.unused_ignores(_code_only(lines), silenced, finding_fields, scanned_rules())


def _print_findings(shown: list[str], silenced: int, baselined: int, args: argparse.Namespace, read: int | None = None,
                    red: frozenset[str] = frozenset()) -> None:
    fmt = getattr(args, "format", "text")
    if fmt == "agent":
        print("\n".join(agent_lines(shown)), end="\n" if shown else "")
        return
    if fmt == "markdown":
        rows = [f for f in map(finding_fields, shown) if f]
        footer = upgrade_line("pr-comment", not getattr(args, "no_upgrade_line", False))
        print(set_module("pipeline", MARKDOWN_MODULE).render(rows, silenced, baselined, args.rules_url, footer, read, args.diff is not None), end="")
        return
    if fmt == "json":
        print(json.dumps({"findings": [finding_fields(f) for f in shown], "count": len(shown)}, indent=2))
        return
    if fmt == "github":
        for f in shown:     # an annotation is an error when the check is red because of it (``red``), else a warning (revisit D28)
            fields = finding_fields(f) or {}
            print(f"::{'error' if f in red else 'warning'} file={fields.get('path', '')},line={fields.get('line', 1)},title={fields.get('rule', '')}::{fields.get('message', '')}")
        return
    for f in shown:
        print(f)


def _scan_notes(named: list[str], args: argparse.Namespace, lines: Numbered, triaged: Triaged) -> None:
    """The stderr notes of a scan: the base a bare ``--diff`` fell back to, and what a whole-folder scan found stale."""
    if args.diff == "" and _base("") == "HEAD":
        print("brevio-core: base: HEAD (no origin/main or main in this checkout), so only uncommitted changes are read", file=sys.stderr)
    if args.diff is None and named in ([], ["."]) and baseline_tools() is not None and not args.forbid_ignores:
        for note in _stale_notes(lines, triaged):
            print(f"brevio-core: note: {note}", file=sys.stderr)


def _run_scan(named: list[str], args: argparse.Namespace) -> int:
    fail_on = getattr(args, "fail_on", "new")
    lines = select_lines(named, args.diff)
    proj = project(lines)
    raw = scan_surface(lines, proj) if getattr(args, "surface", False) else scan(lines, proj)
    triaged = triage(raw, fail_on, not args.forbid_ignores, whole=sorted(lines) if args.diff is not None else None)
    shown, silenced, baselined = triaged
    _scan_notes(named, args, lines, triaged)
    if args.diff is not None and not getattr(args, "allow_new_suppressions", True):
        shown += new_suppressions(lines, args.diff)
    for w in warnings(lines) + (proj.warnings(known_rules()) if proj is not None else []):
        print(f"brevio-core: warning: {w}", file=sys.stderr)
    core_surface.warn_select(proj, lines, getattr(args, "surface", False), GUARD_PREFIX)
    _print_findings(shown, len(silenced), len(baselined), args, len(lines), frozenset(blocking(shown, fail_on, proj)))
    if args.sarif:
        write_atomic(args.sarif, json.dumps(sarif(shown, silenced, lambda f: level_of(f, proj)), indent=2))
    tail = f", {len(baselined)} baselined, {len(silenced)} silenced" if baseline_tools() is not None else ""
    warned = sum(1 for f in shown if level_of(f, proj) == "warn")
    tail += f", {warned} at warn level" if warned else ""
    print(f"brevio-core: {len(shown)} finding(s) in {len(lines)} file(s){tail}", file=sys.stderr)
    if getattr(args, "format", "text") == "text" and shown:
        first_rule = str((finding_fields(shown[0]) or {}).get("rule", ""))
        if first_rule.startswith("R"):
            print(f"Explain: brevio-core explain {first_rule}", file=sys.stderr)
        core_surface.print_fixable(shown, fixers(), named, args.diff, finding_fields, line_reader())
    return _failed(shown, fail_on, proj)


AGENT_TOOLS = "agent_cmds"      # the module of the 0.2 set that holds the commands; named here so a build without it is detected


def agent_tools() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    """``(commands, help)`` of the 0.2 agent tools, or two empty tables in a build that does not ship them."""
    mod = set_module("operator", AGENT_TOOLS)
    return (mod.COMMANDS, mod.HELP) if mod is not None else ({}, {})


INIT_MODULE = "core_init"       # the 0.4 set's ``init`` and ``init --undo`` (ROADMAP E22); a build without it has no ``init``
UP_MODULE = "core_up"           # the 0.6 set's ``up`` preview and upgrade line (ROADMAP E37); a build without it has neither
AEO_MODULE = "core_aeo"         # the 0.6 set's ``aeo``, the AEO rubric on local pages (ADOPTION.md 0.29); absent before it
CHECK_MODULE = "core_gates"     # the 0.6 set's ``check`` pre-PR flight check
BRIEF_MODULE = "core_memory"    # the 0.6 set's ``brief`` surface rules & trap recall
SCORE_MODULE = "core_score"     # the 0.6 set's ``score`` (preview): findings counted by rule
MAP_MODULE = "core_map"
HEAL_MODULE = "core_heal"
SHIELD_MODULE = "core_shield"
TASK_MODULE = "core_task"
ROAST_MODULE = "core_roast"     # ``roast``, the same counts as ``score`` under its old name: a module of its own, so a trimmed release can leave it out
PATCH_MODULE = "core_patch"     # the 0.6 set's ``patch``, an edit written only when the rules find it clean (2026-11-11)
AI_MODULE = "core_ai"           # the 0.7 set's ``ai``, the one command that may call a model: found by name, loaded only when it runs (2026-11-12)
AI_HELP = "ask a model, or a program, you connect (connect, status, explain, fix, summarize, learn); asks before it sends"   # not in the module: listing loads nothing


def _opt_cmd(cmd: str, pkg: str, mod_name: str) -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    mod = set_module(pkg, mod_name)
    if mod is None or not hasattr(mod, "main"):
        return ({}, {})
    hlp = getattr(mod, "HELP", f"run brevio-core {cmd}")
    return ({cmd: mod.main}, {cmd: hlp})


def init_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    """``({"init": main}, {"init": help})`` in a build that ships ``init``, else two empty tables."""
    return _opt_cmd("init", "pipeline", INIT_MODULE)


def up_tools() -> Any:
    """The ``up`` module of the 0.6 set (the preview and the upgrade line), or ``None`` in a build without it."""
    return set_module("pipeline", UP_MODULE)


def up_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    """``({"up": main}, {"up": help})`` in a build that ships ``up``, else two empty tables."""
    return _opt_cmd("up", "pipeline", UP_MODULE)


def upgrade_line(ref: str, wanted: bool = True) -> str:
    """The labelled Teams line for ``ref``, or "" when it is turned off or the build does not ship ``up``."""
    up = up_tools() if wanted else None
    return up.upgrade_line(ref) if up is not None else ""


def aeo_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("aeo", "pipeline", AEO_MODULE)


def check_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("check", "pipeline.gates", CHECK_MODULE)


def brief_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("brief", "pipeline", BRIEF_MODULE)


def score_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("score", "pipeline", SCORE_MODULE)


def map_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("map", "pipeline", MAP_MODULE)


def heal_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("heal", "pipeline", HEAL_MODULE)


def shield_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("shield", "pipeline", SHIELD_MODULE)


def patch_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("patch", "pipeline", PATCH_MODULE)


def ai_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    top = "brevio" if __name__.startswith("__main__") else __name__.split(".")[0]
    found = importlib.util.find_spec(f"{top}.pipeline.{AI_MODULE}") is not None      # found, not loaded (2026-11-12, claims check)
    return ({"ai": lambda argv: int(set_module("pipeline", AI_MODULE).main(argv))}, {"ai": AI_HELP}) if found else ({}, {})


def task_commands() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    mod = set_module("pipeline", TASK_MODULE)
    if mod is None:
        return ({}, {})
    cmds: dict[str, Callable[[list[str]], int]] = {}
    helps: dict[str, str] = {}
    if hasattr(mod, "main_connect"):
        cmds["connect"] = mod.main_connect
        helps["connect"] = getattr(mod, "CONNECT_HELP", "detect stack and coding clients and export CLAUDE.md, CONSTITUTION.md & roadmap.json")
    if hasattr(mod, "main_plan"):
        cmds["plan"] = mod.main_plan
        helps["plan"] = getattr(mod, "PLAN_HELP", "create a single-task implementation plan (.brevio/task_plan.json & .brevio/TASK_PLAN.md)")
    if hasattr(mod, "main_build"):
        cmds["build"] = mod.main_build
        helps["build"] = getattr(mod, "BUILD_HELP", "execute single-task build preparation and deterministic auto-fixes")
    if hasattr(mod, "main_qa"):
        cmds["qa"] = mod.main_qa
        helps["qa"] = getattr(mod, "QA_HELP", "run the single-task pre-PR quality gates and safety scorecard")
    if hasattr(mod, "main_pr"):
        cmds["pr"] = mod.main_pr
        helps["pr"] = getattr(mod, "PR_HELP", "verify QA gates and generate a pull-request receipt (.brevio/PR_BODY.md)")
    if hasattr(mod, "main"):
        cmds["task"] = mod.main
        helps["task"] = getattr(mod, "HELP", "run the single-task local workflow (connect -> plan -> build -> qa -> pr)")
    return (cmds, helps)


def roast_command() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    return _opt_cmd("roast", "pipeline", ROAST_MODULE)


def _add_baseline_arguments(sub: Any, sc: argparse.ArgumentParser) -> None:
    """The 0.4 set: ``baseline`` and the scan flags that read it."""
    tools = baseline_tools()
    by_level = config_tools() is not None       # the 0.6 set: levels from brevio-core.toml
    sc.add_argument("--fail-on", choices=(*tools.FAIL_ON, *(("error",) if by_level else ())), default="new",
                    help="new (default): baselined findings are hidden and pass; all: ignore the baseline, any finding "
                         "fails; none: report what is not baselined and always exit 0"
                         + ("; error: like new, but only error-level findings fail (warn-level ones are reported)"
                            if by_level else "") + ". A preview rule does not fail a run; a learned guard fails one only at error level")
    sc.add_argument("--allow-new-suppressions", action="store_true",
                    help="with --diff: do not report the ignore comments and baseline entries the change adds "
                         "(for a change a person has reviewed)")
    if set_module("pipeline", MARKDOWN_MODULE) is not None:
        sc.add_argument("--format", choices=FORMATS, default="text",
                        help="how to print the findings (agent: one short line each with a fix hint, grouped by rule, "
                             f"at most {AGENT_MAX})")
        sc.add_argument("--rules-url", default="", metavar="BASE", help="with --format markdown: link each rule ID to BASE<ID>")
        if up_tools() is not None:
            sc.add_argument("--no-upgrade-line", action="store_true",
                            help="with --format markdown: leave out the closing Teams line (the Action's footer: false)")
    bl = sub.add_parser("baseline", help=f"record the current findings in {tools.BASELINE_PATH}, so scan reports only new ones")
    bl.add_argument("paths", nargs="*", help="files or folders (default: the current folder)")
    bl.add_argument("--diff", nargs="?", const="", default=None, metavar="BASE",
                    help="record only the findings on the lines added against BASE (default origin/main, else main, else HEAD); a BASE the checkout does not have exits 2")
    bl.add_argument("--prune", action="store_true", help="only drop the entries no finding matches any more, and list them")


def parser(tools: Iterable[str], tool_help: dict[str, str]) -> argparse.ArgumentParser:
    """The ``brevio-core`` argument parser; the commands and flags of later sets appear when the build ships them."""
    ap = argparse.ArgumentParser(prog="brevio-core", description="Zero-token code rules for coding-agent mistakes.")
    ap.add_argument("-V", "--version", action="store_true", help="print the version and exit")      # read in ``main`` before parsing
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in tools:      # listed for ``--help``; the command itself reads the rest of the line
        sub.add_parser(name, help=tool_help[name], add_help=False)
    sc = sub.add_parser("scan", help=f"scan files, folders or a diff against the {len(scanned_rules())} rules the scanner "
                                     f"checks (of {len(PR_RULES_122)} documented)")
    sc.add_argument("paths", nargs="*", help="files or folders to scan (default: the current folder)")
    sc.add_argument("--file", action="append", default=[], metavar="PATH", help="a file to scan (repeatable)")
    sc.add_argument("--diff", nargs="?", const="", default=None, metavar="BASE",
                    help="scan only the lines added against BASE (default origin/main, else main, else HEAD); a BASE the checkout does not have exits 2")
    sc.add_argument("--sarif", metavar="FILE", help="also write the findings to FILE as SARIF")
    sc.add_argument("--forbid-ignores", action="store_true",
                    help="never honour an ignore comment (the agent hook runs with this, so an agent cannot silence a rule)")
    sc.add_argument("--surface", action="store_true",
                    help="also run the 9 surface-scoped rules (UI blur, SSR reveal, GTM deferral, webhook HMAC, integer cents, self-mocking tests, weak assertions, contract purity, package imports)")
    if fixers() is not None:
        sc.add_argument("--fix", action="store_true", help="rewrite in place what a rule has one correct fix for, then scan again")
        sc.add_argument("--dry-run", action="store_true", help="with --fix: print the fixes as a unified diff and write nothing")
    if baseline_tools() is not None:
        _add_baseline_arguments(sub, sc)
    cfg = config_tools()
    if cfg is not None:
        cf = sub.add_parser("config", help=f"show the {cfg.CONFIG_NAME} settings that apply to a file or folder")
        cf.add_argument("path", nargs="?", default=".", help="the file or folder (default: the current folder)")
        cf.add_argument("--json", action="store_true", help="print it as JSON")
    exp = sub.add_parser("explain", help="explain a rule: why it exists, how to fix it, and its measured precision")
    exp.add_argument("rule_id", help="the rule id (e.g. R13.1)")
    rl = sub.add_parser("rules", help="list or query documented rules")
    rl.add_argument("--json", action="store_true", help="print rules catalog as JSON")
    rl.add_argument("-q", "--query", default=None, metavar="QUERY", help="filter rules by text query")
    rl.add_argument("--scanned", action="store_true", help="list only the rules that scan checks (--surface adds more)")
    lk = sub.add_parser("leaks", help="check a folder for stray URLs, emails and paths that should not be public")
    lk.add_argument("root", nargs="?", default=".", help="the folder to check (default: the current folder)")
    hp = sub.add_parser("help", help="show the short list of commands to start with")
    hp.add_argument("--all", action="store_true", help="show the full list of commands")
    return ap


def _run_explain(rule_id: str) -> int:
    rid = rule_id.upper()
    if rid not in PR_RULES_122:
        raise UsageError(f"unknown rule: {rule_id}")
    print(core_surface.explain_text(rid))
    return 0


def _run_config(path: str, as_json: bool) -> int:
    """``brevio-core config``: the effective settings for ``path``, with the files they come from."""
    if not os.path.exists(path):
        raise UsageError(f"no such file or folder: {path}")
    rel = core_platform.shown(path)
    proj = project([rel])
    for w in proj.warnings(known_rules()):
        print(f"brevio-core: warning: {w}", file=sys.stderr)
    desc = config_tools().describe(proj, rel, scanned_rules(), rule_status, os.path.isdir(path))
    print(json.dumps(desc, indent=2) if as_json else config_tools().render(desc), end="\n" if as_json else "")
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "explain":
        return _run_explain(args.rule_id)
    if args.cmd == "rules":
        return core_surface.run_rules(args.json, args.query, args.scanned)
    if args.cmd == "config":
        return _run_config(args.path, args.json)
    named = [*args.paths, *getattr(args, "file", [])]
    missing = [p for p in named if not os.path.exists(p)]
    if missing:
        raise UsageError(f"no such file or folder: {', '.join(missing)}")
    _need_base(getattr(args, "diff", None))
    if args.cmd == "baseline":
        return _run_baseline(named, args.diff, args.prune)
    if getattr(args, "dry_run", False) and not args.fix:
        raise UsageError("--dry-run needs --fix")
    if getattr(args, "fix", False):
        if getattr(args, "format", "text") != "text":
            raise UsageError("--fix prints text only; leave out --format")
        return _run_fix(named, args.diff, args.dry_run, getattr(args, "fail_on", "new"), not args.forbid_ignores)
    return _run_scan(named, args)


def line_commands() -> tuple[dict[str, Callable[[list[str]], int]], dict[str, str]]:
    """``(commands, help)`` of every command that reads the rest of its line itself, in the order ``--help`` lists them:
    the agent tools, ``init``, ``aeo``, ``check``, ``brief``, ``score`` and ``up``, each where the build ships it."""
    tools, tool_help = agent_tools()
    for more, more_help in (
        init_command(),
        aeo_command(),
        check_command(),
        brief_command(),
        score_command(),
        roast_command(),
        map_command(),
        heal_command(),
        shield_command(),
        patch_command(),
        ai_command(),
        task_commands(),
        up_command(),
    ):
        tools, tool_help = {**tools, **more}, {**tool_help, **more_help}
    if "learn" in tools:
        orig_learn = tools["learn"]

        def _learn_wrapped(argv: list[str]) -> int:
            rc = orig_learn(argv)
            if rc == 0:
                try:
                    skill_mod = set_module("pipeline.gates", "agent_skill")
                    if skill_mod is not None and hasattr(skill_mod, "sync_learned_guards_to_skill"):
                        skill_mod.sync_learned_guards_to_skill(".")
                except Exception:
                    pass
            return rc

        tools["learn"] = _learn_wrapped
    return tools, tool_help


def main(argv: list[str] | None = None) -> int:
    core_platform.use_utf8_streams()
    return core_surface.guarded(lambda: _main(sys.argv[1:] if argv is None else argv))


def _main(line: list[str]) -> int:
    tools, tool_help = line_commands()
    ap = parser(tools, tool_help)
    if line[:1] in (["--version"], ["-V"]):
        init = set_module("pipeline", INIT_MODULE)
        return core_surface.print_version(init.installed_metadata if init is not None else None)
    if not line or line in (["-h"], ["--help"]) or line[0] == "help":      # the short help, at a terminal too: a bare run starts no wizard that writes files; ``help --all`` is the full one (2026-11-12)
        return core_help.run(line[1:] if line[:1] == ["help"] else [], tools, ap.format_help, lambda name: _main([name, "--help"]))
    if line[0] in tools:
        return tools[line[0]](line[1:])
    args = ap.parse_args(line)
    if args.cmd == "leaks":
        problems = leak_scan.scan_tree(args.root)
        print("\n".join(problems) or core_surface.no_leaks(args.root))
        return 1 if problems else 0
    try:
        return _dispatch(args)
    except UsageError as exc:
        print(f"brevio-core: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
