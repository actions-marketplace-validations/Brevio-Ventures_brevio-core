"""Deterministic 100-Point Agentic Engine Optimization (AEO) Scorecard & Dual-Surface Auditor.

Synthesizes the 5-pillar ``addyosmani/agentic-seo`` 100-point rubric and the
``codydeny/agent-seo-skill`` source-vs-live dual-surface audit, for the pages that
``brevio-core aeo`` scores: a 100-pt scorecard in place of 2-boolean checks.

5 Weighted Pillars (100 points total):
  - Discovery (25 pts): AI crawler allowances in robots.txt, token-counted /llms.txt, canonical URL.
  - Structure & Answer Density (25 pts): First-paragraph answer capsule, heading tree, comparison
    tables, JSON-LD schema, and zero client-only JS hydration wall.
  - Token Economics (25 pts): <25% HTML-to-text markup noise, <=8,000 token budget, and raw
    Markdown content negotiation (Accept: text/markdown or .md mirror).
  - Capability Signaling (15 pts): /skill.md or /.well-known/agent-card.json advertising callable
    agent actions / MCP endpoints.
  - UX Bridge (10 pts): a one-click hand-off link on a product's own funnel pages. It is optional:
    ``brevio-core aeo`` leaves it out (``score_page(ux_bridge=False)``) and scores the other four pillars out of 90,
    scaled to 100, because it is advice about one product's funnel and not a gap that applies to a site in general (2026-11-13).
"""
from __future__ import annotations

import os
import re

from brevio.growth.geo_audit import robots as _robots

AI_CRAWLERS = ("GPTBot", "OAI-SearchBot", "ClaudeBot", "PerplexityBot", "Google-Extended")
MAX_PAGE_TOKENS = 8000

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>", re.I | re.S)
_JSONLD_TYPE_RE = re.compile(r'"@type"\s*:\s*"([A-Za-z0-9_]+)"')
_CANONICAL_RE = re.compile(r'rel=["\']canonical["\']|^canonical:\s*\S+', re.I | re.M)
_TOKEN_HINT_RE = re.compile(r"\b\d[\d,]*\s*tokens?\b", re.I)
_HEADING_RE = re.compile(r"(<h[123]\b|^#{1,3}\s+\S+)", re.I | re.M)
_TABLE_RE = re.compile(r"(<table\b|^\|.+\|\s*\n\|[-:| ]+\|)", re.I | re.M)
_UX_BRIDGE_RE = re.compile(
    r"(/geo-audit|/claim-audit|/teardown/|[?&](?:domain|prefill_domain)=)", re.I
)
_CAPABILITY_RE = re.compile(
    r"(/skill\.md|/\.well-known/agent-card\.json|/\.well-known/agent-capabilities\.json|\bmcp\b)",
    re.I,
)


def estimate_tokens(text: str) -> int:
    """Approximate LLM token count (~4 chars per token on English/code/HTML)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def html_to_text_stats(raw: str) -> dict:
    """Measure markup noise ratio, visible text tokens, and client-only JS hydration walls."""
    content = raw or ""
    raw_len = len(content)
    if not raw_len:
        return {
            "raw_chars": 0, "text_chars": 0, "noise_pct": 100.0,
            "estimated_tokens": 0, "text_tokens": 0, "is_hydration_wall": True,
        }
    stripped_scripts = _SCRIPT_STYLE_RE.sub(" ", content)
    visible = re.sub(r"\s+", " ", _TAG_RE.sub(" ", stripped_scripts)).strip()
    text_len = len(visible)
    is_markdown = not bool(re.search(r"<(!doctype|html|head|body|div)\b", content[:1000], re.I))
    noise_pct = 0.0 if is_markdown else round(max(0.0, (raw_len - text_len) / raw_len * 100.0), 1)
    has_shell = bool(re.search(r'id=["\'](?:__next|root|app)["\']', content, re.I))
    is_wall = (not is_markdown) and (text_len < 120) and (has_shell or raw_len > 1500)
    return {
        "raw_chars": raw_len,
        "text_chars": text_len,
        "noise_pct": noise_pct,
        "estimated_tokens": estimate_tokens(content),
        "text_tokens": estimate_tokens(visible),
        "is_hydration_wall": is_wall,
    }


def check_robots_txt(robots_txt: str) -> dict:
    """Parse robots.txt to detect explicit AI crawler blocks vs allowances."""
    if not (robots_txt or "").strip():
        return {"present": False, "blocked_bots": [], "allowed_bots": list(AI_CRAWLERS)}
    blocked = _robots.blocked_bots(robots_txt, AI_CRAWLERS)
    allowed = [b for b in AI_CRAWLERS if b not in blocked]
    return {"present": True, "blocked_bots": blocked, "allowed_bots": allowed}


def score_discovery(html: str, headers: dict | None = None,
                    robots_txt: str = "", llms_txt: str = "") -> dict:
    """Pillar 1: Discovery (25 pts)."""
    pts = 0
    gaps: list[str] = []
    rob = check_robots_txt(robots_txt)
    if not rob["blocked_bots"]:
        pts += 8
    else:
        gaps.append(f"robots.txt blocks AI crawlers ({', '.join(rob['blocked_bots'][:3])})")

    has_llms = bool((llms_txt or "").strip()) or ("llms.txt" in (html or "").lower())
    if has_llms:
        pts += 7
    else:
        gaps.append("missing /llms.txt discovery index")

    has_token_counts = bool(_TOKEN_HINT_RE.search(llms_txt or ""))
    if has_token_counts:
        pts += 5
    elif has_llms:
        gaps.append("llms.txt lacks per-page token budget counts")

    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    has_canon = bool(_CANONICAL_RE.search(html or "")) or ("rel=\"canonical\"" in hdrs.get("link", ""))
    if has_canon:
        pts += 5
    else:
        gaps.append("missing canonical URL tag or Link header")
    return {"score": pts, "max": 25, "gaps": gaps, "blocked_bots": rob["blocked_bots"]}


def score_structure(html: str) -> dict:
    """Pillar 2: Structure & Answer Density (25 pts)."""
    pts = 0
    gaps: list[str] = []
    stats = html_to_text_stats(html)
    if not stats["is_hydration_wall"]:
        pts += 6
    else:
        gaps.append("client-only JS hydration wall (<120 chars visible SSR text)")

    headings = _HEADING_RE.findall(html or "")
    if len(headings) >= 2:
        pts += 5
    else:
        gaps.append("weak heading hierarchy (<2 H1/H2/H3 sections)")

    if _TABLE_RE.search(html or ""):
        pts += 5
    else:
        gaps.append("no structured comparison table for LLM synthesis")

    has_ld = ("application/ld+json" in (html or "").lower()) or bool(_JSONLD_TYPE_RE.search(html or ""))
    has_frontmatter = bool(re.search(r"^---\s*\n.*?\btitle:\s*\S+", html or "", re.S))
    if has_ld or has_frontmatter:
        pts += 5
    else:
        gaps.append("missing JSON-LD structured data (SoftwareApplication/FAQPage)")

    if stats["text_chars"] >= 200:
        pts += 4
    else:
        gaps.append("insufficient answer density (<200 chars of substantive text)")
    return {"score": pts, "max": 25, "gaps": gaps}


def score_token_economics(html: str, headers: dict | None = None,
                          has_md_mirror: bool = False) -> dict:
    """Pillar 3: Token Economics (25 pts)."""
    pts = 0
    gaps: list[str] = []
    stats = html_to_text_stats(html)
    noise = stats["noise_pct"]
    if noise <= 25.0:
        pts += 10
    elif noise <= 50.0:
        pts += 5
        gaps.append(f"moderate HTML markup noise ({noise}% > 25% target)")
    else:
        gaps.append(f"high HTML DOM bloat ({noise}% markup noise wastes crawler token budget)")

    toks = stats["estimated_tokens"]
    if 0 < toks <= 8000:
        pts += 8
    elif toks <= 16000:
        pts += 4
        gaps.append(f"page size ({toks:,} tokens) exceeds 8,000-token optimal crawler window")
    else:
        gaps.append(f"page token bloat ({toks:,} tokens) risks mid-page crawler truncation")

    hdrs = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    is_md_resp = "text/markdown" in hdrs.get("content-type", "") or "text/markdown" in hdrs.get("vary", "")
    if has_md_mirror or is_md_resp or not re.search(r"<html\b", (html or "")[:500], re.I):
        pts += 7
    else:
        gaps.append("no raw Markdown mirror (.md) or Accept: text/markdown negotiation")
    return {"score": pts, "max": 25, "gaps": gaps, "token_stats": stats}


def score_capability_signaling(html: str, skill_md: str = "", agent_card: str = "") -> dict:
    """Pillar 4: Capability Signaling (15 pts)."""
    pts = 0
    gaps: list[str] = []
    has_skill = bool((skill_md or "").strip()) or ("/skill.md" in (html or "").lower())
    has_card = bool((agent_card or "").strip()) or ("agent-card.json" in (html or "").lower())
    if has_skill:
        pts += 8
    else:
        gaps.append("missing /skill.md agent capability specification")
    if has_card or bool(_CAPABILITY_RE.search(html or "")):
        pts += 7
    else:
        gaps.append("missing /.well-known/agent-card.json or MCP tool descriptor")
    return {"score": pts, "max": 15, "gaps": gaps}


def score_ux_bridge(html: str) -> dict:
    """Pillar 5: UX Bridge (10 pts)."""
    if _UX_BRIDGE_RE.search(html or ""):
        return {"score": 10, "max": 10, "gaps": []}
    return {
        "score": 0,
        "max": 10,
        "gaps": ["missing 1-click UX Bridge deep-link (/geo-audit?domain=... or /claim-audit)"],
    }


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def score_page(html: str = "", url: str = "", headers: dict | None = None,
               robots_txt: str | None = "", llms_txt: str | None = "",
               skill_md: str | None = "", agent_card: str | None = "",
               has_md_mirror: bool = False, has_mcp_manifest: bool = False, ux_bridge: bool = True) -> dict:
    """Compute the 100-Point Agentic SEO Scorecard across the 5 pillars; without ``ux_bridge`` the four others, scaled."""
    eff_agent_card = (agent_card or "") or ('{"mcp":true}' if has_mcp_manifest else "")
    d = score_discovery(html, headers=headers, robots_txt=robots_txt or "", llms_txt=llms_txt or "")
    s = score_structure(html)
    t = score_token_economics(html, headers=headers, has_md_mirror=has_md_mirror)
    c = score_capability_signaling(html, skill_md=skill_md or "", agent_card=eff_agent_card)
    u = score_ux_bridge(html) if ux_bridge else None
    parts = [d, s, t, c] + ([u] if u else [])
    total = round(100 * sum(p["score"] for p in parts) / sum(p["max"] for p in parts))
    gaps = [g for p in parts for g in p["gaps"]]
    tok_pillar = {**t, **t["token_stats"]}
    pillars = {"discovery": d, "structure": s, "token_economics": tok_pillar, "capability": c}
    if u:
        pillars["ux_bridge"] = u
    return {
        "url": url,
        "score": total,
        "total": total,
        "grade": _grade(total),
        "pillars": pillars,
        "gaps": gaps,
        "blocked_bots": d["blocked_bots"],
        "token_stats": t["token_stats"],
    }


def aeo_publish_gate(mdx_text: str, url: str = "/blog/candidate", min_score: int = 90) -> dict:
    """Deterministic 0-token gate for auto-publishing programmatic/blog MDX pages."""
    synthetic_llms = "# Brevio\n- [Post](/blog/post) (~800 tokens)\n"
    res = score_page(
        html=mdx_text or "",
        url=url,
        llms_txt=synthetic_llms,
        skill_md="# Brevio Skill\n",
        agent_card='{"name":"Brevio"}',
        has_md_mirror=True,
    )
    ok = res["score"] >= min_score
    return {
        "ok": ok,
        "verdict": "PASS" if ok else "REVISE",
        "score": res["score"],
        "min_score": min_score,
        "gaps": res["gaps"],
        "blocking_issues": res["gaps"],
        "reason": "passed 100-pt AEO gate" if ok else f"score {res['score']}/{min_score}: {'; '.join(res['gaps'][:3])}",
    }


def _discover_source_routes(workspace_dir: str) -> list[str]:
    """Locate public Next.js page.tsx routes in workspace_dir."""
    app_dir = os.path.join(workspace_dir or "", "apps", "dashboard", "src", "app")
    if not os.path.isdir(app_dir):
        return []
    routes: list[str] = []
    for root, _dirs, files in os.walk(app_dir):
        if "page.tsx" not in files:
            continue
        rel = os.path.relpath(root, app_dir).replace("\\", "/")
        parts = [p for p in rel.split("/") if p and not (p.startswith("(") and p.endswith(")")) and p != "."]
        route = "/" + "/".join(parts)
        if not any(seg.startswith("[") for seg in parts):
            routes.append(route)
    return sorted(set(routes))


def _probe_live_routes(routes: list[str], base_url: str, fetch_fn, live: dict[str, dict]) -> list[str]:
    if not (fetch_fn and base_url):
        return []
    base = base_url.rstrip("/")
    missing: list[str] = []
    for r in routes:
        resp = fetch_fn(base + r) or {}
        if resp.get("status") != 200:
            missing.append(r)
        else:
            live[r] = resp
    return missing


def _score_live_map(live: dict[str, dict]) -> tuple[dict[str, int], list[dict]]:
    scored_routes: dict[str, int] = {}
    mismatches: list[dict] = []
    for route, payload in live.items():
        sc = score_page(
            html=str(payload.get("text") or ""),
            url=route,
            headers=payload.get("headers") or {},
            robots_txt=str(payload.get("robots_txt") or ""),
            llms_txt=str(payload.get("llms_txt") or ""),
            skill_md=str(payload.get("skill_md") or ""),
        )
        scored_routes[route] = sc["score"]
        if sc["score"] < 75 or sc["token_stats"]["is_hydration_wall"]:
            mismatches.append({"route": route, "score": sc["score"], "gaps": sc["gaps"][:3]})
    return scored_routes, mismatches


def audit_workspace_vs_live(workspace_dir: str,
                            base_url: str = "",
                            fetch_fn=None,
                            live_pages: dict[str, dict] | None = None) -> dict:
    """Dual-surface audit (codydeny/agent-seo-skill pattern): compares static workspace
    routes and public AEO assets against rendered live responses.
    """
    routes = _discover_source_routes(workspace_dir)
    pub_dir = os.path.join(workspace_dir or "", "apps", "dashboard", "public")
    asset_names = ("/llms.txt", "/llms-full.txt", "/skill.md", "/robots.txt")
    static_assets = {
        name.lstrip("/"): os.path.isfile(os.path.join(pub_dir, name.lstrip("/")))
        for name in asset_names
    }
    workspace_aeo_assets = [n for n in asset_names if static_assets.get(n.lstrip("/"))]
    missing_aeo_assets = [n for n in asset_names if not static_assets.get(n.lstrip("/"))]
    live = dict(live_pages or {})
    missing_live_routes = _probe_live_routes(routes, base_url, fetch_fn, live)
    scored_routes, mismatches = _score_live_map(live)
    return {
        "ok": len(mismatches) == 0 and len(missing_live_routes) == 0,
        "source_routes": routes,
        "workspace_routes": routes,
        "missing_live_routes": missing_live_routes,
        "static_assets": static_assets,
        "workspace_aeo_assets": workspace_aeo_assets,
        "missing_aeo_assets": missing_aeo_assets,
        "scored_routes": scored_routes,
        "mismatches": mismatches,
    }
