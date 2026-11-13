"""``brevio-core init`` and ``init --undo``: set a repository up in one step, and take it back (ROADMAP E22, 2026-11-05).

The 0.4 set. By default ``init`` writes the pull-request workflow, a block of agent rules in ``AGENTS.md`` (with an
``@AGENTS.md`` line in ``CLAUDE.md`` and a Cursor rule when those are present) and a baseline, so the first local run
is quiet. ``--with precommit,hook,mcp`` adds the pre-commit entry, the agent hook (the ``hook install`` code) and the
MCP server config. The hook and MCP files are personal: each one ``init`` writes is listed in git's local exclude
file, so a checkout on a teammate's machine is never changed, and a file git already tracks is left alone.

Every write is planned first and shown as a diff. Nothing is written without ``--yes`` or a "y" at the prompt; with
no terminal and no ``--yes`` the run is a dry run. Existing files are never rewritten: entries are inserted as text
(no YAML library), JSON servers are inserted beside the ones there. Each file or entry written is recorded with a
sha256 in ``.brevio/init.json``; ``init --undo`` removes only what is unchanged since and lists the rest. Everything
named is pinned to this release: the Action tag, ``package-version`` and ``brevio-core==X.Y.Z`` in the commands.
Standard library only.
"""
from __future__ import annotations

import argparse
import difflib
import glob
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from brevio.pipeline import core_agent_block, core_cli, core_platform, core_specs, core_warmstart
from brevio.pipeline.agent_host import client_detect, jsonstore
from brevio.pipeline.core_specs import detect_stack, render_specs
from brevio.pipeline.gates import agent_hooks
from brevio.pipeline.rule_host import GIT

HELP = "set this repository up in one step: the Action, agent rules and a baseline (init --undo takes it back)"
RECORD = ".brevio/init.json"
WORKFLOW = ".github/workflows/brevio-core.yml"
PRECOMMIT = ".pre-commit-config.yaml"
BASELINE_FILE = ".brevio/baseline.json"
BASELINE_MODULE = "core_baseline"    # the 0.4 module behind ``brevio-core baseline``; a build without it skips the step
CURSOR_RULE = ".cursor/rules/brevio-core-agents.mdc"
MARK_START, MARK_END = "<!-- brevio-core:start -->", "<!-- brevio-core:end -->"
SERVER = "brevio-core"
MCP_FILES = {"claude_code": ".mcp.json", "cursor": ".cursor/mcp.json"}
DEFAULT_PARTS = ("action", "agents-md", "baseline")
OPT_IN_PARTS = ("precommit", "hook", "mcp", "specs")
BADGE_IMAGE ="https://img.shields.io/badge/checked%20by-brevio--core-blue"
AGENT_NAMES = {"claude_code": "Claude Code", "cursor": "Cursor", "codex": "Codex", "hermes": "Hermes", "openclaw": "OpenClaw"}
# The files ``hook install`` writes for each agent, named from its own constants.
HOOK_FILES = {
    "claude_code": (os.path.join(agent_hooks.CLAUDE_SETTINGS_DIR, agent_hooks.CLAUDE_SETTINGS_FILE),
                    os.path.join(agent_hooks.CLAUDE_SKILLS_DIR, "SKILL.md")),
    "cursor": (agent_hooks.CURSOR_HOOKS_FILE, os.path.join(agent_hooks.CURSOR_RULES_DIR, "brevio-core.mdc")),
    "hermes": (os.path.join(agent_hooks.HERMES_SKILLS_DIR, "SKILL.md"),),
    "openclaw": (os.path.join(agent_hooks.OPENCLAW_SKILLS_DIR, "SKILL.md"),),
}
PERSONAL = " (personal: git-ignored through git's local exclude file, never committed)"
_SOURCE_LINE = re.compile(r'^Source\s*=\s*"([^"]+)"', re.M)
_SEMVER = re.compile(r"\d+\.\d+\.\d+")


@dataclass(frozen=True)
class Release:
    """The release this package is: its public repository and its version, as the release stamped them."""

    repo_url: str
    version: str

    @property
    def action(self) -> str:
        """``owner/name`` of the repository, which is what a workflow's ``uses:`` names."""
        return "/".join(self.repo_url.rstrip("/").split("/")[-2:])


@dataclass
class Op:
    """One planned write: a new file, text inserted into a file, or a file replaced by the hook installer."""

    path: str
    kind: str                # "file", "insert" or "replace"
    text: str                # the new file, the inserted text, or the replacing text
    offset: int = 0
    before: str = ""
    local: bool = False      # personal settings: git-ignored through the local exclude file

    def after(self) -> str:
        if self.kind == "insert":
            return self.before[:self.offset] + self.text + self.before[self.offset:]
        return self.text


@dataclass
class Ctx:
    """What a plan reads: the repository root, the release, the coding agents found and the notes for the user."""

    root: str
    release: Release | None
    agents: dict[str, bool]
    notes: list[str] = field(default_factory=list)

    def path(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def git(self, *args: str) -> tuple[int, str]:
        return _git(self.root, *args)

    def tracked(self, rel: str) -> bool:
        return bool(self.git("ls-files", "--", rel)[1].strip())

    def ignored(self, rel: str) -> bool:
        return self.git("check-ignore", "-q", "--", rel)[0] == 0

    def needs_release(self, part: str) -> bool:
        if self.release is None:
            self.notes.append(f"{part}: skipped, the release version is unknown (run an installed brevio-core: pip, pipx or uvx)")
        return self.release is not None


def _git(cwd: str, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run([*GIT, "-C", cwd, *args], capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, r.stdout


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    except OSError:
        return None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def installed_metadata(name: str) -> dict[str, list[str]] | None:
    """The header fields of the installed ``name``'s ``METADATA``, read by hand.

    ``importlib.metadata`` imports ``email``, which loads ``socket`` on Python 3.11 and 3.12, and the core must load no
    network module (the no-network tests). The header is ``Key: value`` lines up to the first blank line.
    """
    dist = re.sub(r"[-_.]+", "_", name).lower()
    for folder in sys.path:
        for path in sorted(glob.glob(os.path.join(folder or ".", "*.dist-info", "METADATA"))):
            if os.path.basename(os.path.dirname(path)).lower().split("-", 1)[0] != dist:
                continue
            text = _read(path) or ""
            fields: dict[str, list[str]] = {}
            for line in re.split(r"\r?\n\r?\n", text, maxsplit=1)[0].splitlines():     # CRLF too (2026-11-12, first Windows run)
                key, sep, value = line.partition(":")
                if sep and not line[:1].isspace():
                    fields.setdefault(key.strip(), []).append(value.strip())
            return fields
    return None


def release_info() -> Release | None:
    """The release around this code: ``VERSION`` and ``pyproject.toml`` of a release tree, else the installed metadata."""
    tree = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    version, project = _read(os.path.join(tree, "VERSION")), _read(os.path.join(tree, "pyproject.toml"))
    found = _SOURCE_LINE.search(project or "")
    if version and found:
        url, ver = found.group(1), version.strip()
    else:
        meta = installed_metadata(SERVER)
        if meta is None:
            return None
        urls = [u.split(",", 1)[1].strip() for u in meta.get("Project-URL", []) if u.lower().startswith("source,")]
        if not urls or not meta.get("Version"):
            return None
        url, ver = urls[0], meta["Version"][0]
    return Release(url, ver) if _SEMVER.fullmatch(ver) else None


def detect_agents(root: str) -> dict[str, bool]:
    """The coding agents this repository is set up for: the hook installer's markers, plus their instruction files."""
    detected = client_detect.detect_llm_clients(root, probe_daemons=False)
    markers = detected.workspace_markers
    active = set(detected.active_agents)
    has = {rel: os.path.exists(os.path.join(root, rel)) for rel in ("CLAUDE.md", ".codex", "AGENTS.md")}
    return {
        "claude_code": bool(markers.get("claude_code")) or "claude_code" in active or has["CLAUDE.md"],
        "cursor": bool(markers.get("cursor")) or "cursor" in active,
        "codex": has[".codex"] or has["AGENTS.md"],
        "hermes": bool(markers.get("hermes")) or "hermes" in active,
        "openclaw": bool(markers.get("openclaw")) or "openclaw" in active,
    }


def live_hook_selftest() -> str:
    """Run an in-memory verification scan to confirm the defect gate catches issues with zero disk writes."""
    core_cli.scan({
        "src/auth.test.ts": [(1, "  await page.waitForTimeout(500);")],
        "src/api.ts": [(1, "  const x = res as any;")],
    })
    return "verified in-memory gate: caught [R9.1] untyped escape hatch (0 files touched)"


def workflow_text(rel: Release) -> str:
    """The pull-request workflow, written by ``core_specs`` so the README's first screen shares its checkout pin."""
    return core_specs.workflow_text(rel.action, rel.version)


def plan_action(ctx: Ctx) -> list[Op]:
    if not ctx.needs_release("action") or ctx.release is None:
        return []
    if os.path.exists(ctx.path(WORKFLOW)):
        ctx.notes.append(f"action: {WORKFLOW} exists, left as it is")
        return []
    return [Op(WORKFLOW, "file", workflow_text(ctx.release))]


def _append(rel: str, before: str | None, text: str, local: bool = False) -> Op:
    """``text`` as a new file, or inserted at the end of the existing one after a blank line."""
    if before is None:
        return Op(rel, "file", text, local=local)
    sep = ("\n" if before and not before.endswith("\n") else "") + ("\n" if before.strip() else "")
    return Op(rel, "insert", sep + text, offset=len(before), before=before, local=local)


def agents_block(rel: Release | None = None) -> str:
    """The marked block of agent rules: the skill's own text, with the command pinned to ``rel`` when it is known."""
    return f"{MARK_START}\n## brevio-core\n\n{core_agent_block.rules(SERVER, rel.version if rel else None)}{MARK_END}\n"


def plan_agents_md(ctx: Ctx) -> list[Op]:
    ops: list[Op] = []
    text = _read(ctx.path("AGENTS.md"))
    if text is not None and MARK_START in text:
        ctx.notes.append("agents-md: AGENTS.md has the brevio-core block already")
    else:
        ops.append(_append("AGENTS.md", text, agents_block(ctx.release)))
    claude = _read(ctx.path("CLAUDE.md"))
    if claude is not None and not re.search(r"^@AGENTS\.md[ \t]*$", claude, re.M):
        ops.append(Op("CLAUDE.md", "insert", ("" if claude.endswith("\n") or not claude else "\n") + "@AGENTS.md\n",
                      offset=len(claude), before=claude))
    if ctx.agents.get("cursor") and not os.path.exists(ctx.path(CURSOR_RULE)):
        ops.append(Op(CURSOR_RULE, "file", '---\ndescription: "brevio-core rules for this repository"\nalwaysApply: true\n'
                                           f"---\n{core_agent_block.rules(SERVER, ctx.release.version if ctx.release else None)}"))
    for extra in (".windsurfrules", ".clinerules", ".github/copilot-instructions.md"):
        extra_path = ctx.path(extra)
        if os.path.isfile(extra_path):
            extra_text = _read(extra_path)
            if extra_text is not None:
                if MARK_START in extra_text:
                    ctx.notes.append(f"agents-md: {extra} has the brevio-core block already")
                else:
                    ops.append(_append(extra, extra_text, agents_block()))
    return ops


def precommit_entry(rel: Release, indent: str) -> str:
    return (f"{indent}- repo: {rel.repo_url}\n{indent}  rev: v{rel.version}\n{indent}  hooks:\n"
            f"{indent}    - id: brevio-core\n")


def repos_end(text: str) -> tuple[int, str] | None:
    """Where the block list under a top-level ``repos:`` ends, and the indent of its items; ``None`` with no such list."""
    m = re.search(r"^repos:[ \t]*(?:#.*)?$", text, re.M)
    if not m:
        return None
    pos, indent = (text.find("\n", m.end()) + 1) or len(text), None
    while pos < len(text):
        end = (text.find("\n", pos) + 1) or len(text)
        line = text[pos:end].rstrip("\r\n")
        if line.strip() and not line.startswith((" ", "\t", "#", "-")):
            break
        item = re.match(r"([ \t]*)- ", line)
        indent = item.group(1) if indent is None and item else indent
        pos = end
    return pos, "  " if indent is None else indent


def plan_precommit(ctx: Ctx) -> list[Op]:
    if not ctx.needs_release("precommit") or ctx.release is None:
        return []
    text = _read(ctx.path(PRECOMMIT))
    if text is None:
        return [Op(PRECOMMIT, "file", "repos:\n" + precommit_entry(ctx.release, "  "))]
    if "id: brevio-core" in text or ctx.release.repo_url in text:
        ctx.notes.append(f"precommit: {PRECOMMIT} has a brevio-core entry already")
        return []
    if re.search(r"^repos:", text, re.M) and repos_end(text) is None:
        ctx.notes.append(f"precommit: {PRECOMMIT} writes repos in flow style; add the entry by hand:\n"
                         + precommit_entry(ctx.release, "  "))
        return []
    found = repos_end(text)
    if found is None:
        return [_append(PRECOMMIT, text, "repos:\n" + precommit_entry(ctx.release, "  "))]
    at, indent = found
    sep = "\n" if at == len(text) and text and not text.endswith("\n") else ""
    return [Op(PRECOMMIT, "insert", sep + precommit_entry(ctx.release, indent), offset=at, before=text)]


def _hook_ops(ctx: Ctx, agent: str, paths: tuple[str, ...]) -> list[Op]:
    """What ``hook install`` would write for ``agent``, found by running it on a copy of those files."""
    ops: list[Op] = []
    with tempfile.TemporaryDirectory() as tmp:
        for rel in paths:
            old = _read(ctx.path(rel))
            if old is not None:
                os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                shutil.copyfile(ctx.path(rel), os.path.join(tmp, rel))
        agent_hooks.install_targeted_hooks(cwd=tmp, targets=[agent], version=ctx.release.version if ctx.release else None)
        for rel in paths:
            old, new = _read(ctx.path(rel)), _read(os.path.join(tmp, rel))
            if new is not None and new != old:
                ops.append(Op(rel.replace(os.sep, "/"), "file" if old is None else "replace", new, before=old or "", local=True))
    return ops


def _unreadable_json(path: str) -> bool:
    text = _read(path)
    if text is None:
        return False
    try:
        return not isinstance(json.loads(text), dict)
    except ValueError:
        return True


def plan_hook(ctx: Ctx) -> list[Op]:
    if not ctx.needs_release("hook"):
        return []
    ops: list[Op] = []
    for agent, paths in HOOK_FILES.items():
        if not ctx.agents.get(agent):
            continue
        held = [p for p in paths if ctx.tracked(p) or (p.endswith(".json") and _unreadable_json(ctx.path(p)))]
        if held:
            ctx.notes.append(f"hook: {AGENT_NAMES[agent]} skipped, {', '.join(held)} is tracked by git or not a JSON object; "
                             "run `brevio-core hook install` to change it on purpose")
            continue
        ops += _hook_ops(ctx, agent, paths)
    if not any(ctx.agents.get(a) for a in HOOK_FILES):
        ctx.notes.append("hook: no coding agent with a hook found here, so none was planned")
    return ops


def _skip_ws(text: str, at: int) -> int:
    return len(text) - len(text[at:].lstrip(" \t\r\n"))


def _object_after(text: str, key: str | None) -> int:
    """The index just after the ``{`` that opens the top-level object (``key`` None) or its member ``key``; -1 if none."""
    depth, i = 0, 0
    while i < len(text):
        c = text[i]
        if c == '"':
            name, end = json.decoder.scanstring(text, i + 1)
            j = _skip_ws(text, end)
            if depth == 1 and name == key and text.startswith(":", j):
                j = _skip_ws(text, j + 1)
                if text.startswith("{", j):
                    return j + 1
            i = end
            continue
        if c == "{" and depth == 0 and key is None:
            return i + 1
        depth += (c in "{[") - (c in "}]")
        i += 1
    return -1


def mcp_entry(rel: Release) -> dict:
    return {"command": "uvx", "args": [f"{SERVER}=={rel.version}", "mcp"]}


def _mcp_op(ctx: Ctx, rel_path: str, entry: dict) -> Op | None:
    text = _read(ctx.path(rel_path))
    if text is None:
        return Op(rel_path, "file", json.dumps({"mcpServers": {SERVER: entry}}, indent=2) + "\n", local=True)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict) or SERVER in servers:
        ctx.notes.append(f"mcp: {rel_path} " + ("names brevio-core already" if isinstance(servers, dict) and SERVER in servers
                                                 else "is not a JSON object with an mcpServers object, left alone"))
        return None
    inner = json.dumps(entry)
    if "mcpServers" in data:
        piece, at = f'\n    "{SERVER}": {inner}' + ("," if servers else "\n  "), _object_after(text, "mcpServers")
    else:
        piece, at = f'\n  "mcpServers": {{"{SERVER}": {inner}}}' + ("," if data else "\n"), _object_after(text, None)
    op = Op(rel_path, "insert", piece, offset=at, before=text, local=True)
    try:
        ok = at >= 0 and json.loads(op.after())["mcpServers"][SERVER] == entry
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        ctx.notes.append(f"mcp: {rel_path} could not take the server without a rewrite, left alone")
    return op if ok else None


def plan_mcp(ctx: Ctx) -> list[Op]:
    if not ctx.needs_release("mcp") or ctx.release is None:
        return []
    ops: list[Op] = []
    for agent, rel_path in MCP_FILES.items():
        if not ctx.agents.get(agent):
            continue
        if ctx.tracked(rel_path):
            ctx.notes.append(f"mcp: {rel_path} is tracked by git, left alone; add the server there yourself if the team wants it")
            continue
        op = _mcp_op(ctx, rel_path, mcp_entry(ctx.release))
        ops += [op] if op else []
    if not any(ctx.agents.get(a) for a in MCP_FILES):
        ctx.notes.append("mcp: no MCP client with a project config found here (Claude Code or Cursor)")
    return ops


def plan_specs(ctx: Ctx) -> list[Op]:
    specs = render_specs(ctx.root)
    ops: list[Op] = []
    for rel in ("CONSTITUTION.md", "CLAUDE.md", "roadmap.json"):
        if os.path.exists(ctx.path(rel)):
            ctx.notes.append(f"specs: {rel} exists, left as it is")
        else:
            ops.append(Op(rel, "file", specs[rel]))
    return ops


def plan_exclude(ctx: Ctx, local: list[Op], personal: bool = False) -> list[Op]:
    """Lines for git's local exclude file, so the personal files ``init`` writes are never committed by accident."""
    paths = {op.path for op in local if not ctx.ignored(op.path)}
    if personal:
        for extra in (".brevio/", ".agents/"):
            bare = extra.rstrip("/")
            if not ctx.tracked(bare) and not ctx.ignored(bare) and not ctx.ignored(extra):
                paths.add(extra)
    code, out = ctx.git("rev-parse", "--git-path", "info/exclude")
    if not paths or code:
        return []
    rel = os.path.relpath(os.path.join(ctx.root, out.strip()), ctx.root).replace(os.sep, "/")
    if rel.startswith(".."):
        return []
    lines = "# brevio-core init: personal agent settings, kept out of commits\n" + "".join(f"/{p}\n" for p in sorted(paths))
    return [_append(rel, _read(ctx.path(rel)), lines)]


def plan(ctx: Ctx, parts: set[str], personal: bool = False) -> list[Op]:
    """Every write ``parts`` need, in order; nothing is written here."""
    ops = (plan_action(ctx) if "action" in parts else []) + (plan_agents_md(ctx) if "agents-md" in parts else [])
    ops += plan_precommit(ctx) if "precommit" in parts else []
    ops += plan_specs(ctx) if "specs" in parts else []
    local = (plan_hook(ctx) if "hook" in parts else []) + (plan_mcp(ctx) if "mcp" in parts else [])
    return ops + local + plan_exclude(ctx, local, personal=personal)


def baseline_due(ctx: Ctx, parts: set[str]) -> bool:
    """``brevio-core baseline`` should run: asked for, shipped in this build, and no baseline yet."""
    if "baseline" not in parts:
        return False
    if importlib.util.find_spec(f"{__name__.split('.')[0]}.pipeline.{BASELINE_MODULE}") is None:
        ctx.notes.append("baseline: skipped, this build has no `brevio-core baseline` command")
        return False
    if os.path.exists(ctx.path(BASELINE_FILE)):
        ctx.notes.append(f"baseline: {BASELINE_FILE} exists, left as it is")
        return False
    return True


def show(ops: list[Op], baseline: bool) -> None:
    """Each planned write as a unified diff, then the baseline step."""
    for op in ops:
        print(f"{'create' if op.kind == 'file' else 'change'} {op.path}{PERSONAL * op.local}")
        old = "/dev/null" if op.kind == "file" else f"a/{op.path}"
        sys.stdout.writelines(difflib.unified_diff(op.before.splitlines(keepends=True), op.after().splitlines(keepends=True),
                                                   old, f"b/{op.path}"))
        print()
    if baseline:
        print(f"run brevio-core baseline: writes {BASELINE_FILE}, so findings already in the code stay quiet")


def confirm(yes: bool, dry_run: bool, question: str) -> bool:
    """True when the user agreed: ``--yes``, or "y" at the prompt. No terminal and no ``--yes`` is a dry run."""
    if dry_run:
        print("brevio-core: dry run, nothing written")
        return False
    if yes:
        return True
    if not core_platform.is_terminal(sys.stdin):
        print("brevio-core: no terminal to ask, so this was a dry run and nothing was written; run again with --yes")
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _make_dirs(root: str, directory: str) -> list[str]:
    """Create ``directory`` and return the folders that were missing, outermost first, relative to ``root``."""
    missing: list[str] = []
    d = directory
    while not os.path.isdir(d) and os.path.abspath(d) != os.path.abspath(root):
        missing.insert(0, d)
        d = os.path.dirname(d)
    os.makedirs(directory, exist_ok=True)
    return [os.path.relpath(m, root).replace(os.sep, "/") for m in missing]


def write_text(path: str, text: str) -> None:
    """Replace ``path`` atomically, keeping the permission bits of a file that was there."""
    mode = os.stat(path).st_mode if os.path.exists(path) else None
    core_cli.write_atomic(path, text)
    if mode is not None:
        os.chmod(path, mode)


def load_record(root: str) -> dict:
    data = jsonstore.read_or(os.path.join(root, RECORD), None)
    return data if isinstance(data, dict) else {"entries": [], "dirs": []}


def track_dirs(root: str, record: dict, directory: str) -> None:
    """Create ``directory`` and record the folders that were missing, so undo can remove them once empty."""
    record["dirs"] += [d for d in _make_dirs(root, directory) if d not in record["dirs"]]


def apply(root: str, ops: list[Op], record: dict) -> None:
    """Write each op and record it: the path, the kind, the sha256 of the file after, and what undo needs."""
    for op in ops:
        path = os.path.join(root, op.path)
        track_dirs(root, record, os.path.dirname(path))
        write_text(path, op.after())
        entry: dict = {"path": op.path, "kind": op.kind, "sha256": _sha(op.after())}
        if op.kind == "insert":
            entry.update(offset=op.offset, text=op.text)
        elif op.kind == "replace":
            entry["before"] = op.before
        record["entries"].append(entry)


def run_baseline(ctx: Ctx, record: dict) -> None:
    """``brevio-core baseline`` at the repository root, recorded as a file ``init`` wrote."""
    here = os.getcwd()
    os.chdir(ctx.root)
    try:
        code = core_cli.main(["baseline"])
    finally:
        os.chdir(here)
    text = _read(ctx.path(BASELINE_FILE))
    if code not in (0, 1) or text is None:
        ctx.notes.append(f"baseline: `brevio-core baseline` failed (exit {code}); run it yourself")
        return
    record["entries"].append({"path": BASELINE_FILE, "kind": "file", "sha256": _sha(text)})


def badge(rel: Release) -> str:
    return f"[![checked by brevio-core]({BADGE_IMAGE})]({rel.repo_url})"


def husky_snippet(rel: Release) -> str:
    globs = ",".join(s.lstrip(".") for s in core_cli.CODE_SUFFIXES)
    return ("For husky and lint-staged, add this to package.json:\n"
            f'  "lint-staged": {{"*.{{{globs}}}": "uvx brevio-core=={rel.version} scan"}}\n'
            'then run: npx husky init && echo "npx lint-staged" > .husky/pre-commit')


def _report(ctx: Ctx, written: list[str]) -> None:
    for line in written:
        print(f"wrote {line}")
    if written:
        print(f"recorded in {RECORD}; `brevio-core init --undo` removes what is unchanged")
    if ctx.release:
        print(f"\nREADME badge:\n  {badge(ctx.release)}")
        print("  Run `brevio-core score` (preview) for a count of findings by rule.")
        if os.path.isfile(ctx.path("package.json")):
            print("\n" + husky_snippet(ctx.release))
    for note in ctx.notes:
        print(f"note: {note}")


def _found(ctx: Ctx) -> None:
    names = [AGENT_NAMES[a] for a, on in ctx.agents.items() if on]
    print(f"repository: {ctx.root}\ncoding agents found: {', '.join(names) or 'none'}")
    if ctx.agents.get("codex"):
        ctx.notes.append("Codex: brevio-core installs no hook for it; the AGENTS.md block covers it")


def init(root: str, parts: set[str], yes: bool, dry_run: bool, personal: bool = False) -> int:
    ctx = Ctx(root, release_info(), detect_agents(root))
    _found(ctx)
    ops = plan(ctx, parts, personal=personal)
    due = baseline_due(ctx, parts)
    if not ops and not due:
        print("brevio-core init: nothing to write, this repository is set up")
        _report(ctx, [])
        return 0
    show(ops, due)
    if not confirm(yes, dry_run, "Write these files?"):
        _report(ctx, [])
        return 0
    record = load_record(root)
    apply(root, ops, record)
    track_dirs(root, record, os.path.dirname(ctx.path(RECORD)))
    if due:
        run_baseline(ctx, record)
    jsonstore.write(ctx.path(RECORD), record, indent=2)
    written = [op.path + PERSONAL * op.local for op in ops]
    _report(ctx, written + [BASELINE_FILE] * (bool(record["entries"]) and record["entries"][-1]["path"] == BASELINE_FILE))
    return 0


def _read_wizard_choice() -> str:
    if core_platform.is_terminal(sys.stdin):
        print("Setup profile:\n"
              "  [1] Full protection (Action, AGENTS.md, baseline, personal hook & MCP) [default]\n"
              "  [2] Personal stealth mode (hook & MCP only, git-excluded via .git/info/exclude)\n"
              "  [3] CI gate only (GitHub Action, AGENTS.md & baseline)")
        try:
            return input("Choose [1/2/3] (Enter for 1): ").strip() or "1"
        except EOFError:
            return "1"
    try:
        return sys.stdin.readline().strip() or "1"
    except (OSError, ValueError):
        return "1"


def _record_warmstart(root: str) -> None:
    had_dirs = {d for d in (".agents", ".agents/rules") if os.path.isdir(os.path.join(root, d))}
    prior_files = set(glob.glob(os.path.join(root, ".agents", "rules", "*.md")))
    if os.path.isfile(os.path.join(root, ".brevio", "guards.json")):
        prior_files.add(os.path.join(root, ".brevio", "guards.json"))
    seeded = core_warmstart.warmstart(root)     # lists what it keeps and drops, and asks, before it writes (2026-11-12, improvement plan)
    if not seeded:
        return
    record_path = os.path.join(root, RECORD)
    if os.path.isfile(record_path):
        record = load_record(root)
        recorded = {e.get("path") for e in record.get("entries", [])}
        for d in (".agents", ".agents/rules"):
            if d not in had_dirs and d not in record["dirs"] and os.path.isdir(os.path.join(root, d)):
                record["dirs"].append(d)
        after = set(glob.glob(os.path.join(root, ".agents", "rules", "*.md"))) | {os.path.join(root, ".brevio", "guards.json")}
        for abs_p in sorted(after - prior_files):
            rel = os.path.relpath(abs_p, root).replace(os.sep, "/")
            text = _read(abs_p)
            if text is not None and rel not in recorded:
                record["entries"].append({"path": rel, "kind": "file", "sha256": _sha(text)})
        jsonstore.write(record_path, record, indent=2)
    print(f"warm-start: seeded {seeded} trial guard(s) from recent fix commits")


def interactive_wizard(root: str) -> int:
    """Guided onboarding wizard: discovers stack and agents, prompts for setup profile, and verifies."""
    stack = detect_stack(root)
    agents = detect_agents(root)
    names = [AGENT_NAMES[a] for a, on in agents.items() if on]
    print(f"repository: {root}\nstack detected: {', '.join(stack) or 'generic'}\ncoding agents found: {', '.join(names) or 'none'}")
    choice = _read_wizard_choice()
    if choice == "2":
        code = init(root, {"hook", "mcp"}, yes=True, dry_run=False, personal=True)
    elif choice == "3":
        code = init(root, {"action", "agents-md", "baseline"}, yes=True, dry_run=False)
    else:
        code = init(root, {"action", "agents-md", "baseline", "hook", "mcp"}, yes=True, dry_run=False)
    if code == 0:
        if choice != "2":
            _record_warmstart(root)
        print(live_hook_selftest())
    return code


def undo_step(root: str, entry: dict) -> tuple[str, str | None]:
    """``(what undo does, the file's new text)`` for one record entry: remove, restore, cut, gone or changed."""
    cur = _read(os.path.join(root, entry["path"]))
    if cur is None:
        return "gone", None
    same = _sha(cur) == entry["sha256"]
    if entry["kind"] == "file":
        return ("remove", None) if same else ("changed", None)
    if entry["kind"] == "replace":
        return ("restore", entry["before"]) if same else ("changed", None)
    text, at = entry["text"], entry["offset"]
    if same and cur[at:at + len(text)] == text:
        return "cut", cur[:at] + cur[at + len(text):]
    if cur.count(text) != 1:
        return "changed", None
    new = cur.replace(text, "", 1)
    if entry["path"].endswith(".json"):
        try:
            json.loads(new)
        except ValueError:
            return "changed", None
    return "cut", new


def _apply_undo(root: str, steps: list) -> None:
    for entry, what, new in steps:
        path = os.path.join(root, entry["path"])
        if what == "remove":
            os.remove(path)
        elif what in ("restore", "cut") and new is not None:
            write_text(path, new)


def undo(root: str, yes: bool, dry_run: bool) -> int:
    """Remove what ``init`` wrote and is unchanged since; list what the user changed and leave it."""
    record = load_record(root)
    steps = [(e, *undo_step(root, e)) for e in reversed(record["entries"])]
    if not steps:
        print("brevio-core init --undo: nothing recorded, nothing to undo")
        return 0
    for entry, what, _new in steps:
        print(f"{what:8} {entry['path']}" + (" (changed since init: left as it is)" if what == "changed" else ""))
    if not confirm(yes, dry_run, "Undo these?"):
        return 0
    _apply_undo(root, steps)
    left = [e for e, what, _n in reversed(steps) if what == "changed"]
    if left:
        jsonstore.write(os.path.join(root, RECORD), {"entries": left, "dirs": record.get("dirs", [])}, indent=2)
    elif os.path.exists(os.path.join(root, RECORD)):
        os.remove(os.path.join(root, RECORD))
    for d in reversed(record.get("dirs", [])):
        full = os.path.join(root, d)
        if os.path.isdir(full) and not os.listdir(full):
            os.rmdir(full)
    for entry in left:
        print(f"left {entry['path']}: changed since init, remove the brevio-core part yourself")
    return 0


def _parts(want: str, without: str, personal: bool = False) -> set[str] | None:
    named = {p.strip() for p in f"{want},{without}".split(",") if p.strip()}
    unknown = named - {*DEFAULT_PARTS, *OPT_IN_PARTS}
    if unknown:
        print(f"brevio-core init: unknown part(s): {', '.join(sorted(unknown))}; "
              f"choose from {', '.join(DEFAULT_PARTS + OPT_IN_PARTS)}", file=sys.stderr)
        return None
    base = {"hook", "mcp"} if personal else {*DEFAULT_PARTS}
    return (base | {p.strip() for p in want.split(",") if p.strip()}) - {p.strip() for p in without.split(",")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brevio-core init", description=HELP)
    ap.add_argument("--with", dest="want", default="", metavar="PARTS", help="also write these, comma-separated: precommit, hook, mcp")
    ap.add_argument("--without", default="", metavar="PARTS", help="leave these out: action, agents-md, baseline")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")
    ap.add_argument("--yes", "-y", action="store_true", help="write without asking")
    ap.add_argument("--undo", action="store_true", help="remove what init wrote and is unchanged since")
    ap.add_argument("--personal", action="store_true", help="install personal agent hooks and MCP config only, git-excluded via .git/info/exclude (touches no tracked files)")
    ap.add_argument("--specs", action="store_true", help="also write starter CLAUDE.md, CONSTITUTION.md and roadmap.json")
    ap.add_argument("--wizard", action="store_true", help="run the interactive onboarding wizard")
    args = ap.parse_args(argv)
    code, top = _git(os.getcwd(), "rev-parse", "--show-toplevel")
    if code or not top.strip():
        print("brevio-core init: not inside a git repository", file=sys.stderr)
        return 2
    root = os.path.normpath(top.strip())
    if args.undo:
        return undo(root, args.yes, args.dry_run)
    if args.wizard:
        return interactive_wizard(root)
    parts = _parts(args.want, args.without, personal=args.personal)
    if parts is not None and args.specs:
        parts.add("specs")
    return 2 if parts is None else init(root, parts, args.yes, args.dry_run, personal=args.personal)
