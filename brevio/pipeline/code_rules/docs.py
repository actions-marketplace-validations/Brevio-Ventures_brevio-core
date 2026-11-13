"""One page per rule, generated from the rule definitions (ROADMAP 8.2 item 11, 2026-11-03).

The title and what a rule catches come from ``PR_RULES_122``. Why it exists and its bad and good example
come from ``details.DETAILS``. How it is checked is worked out from the code: ``details.SCANS`` for a
scanner, and ``dev_rules.slice_micro_rules`` for the one-line reminders of ``brief``. Standard library only, so the
exported package carries it.

Each page is also a search landing page (ADOPTION.md E38, 2026-11-05): front matter with a search-shaped title
("<problem> in <language>: why and how to fix") and the short answer as its description, the short answer first on
the page, links to related rules of the same group, and a schema.org ``TechArticle`` in JSON-LD. The docs site's
Jekyll config makes the sitemap. ``render`` is the page without the front matter and the JSON-LD, which is what the
MCP ``explain`` tool returns.

    python -m brevio.pipeline.code_rules.docs --out docs/rules     write the pages
    python -m brevio.pipeline.code_rules.docs --check docs/rules   list a missing, stale or orphan page
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from brevio.pipeline import dev_rules
from brevio.pipeline.code_rules import PR_RULES_122, STATUS_REASONS, measured, rule_status
from brevio.pipeline.code_rules.details import CHECKED, DETAILS, POINTERS, SCANS, SCOPE, scanned_by
from brevio.pipeline.code_rules.hints import page_lines

GROUPS = {
    1: "UI and mobile performance", 2: "Security and tenancy", 3: "Billing and money", 4: "Database and migrations",
    5: "Reliability and fallbacks", 6: "Testing and hermeticity", 7: "Architecture and contracts",
    8: "Marketing and attribution", 9: "Type safety", 10: "API and network robustness",
    11: "State and concurrency", 12: "Memory and resource leaks", 13: "Observability and logging",
    14: "Auth and sessions", 15: "Error boundaries and fallback rendering", 16: "Supply chain",
    17: "React and Next.js patterns", 18: "Accessibility (WCAG 2.1 AA)", 19: "Clean code", 20: "Test custody",
}
_SAMPLE_PATHS = ("a.tsx", "apps/api/x.ts", "src/billing/x.ts", "schema.prisma", "a.test.ts",
                 "packages/contracts/x.ts", "a.ts")
_BRACKET = re.compile(r"\[([^\]]*R\d+\.\d+[^\]]*)\]")
_ID = re.compile(r"R(\d+)\.(\d+)(?:-R(\d+)\.(\d+))?")
README = "README.md"
# The language a page's examples are in, for its search title; a plain-text example names none.
LANGUAGES = {"ts": "TypeScript", "tsx": "React and TypeScript", "py": "Python", "css": "CSS", "json": "package.json",
             "sql": "SQL", "html": "HTML"}      # every JSON example is a package.json
# Rules that gained a Python check after the judged run (2026-11-13).
PYTHON_CHECKS = frozenset({"R2.8", "R5.4"})
RELATED = 4         # related rules linked from a page: the next ones of its group, in catalogue order


def rule_ids() -> list[str]:
    """Every rule id, in catalogue order (R1.1, R1.2, ..., R2.1, ...)."""
    return sorted(PR_RULES_122, key=_key)


def _key(rule_id: str) -> tuple[int, int]:
    major, minor = rule_id[1:].split(".")
    return int(major), int(minor)


def filename(rule_id: str) -> str:
    return f"{rule_id}.md"


def card_ids() -> frozenset[str]:
    """The rules a slice is reminded of, read from what ``slice_micro_rules`` returns for sample paths."""
    found: set[str] = set()
    for path in _SAMPLE_PATHS:
        for group in _BRACKET.findall(dev_rules.slice_micro_rules([path])):
            for m in _ID.finditer(group):
                major, lo = int(m.group(1)), int(m.group(2))
                hi = int(m.group(4)) if m.group(3) else lo
                found.update(f"R{major}.{n}" for n in range(lo, hi + 1))
    return frozenset(found)


def _title_and_text(rule_id: str) -> tuple[str, str]:
    line = PR_RULES_122[rule_id]
    return line.partition(": ")[0], line


def search_title(rule_id: str) -> str:
    """The title a search for the mistake would match: "<problem> in <language>: why and how to fix"."""
    title = _title_and_text(rule_id)[0]
    problem = title[3:] if title.startswith("No ") else title
    lang = LANGUAGES.get(DETAILS[rule_id].lang)
    return f"{problem[:1].upper()}{problem[1:]}{f' in {lang}' if lang else ''}: why and how to fix"


def answer(rule_id: str) -> str:
    """The short answer, first on the page: what to do, then why."""
    text = _title_and_text(rule_id)[1]
    rule = text.partition(": ")[2] or text
    return f"{rule[:1].upper()}{rule[1:]}. {DETAILS[rule_id].why}"


def related(rule_id: str) -> list[str]:
    """Up to ``RELATED`` other rules of the same group: the ones after this rule, wrapping to the group's start."""
    group = [r for r in rule_ids() if _key(r)[0] == _key(rule_id)[0]]
    at = group.index(rule_id)
    return (group[at + 1:] + group[:at])[:RELATED]


def checked_by(rule_id: str, cards: frozenset[str]) -> list[str]:
    """How the code checks a rule, one line each; one line saying so when nothing does."""
    out = [CHECKED[rule_id][0]] if rule_id in CHECKED else []
    if rule_id in SCOPE:
        out.append(f"What it reads: {SCOPE[rule_id]}")
    if rule_id in SCANS:
        out.append("`brevio-core scan --surface` checks the lines you add for it; a plain `brevio-core scan` does not."
                   if SCANS[rule_id][0] == "dev" else "`brevio-core scan` checks the lines you add for it.")
    if rule_id in cards:
        out.append("`brevio-core brief` and the `brief` tool of `brevio-core mcp` print a one-line reminder of it for a "
                   "file that matches.")
    out = out or ["Nothing yet. No `brevio-core` command checks it and no `brief` reminder names it: it is a standing "
                  "rule for authors and reviewers."]
    return [*out, POINTERS[rule_id]] if rule_id in POINTERS else out


def _custody_silence(rule_id: str) -> str:
    """How to silence a rule that ``check`` runs, which is not the scan's ignore comment for all of them."""
    exempt = DETAILS[rule_id].exempt
    parts = [CHECKED[rule_id][1], *([f"What the check already leaves alone: {exempt}"] if exempt else []),
             "To change what the rule flags, edit its check in `gates/core_gates.py`. That is a code change and is "
             "reviewed like one."]
    return "\n\n".join(parts)


def silence_text(rule_id: str) -> str:
    if rule_id in CHECKED:
        return _custody_silence(rule_id)
    detail = DETAILS[rule_id]
    scanned = rule_id in SCANS
    # A person can silence one finding with a reason; an agent cannot (ROADMAP E21, plan review, 2026-11-05).
    lines = [f"A person can silence one finding with `brevio-core: ignore {rule_id} -- reason` on the line or the line "
             "above. Without the reason it does not count. An agent cannot silence it: the agent hook and the MCP "
             "tools never honour the comment, and a pull request that adds one is reported until a reviewer allows "
             "it. Fixing the code is still the better answer."]
    if detail.exempt:
        lines.append(f"What the scanner already leaves alone: {detail.exempt}")
    if scanned:
        lines.append("A scan reads only the lines you add, so a line that was already in the file is not flagged.")
    lines.append("To change what the rule flags, edit its check in the rule code of the package. "
                 "That is a code change and is reviewed like one.")
    return "\n\n".join(lines)


def precision_text(rule_id: str) -> str:
    """What the last judged run says about a rule, read from ``measured`` alone: that module is generated from
    ``precision.json``, so a page shows no figure the file does not hold (improvement plan 2b, 2026-11-12)."""
    fig = measured.RULES.get(rule_id)
    where = f"corpus {measured.RUN['corpus']}, run on {measured.RUN['date']}"
    if fig is None:
        return "Not measured. No judged sample of this rule's hits exists."
    python = (" The Python checks of this rule were added after this run, so they are not measured."
              if rule_id in PYTHON_CHECKS else "")
    if not fig["hits"]:
        return f"Not measured: the rule had no hit in the corpus ({where}), so there was nothing to judge."
    share = fig["precision"]
    if share is None:
        return f"Not measured yet: a sample of its hits was drawn ({where}) and not all of it is judged."
    return (f"{round(share * 100)}% of {fig['judged']} judged hits were real, on human-written code, one "
            f"reviewer ({where}). That is not how often the rule is right on code an agent wrote. See `docs/precision.md` "
            "in the brevio-core repository for the verdicts, the commands to run it again and how to dispute one." + python)


def _fence(lang: str, body: str) -> str:
    return f"```{'' if lang == 'text' else lang}\n{body.rstrip()}\n```"


def _by_kind(rule_id: str) -> str:
    """The fixes by kind of finding and language, for a rule that reports more than one kind (``hints``)."""
    lines = page_lines(rule_id)
    return "The fix depends on the finding and the language:\n\n" + "\n".join(lines) + "\n\n" if lines else ""


def render(rule_id: str, cards: frozenset[str] | None = None) -> str:
    """The page for one rule."""
    cards = card_ids() if cards is None else cards
    title, text = _title_and_text(rule_id)
    d = DETAILS[rule_id]
    group = GROUPS[_key(rule_id)[0]]
    checks = "\n".join(f"- {c}" for c in checked_by(rule_id, cards))
    links = "\n".join([f"- [{r}]({filename(r)}) {_title_and_text(r)[0]}" for r in related(rule_id)]
                      + [f"- [Every rule]({README})"])
    reason = f" {STATUS_REASONS[rule_id]}" if rule_id in STATUS_REASONS else ""
    return (f"# {rule_id}: {title}\n\n"
            f"**Short answer:** {answer(rule_id)}\n\n"
            f"Group: {group}. Status: {rule_status(rule_id)}.{reason} Generated from the rule definitions; do not edit "
            "by hand.\n\n"
            f"## What it catches\n\n{text}.\n\n"
            f"## Why\n\n{d.why}\n\n"
            f"## A bad example\n\n{_fence(d.lang, d.bad)}\n\n"
            f"## A good example\n\n{_fence(d.lang, d.good)}\n\n{_by_kind(rule_id)}"
            f"## How it is checked\n\n{checks}\n\n"
            f"## Measured precision\n\n{precision_text(rule_id)}\n\n"
            f"## How to silence it\n\n{silence_text(rule_id)}\n\n"
            f"## Related rules\n\n{links}\n")


render_rule = render


def json_ld(rule_id: str) -> str:
    """The page as a schema.org ``TechArticle``, in a script block that no text of the rule can close early."""
    group = GROUPS[_key(rule_id)[0]]
    doc = {"@context": "https://schema.org", "@type": "TechArticle", "headline": search_title(rule_id),
           "description": answer(rule_id), "about": group, "inLanguage": "en",
           "keywords": [rule_id, _title_and_text(rule_id)[0], group],
           "isPartOf": {"@type": "WebSite", "name": "brevio-core rules"}}
    body = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">\n{body}\n</script>\n'


def page(rule_id: str, cards: frozenset[str] | None = None) -> str:
    """The published page: front matter with the search title and the short answer, the page, then its JSON-LD."""
    front = f"---\ntitle: {json.dumps(search_title(rule_id))}\ndescription: {json.dumps(answer(rule_id))}\n---\n"
    return f"{front}{render(rule_id, cards)}\n{json_ld(rule_id)}"


def render_index(cards: frozenset[str] | None = None) -> str:
    """The list of every rule page, grouped, saying which rules ``brevio-core scan`` checks and which only ``scan --surface``
    checks (they were counted together until 2026-11-05, which overstated what the package scans)."""
    cards = card_ids() if cards is None else cards
    public = set(scanned_by("code"))
    surface = set(scanned_by("dev"))
    out = ["# Rules\n",
           f"One page per rule: what it catches, why, a bad and a good example, how it is checked and how to "
           f"silence it. There are {len(PR_RULES_122)} rules. `brevio-core scan` checks {len(public)} of them and "
           f"`brevio-core scan --surface` {len(surface)} more; "
           f"{len(CHECKED)} more are checked by `brevio-core check`; {len(cards - public - surface)} more come with a "
           "one-line reminder from `brevio-core brief`; the rest are standing rules for authors and reviewers. "
           "Generated from the rule definitions; do not edit by hand.\n"]
    major = 0
    for rid in rule_ids():
        if _key(rid)[0] != major:
            major = _key(rid)[0]
            out.append(f"\n## R{major}: {GROUPS[major]}\n")
        mark = (" (brevio-core scan)" if rid in public else " (brevio-core scan --surface)" if rid in surface
                else " (brevio-core check)" if rid in CHECKED else "")
        status = "" if rule_status(rid) == "stable" else f" ({rule_status(rid)})"
        out.append(f"- [{rid}]({filename(rid)}) {_title_and_text(rid)[0]}{mark}{status}")
    return "\n".join(out) + "\n"


def all_pages() -> dict[str, str]:
    """File name -> text for the index and every rule page."""
    cards = card_ids()
    pages = {filename(rid): page(rid, cards) for rid in rule_ids()}
    pages[README] = render_index(cards)
    return pages


def write(out_dir: str) -> list[str]:
    """Write every page into ``out_dir`` and return the file names. Stale pages of a removed rule are deleted."""
    os.makedirs(out_dir, exist_ok=True)
    pages = all_pages()
    for name, text in pages.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    for name in os.listdir(out_dir):
        if name.endswith(".md") and name not in pages:
            os.remove(os.path.join(out_dir, name))
    return sorted(pages)


def problems(out_dir: str) -> list[str]:
    """What is wrong with the pages in ``out_dir``: missing, stale (differs from the definitions) or orphan."""
    pages = all_pages()
    found = sorted(n for n in os.listdir(out_dir) if n.endswith(".md")) if os.path.isdir(out_dir) else []
    bad = [f"missing: {n}" for n in sorted(pages) if n not in found]
    bad += [f"orphan: {n} names no rule" for n in found if n not in pages]
    for n in found:
        if n in pages:
            with open(os.path.join(out_dir, n), encoding="utf-8") as fh:
                if fh.read() != pages[n]:
                    bad.append(f"stale: {n} differs from the rule definitions")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="write the pages into this folder")
    ap.add_argument("--check", metavar="DIR", help="list a missing, stale or orphan page in this folder")
    args = ap.parse_args(argv)
    if args.check:
        bad = problems(args.check)
        print("\n".join(bad) if bad else "rule pages are complete and current")
        return 1 if bad else 0
    if not args.out:
        ap.error("give --out DIR or --check DIR")
    print(f"wrote {len(write(args.out))} pages to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
