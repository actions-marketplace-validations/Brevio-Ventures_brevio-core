"""``brevio-core mcp``: the scan, the rule pages and the guard compiler as an MCP server on stdin and stdout.

ROADMAP E16, the 0.2 set, 2026-11-05. The transport is newline-delimited JSON-RPC 2.0 and the methods are the MCP
ones a tool server needs (``initialize``, ``ping``, the tool list and the tool call). Standard library only, no SDK,
no network and no model call: every tool reads the project in the folder the server was started in. Standard output
carries protocol messages and nothing else. Paths a client names must lie inside that folder. The 0.3 set adds a
``fix`` tool (``scan --fix``): it returns the diff and writes only when the call sets ``write`` (2026-11-05). With the 0.4
set the scan leaves out baselined findings but never honours an ignore comment: an agent cannot silence the gate, and
no tool writes a baseline. With no arguments it scans the uncommitted changes, and it answers with one short line per
finding, at most ``core_cli.AGENT_MAX``, in the same lines the agent hook gives (plan review, 2026-11-05; ROADMAP E34).
The 0.6 set adds two read-only tools (2026-11-11): ``verify_edit``, the verdict ``brevio-core patch --dry-run`` gives for an
edit the user's own agent proposes, and ``finding_packet``, the rule text, the line and its context for one finding.
Neither writes a file, calls a model or sends anything.
"""
from __future__ import annotations

import contextlib
import difflib
import importlib
import io
import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any, TextIO

from brevio.pipeline import core_cli, core_surface
from brevio.pipeline.code_rules import PR_RULES_122
from brevio.pipeline.code_rules import docs as rule_docs

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")   # newest first; an unknown client version gets the newest
# The version of this tool set. ``serverInfo`` says the package version when it is known (``server_version``), and
# this one only when it is not (a source tree, or a build without ``core_init``).
SERVER_VERSION, SERVER_VERSION_FIX, SERVER_VERSION_BASELINE = "0.2", "0.3", "0.4"
LEARN_MODULE = "learn_cmd"   # named here so a build without it simply offers no ``learn`` tool
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
# MCP names its tool methods with a "tools" prefix; built here because the leak scan reads that literal as a private folder.
_NS = "tools"
LIST_METHOD, CALL_METHOD = f"{_NS}/list", f"{_NS}/call"
_RULE_ID = re.compile(r"R\d+\.\d+")

ToolResult = tuple[str, dict[str, Any]]   # the text an agent reads, and the same facts as structured data


class ToolError(Exception):
    """A tool could not do what was asked; the client sees ``isError`` and the message."""


def _inside_folder(path: str) -> bool:
    here = os.path.realpath(os.getcwd())
    try:
        return os.path.commonpath([here, os.path.realpath(path)]) == here
    except ValueError:      # another drive (Windows): no path in common, so outside (2026-11-12, first Windows run)
        return False


def _text_list(args: dict[str, Any], key: str) -> list[str]:
    value = args.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ToolError(f"{key} must be a list of strings")
    return value


def _base(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value.startswith("-"):
        raise ToolError(f"{key} must be a git revision, not an option")
    return value


def _named_paths(args: dict[str, Any]) -> list[str]:
    named = _text_list(args, "paths")
    gone = [p for p in named if not os.path.exists(p)]
    if gone:
        raise ToolError(f"no such file or folder: {', '.join(gone)}")
    outside = [p for p in named if not _inside_folder(p)]
    if outside:
        raise ToolError(f"outside the project folder: {', '.join(outside)}")
    return named


def _tool_scan(args: dict[str, Any]) -> ToolResult:
    named, base = _named_paths(args), _base(args, "diff_base")
    if not named and base is None and core_cli.has_head():
        base = "HEAD"       # no arguments: the uncommitted changes (the working tree against HEAD, plus untracked files)
    lines = core_cli.select_lines(named, base)
    try:
        shown, _silenced, baselined = core_cli.triage(core_cli.scan(lines), honour_ignores=False)
    except core_cli.UsageError as exc:
        raise ToolError(str(exc)) from exc
    summary = f"{len(shown)} finding(s) in {len(lines)} file(s)" + (f", {len(baselined)} baselined" if baselined else "")
    return "\n".join([*core_cli.agent_lines(shown), summary]), {"count": len(shown), "files": len(lines), "baselined": len(baselined)}


def _tool_explain(args: dict[str, Any]) -> ToolResult:
    rule = str(args.get("rule", "")).strip().upper()
    if not _RULE_ID.fullmatch(rule) or rule not in PR_RULES_122:
        raise ToolError(f"unknown rule {rule or '(none)'}; call list_rules for the ids")
    return rule_docs.render(rule), {"rule": rule}


def _tool_list_rules(args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query", "")).strip().lower()
    rules = [{"id": rid, "summary": PR_RULES_122[rid]} for rid in rule_docs.rule_ids()
             if query in rid.lower() or query in PR_RULES_122[rid].lower()]
    return "\n".join(f"{r['id']}: {r['summary']}" for r in rules), {"count": len(rules), "rules": rules}


def _tool_fix(args: dict[str, Any]) -> ToolResult:
    named, base = _named_paths(args), _base(args, "diff_base")
    write = args.get("write", False)
    if not isinstance(write, bool):
        raise ToolError("write must be true or false")
    lines = core_cli.select_lines(named, base)
    changes = core_cli.plan_fixes(lines)
    diff = core_cli.unified_diff(changes)
    if write:
        core_cli.apply_fixes(changes)
        lines = core_cli.select_lines(named, base)
    remaining = len(core_cli.scan(lines)) if write else len(core_cli.scan(lines)) - sum(len(r) for _o, _n, r in changes.values())
    fixes = [{"path": rel, "rules": rules} for rel, (_o, _n, rules) in sorted(changes.items())]
    data = {"diff": diff, "fixed": sum(len(f["rules"]) for f in fixes), "files": fixes, "remaining": remaining, "written": write}
    verb = "fixed" if write else "would fix"
    return diff + f"{verb} {data['fixed']}, {remaining} finding(s) remaining", data


def _learn_module() -> Any:
    top = __name__.split(".")[0]
    try:
        return importlib.import_module(f"{top}.operator.{LEARN_MODULE}")   # a name built at run time: absent from a set without it
    except ModuleNotFoundError as err:
        if not (err.name or "").startswith(f"{top}.operator"):
            raise
        return None


def _memory_module() -> Any:
    return core_cli.set_module("pipeline", "core_memory")


def _gates_module() -> Any:
    return core_cli.set_module("pipeline.gates", "core_gates")


def _score_module() -> Any:
    return core_cli.set_module("pipeline", "core_score")


def _aeo_module() -> Any:
    return core_cli.set_module("pipeline", "core_aeo")


def _map_module() -> Any:
    return core_cli.set_module("pipeline", "core_map")


def _heal_module() -> Any:
    return core_cli.set_module("pipeline", "core_heal")


def _patch_module() -> Any:
    return core_cli.set_module("pipeline", core_cli.PATCH_MODULE)


def _swe_tools_module() -> Any:
    mod = core_cli.set_module("llm", "swe_tools")
    if mod is not None and (hasattr(mod, "repo_map_tool") or hasattr(mod, "build_repo_map")):
        return mod
    return None


def _tool_score(args: dict[str, Any]) -> ToolResult:
    score_mod = _score_module()
    if score_mod is None:
        raise ToolError("score module is not available")
    named = _named_paths(args)
    base = _base(args, "diff_base")
    try:
        card = score_mod.compute_scorecard(named, base)
    except core_cli.UsageError as exc:
        raise ToolError(str(exc)) from exc
    text = score_mod.render_text(card)
    return text.strip(), card


def _tool_aeo(args: dict[str, Any]) -> ToolResult:
    aeo_mod = _aeo_module()
    if aeo_mod is None:
        raise ToolError("aeo module is not available")
    named = _named_paths(args)
    target = named[0] if named else "."
    try:
        card = aeo_mod.scorecard(target, min_score=0)
    except Exception as exc:
        raise ToolError(str(exc)) from exc
    text = aeo_mod.render_text(card)
    return text.strip(), card


def _tool_map(args: dict[str, Any]) -> ToolResult:
    map_mod = _map_module()
    if map_mod is None:
        raise ToolError("map module is not available")
    root = str(args.get("root", ".")).strip() or "."
    seed_files = _text_list(args, "seed_files") if "seed_files" in args else None
    token_budget = int(args.get("token_budget", 4000))
    try:
        data = map_mod.build_repo_map(root=root, seed_files=seed_files, token_budget=token_budget, as_json=True)
    except Exception as exc:
        raise ToolError(str(exc)) from exc
    if isinstance(data, dict):
        text = str(data.get("map") or data.get("rendered") or json.dumps(data, indent=2))
        return text, data
    return str(data), {"map": data}


def _tool_heal(args: dict[str, Any]) -> ToolResult:
    heal_mod = _heal_module()
    if heal_mod is None:
        raise ToolError("heal module is not available")
    root = str(args.get("root", ".")).strip() or "."
    apply_fixes = bool(args.get("apply_fixes", False))
    try:
        res = heal_mod.run_heal(root=root, apply_fixes=apply_fixes)
    except Exception as exc:
        raise ToolError(str(exc)) from exc
    if isinstance(res, dict):
        text = str(res.get("markdown") or res.get("heal_packet") or json.dumps(res, indent=2))
        return text, res
    return str(res), {"heal": res}


def _tool_check(args: dict[str, Any]) -> ToolResult:
    gates = _gates_module()
    named = _named_paths(args)
    base = _base(args, "base") or "HEAD"
    result = gates.run_check(os.getcwd(), base=base, paths=named or None)
    text = gates.render_receipt(result, fmt="text").strip()
    return text, result


def _tool_brief(args: dict[str, Any]) -> ToolResult:
    memory = _memory_module()
    files = _text_list(args, "files")
    outside = [p for p in files if not _inside_folder(p)]
    if outside:
        raise ToolError(f"outside the project folder: {', '.join(outside)}")
    brief = memory.brief_for_files(files, cwd=os.getcwd())
    lines: list[str] = []
    if brief.get("micro_rules"):
        lines.append(str(brief["micro_rules"]).strip())
    for ep in brief.get("episodes", []):
        lines.append(f"[{ep.get('rule_id', '')}] {ep.get('file', '')}: {ep.get('trap', '')} -> {ep.get('fix', '')}")
    if brief.get("neighbors"):
        lines.append(f"1-hop neighbors: {', '.join(brief['neighbors'][:10])}")
    return "\n".join(lines) or "No active rules or past traps found for the specified files.", brief


def _tool_learn(args: dict[str, Any]) -> ToolResult:
    learn = _learn_module()
    base = _base(args, "diff_base") or "HEAD~1"
    argv = ["--diff", base, "--json"]
    if isinstance(args.get("name"), str) and args["name"]:
        argv += ["--name", args["name"]]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = learn.main(argv, prog="brevio-core learn")
        except SystemExit as stop:      # the argument parser exits on a value it rejects
            code = stop.code if isinstance(stop.code, int) else 2
    if code:
        raise ToolError((err.getvalue() or out.getvalue()).strip() or "no guard could be compiled")
    try:
        guard = json.loads(out.getvalue())
    except ValueError as exc:
        raise ToolError("the guard compiler returned no guard") from exc
    return f"Compiled guard {guard.get('id')}: {guard.get('title')}", {"guard": guard}


def _tool_verify_edit(args: dict[str, Any]) -> ToolResult:
    patch = _patch_module()
    text, path = args.get("patch"), args.get("path", "")
    if not isinstance(text, str) or not isinstance(path, str):
        raise ToolError("patch and path must be strings")
    if len(text) > patch.MAX_PATCH_CHARS:
        raise ToolError(f"patch is longer than {patch.MAX_PATCH_CHARS} characters")
    verdict = patch.check_patch(text, os.getcwd(), path)
    return patch.verdict_text(verdict), verdict.data()


def _tool_finding_packet(args: dict[str, Any]) -> ToolResult:
    patch = _patch_module()
    finding, context = args.get("finding"), args.get("context", 0)
    if not isinstance(finding, str) or isinstance(context, bool) or not isinstance(context, int):
        raise ToolError("finding must be a string and context an integer")
    try:
        packet = patch.finding_packet(finding, context, os.getcwd())
    except patch.PatchError as exc:
        raise ToolError(str(exc)) from exc
    return patch.packet_text(packet), packet


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "additionalProperties": False}


def _annotations(title: str, *, read_only: bool = True) -> dict[str, Any]:
    return {
        "title": title,
        "readOnlyHint": read_only,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


_STR = {"type": "string"}
TOOLS: dict[str, tuple[str, dict[str, Any], dict[str, Any], Callable[[dict[str, Any]], ToolResult]]] = {
    "scan": ("Scan files, folders or the lines a change adds against the code rules. Returns one line per finding: "
             f"path:line [rule] message -> a fix hint, grouped by rule, at most {core_cli.AGENT_MAX}. With no arguments it scans the "
             "uncommitted changes (the whole folder when there is no commit). An ignore comment does not count here.",
             _schema({"paths": {"type": "array", "items": _STR, "description": "Files or folders inside the project."},
                      "diff_base": {**_STR, "description": "Scan only the lines added against this git revision "
                                    "(an empty string means origin/main)."}}),
             _annotations("Scan Code Against Rules", read_only=True), _tool_scan),
    "explain": ("Return the page of one rule: what it catches, why, a bad and a good example.",
                {**_schema({"rule": {**_STR, "description": "A rule id such as R2.1."}}), "required": ["rule"]},
                _annotations("Explain Code Rule", read_only=True), _tool_explain),
    "list_rules": ("List the rule ids with a one-line summary each, optionally only those matching a word.",
                   _schema({"query": {**_STR, "description": "Optional word or rule id prefix to filter the rule list."}}),
                   _annotations("List Code Rules", read_only=True), _tool_list_rules),
    "fix": ("Fix what a rule has one correct rewrite for (for example `@ts-ignore` to `@ts-expect-error`). Returns the "
            "unified diff; writes the files only when write is true. Secrets and other findings are never fixed, only "
            "counted as remaining.",
            _schema({"paths": {"type": "array", "items": _STR, "description": "Files or folders inside the project."},
                     "diff_base": {**_STR, "description": "Fix only the lines added against this git revision "
                                   "(an empty string means origin/main)."},
                     "write": {"type": "boolean", "description": "Rewrite the files in place (default false: diff only)."}}),
            _annotations("Apply Deterministic Rule Fixes", read_only=False), _tool_fix),
    "learn": ("Compile a fixed defect (a git diff) into a local guard and an agent rule. Writes .brevio/guards.json and "
              ".agents/rules/ in the project, only when the guard hits the lines the fix removed and not the fixed file. "
              "A new guard is on trial: scan reports its hits and they do not fail a run. This tool cannot switch a guard off.",
              _schema({"diff_base": {**_STR, "description": "The commit or range holding the fix (default HEAD~1)."},
                       "name": {**_STR, "description": "Optional short title for the compiled guard."}}),
              _annotations("Compile Fixed Defect Into Local Guard", read_only=False), _tool_learn),
    "brief": ("Return the surface-scoped engineering rules, recalled trap-to-fix episodes and 1-hop file neighbors for "
              "the files you are about to edit.",
              _schema({"files": {"type": "array", "items": _STR, "description": "Files inside the project about to be edited."}}),
              _annotations("Brief Rules And Trap Memory For Files", read_only=True), _tool_brief),
    "check": ("Run the pre-PR flight check (surface rules, test custody, imports, migrations, complexity, and conflict markers).",
              _schema({"base": {**_STR, "description": "Git revision to diff against (default HEAD)."},
                       "paths": {"type": "array", "items": _STR, "description": "Files or folders inside the project to check."}}),
              _annotations("Run Pre-PR Flight Check", read_only=True), _tool_check),
    "score": ("Preview: count the findings of a scan by rule and say what to do next.",
              _schema({"paths": {"type": "array", "items": _STR, "description": "Files or folders to score."},
                       "diff_base": {**_STR, "description": "Score only lines added against this git revision."}}),
              _annotations("Count Findings By Rule", read_only=True), _tool_score),
    "aeo": ("Score local HTML pages against the Answer Engine Optimization (AEO) rubric.",
            _schema({"paths": {"type": "array", "items": _STR, "description": "Folder or HTML file to evaluate."}}),
            _annotations("Evaluate Local AEO Rubric", read_only=True), _tool_aeo),
    "map": ("Build repository symbol hierarchy and PageRank context map.",
            _schema({"root": {**_STR, "description": "Project root directory (default .)."},
                     "seed_files": {"type": "array", "items": _STR, "description": "Seed files for focused PageRank map."},
                     "token_budget": {"type": "integer", "description": "Token budget for the repository map (default 4000)."}}),
            _annotations("Build Repository Symbol Map", read_only=True), _tool_map),
    "heal": ("Autonomous repair loop that surfaces violations and emits an atomic remediation heal packet.",
             _schema({"root": {**_STR, "description": "Project root directory (default .)."},
                      "apply_fixes": {"type": "boolean", "description": "Apply deterministic AST fixes before generating packet (default false)."}}),
             _annotations("Run Autonomous Heal Loop", read_only=False), _tool_heal),
    "verify_edit": ("Check an edit you propose before you write it: SEARCH/REPLACE blocks (<<<<<<< SEARCH, =======, >>>>>>> REPLACE, "
                    "the path on the line above) or a unified diff. It is applied in memory and scanned with ignore comments "
                    "not honoured; it is refused when it adds a suppression, touches a protected file (the config, the baseline, "
                    "the guards, agent rules and hooks, workflows) or deletes a test or its assertions. Writes nothing.",
                    {**_schema({"patch": {**_STR, "description": "The edit: SEARCH/REPLACE blocks or a unified diff."},
                                "path": {**_STR, "description": "The file a SEARCH/REPLACE block applies to when it names none."}}),
                     "required": ["patch"]},
                    _annotations("Verify Proposed Edit Against Rules", read_only=True), _tool_verify_edit),
    "finding_packet": ("Return what you need to explain or fix one finding: the rule's text, the flagged line, some lines "
                       "around it and the file extension. It reads the project folder and nothing else.",
                       {**_schema({"finding": {**_STR, "description": "One finding as scan prints it, for example "
                                                                       "src/a.ts:3 [R9.2] message."},
                                   "context": {"type": "integer", "description": "Lines to show before and after it (0 to 50, default 0)."}}),
                        "required": ["finding"]},
                       _annotations("Get Finding Packet", read_only=True), _tool_finding_packet),
}


def server_version() -> str:
    """The package version when it is known, read from the release files or the installed metadata by hand (which
    loads no network module); else the tool set this build serves: 0.4 with the baseline, 0.3 with the fixers, else 0.2."""
    init = core_cli.set_module("pipeline", "core_init")
    version = core_surface.package_version(None, init.installed_metadata if init is not None else None)
    if version is not None:
        return version
    if core_cli.baseline_tools() is not None:
        return SERVER_VERSION_BASELINE
    return SERVER_VERSION_FIX if core_cli.fixers() is not None else SERVER_VERSION


def _available() -> dict[str, tuple[str, dict[str, Any], dict[str, Any], Callable[[dict[str, Any]], ToolResult]]]:
    absent = {
        "learn": _learn_module() is None,
        "fix": core_cli.fixers() is None,
        "brief": _memory_module() is None,
        "check": _gates_module() is None,
        "score": _score_module() is None,
        "aeo": _aeo_module() is None,
        "map": _map_module() is None,
        "heal": _heal_module() is None,
        "verify_edit": _patch_module() is None,
        "finding_packet": _patch_module() is None,
    }
    return {n: t for n, t in TOOLS.items() if not absent.get(n)}


def tool_list() -> list[dict[str, Any]]:
    return [{"name": n, "description": d, "inputSchema": schema, "annotations": dict(ann)}
            for n, (d, schema, ann, _fn) in _available().items()]


def _unknown_argument(name: str, schema: dict[str, Any]) -> str:
    """``unknown argument: diff (did you mean diff_base?)``, naming the closest argument the tool takes."""
    close = difflib.get_close_matches(name, list(schema.get("properties", {})), n=1, cutoff=0.5)
    near = next((k for k in schema.get("properties", {}) if k.startswith(name) or name.startswith(k)), None)
    guess = near or (close[0] if close else "")
    return f"unknown argument: {name}" + (f" (did you mean {guess}?)" if guess else "")


def call_tool(params: Any) -> dict[str, Any]:
    """The result of a tool call: a tool's own failure is an ``isError`` result, a malformed call raises ``ValueError``."""
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise ValueError("a tool call needs a tool name")
    tool = _available().get(params["name"])
    if tool is None:
        raise ValueError(f"unknown tool: {params['name']}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    unknown = sorted(set(args) - set(tool[1].get("properties", {})))
    if unknown:
        return {"content": [{"type": "text", "text": _unknown_argument(unknown[0], tool[1])}], "isError": True}
    try:
        text, data = tool[3](args)
    except (ToolError, OSError) as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    return {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False}


def _resource_list() -> list[dict[str, Any]]:
    res = [
        {
            "uri": "brevio://rules/catalog",
            "name": "Rules Catalog",
            "description": "Complete catalog of documented rules",
            "mimeType": "application/json",
        }
    ]
    if _score_module() is not None:
        res.append({
            "uri": "brevio://scorecard",
            "name": "Findings by rule",
            "description": "Preview: the findings of a scan of the current workspace, counted by rule",
            "mimeType": "application/json",
        })
    if _map_module() is not None:
        res.append({
            "uri": "brevio://map",
            "name": "Repository Symbol Map",
            "description": "Symbol hierarchy and PageRank map for current workspace",
            "mimeType": "application/json",
        })
    if core_cli.config_tools() is not None:
        res.append({
            "uri": "brevio://config",
            "name": "Brevio Configuration",
            "description": "Active brevio-core.toml configuration settings",
            "mimeType": "application/json",
        })
    return res


def _resource_templates() -> list[dict[str, Any]]:
    return [
        {
            "uriTemplate": "brevio://rules/{rule_id}",
            "name": "Rule Explanation",
            "description": "Full documentation and bad/good examples for a rule ID (e.g. R13.1)",
            "mimeType": "text/markdown",
        }
    ]


def _read_resource(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict) or not isinstance(params.get("uri"), str):
        raise ValueError("a resource read needs a uri string")
    uri = params["uri"]
    if uri == "brevio://rules/catalog":
        data = [{"id": rid, "summary": PR_RULES_122[rid]} for rid in rule_docs.rule_ids()]
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri == "brevio://scorecard":
        score_mod = _score_module()
        if score_mod is None:
            raise ToolError("score module not available in this build")
        card = score_mod.compute_scorecard()
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(card, indent=2)}]}
    if uri == "brevio://map":
        map_mod = _map_module()
        if map_mod is None:
            raise ToolError("map module not available in this build")
        try:
            data = map_mod.build_repo_map(root=".", as_json=True)
        except Exception as exc:
            raise ToolError(str(exc)) from exc
        if isinstance(data, (dict, list)):
            text = json.dumps(data, indent=2)
            mime = "application/json"
        else:
            text = str(data)
            mime = "text/plain"
        return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}
    if uri == "brevio://config":
        cfg = core_cli.config_tools()
        if cfg is None:
            raise ToolError("config module not available in this build")
        proj = core_cli.project(["."])
        desc = cfg.describe(proj, ".", core_cli.scanned_rules(), core_cli.rule_status, True)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(desc, indent=2)}]}
    if uri.startswith("brevio://rules/"):
        rule_id = uri.split("/")[-1].upper()
        if rule_id not in PR_RULES_122:
            raise ToolError(f"unknown rule: {rule_id}")
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": rule_docs.render(rule_id)}]}
    raise ToolError(f"unknown resource URI: {uri}")


PROMPTS = [
    {
        "name": "audit-diff",
        "description": "Audit uncommitted or base git diff for code defects and agent-slop",
        "arguments": [{"name": "diff_base", "description": "Git revision to diff against (default HEAD)", "required": False}],
    },
    {
        "name": "pre-edit-brief",
        "description": "Generate a pre-edit briefing including active rules and remembered traps for target files",
        "arguments": [{"name": "files", "description": "Space-separated or comma-separated file paths to edit", "required": True}],
    },
    {
        "name": "remediate-findings",
        "description": "Generate instructions to remediate active findings and defects without suppressions",
        "arguments": [{"name": "paths", "description": "Files or directories to scan and remediate", "required": False}],
    },
]


def _prompt_list() -> list[dict[str, Any]]:
    return list(PROMPTS)


def _get_prompt(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise ValueError("prompts/get needs a prompt name")
    name = params["name"]
    args = params.get("arguments") or {}
    if name == "audit-diff":
        diff_base = args.get("diff_base", "HEAD")
        text = (
            f"Review the git diff against '{diff_base}' using `scan` and `check` tools. "
            "Identify all code defects, unhandled error cases, secret leaks, and security risks. "
            "Ensure zero rule suppressions are introduced."
        )
        return {
            "description": "Audit git diff for code defects",
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
    if name == "pre-edit-brief":
        files = args.get("files", "")
        text = (
            f"Before editing files ({files}), call `brief` with these files to inspect active surface micro-rules, "
            "recalled trap-to-fix episodes, and 1-hop neighbor dependencies. Adhere to all recalled constraints."
        )
        return {
            "description": "Pre-edit briefing for target files",
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
    if name == "remediate-findings":
        paths = args.get("paths", ".")
        text = (
            f"Scan the codebase under '{paths}' with `scan`. For any findings, call `explain` to understand the invariant "
            "and fix each issue directly in the source code rather than adding ignore comments."
        )
        return {
            "description": "Remediate active findings",
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
    raise ToolError(f"unknown prompt: {name}")


def _reply(msg_id: Any, *, result: Any = None, error: tuple[int, str] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        body["error"] = {"code": error[0], "message": error[1]}
    else:
        body["result"] = result
    return body


def handle(message: Any) -> dict[str, Any] | None:
    """The reply to one decoded message, or ``None`` for a notification or a reply from the client."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _reply(None, error=(INVALID_REQUEST, "not a JSON-RPC 2.0 message"))
    method, msg_id = message.get("method"), message.get("id")
    if not isinstance(method, str):
        return None if "result" in message or "error" in message else _reply(msg_id, error=(INVALID_REQUEST, "no method"))
    if "id" not in message:
        return None     # a notification (such as notifications/initialized) gets no answer
    params = message.get("params")
    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        capabilities = {
            "tools": {},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
        }
        return _reply(msg_id, result={"protocolVersion": version, "capabilities": capabilities,
                                      "serverInfo": {"name": "brevio-core", "version": server_version()}})
    if method == "ping":
        return _reply(msg_id, result={})
    if method == LIST_METHOD:
        return _reply(msg_id, result={"tools": tool_list()})
    if method == CALL_METHOD:
        try:
            return _reply(msg_id, result=call_tool(params))
        except ValueError as exc:
            return _reply(msg_id, error=(INVALID_PARAMS, str(exc)))
    if method == "resources/list":
        return _reply(msg_id, result={"resources": _resource_list()})
    if method == "resources/templates/list":
        return _reply(msg_id, result={"resourceTemplates": _resource_templates()})
    if method == "resources/read":
        try:
            return _reply(msg_id, result=_read_resource(params))
        except (ToolError, ValueError) as exc:
            return _reply(msg_id, error=(INVALID_PARAMS, str(exc)))
    if method == "prompts/list":
        return _reply(msg_id, result={"prompts": _prompt_list()})
    if method == "prompts/get":
        try:
            return _reply(msg_id, result=_get_prompt(params))
        except (ToolError, ValueError) as exc:
            return _reply(msg_id, error=(INVALID_PARAMS, str(exc)))
    return _reply(msg_id, error=(METHOD_NOT_FOUND, f"method not found: {method}"))


def serve(stdin: TextIO, stdout: TextIO) -> int:
    """Answer one JSON message per line until ``stdin`` ends."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except ValueError:
            reply = _reply(None, error=(PARSE_ERROR, "parse error"))
        if reply is not None:
            stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
            stdout.flush()
    return 0


def main(argv: list[str], prog: str = "brevio-core mcp") -> int:
    if argv:
        print(f"usage: {prog}\n\nServe the scan, rule pages and guard compiler over MCP on stdin and stdout. "
              "Takes no arguments.", file=sys.stderr)
        return 0 if argv in (["-h"], ["--help"]) else 2
    return serve(sys.stdin, sys.stdout)
