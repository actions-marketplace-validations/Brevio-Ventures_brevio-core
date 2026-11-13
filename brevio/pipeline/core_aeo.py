"""``brevio-core aeo``: the AEO rubric on a folder of built pages, printed as a scorecard (ADOPTION.md 0.29, 2026-11-05).

It scores how well answer engines can find, read and cite a site, with the exported rubric (``geo_audit.rubric``) and
robots parser (``geo_audit.robots``). It reads local files only: every ``.html`` page under a folder (or one page), and
``robots.txt``, ``llms.txt``, ``skill.md`` and ``.well-known/agent-card.json`` at the folder's root. It fetches
nothing, so a URL is refused with a hint to save the page first or to use the hosted check (OSS.md section 2: the
rubric is a second, separate command, not the headline). The exit code is 0 when every page meets ``--min``, 1 when
one does not and 2 for a usage error. Standard library only (the 0.6 set).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

from brevio.growth.geo_audit import rubric
from brevio.pipeline import core_cli
from brevio.pipeline.core_markdown import escape

HELP = "score a folder of built HTML pages, with its robots.txt and llms.txt, against the AEO rubric"
PAGE_SUFFIXES = (".html", ".htm")
SKIP_DIRS = frozenset({"node_modules"})          # plus every hidden folder
# The site files the rubric reads, by the keyword ``rubric.score_page`` takes them under.
SITE_FILES = {"robots_txt": "robots.txt", "llms_txt": "llms.txt", "skill_md": "skill.md",
              "agent_card": ".well-known/agent-card.json"}
FORMATS = ("text", "markdown", "json")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_HOST = re.compile(r"^(?:[a-z0-9-]+\.)+([a-z]{2,})(?::\d+)?(?:/.*)?$", re.I)
_LOCALHOST = re.compile(r"^localhost(?::\d+)?(?:/.*)?$", re.I)
_FILE_WORDS = frozenset({"html", "htm", "md", "txt", "json", "xml"})


class UsageError(Exception):
    """The command cannot run as asked (exit code 2)."""


def is_url(arg: str) -> bool:
    """``arg`` names a web address, not a local path: it has a scheme, or it is a host name that is not on disk."""
    if _SCHEME.match(arg):
        return True
    if _LOCALHOST.match(arg) and not os.path.exists(arg):
        return True
    m = _HOST.match(arg)
    return bool(m) and not os.path.exists(arg) and m.group(1).lower() not in _FILE_WORDS


def refusal(arg: str) -> str:
    """What ``aeo`` says when it is given a URL: it reads local files only."""
    return (f"aeo reads local files only and does not fetch {arg}. Save the built page first (your browser's "
            "\"Save page as\", or your site's build output), put its robots.txt and llms.txt beside it, and run "
            "`brevio-core aeo` on that folder.")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return ""


def pages(root: str) -> list[str]:
    """Every ``.html`` page under ``root`` (hidden folders and ``node_modules`` left out), relative, sorted."""
    if os.path.isfile(root):
        return [os.path.basename(root)] if root.lower().endswith(PAGE_SUFFIXES) else []
    found: list[str] = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        found += [os.path.relpath(os.path.join(dirpath, n), root).replace(os.sep, "/")
                  for n in names if n.lower().endswith(PAGE_SUFFIXES)]
    return sorted(found)


def site_files(root: str) -> dict[str, str]:
    """The text of each site file at ``root``; an empty string for one that is missing."""
    return {key: _read(os.path.join(root, rel)) for key, rel in SITE_FILES.items()}


def has_md_mirror(root: str, rel: str) -> bool:
    """A Markdown copy of the page sits beside it: ``page.md`` or ``page.html.md``."""
    path = os.path.join(root, rel)
    return os.path.isfile(os.path.splitext(path)[0] + ".md") or os.path.isfile(path + ".md")


def score_page(root: str, rel: str, site: dict[str, str]) -> dict:
    """The rubric's scorecard of one page, trimmed to what the report prints."""
    # No UX Bridge pillar: its advice is written for one product's own funnel pages, not for a site in general (2026-11-13).
    card = rubric.score_page(html=_read(os.path.join(root, rel)), url=rel, has_md_mirror=has_md_mirror(root, rel),
                             ux_bridge=False, **site)
    return {"path": rel, "score": card["score"], "grade": card["grade"],
            "pillars": {name: p["score"] for name, p in card["pillars"].items()}, "gaps": card["gaps"]}


def scorecard(target: str, min_score: int) -> dict:
    """Score every page of ``target`` (a folder, or one page whose folder holds the site files)."""
    if is_url(target):
        raise UsageError(refusal(target))
    if not os.path.exists(target):
        raise UsageError(f"no such file or folder: {target}")
    if os.path.isdir(target):
        root, rels = target, pages(target)
    else:
        if not target.lower().endswith(PAGE_SUFFIXES):
            raise UsageError(f"{target} is not an .html page")
        root, rels = os.path.dirname(target) or ".", [os.path.basename(target)]
    if not rels:
        raise UsageError(f"no .html page under {target}; point it at your site's build output")
    site = site_files(root)
    scored = sorted((score_page(root, rel, site) for rel in rels), key=lambda p: (p["score"], p["path"]))
    robots = rubric.check_robots_txt(site["robots_txt"])
    below = [p["path"] for p in scored if p["score"] < min_score]
    return {"target": target, "min": min_score, "ok": not below, "lowest": scored[0]["score"], "below_min": below,
            "site": {"robots_txt": robots["present"], "blocked_bots": robots["blocked_bots"],
                     **{key: bool(site[key].strip()) for key in ("llms_txt", "skill_md", "agent_card")}},
            "pages": scored}


def _site_lines(card: dict) -> list[str]:
    s = card["site"]
    blocked = ", ".join(s["blocked_bots"])
    robots = ("missing" if not s["robots_txt"] else f"blocks {blocked}" if blocked else "present, blocks no AI crawler")
    return [f"robots.txt: {robots}"] + [f"{SITE_FILES[k]}: {'present' if s[k] else 'missing'}"
                                         for k in ("llms_txt", "skill_md", "agent_card")]


def _verdict(card: dict) -> str:
    n = len(card["pages"])
    if card["ok"]:
        return f"{n} page(s), lowest score {card['lowest']}, all at or above --min {card['min']}"
    return f"{n} page(s), lowest score {card['lowest']}: {len(card['below_min'])} below --min {card['min']}"


def render_text(card: dict) -> str:
    out = [f"brevio-core aeo: {card['target']}", *(f"  {line}" for line in _site_lines(card)), ""]
    for p in card["pages"]:
        out.append(f"{p['score']:>3} {p['grade']}  {p['path']}")
        out += [f"      - {gap}" for gap in p["gaps"]]
    return "\n".join([*out, "", _verdict(card)]) + "\n"


def render_markdown(card: dict) -> str:
    """A heading, the site files, and a table of page, score, grade and gaps, escaped like the scan's report."""
    out = [f"### brevio-core aeo: {escape(card['target'])}", "", *(f"- {escape(line)}" for line in _site_lines(card)), "",
           "| Page | Score | Grade | Gaps |", "| --- | --- | --- | --- |"]
    out += [f"| {escape(p['path'])} | {p['score']} | {p['grade']} | {escape('; '.join(p['gaps'])) or 'none'} |"
            for p in card["pages"]]
    return "\n".join([*out, "", _verdict(card) + "."]) + "\n"


def _min_score(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text}") from None
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError("must be from 0 to 100")
    return value


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._in_h1 = False
        self._in_svg = False
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "svg":
            self._in_svg = True
        elif t == "title" and not self._in_svg and not self.title_parts:
            self._in_title = True
        elif t == "h1" and not self.h1_parts:
            self._in_h1 = True
        elif t == "meta":
            attr_map = {k.lower(): (v or "") for k, v in attrs if k}
            name = attr_map.get("name", "").lower()
            prop = attr_map.get("property", "").lower()
            content = attr_map.get("content", "").strip()
            if name == "description" and content:
                self.description = content
                self._has_standard_desc = True
            elif not getattr(self, "_has_standard_desc", False) and not self.description and content:
                if prop in ("og:description", "twitter:description") or name in ("og:description", "twitter:description"):
                    self.description = content

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "svg":
            self._in_svg = False
        elif t == "title" and self._in_title:
            self._in_title = False
        elif t == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif self._in_h1:
            self.h1_parts.append(data)


def _parse_html_meta(page_html: str) -> _MetaParser:
    p = _MetaParser()
    p.feed(page_html or "")
    return p


def _extract_title(page_html: str) -> str:
    p = _parse_html_meta(page_html)
    for parts in (p.title_parts, p.h1_parts):
        cleaned = "".join(parts).strip()
        if cleaned:
            return html.unescape(re.sub(r"\s+", " ", cleaned))
    return ""


def _extract_description(page_html: str) -> str:
    desc = _parse_html_meta(page_html).description
    return html.unescape(re.sub(r"\s+", " ", desc)) if desc else ""


def _clean_md_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().replace("[", "(").replace("]", ")")


def _find_index_html(root: str, rels: list[str]) -> str:
    for cand in ("index.html", "index.htm"):
        if cand in rels or os.path.isfile(os.path.join(root, cand)):
            return _read(os.path.join(root, cand))
    return ""


def _page_entry(root: str, rel: str) -> str:
    norm_rel = rel.replace("\\", "/")
    page_html = _read(os.path.join(root, rel))
    page_title = _clean_md_text(_extract_title(page_html))
    if not page_title:
        stem = os.path.splitext(os.path.basename(norm_rel))[0]
        page_title = _clean_md_text(stem.replace("-", " ").replace("_", " ").title() or norm_rel)
    page_desc = _clean_md_text(_extract_description(page_html) or page_title)
    link_rel = norm_rel if norm_rel.startswith("/") else f"/{norm_rel}"
    est_budget = max(50, (len(page_html) + 3) // 4)
    return f"- [{page_title}]({link_rel}) (~{est_budget} tokens): {page_desc}"


def _render_llms_txt(root: str, rels: list[str]) -> str:
    index_html = _find_index_html(root, rels)
    site_title = _clean_md_text(_extract_title(index_html) or os.path.basename(os.path.abspath(root)) or "Site")
    site_desc = _clean_md_text(_extract_description(index_html) or f"{site_title} documentation and pages.")
    out = [f"# {site_title}", f"> {site_desc}", "", "## Pages"]
    out += [_page_entry(root, rel) for rel in sorted(rels)]
    return "\n".join(out) + "\n"


def generate_llms_txt(target: str, rels: list[str] | None = None) -> str:
    """Build a deterministic, zero-network /llms.txt document, write it to root, and return it."""
    is_file = os.path.isfile(target)
    target_dir = (os.path.dirname(target) or ".") if is_file else (target or ".")
    root = os.path.dirname(os.path.abspath(target)) if is_file else os.path.abspath(target_dir)
    page_list = rels if rels is not None else (pages(target) if is_file else pages(root))
    content = _render_llms_txt(root, page_list)
    llms_path = os.path.join(root, "llms.txt")
    core_cli.write_atomic(llms_path, content)
    return content


def _handle_generate_llms(target: str) -> str | None:
    if is_url(target) or not os.path.exists(target):
        return None
    if not os.path.isdir(target) and not target.lower().endswith(PAGE_SUFFIXES):
        return None
    target_dir = (os.path.dirname(target) or ".") if os.path.isfile(target) else target
    llms_path = os.path.join(target_dir, "llms.txt")
    if not os.path.exists(llms_path):
        page_list = pages(target) if os.path.isfile(target) else pages(target_dir)
        if page_list:
            generate_llms_txt(target, page_list)
            return llms_path
    return None


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="brevio-core aeo", description=HELP + ". It reads local files only; save a page "
                                 "before you score it.")
    ap.add_argument("target", help="a folder of built pages (with robots.txt and llms.txt at its root), or one .html page")
    ap.add_argument("--min", dest="min_score", type=_min_score, default=0, metavar="SCORE",
                    help="exit 1 when a page scores below SCORE, out of 100 (default 0: report only)")
    ap.add_argument("--format", choices=FORMATS, default="text", help="how to print the scorecard")
    ap.add_argument("--generate-llms", action="store_true",
                    help="write a missing llms.txt at the target folder's root from its HTML pages before scoring")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    wrote_llms = _handle_generate_llms(args.target) if args.generate_llms else None
    try:
        card = scorecard(args.target, args.min_score)
    except UsageError as exc:
        print(f"brevio-core: {exc}", file=sys.stderr)
        return 2
    if wrote_llms and args.format != "json":
        print(f"Wrote {wrote_llms}")
    if args.format == "json":
        print(json.dumps(card, indent=2))
    elif args.format == "markdown":
        print(render_markdown(card), end="")
    else:
        print(render_text(card), end="")
    return 0 if card["ok"] else 1
