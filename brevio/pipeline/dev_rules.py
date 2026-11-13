"""Zero-token software engineering rules engine.

Enforces the 46-rule software engineering catalog without bloating the per-turn
developer prompt prefix:
- Layer 1 (0 prompt tokens): ``scan_added_lines`` runs deterministically over the lines a
  change adds, which ``brevio-core scan --surface`` reports.
- Layer 2 (0-180 tokens): ``slice_micro_rules`` routes compact 1-line micro-cards
  strictly to slices whose target files match the surface globs, which ``brevio-core brief`` prints.
"""
from __future__ import annotations

import os
import re
from collections import Counter

from brevio.pipeline import rule_host
from brevio.pipeline.code_rules import PR_RULES_122

CATALOG_RULES: dict[str, str] = {
    "R1.1": "Mobile GPU budget: scope blur >=40px to md:/lg: desktop viewports",
    "R1.2": "SSR visibility resilience: never leave [data-reveal] at opacity:0 without CSS animation fallback",
    "R1.3": "Third-party script deferral: load GTM/GA4 with lazyOnload after hydration",
    "R1.4": "Zero-flash theme bootstrap: paired semantic tokens across light/dark modes",
    "R1.5": "No dead controls: every interactive element wires a handler and loading/error feedback",
    "R1.6": "Accessible switch geometry: inline-flex items-center shrink-0 on toggle tracks",
    "R1.7": "LCP asset priority: priority/fetchpriority=high on hero LCP image, lazy below fold",
    "R1.8": "Async polling hygiene: clear intervals on unmount and stop polling on terminal states",
    "R2.1": "Tenant context derivation: derive tenantId from verified session, never request payload",
    "R2.2": "Tenant-scoped data access: every DB read/write filters by tenantId",
    "R2.3": "Dual-key workspace resolution: discriminate UUID/CUID vs domain/slug before query",
    "R2.4": "Raw-body webhook verification: verify HMAC against raw request text before JSON parse",
    "R2.5": "Outbound SSRF & timeout guard: validate URL host and set AbortSignal.timeout()",
    "R2.6": "Constant-time secret comparison: use timingSafeEqual / compare_digest",
    "R2.7": "Secure cookie & CORS hygiene: HttpOnly, Secure, SameSite, no wildcard CORS with credentials",
    "R3.1": "Idempotent webhook processing: deduplicate event_id with monotonic state transitions",
    "R3.2": "Monetary integer representation: store money as integer minor units (cents) + currency",
    "R3.3": "Gateway ID discrimination: validate customer/subscription/checkout prefix before API call",
    "R3.4": "Pre-execution quota enforcement: verify tier entitlement before paid LLM/API calls",
    "R3.5": "Transactional outbox: persist DB state before enqueuing external side effects",
    "R4.1": "Expand-contract migrations: additive nullable/default columns; no single-step drop/rename",
    "R4.2": "Foreign-key & tenant composite indexing: index FKs and (tenant_id, created_at)",
    "R4.3": "N+1 query prevention: batch queries with IN / joins instead of per-row loop queries",
    "R4.4": "Ingestion FK pre-resolution: upsert or verify parent tenant/workspace before child rows",
    "R4.5": "Bounded pagination: enforce hard LIMIT (<=100) on every list query",
    "R5.1": "Deterministic graceful fallbacks: return explicit degraded status, never fabricated metrics",
    "R5.2": "HTTP empty-body hygiene: 204 returns empty body; 200 JSON sets Content-Type",
    "R5.3": "Bounded retry with jitter: retry only transient 429/502/503/504 with backoff + jitter",
    "R5.4": "No silent exception swallowing: log or surface errors; never use empty catch blocks",
    "R5.5": "Process & lock safety: every subprocess.run carries timeout=; locks use try/finally",
    "R6.1": "Real production import gate: tests must import and invoke real production code",
    "R6.2": "Mutation side-effect assertions: assert persisted state or DOM changes, not just HTTP 200",
    "R6.3": "Deterministic condition waits: never use waitForTimeout/sleep in tests",
    "R6.4": "Hermetic test state isolation: isolate DB/state paths per test in setUp/addCleanup",
    "R6.5": "Post-incident regression crystallization: every bug fix includes a failing-first test",
    "R7.1": "Contract-first monorepo changes: update shared Zod/OpenAPI schema before consumers",
    "R7.2": "Granular field validation: return field-level errors and preserve user form inputs",
    "R7.3": "Single canonical route per path: no duplicate route group paths",
    "R7.4": "Acyclic module layering: domain -> application -> adapters; no circular imports",
    "R7.5": "Universal contracts purity: packages/contracts barrels must not import Node built-ins (node:fs, node:path, etc.) or perform runtime I/O; isolate Node utilities to server-only files",
    "R8.1": "Anti-cloaking & markdown parity: serve identical facts to crawlers and browsers",
    "R8.2": "Honest telemetry attribution: label synthetic/demo data; never mix with live metrics",
    "R8.3": "Consent Mode v2 ordering: initialize default denied consent before analytics tags",
    "R9.1": "No untyped escape hatches: no `as any` casts or `z.any()` schemas in production source",
    "R9.2": "No silenced type errors: no @ts-ignore; every @ts-expect-error states its reason",
    "R9.3": "Package-name imports: import workspace packages by name, never via ../../packages/ paths",
}

_UI_EXT_RX = re.compile(r"\.(tsx|jsx|css|scss)$", re.I)
_API_PATH_RX = re.compile(r"(?:^|/)(?:api|routes?|actions?|middleware|webhook|proxy|mcp)(?:/|\.|$)", re.I)
_BILLING_PATH_RX = re.compile(r"(?:billing|payment|stripe|polar|checkout|entitlement|subscription|tier|quota)", re.I)
_DB_PATH_RX = re.compile(r"(?:\.(?:prisma|sql)$|(?:^|/)(?:migrations?|drizzle|schema|models?|repositor(?:y|ies))(?:/|\.|$))", re.I)
_TEST_PATH_RX = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$", re.I)
_CONTRACTS_PATH_RX = re.compile(r"(?:^|/)(?:packages/contracts|contracts/|shared/contracts)(?:/|\.|$)", re.I)
_NODE_BUILTIN_RX = re.compile(r"""(?:from\s+['"]node:(?:fs|path|crypto|child_process|os|net|http|stream|buffer)['"]|import\s*\(\s*['"]node:(?:fs|path|crypto|child_process|os|net|http|stream|buffer)['"]\))""")

_HIGH_BLUR_TSX_RX = re.compile(r"(?<![:\w-])blur-\[(?:[4-9]\d|\d{3,})px\]")
_RESPONSIVE_BLUR_RX = re.compile(r"(?:sm|md|lg|xl|2xl):blur-")
_REVEAL_OPACITY_ZERO_RX = re.compile(r"\[data-reveal\][^{]*\{[^}]*opacity\s*:\s*0\b")
_GTM_ASYNC_SCRIPT_RX = re.compile(r"<script\b[^>]*\basync\b[^>]*googletagmanager\.com/gtag/js", re.I)
_EMPTY_CATCH_RX = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}")
_WAIT_FOR_TIMEOUT_RX = re.compile(r"\bwaitForTimeout\s*\(\s*\d+")
_REQ_JSON_RX = re.compile(r"\bawait\s+(?:req|request)\.json\s*\(")
_SIG_CHECK_RX = re.compile(r"(?:signature|hmac|timingSafeEqual|verifyWebhook|svix)", re.I)
_RAW_BODY_RX = re.compile(r"(?:\.text\s*\(|\.arrayBuffer\s*\(|rawBody)", re.I)

_SCRIPT_EXT_RX = re.compile(r"\.[cm]?[jt]sx?$", re.I)
# Test helpers, mocks, fixtures and runner setup outside ``*.test.*`` files: not production source for R9.1.
_TEST_SUPPORT_RX = re.compile(
    r"(?:^|/)(?:__tests__|__mocks__|__fixtures__|tests?|e2e|fixtures)/|(?:^|/)(?:vitest|jest)\.setup\.|(?:^|/)setupTests\.",
    re.I,
)
_COMMENT_LINE_RX = re.compile(r"^\s*(?://|/\*|\*)")
_AS_ANY_RX = re.compile(r"\bas\s+any(?![\w$])|\bz\.any\s*\(")
_TS_IGNORE_RX = re.compile(r"@ts-ignore\b")
# A bare ``@ts-expect-error`` (followed only by ``:``/``--`` or a block-comment close, JSX ``{/* */}`` too)
# gives no reason.
_TS_EXPECT_BARE_RX = re.compile(r"@ts-expect-error(?![\w-])[\s:-]*(?:\*/\s*\}?\s*)?$")
_PACKAGES_CLIMB_RX = re.compile(r"""\b(?:from|import|require)\s*\(?\s*['"](?:\.\./){2,}packages(?:/|['"])""")
_SELF_MOCK_RX = re.compile(
    r"""\b(?:vi|jest)\.(?:mock|doMock)\s*\(\s*(?:import\s*\(\s*)?['"]((?:\.{1,2}|@|~)/[^'"]+)['"]"""
)
_EXPECT_CALL_RX = re.compile(r"\bexpect\s*\(")
_MATCHER_RX = re.compile(r"\.\s*(to(?:Be|Have|Equal|StrictEqual|Match|Contain|Throw|Satisfy|Return|Pass)\w*)\s*\(")
_WEAK_MATCHERS = frozenset({"toBeDefined", "toBeTruthy"})

# Mutation-audit survivors (DELIVERY.md L3a). R2.2: a tenant filter is ``eq(<table>.tenantId, ...)``,
# alone in ``.where(...)`` or inside ``and(...)``; only API code under apps/api/ is checked.
_TENANT_API_RX = re.compile(r"^apps/api/.+\.[cm]?[jt]sx?$")
_TENANT_EQ_RX = re.compile(r"\beq\(\s*([\w$]+(?:\.[\w$]+)*)\.tenantId\b")
# R3.2: a minor-units value turned into a decimal (``percents`` is not a cents value).
_CENTS_TO_FLOAT_RX = re.compile(r"[\w$.]*(?:Cents|(?<![A-Za-z])cents)\b\s*(?:/\s*100\b(?!\.\d)|\*\s*0?\.01\b(?!\d))")
# R3.2 exemptions, the whole list: display code may show cents as a decimal. A file named format*,
# display* or *formatter*, a component (.tsx/.jsx or under components/), a function named format*,
# display* or *Formatter, or a line that formats the number itself.
_MONEY_DISPLAY_FILE_RX = re.compile(r"(?:^|/)(?:format|display)[^/]*$|formatter[^/]*$|\.[jt]sx$|(?:^|/)components/", re.I)
_MONEY_DISPLAY_FUNC_RX = re.compile(r"^(?:format|display)|Formatter$", re.I)
_MONEY_DISPLAY_CALL_RX = re.compile(r"Intl\.NumberFormat|\.toLocaleString\(|\.toFixed\(")
_FUNC_HEAD_RX = re.compile(
    r"\b(?:function\s*\*?\s*|class\s+)([\w$]+)"
    r"|\b(?:const|let|var)\s+([\w$]+)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^)]*\)[^=]*=>|[\w$]+\s*=>)"
    r"|^\s*(?:(?:public|private|protected|static|async)\s+)*([\w$]+)\s*\([^)]*\)\s*(?::[^{]+)?\{")
_NOT_A_FUNCTION = frozenset({"if", "for", "while", "switch", "catch", "return", "with"})
_MONEY_NAME_RX = re.compile(r"(?:money|price|amount|cents|invoice|currency|revenue)", re.I)
# The UI-fix gate's palette check (``rule_host.PALETTE``), on every change to a dashboard component.
_DASHBOARD_TSX_RX = re.compile(r"^apps/dashboard/.+\.tsx$")


def slice_micro_rules(files: list[str] | tuple[str, ...] | set[str]) -> str:
    """Return compact (<180 token) surface-specific engineering rules for ``files``."""
    paths = [str(f) for f in (files or []) if f]
    if not paths:
        return ""
    bullets: list[str] = []
    if any(_UI_EXT_RX.search(p) for p in paths):
        bullets.append(
            "- [UI/Mobile R1.1-R1.7] Scope blur >=40px to md:/lg: (e.g. blur-none md:blur-[120px]). "
            "Never set SSR [data-reveal] opacity:0 without a CSS @keyframes fallback. "
            "Load GTM/GA4 via <Script strategy=\"lazyOnload\">. Every button/switch/form needs a real handler + loading/error state."
        )
    if any(_API_PATH_RX.search(p) for p in paths):
        bullets.append(
            "- [API/Security R2.1-R2.7, R5.1-R5.4] Derive tenantId from verified session (never trust body/query IDs) and filter every DB query by tenantId. "
            "Set AbortSignal.timeout() on outbound fetch(). Return 204 with empty body; never swallow errors in empty catch {} blocks."
        )
    if any(_BILLING_PATH_RX.search(p) for p in paths):
        bullets.append(
            "- [Billing/Webhooks R2.4, R3.1-R3.4] Verify webhook HMAC against raw req.text() before JSON.parse. "
            "Deduplicate event_id with monotonic state transitions. Store money as integer cents (no `/ 100` outside format*/display* code); "
            "enforce tier quota before paid calls."
        )
    if any(_DB_PATH_RX.search(p) for p in paths):
        bullets.append(
            "- [Database R4.1-R4.5] Expand-contract migrations only (additive nullable/default columns; no single-step drop/rename). "
            "Index every FK and (tenant_id, created_at); batch queries to avoid N+1 loops."
        )
    if any(_TEST_PATH_RX.search(p) for p in paths):
        bullets.append(
            "- [Tests R6.1-R6.4] Import and call real production code (no local mock copies); never vi.mock/jest.mock "
            "the module under test. Assert persisted state or DOM mutations, not only toBeDefined()/toBeTruthy(); "
            "never use waitForTimeout()."
        )
    bullets += _anti_slop_bullets(paths)
    bullets += _audit_rule_bullets(paths)
    if any(_CONTRACTS_PATH_RX.search(p) for p in paths):
        bullets.append(
            "- [Contracts Purity R7.1, R7.5] Keep the shared contracts package pure (Zod schemas, types, pure transformations). "
            "Never import Node built-ins (node:fs, node:path, node:crypto) in universal barrels re-exported to client apps. "
            "Isolate Node utilities to server-only entrypoints not re-exported in index.ts."
        )
    if not bullets:
        return ""
    return "\nSLICE-SCOPED ENGINEERING RULES FOR THESE FILES:\n" + "\n".join(bullets) + "\n"


def _anti_slop_bullets(paths: list[str]) -> list[str]:
    """The R9.x card, up front, for a slice with script files: the same rules ``scan_added_lines``
    rejects in pre-flight. R9.1 only when one of them is production source."""
    scripts = [p for p in paths if _SCRIPT_EXT_RX.search(p)]
    if not scripts:
        return []
    src = any(not (_TEST_PATH_RX.search(p) or _TEST_SUPPORT_RX.search(p)) for p in scripts)
    return ["- [Anti-slop " + ("R9.1-R9.3] No `as any` or `z.any()` in production source. " if src else "R9.2-R9.3] ")
            + "No @ts-ignore; every @ts-expect-error states its reason. "
            "Import workspace packages by name, never via ../../packages/."]


def _audit_rule_bullets(paths: list[str]) -> list[str]:
    """One line each for the mutation-audit rules (R2.2 tenant filter, R3.2 integer money, dashboard
    palette), only for a slice with files they check. A billing slice has R3.2 in its billing card."""
    src = [p for p in paths if _SCRIPT_EXT_RX.search(p) and not (_TEST_PATH_RX.search(p) or _TEST_SUPPORT_RX.search(p))]
    out = []
    if any(_TENANT_API_RX.match(p) for p in src):
        out.append("- [R2.2] Never drop a `.where(eq(<table>.tenantId, ...))` filter from a query that stays.")
    if not any(_BILLING_PATH_RX.search(p) for p in paths) and any(
            not _MONEY_DISPLAY_FILE_RX.search(p) and (_MONEY_NAME_RX.search(p) or _TENANT_API_RX.match(p)) for p in src):
        out.append("- [R3.2] Keep *Cents values integer: `/ 100` or `* 0.01` only in a format*/display* function.")
    if any(_DASHBOARD_TSX_RX.match(p) and rule_host.ui_source(p) for p in src):
        out.append("- [UI tokens] No raw palette classes (bg-slate-900, text-zinc-500) in dashboard components.")
    return out


def _added_lines(workspace_dir: str, rel_path: str, full_path: str) -> list[tuple[int, str]]:
    """Return ``(line_no, line_text)`` for added lines in ``rel_path``, falling back to full file if untracked."""
    found = rule_host.head_added_lines(workspace_dir, rel_path)
    if found:
        return found
    try:
        with open(full_path, encoding="utf-8", errors="ignore") as f:
            return list(enumerate(f.read().splitlines(), 1))
    except OSError:
        return []


def _check_contract_purity(
    rel_path: str, is_contracts: bool, is_ts_prod: bool, is_server_only: bool, added: list[tuple[int, str]]
) -> list[str]:
    """Flag Node built-in imports in universal contracts barrels."""
    if not (is_contracts and is_ts_prod and not is_server_only):
        return []
    findings = []
    for lineno, line in added:
        m = _NODE_BUILTIN_RX.search(line)
        if m:
            findings.append(
                f"- {rel_path}:{lineno} [R7.5 Universal Contracts Purity]: Node built-in `{m.group(0)}` "
                "forbidden in universal contracts barrel. Next.js Webpack client bundler fails on node: schemes. "
                "Keep contracts pure or isolate in unexported server-only file (e.g. *-io.ts)."
            )
    return findings


def _scan_added_line(rel_path: str, is_ui: bool, is_test: bool, is_ts_prod: bool, lineno: int, line: str) -> list[str]:
    """Check a single added line against code pattern rules."""
    res = []
    if is_ui and _HIGH_BLUR_TSX_RX.search(line) and not _RESPONSIVE_BLUR_RX.search(line):
        res.append(
            f"- {rel_path}:{lineno} [R1.1 Mobile GPU Budget]: Unscoped high blur `{_HIGH_BLUR_TSX_RX.search(line).group(0)}` "
            "causes mobile GPU tile thrashing. Scope to desktop with `blur-none md:blur-...`."
        )
    if is_ui and _GTM_ASYNC_SCRIPT_RX.search(line):
        res.append(
            f"- {rel_path}:{lineno} [R1.3 Script Deferral]: Raw `<script async>` for GTM/GA4 blocks mobile hydration. "
            "Use Next.js `<Script strategy=\"lazyOnload\">`."
        )
    if is_test and _WAIT_FOR_TIMEOUT_RX.search(line):
        res.append(
            f"- {rel_path}:{lineno} [R6.3 No Sleep in Tests]: `waitForTimeout()` is forbidden. "
            "Await a deterministic condition or assertion instead."
        )
    if is_ts_prod and _EMPTY_CATCH_RX.search(line):
        res.append(
            f"- {rel_path}:{lineno} [R5.4 No Silent Catch]: Empty `catch {{}}` block swallows runtime failures. "
            "Log or propagate a structured error."
        )
    return res


def _scan_slop_line(rel_path: str, test_stem: str, is_src: bool, lineno: int, line: str) -> list[str]:
    """Anti-slop checks on one added script line: R9.1-R9.3, and R6.1 when ``test_stem`` names a test file.
    ``is_src`` is production source (no test, test helper or setup file). R9.1 and R9.3 skip comment lines."""
    res = []
    code = not _COMMENT_LINE_RX.match(line)
    any_cast = _AS_ANY_RX.search(line) if is_src and code else None
    if any_cast:
        res.append(f"- {rel_path}:{lineno} [R9.1 No Untyped Escape]: `{any_cast.group(0)}` discards "
                   "type checking. Use a real type, `unknown` plus narrowing, or a concrete Zod schema.")
    if _TS_IGNORE_RX.search(line):
        res.append(f"- {rel_path}:{lineno} [R9.2 No Silenced Type Errors]: `@ts-ignore` hides type errors silently. "
                   "Fix the type, or use `@ts-expect-error -- <reason>`.")
    elif _TS_EXPECT_BARE_RX.search(line):
        res.append(f"- {rel_path}:{lineno} [R9.2 No Silenced Type Errors]: `@ts-expect-error` without a reason. "
                   "State why after the directive (`@ts-expect-error -- <reason>`).")
    if code and _PACKAGES_CLIMB_RX.search(line):
        res.append(f"- {rel_path}:{lineno} [R9.3 Package-Name Imports]: Relative import climbs into `packages/`. "
                   "Import the workspace package by its name so its exports map and build boundary apply.")
    mocked = _SELF_MOCK_RX.search(line) if test_stem else None
    if mocked and _SCRIPT_EXT_RX.sub("", mocked.group(1).rsplit("/", 1)[-1]) == test_stem:
        res.append(f"- {rel_path}:{lineno} [R6.1 Real Production Import Gate]: Test mocks `{mocked.group(1)}`, the "
                   "module it is named after, so it never runs the real code. Mock its dependencies instead.")
    return res


def _enclosing_functions(full_lines: list[str], lineno: int) -> list[str]:
    """The names of the function, method and class heads whose block is still open at ``lineno``
    (1-based), innermost first: a head on that line, an ``=>`` head ending the line before it, or a
    head whose ``{`` is not closed before it. A sibling that already closed does not count."""
    names, pending = [], 0
    for i in range(min(lineno, len(full_lines)) - 1, -1, -1):
        line, opens = full_lines[i], False
        if i < lineno - 1:
            for ch in reversed(line):
                if ch == "}":
                    pending += 1
                elif ch == "{":
                    opens = opens or not pending
                    pending = max(0, pending - 1)
        m = _FUNC_HEAD_RX.search(line)
        name = next((g for g in m.groups() if g), "") if m else ""
        arrow = i == lineno - 2 and line.rstrip().endswith("=>")
        if name and name not in _NOT_A_FUNCTION and (i == lineno - 1 or opens or arrow):
            names.append(name)
    return names


def _cents_to_float(rel_path: str, full_text: str, added: list[tuple[int, str]]) -> list[str]:
    """R3.2 on added lines of production source: ``/ 100`` or ``* 0.01`` on a *Cents value outside
    display code (``_MONEY_DISPLAY_*``)."""
    if _MONEY_DISPLAY_FILE_RX.search(rel_path):
        return []
    res, full_lines = [], full_text.splitlines()
    for lineno, line in added:
        m = _CENTS_TO_FLOAT_RX.search(line)
        if (not m or _COMMENT_LINE_RX.match(line) or _MONEY_DISPLAY_CALL_RX.search(line)
                or any(_MONEY_DISPLAY_FUNC_RX.search(n) for n in _enclosing_functions(full_lines, lineno))):
            continue
        res.append(f"- {rel_path}:{lineno} [R3.2 Integer Money]: `{m.group(0)}` turns integer minor units into "
                   "a float. Keep cents as integers; convert only in a format*/display* function.")
    return res


def _dropped_tenant_filters(workspace_dir: str, rel_path: str, full_text: str) -> list[str]:
    """R2.2: the change removes an ``eq(<table>.tenantId, ...)`` filter against main and adds none back
    for that table (nor a line naming the table and tenantId, e.g. a scoping helper), while the query
    stays: its head (``from(<table>)``, ``update(<table>)``, ``delete(<table>)``) is not removed too."""
    removed = rule_host.removed_lines(workspace_dir, [rel_path]).get(rel_path) or []
    lost = Counter(m.group(1) for ln in removed for m in _TENANT_EQ_RX.finditer(ln))
    if not lost:
        return []
    added = rule_host.added_lines(workspace_dir, [rel_path]).get(rel_path) or []
    res = []
    for table in sorted(lost):
        at = _unfiltered_query_line(table, lost[table], removed, added, full_text)
        if at:
            res.append(f"- {rel_path}:{at} [R2.2 Tenant-Scoped Data Access]: The change removes the tenantId "
                       f"filter on `{table}` from a query that stays. Keep `.where(eq({table}.tenantId, ...))`.")
    return res


def _unfiltered_query_line(table: str, lost: int, removed: list[str], added: list[str], full_text: str) -> int:
    """The line of the first query head on ``table`` left without the ``lost`` tenant filters, or 0.
    A filter counts as kept when re-added or when an added code line names the table and tenantId; one
    counts as gone with its query for each query head on ``table`` the change removes on balance."""
    word = re.compile(rf"(?<![\w$.]){re.escape(table)}(?![\w$])")
    head = re.compile(rf"\b(?:from|update|delete)\(\s*{re.escape(table)}\s*\)")
    code = [ln for ln in added if not _COMMENT_LINE_RX.match(ln)]
    kept = sum(m.group(1) == table for ln in code for m in _TENANT_EQ_RX.finditer(ln))
    kept += sum(1 for ln in code if "tenantId" in ln and word.search(ln) and not _TENANT_EQ_RX.search(ln))
    deleted = sum(1 for ln in removed if head.search(ln)) - sum(1 for ln in code if head.search(ln))
    if lost - kept - max(0, deleted) <= 0:
        return 0
    return next((i for i, ln in enumerate(full_text.splitlines(), 1) if head.search(ln)), 0)


def _dashboard_palette(workspace_dir: str, rel_path: str, full_text: str) -> list[str]:
    """The UI-fix gate's palette check, for every task: raw palette classes the change adds against main
    to a dashboard component (same ``rule_host.PALETTE`` and test/docs exemptions)."""
    if not (_DASHBOARD_TSX_RX.match(rel_path) and rule_host.ui_source(rel_path)):
        return []
    hits = [ln for ln in rule_host.added_lines(workspace_dir, [rel_path]).get(rel_path) or []
            if rule_host.PALETTE.search(ln)]
    if not hits:
        return []
    pal = sorted({m.group(0) for ln in hits for m in rule_host.PALETTE.finditer(ln)})
    lineno = next((i for i, ln in enumerate(full_text.splitlines(), 1) if ln in hits), 1)
    return [f"- {rel_path}:{lineno} [UI Design Tokens]: Raw palette classes {', '.join(pal[:5])} in a dashboard "
            "component. Use the design tokens instead."]


def _weak_assertions_only(rel_path: str, full_text: str, added: list[tuple[int, str]]) -> list[str]:
    """R6.2 for a test file whose every matcher is ``toBeDefined()``/``toBeTruthy()``. Judged on the whole
    file, reported on the first added line using one, so a file the change did not weaken is not blamed."""
    matchers = set(_MATCHER_RX.findall(full_text))
    if not _EXPECT_CALL_RX.search(full_text) or not matchers or not matchers <= _WEAK_MATCHERS:
        return []
    for lineno, line in added:
        if _MATCHER_RX.search(line):
            return [f"- {rel_path}:{lineno} [R6.2 Mutation Side-Effect Assertions]: Every assertion in this test "
                    "file is `toBeDefined()`/`toBeTruthy()`, which pass on almost any value. Assert concrete values."]
    return []


def _multiline_empty_catches(rel_path: str, added: list[tuple[int, str]]) -> list[str]:
    """R5.4 for an empty catch written over several added lines (``catch (e) {`` then ``}``), which the
    per-line check cannot see (DELIVERY review). Runs of consecutive added lines are joined; a match on
    one line is left to ``_scan_added_line``."""
    res: list[str] = []
    run: list[tuple[int, str]] = []
    for item in [*added, (None, "")]:
        if run and (item[0] is None or item[0] != run[-1][0] + 1):
            if len(run) > 1:
                text = "\n".join(line for _, line in run)
                for m in _EMPTY_CATCH_RX.finditer(text):
                    if "\n" in m.group(0):
                        lineno = run[text.count("\n", 0, m.start())][0]
                        res.append(f"- {rel_path}:{lineno} [R5.4 No Silent Catch]: Empty `catch {{}}` block swallows "
                                   "runtime failures. Log or propagate a structured error.")
            run = []
        if item[0] is not None:
            run.append(item)
    return res


def _scan_full_text(rel_path: str, low: str, full_text: str, is_ui: bool, is_ts_prod: bool) -> list[str]:
    """Check full file contents against architectural pattern rules."""
    findings = []
    if is_ui and _REVEAL_OPACITY_ZERO_RX.search(full_text) and "keyframes" not in full_text:
        findings.append(
            f"- {rel_path}:1 [R1.2 SSR Visibility Resilience]: `[data-reveal]` sets `opacity: 0` without a `@keyframes` "
            "CSS fallback, risking permanent blank pages if JS hydration is delayed."
        )
    if "webhook" in low and is_ts_prod and _REQ_JSON_RX.search(full_text):
        if _SIG_CHECK_RX.search(full_text) and not _RAW_BODY_RX.search(full_text):
            findings.append(
                f"- {rel_path}:1 [R2.4 Raw Webhook Signature]: Webhook route calls `req.json()` without reading raw "
                "`req.text()` for HMAC verification."
            )
    return findings


def _scan_single_file(rel_path: str, full_text: str, added: list[tuple[int, str]]) -> list[str]:
    """Check one file's added lines against deterministic Layer-1 rules."""
    findings: list[str] = []
    low = rel_path.lower()
    is_test = bool(_TEST_PATH_RX.search(low))
    is_ui = low.endswith((".tsx", ".jsx", ".css", ".scss"))
    is_ts_prod = low.endswith((".ts", ".tsx", ".js", ".jsx")) and not is_test

    if is_ts_prod and ("'use client'" in full_text or '"use client"' in full_text) and (
        "'server-only'" in full_text or '"server-only"' in full_text
    ):
        findings.append(f"{rel_path}: [R17.2] {PR_RULES_122['R17.2']}")

    is_contracts = bool(_CONTRACTS_PATH_RX.search(low))
    is_server_only = any(x in low for x in ("drift.ts", "-io.ts", "-node.ts", "server/"))
    findings.extend(_check_contract_purity(rel_path, is_contracts, is_ts_prod, is_server_only, added))

    is_script = bool(_SCRIPT_EXT_RX.search(low))
    is_src = is_script and not is_test and not _TEST_SUPPORT_RX.search(low)
    test_stem = _TEST_PATH_RX.sub("", os.path.basename(rel_path)) if is_test else ""
    for lineno, line in added:
        findings.extend(_scan_added_line(rel_path, is_ui, is_test, is_ts_prod, lineno, line))
        if is_script:
            findings.extend(_scan_slop_line(rel_path, test_stem, is_src, lineno, line))
    if is_test:
        findings.extend(_weak_assertions_only(rel_path, full_text, added))
    if is_ts_prod:
        findings.extend(_multiline_empty_catches(rel_path, added))
    if is_src:
        findings.extend(_cents_to_float(rel_path, full_text, added))

    findings.extend(_scan_full_text(rel_path, low, full_text, is_ui, is_ts_prod))
    return findings


def scan_added_lines(workspace_dir: str, changed_files: list[str]) -> list[str]:
    """Scan added lines across ``changed_files`` in ``workspace_dir`` for deterministic rule violations."""
    if not workspace_dir or not changed_files:
        return []
    violations: list[str] = []
    for rel in sorted(set(changed_files)):
        if not rel or ".invariant.test." in rel:
            continue
        full = os.path.join(workspace_dir, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="ignore") as f:
                full_text = f.read()
        except OSError:
            continue
        added = _added_lines(workspace_dir, rel, full)
        violations.extend(_scan_single_file(rel, full_text, added))
        if _TENANT_API_RX.match(rel) and not (_TEST_PATH_RX.search(rel) or _TEST_SUPPORT_RX.search(rel)):
            violations.extend(_dropped_tenant_filters(workspace_dir, rel, full_text))
        violations.extend(_dashboard_palette(workspace_dir, rel, full_text))
    return violations
