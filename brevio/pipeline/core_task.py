"""Single-task local CLI workflow for brevio-core (Pillar 1: Single-Task Workflow).

Implements the connect -> plan -> build -> qa -> pr deterministic loop and starter spec scaffolding
(CLAUDE.md, CONSTITUTION.md, and roadmap.json).

Standard library and exported brevio.* modules only. Zero foreign imports, no dynamic code, no network.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

from brevio.pipeline import core_heal, core_init, core_map, core_memory, core_platform, core_score
from brevio.pipeline.gates import core_gates

MAX_SHOWN = 10      # findings a failed qa or pr prints; the rest are in .brevio/qa_report.json
HELP = "run the single-task local workflow (connect -> plan -> build -> qa -> pr)"
PLAN_HELP = "create a single-task implementation plan (.brevio/task_plan.json & .brevio/TASK_PLAN.md)"
BUILD_HELP = "execute single-task build preparation and deterministic auto-fixes"
QA_HELP = "run the single-task pre-PR quality gates and count the findings (of the task's diff; --all counts the whole repository)"
PR_HELP = "verify QA gates and generate a pull-request receipt (.brevio/PR_BODY.md)"
CONNECT_HELP = "detect stack and coding clients and write CLAUDE.md, CONSTITUTION.md & roadmap.json (--dry-run lists them first)"


def scaffold_specs(root: str = ".", force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Detect stack and coding clients, and scaffold starter spec files."""
    abs_root = os.path.abspath(root)
    stack = core_init.detect_stack(abs_root)
    agents = core_init.detect_agents(abs_root)
    raw_slug = os.path.basename(abs_root)
    project_slug = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_slug).strip("-") or "project"

    specs = core_init.render_specs(abs_root, stack=stack)
    written: list[str] = []
    skipped: list[str] = []

    for fname in ("CONSTITUTION.md", "CLAUDE.md", "roadmap.json"):
        fpath = os.path.join(abs_root, fname)
        if force or not os.path.exists(fpath):
            if not dry_run:
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(specs[fname])
            written.append(fname)
        else:
            skipped.append(fname)

    return {
        "ok": True,
        "root": abs_root,
        "project": project_slug,
        "stack": stack,
        "agents": [k for k, v in agents.items() if v],
        "written": written,
        "skipped": skipped,
    }


def run_connect(root: str = ".", force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Connect repository: detect stack & agents, scaffold specs, and write connection.json (nothing with ``dry_run``)."""
    specs_res = scaffold_specs(root=root, force=force, dry_run=dry_run)
    brevio_dir = os.path.join(root, ".brevio")
    if not dry_run:
        os.makedirs(brevio_dir, exist_ok=True)
    conn = {
        "ok": True,
        "root": specs_res["root"],
        "project": specs_res["project"],
        "stack": specs_res["stack"],
        "agents": specs_res["agents"],
        "edition": "core",
        "specs": {
            "constitution": os.path.join(specs_res["root"], "CONSTITUTION.md"),
            "claude": os.path.join(specs_res["root"], "CLAUDE.md"),
            "roadmap": os.path.join(specs_res["root"], "roadmap.json"),
        },
        "written": specs_res["written"],
        "skipped": specs_res["skipped"],
    }
    if dry_run:
        conn["dry_run"] = True
        return conn
    conn_path = os.path.join(brevio_dir, "connection.json")
    with open(conn_path, "w", encoding="utf-8") as fh:
        json.dump(conn, fh, indent=2)
        fh.write("\n")
    return conn


def _load_roadmap_items(root: str) -> list[dict[str, Any]]:
    for cand in (os.path.join(root, "roadmap.json"), os.path.join(root, ".brevio", "roadmap.json")):
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    items = data.get("items", [])
                    if isinstance(items, list):
                        return items
            except (OSError, ValueError):
                pass
    return []


def _resolve_task_target(
    task: str | None,
    items: list[dict[str, Any]],
    default_criteria: list[str],
) -> tuple[str, str, list[str]]:
    task_id = "TASK-001"
    title = "Eliminate code rule findings and pass pre-PR flight gates"
    criteria = list(default_criteria)
    if task is None or not str(task).strip():
        for it in items:
            if it.get("status") in ("todo", "pending", "in_progress"):
                return (
                    it.get("id", task_id),
                    it.get("title", title),
                    it.get("acceptance_criteria", criteria),
                )
        return task_id, title, criteria

    task_str = str(task).strip()
    matched = next(
        (it for it in items if it.get("id") == task_str or it.get("title") == task_str),
        None,
    )
    if not matched:
        matched = next(
            (it for it in items if task_str.lower() in str(it.get("title", "")).lower()),
            None,
        )
    if matched:
        return (
            matched.get("id", task_id),
            matched.get("title", task_str),
            matched.get("acceptance_criteria", criteria),
        )
    return task_id, task_str, criteria


def run_plan(task: str | None = None, root: str = ".", budget: int = 2000) -> dict[str, Any]:
    """Create a single-task implementation plan and write task_plan.json & TASK_PLAN.md."""
    items = _load_roadmap_items(root)
    default_criteria = [
        "Pass brevio-core scan --diff HEAD with 0 findings",
        "Pass pre-PR check gates",
        "Pass brevio-core score with 0 findings",
    ]
    task_id, title, criteria = _resolve_task_target(task, items, default_criteria)

    map_res = core_map.build_repo_map(root=root, token_budget=budget, as_json=True)
    ranked_files = map_res.get("ranked_files", []) if isinstance(map_res, dict) else []
    top_files = [str(f.get("file", "")) for f in ranked_files if f.get("file")][:10]
    ranked_count = sum(len(f.get("defs", [])) for f in ranked_files) if isinstance(map_res, dict) else 0

    brief = core_memory.brief_for_files(top_files, root=root) if top_files else {}
    applicable_rules = brief.get("surface_rules") or brief.get("micro_rules", [])

    brevio_dir = os.path.join(root, ".brevio")
    os.makedirs(brevio_dir, exist_ok=True)

    plan_data = {
        "ok": True,
        "task_id": task_id,
        "task": title,
        "acceptance_criteria": criteria,
        "target_files": top_files,
        "ranked_symbols": ranked_count,
        "applicable_rules": applicable_rules,
        "diff_base": _head_commit(root),
        "status": "planned",
        "plan_json": ".brevio/task_plan.json",
        "plan_md": ".brevio/TASK_PLAN.md",
    }
    plan_json_path = os.path.join(brevio_dir, "task_plan.json")
    with open(plan_json_path, "w", encoding="utf-8") as fh:
        json.dump(plan_data, fh, indent=2)
        fh.write("\n")

    crit_lines = "\n".join(f"- {c}" for c in criteria)
    file_lines = "\n".join(f"- `{f}`" for f in top_files) if top_files else "- None detected"
    rule_lines = "\n".join(f"- {r}" for r in applicable_rules) if applicable_rules else "- Standard flight rules active"

    plan_md_content = (
        f"# Task Plan: {task_id} - {title}\n\n"
        "## Acceptance Criteria\n"
        f"{crit_lines}\n\n"
        f"## Target Files ({len(top_files)})\n"
        f"{file_lines}\n\n"
        "## Repository Scope\n"
        f"- Ranked symbols: {ranked_count}\n\n"
        "## Applicable Surface Rules\n"
        f"{rule_lines}\n"
    )
    plan_md_path = os.path.join(brevio_dir, "TASK_PLAN.md")
    with open(plan_md_path, "w", encoding="utf-8") as fh:
        fh.write(plan_md_content)

    return {
        "ok": True,
        "task_id": task_id,
        "task": title,
        "acceptance_criteria": criteria,
        "target_files": top_files,
        "ranked_symbols": ranked_count,
        "applicable_rules": applicable_rules,
        "diff_base": plan_data["diff_base"],
        "plan_json": ".brevio/task_plan.json",
        "plan_md": ".brevio/TASK_PLAN.md",
    }


def _head_commit(root: str) -> str:
    """The commit the task starts from ("" when there is none): ``qa`` scores what the task changed since then."""
    return core_gates._git(root, "rev-parse", "--verify", "-q", "HEAD").strip()


def _load_task_plan(plan_path: str) -> dict[str, Any]:
    if os.path.isfile(plan_path):
        try:
            with open(plan_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def _mark_roadmap_done(root: str, task_id: str, pr_title: str) -> None:
    for cand in (os.path.join(root, "roadmap.json"), os.path.join(root, ".brevio", "roadmap.json")):
        if not os.path.isfile(cand):
            continue
        try:
            with open(cand, encoding="utf-8") as fh:
                rd = json.load(fh)
            if isinstance(rd, dict) and isinstance(rd.get("items"), list):
                updated = False
                for it in rd["items"]:
                    if it.get("id") == task_id or it.get("title") == pr_title:
                        it["status"] = "done"
                        updated = True
                if updated:
                    with open(cand, "w", encoding="utf-8") as fh:
                        json.dump(rd, fh, indent=2)
                        fh.write("\n")
        except (OSError, ValueError):
            pass


def run_build(task: str | None = None, root: str = ".", apply_fixes: bool = True) -> dict[str, Any]:
    """Execute single-task build preparation and deterministic auto-fixes."""
    brevio_dir = os.path.join(root, ".brevio")
    plan_path = os.path.join(brevio_dir, "task_plan.json")
    plan = _load_task_plan(plan_path) if task is None else {}

    if not plan or task is not None:
        plan = run_plan(task=task, root=root)

    # No guards are compiled here: the working-tree diff holds the task's own outputs, and a guard learned from them (or an
    # active guard with no pattern) would join the repository's guard set on every run (2026-11-13).
    heal_res = core_heal.run_heal(root=root, apply_fixes=apply_fixes, compile_guards=False)
    fixed_files = heal_res.get("fixed_files") or heal_res.get("auto_fixed_files", [])
    fixed_rules = heal_res.get("fixed_rules", heal_res.get("auto_fixed_count", 0))
    remaining_count = heal_res.get("remaining_count", len(heal_res.get("remaining_findings", [])))

    plan["status"] = "built"
    plan["heal_summary"] = {
        "fixed_files": fixed_files,
        "fixed_rules": fixed_rules,
        "remaining_findings": remaining_count,
    }
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)
        fh.write("\n")

    fixed_list = "\n".join(f"- `{f}`" for f in fixed_files) if fixed_files else "- None"
    rem_findings = heal_res.get("remaining_findings", [])
    rem_list = "\n".join(f"- {f}" for f in rem_findings) if rem_findings else "- Zero remaining findings"
    heal_pkt = heal_res.get("heal_packet", "")

    build_packet_content = (
        f"# Build Packet: {plan.get('task_id', 'TASK-001')} - {plan.get('task', '')}\n\n"
        f"- Status: built\n"
        f"- Auto-fixed files: {len(fixed_files)}\n"
        f"- Auto-fixed rules: {fixed_rules}\n"
        f"- Remaining findings: {remaining_count}\n\n"
        f"## Fixed Files\n"
        f"{fixed_list}\n\n"
        f"## Remaining Findings\n"
        f"{rem_list}\n"
    )
    if heal_pkt:
        build_packet_content += f"\n## Heal Details\n```text\n{heal_pkt}\n```\n"

    packet_path = os.path.join(brevio_dir, "BUILD_PACKET.md")
    with open(packet_path, "w", encoding="utf-8") as fh:
        fh.write(build_packet_content)

    return {
        "ok": True,
        "task_id": plan.get("task_id", "TASK-001"),
        "task": plan.get("task", ""),
        "status": "built",
        "fixed_files": fixed_files,
        "fixed_rules": fixed_rules,
        "remaining_findings": remaining_count,
        "build_packet": ".brevio/BUILD_PACKET.md",
    }


def run_qa(root: str = ".", diff_base: str | None = None, whole: bool = False) -> dict[str, Any]:
    """Run the pre-PR quality gates and count the findings (no score or grade, 2026-11-12).

    By default the count is the task's own diff, as ``check`` reads it: counting the whole repository let one old finding
    stop every task from passing (2026-11-11). ``whole`` counts every file, which is what 0.9.1 did (2026-11-13)."""
    plan = _load_task_plan(os.path.join(root, ".brevio", "task_plan.json"))
    base_ref = diff_base or str(plan.get("diff_base") or "")      # empty: the base of ``scan --diff`` (origin/main, main, HEAD; I10)
    check_res = core_gates.run_check(root=root, base=base_ref)
    used = str(check_res.get("base") or base_ref)       # the base ``check`` really read, also when none was named
    no_base = not whole and not core_gates._git(root, "rev-parse", "--verify", "-q", used).strip()
    score_res = core_score.score_repository(root=root, diff_base=None if whole or no_base else used)
    scope = ("the whole repository (there is no commit to compare with)" if no_base
             else "the whole repository" if whole else f"changes since {used[:12]}")

    gate_ok = bool(check_res.get("ok", check_res.get("passed", False)))
    finding_count = int(score_res.get("finding_count", len(score_res.get("findings", []))))
    ok = gate_ok and finding_count == 0

    all_findings = sorted(set(check_res.get("findings", []) + score_res.get("findings", [])))
    qa_report = {
        "ok": ok,
        "gate_ok": gate_ok,
        "finding_count": finding_count,
        "base": check_res.get("base", ""),
        "scope": scope,
        "gates": check_res.get("checks", {}),
        "findings": all_findings,
    }

    brevio_dir = os.path.join(root, ".brevio")
    os.makedirs(brevio_dir, exist_ok=True)
    report_path = os.path.join(brevio_dir, "qa_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(qa_report, fh, indent=2)
        fh.write("\n")

    return qa_report


def run_pr(title: str | None = None, root: str = ".", diff_base: str | None = None, whole: bool = False) -> dict[str, Any]:
    """Verify QA gates and generate PR body (.brevio/PR_BODY.md)."""
    brevio_dir = os.path.join(root, ".brevio")
    plan_path = os.path.join(brevio_dir, "task_plan.json")
    plan = _load_task_plan(plan_path)

    qa = run_qa(root=root, diff_base=diff_base, whole=whole)
    pr_title = title or plan.get("task") or "chore: pass brevio-core single-task QA gates"
    task_id = plan.get("task_id", "TASK-001")

    verdict = "PASS" if qa["ok"] else "FAIL"
    body_md = (
        f"# {pr_title}\n\n"
        "## Summary\n"
        f"- Task ID: `{task_id}`\n"
        f"- Title: {pr_title}\n"
        f"- Status: Single-task quality gates **{verdict}**\n\n"
        "## Single-Task Pipeline Receipt\n"
        "Deterministic local execution sequence:\n"
        "1. `connect`: Stack & agents detected, starter specs verified\n"
        "2. `plan`: PageRank symbol ranking & surface rules gathered\n"
        "3. `build`: Build preparation & deterministic auto-fixes applied\n"
        "4. `qa`: Quality flight gates and finding count checked\n"
        "5. `pr`: Pull-request receipt synthesized\n\n"
        "## QA Gate Verdict\n"
        f"- Gate Status: **{verdict}**\n"
        f"- Flight Gates: {'PASSED' if qa['gate_ok'] else 'FAILED'}\n"
        f"- Code Findings: {qa['finding_count']}\n\n"
        "## Verification Checklist\n"
        f"- [{'x' if qa['gate_ok'] else ' '}] Pre-PR flight checks passed\n"
        f"- [{'x' if qa['finding_count'] == 0 else ' '}] Zero code rule findings\n"
        f"- [{'x' if qa['ok'] else ' '}] Single-task receipt verified\n"
    )

    body_path = os.path.join(brevio_dir, "PR_BODY.md")
    with open(body_path, "w", encoding="utf-8") as fh:
        fh.write(body_md)

    if qa["ok"]:
        _mark_roadmap_done(root, task_id, pr_title)

    return {
        "ok": qa["ok"],
        "title": pr_title,
        "task_id": task_id,
        "pr_body_path": ".brevio/PR_BODY.md",
        "pr_body": body_md,
        "findings": qa["findings"],
        "gh_command": f'gh pr create --title "{pr_title}" --body-file .brevio/PR_BODY.md',
    }


def run_pipeline(
    task: str | None = None,
    root: str = ".",
    apply_fixes: bool = True,
    diff_base: str | None = None,
) -> dict[str, Any]:
    """Run connect -> plan -> build -> qa -> pr sequentially."""
    conn_res = run_connect(root=root)
    plan_res = run_plan(task=task, root=root)
    build_res = run_build(task=task, root=root, apply_fixes=apply_fixes)
    qa_res = run_qa(root=root, diff_base=diff_base)
    pr_res = run_pr(title=None, root=root, diff_base=diff_base)
    return {
        "ok": pr_res["ok"],
        "connect": conn_res,
        "plan": plan_res,
        "build": build_res,
        "qa": qa_res,
        "pr": pr_res,
    }


def _show_findings(findings: list[str], out: Any = None) -> None:
    """Print up to ``MAX_SHOWN`` findings, one per line, and how many more the report holds."""
    for f in findings[:MAX_SHOWN]:
        print(f"  {f}", file=out)
    if len(findings) > MAX_SHOWN:
        print(f"  ... and {len(findings) - MAX_SHOWN} more", file=out)


def _agreed(yes: bool) -> bool:
    """True when ``--yes`` was given, there is no terminal to ask (0.9.1 behaviour), or the person answers y."""
    if yes or not core_platform.is_terminal(sys.stdin):
        return True
    try:
        return input("Write these files? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _pr_tail(pr: dict[str, Any]) -> None:
    """The lines after a pr verdict: the receipt and the command on PASS; the findings and the next step on FAIL."""
    print(f"  PR body written: {pr.get('pr_body_path', '.brevio/PR_BODY.md')}")
    if pr.get("ok"):
        print(f"  Command: {pr.get('gh_command', '')}")
        return
    _show_findings(list(pr.get("findings", [])))
    print("  Fix the findings above, run qa, then pr.")


def _files_written(res: dict[str, Any]) -> None:
    """What ``task`` wrote besides the receipt: the spec files, the plan and packet, and any source file it fixed."""
    names = list(res.get("connect", {}).get("written", [])) + [".brevio/connection.json", ".brevio/task_plan.json",
                                                              ".brevio/TASK_PLAN.md", ".brevio/BUILD_PACKET.md", ".brevio/qa_report.json"]
    print(f"  Wrote: {', '.join(names)}")
    fixed = res.get("build", {}).get("fixed_files", [])
    if fixed:
        print(f"  Rewrote source: {', '.join(fixed)}")


def main_connect(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brevio-core connect", description=CONNECT_HELP)
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--force", action="store_true", help="overwrite existing spec files")
    ap.add_argument("--dry-run", action="store_true", help="list the files it would write and write nothing")
    ap.add_argument("--yes", action="store_true", help="write without asking (a terminal is asked first, a pipe is not)")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args(argv)
    res = run_connect(root=args.root, force=args.force, dry_run=True)
    dry = args.dry_run
    if not dry and res["written"] and not args.json and not args.yes and core_platform.is_terminal(sys.stdin):
        stk = ", ".join(res["stack"]) or "generic"
        print(f"brevio-core connect: project={res['project']} stack={stk}")
        print(f"  Would write: {', '.join(res['written'])}, .brevio/connection.json")
        dry = not _agreed(False)
    if not dry:
        res = run_connect(root=args.root, force=args.force)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    stk = ", ".join(res["stack"]) or "generic"
    print(f"brevio-core connect: project={res['project']} stack={stk}")
    verb = "Would write (dry run, nothing written)" if dry else "Written specs"
    print(f"  {verb}: {', '.join(res['written']) or 'none'}")
    print("  Would write: .brevio/connection.json" if dry else "  Written: .brevio/connection.json")
    if res["skipped"]:
        print(f"  Skipped (existing): {', '.join(res['skipped'])}")
    return 0


def main_plan(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brevio-core plan", description=PLAN_HELP)
    ap.add_argument("task", nargs="?", default=None, help="task description or id")
    ap.add_argument("--task", dest="task_flag", default=None, help="task description or id")
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--budget", type=int, default=2000, help="token budget for repo map")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args(argv)
    task_name = args.task or args.task_flag
    res = run_plan(task=task_name, root=args.root, budget=args.budget)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"brevio-core plan: [{res['task_id']}] {res['task']}")
        print(f"  Target files ({len(res['target_files'])}): {', '.join(res['target_files']) or 'none'}")
        print(f"  Ranked symbols: {res['ranked_symbols']}")
        print(f"  Plan written to {res['plan_json']} and {res['plan_md']}")
    return 0


def main_build(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brevio-core build", description=BUILD_HELP)
    ap.add_argument("task", nargs="?", default=None, help="task description or id")
    ap.add_argument("--task", dest="task_flag", default=None, help="task description or id")
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--no-fix", dest="apply_fixes", action="store_false", default=True, help="do not auto-apply deterministic fixes")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args(argv)
    task_name = args.task or args.task_flag
    res = run_build(task=task_name, root=args.root, apply_fixes=args.apply_fixes)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"brevio-core build: [{res['task_id']}] {res['task']}")
        print(f"  Fixed files: {len(res['fixed_files'])} | Fixed rules: {res['fixed_rules']} | Remaining: {res['remaining_findings']}")
        if res["fixed_files"]:
            print(f"  Rewrote: {', '.join(res['fixed_files'])}")
        print(f"  Build packet: {res['build_packet']}")
    return 0


def main_qa(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brevio-core qa", description=QA_HELP)
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--diff", dest="diff_base", default=None, help="base ref to diff against")
    ap.add_argument("--all", dest="whole", action="store_true", help="count the whole repository, not only the task's changes (the 0.9.1 count)")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args(argv)
    res = run_qa(root=args.root, diff_base=args.diff_base, whole=args.whole)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        status_str = "PASS" if res["ok"] else "FAIL"
        print(f"brevio-core qa: {status_str} | Findings: {res['finding_count']} | Counted: {res['scope']}")
        if not res["ok"]:
            _show_findings(res["findings"])
            print("  Details: .brevio/qa_report.json")
    return 0 if res["ok"] else 1


def main_pr(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brevio-core pr", description=PR_HELP)
    ap.add_argument("title", nargs="?", default=None, help="PR title")
    ap.add_argument("--title", dest="title_flag", default=None, help="PR title")
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--diff", dest="diff_base", default=None, help="base ref to diff against")
    ap.add_argument("--all", dest="whole", action="store_true", help="count the whole repository, not only the task's changes")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args(argv)
    pr_title = args.title or args.title_flag
    res = run_pr(title=pr_title, root=args.root, diff_base=args.diff_base, whole=args.whole)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        status_str = "PASS" if res["ok"] else "FAIL"
        print(f"brevio-core pr: {status_str} [{res['task_id']}] {res['title']}")
        _pr_tail(res)
    return 0 if res["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brevio-core task", description=HELP)
    ap.add_argument("task", nargs="?", default=None, help="task description or id")
    ap.add_argument("--task", dest="task_flag", default=None, help="task description or id")
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--no-fix", dest="apply_fixes", action="store_false", default=True, help="do not auto-apply deterministic fixes")
    ap.add_argument("--diff", dest="diff_base", default=None, help="base ref to diff against")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args(argv)
    task_name = args.task or args.task_flag
    res = run_pipeline(task=task_name, root=args.root, apply_fixes=args.apply_fixes, diff_base=args.diff_base)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        pr = res.get("pr", {})
        status_str = "PASS" if res.get("ok") else "FAIL"
        print(f"brevio-core task: {status_str} [{pr.get('task_id', 'TASK-001')}] {pr.get('title', '')}")
        _pr_tail(pr)
        _files_written(res)
    return 0 if res.get("ok") else 1
