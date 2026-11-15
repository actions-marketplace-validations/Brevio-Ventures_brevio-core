"""PR-Checkable Zero-Token Code Rules Engine (R1–R20).

Pure deterministic AST/regex rule catalog and pre-commit / PR diff scanner.
Holds no tenant-derived text or markers, so it runs unchanged in the exported package.
"""
from __future__ import annotations

import functools
import math
import os
import re
from collections import Counter

# PR-Checkable Zero-Token Engineering Rules Catalog (R1–R20). The name keeps the count of the first catalogue: R2.8 was
# added on 2026-11-05, before the first release, so the eval and raw-HTML findings stop carrying R2.5 (the SSRF guard).
PR_RULES_122: dict[str, str] = {
    # R1: UI / Mobile Performance (8 rules)
    "R1.1": "Mobile GPU budget: scope blur >=40px to md:/lg: desktop viewports",
    "R1.2": "SSR visibility resilience: leave no [data-reveal] at opacity:0 without a CSS animation fallback",
    "R1.3": "Third-party script deferral: load GTM/GA4 with lazyOnload after hydration",
    "R1.4": "Flash-free theme bootstrap: paired semantic tokens across light and dark modes",
    "R1.5": "No dead controls: an interactive element wires a handler and loading and error feedback",
    "R1.6": "Accessible switch geometry: inline-flex items-center shrink-0 on toggle tracks",
    "R1.7": "LCP asset priority: priority/fetchpriority=high on hero LCP image, lazy below fold",
    "R1.8": "Async polling hygiene: clear intervals on unmount and stop polling on terminal states",
    # R2: Security & Tenancy (8 rules)
    "R2.1": "Tenant context derivation: derive tenant context from the verified session, not the client payload",
    "R2.2": "Tenant-scoped data access: a DB read or write filters by the authenticated workspace scope",
    "R2.3": "Dual-key entity resolution: discriminate UUID/CUID vs domain/slug before database query",
    "R2.4": "Raw-body webhook verification: verify HMAC against raw request text before JSON parse",
    "R2.5": "Outbound SSRF & timeout guard: validate URL host and set AbortSignal.timeout()",
    "R2.6": "Constant-time secret comparison: use timingSafeEqual / compare_digest",
    "R2.7": "Secure cookie & CORS hygiene: HttpOnly, Secure, SameSite, no wildcard CORS with credentials",
    "R2.8": "No unsafe code or HTML sinks: eval or exec on a non-literal, new Function, unsanitised dangerouslySetInnerHTML, "
            "interpolated SQL strings and hidden control characters",
    # R3: Billing & FinOps (5 rules)
    "R3.1": "Idempotent webhook processing: deduplicate event_id with monotonic state transitions",
    "R3.2": "Monetary integer representation: store money as integer minor units (cents) + currency",
    "R3.3": "Gateway ID discrimination: validate customer/subscription/checkout prefix before API call",
    "R3.4": "Pre-execution quota enforcement: verify tier entitlement before paid LLM/API calls",
    "R3.5": "Transactional outbox: persist DB state before enqueuing external side effects",
    # R4: Database & Migrations (5 rules)
    "R4.1": "Expand-contract migrations: additive nullable/default columns; no single-step drop/rename",
    "R4.2": "Foreign-key & tenant composite indexing: index FKs and (tenant_id, created_at)",
    "R4.3": "N+1 query prevention: batch queries with IN / joins instead of per-row loop queries",
    "R4.4": "Ingestion FK pre-resolution: upsert or verify parent workspace before child rows",
    "R4.5": "Bounded pagination: enforce a hard LIMIT (<=100) on list queries",
    # R5: Reliability & Fallbacks (5 rules)
    "R5.1": "Deterministic graceful fallbacks: return an explicit degraded status, not fabricated metrics",
    "R5.2": "HTTP empty-body hygiene: 204 returns empty body; 200 JSON sets Content-Type",
    "R5.3": "Bounded retry with jitter: retry only transient 429/502/503/504 with backoff + jitter",
    "R5.4": "No silent exception swallowing: log or surface errors; empty catch blocks, bare except and except Exception: pass "
            "are flagged",
    "R5.5": "Process safety: no subprocess call with shell=True and no os.system call built from an f-string",
    # R6: Testing & Hermeticity (5 rules)
    "R6.1": "Real production import gate: tests must import and invoke real production code",
    "R6.2": "Mutation side-effect assertions: assert persisted state or DOM changes, not just HTTP 200",
    "R6.3": "No fixed-length waitForTimeout in tests: wait for a condition instead",
    "R6.4": "Hermetic test state isolation: isolate DB/state paths per test in setUp/addCleanup",
    "R6.5": "Post-incident regression crystallization: a bug fix comes with a failing-first test",
    # R7: Architecture & Contracts (5 rules)
    "R7.1": "Contract-first monorepo changes: update shared Zod/OpenAPI schema before consumers",
    "R7.2": "Granular field validation: return field-level errors and preserve user form inputs",
    "R7.3": "Single canonical route per path: no duplicate route group paths",
    "R7.4": "Acyclic module layering: domain -> application -> adapters; no circular imports",
    "R7.5": "Universal contracts purity: contracts barrels must not import Node built-ins (node:fs, node:path)",
    # R8: Marketing & Attribution (3 rules)
    "R8.1": "Anti-cloaking & markdown parity: serve identical facts to crawlers and browsers",
    "R8.2": "Honest telemetry attribution: label synthetic or demo data and keep it apart from live metrics",
    "R8.3": "Consent Mode v2 ordering: initialize default denied consent before analytics tags",
    # R9: TypeScript / Type Safety (3 rules)
    "R9.1": "No untyped escape hatches: no `as any` casts or `z.any()` schemas in production source",
    "R9.2": "No silenced type errors: @ts-ignore is flagged; use @ts-expect-error and say why",
    "R9.3": "Package-name imports: import workspace packages by name, not via ../../packages/ paths",
    # R10: API & Network Robustness (9 rules)
    "R10.1": "Explicit HTTP request timeouts: Python requests calls pass timeout=",
    "R10.2": "Idempotency key transmission: mutating external POST/PUT requests send Idempotency-Key",
    "R10.3": "Rate-limit header handling: parse 429 Retry-After and respect backoff deadlines",
    "R10.4": "Structured error envelope: error responses adhere to standard JSON error schema",
    "R10.5": "Response size bounding: clamp response body reads to avoid unbounded buffer allocation",
    "R10.6": "Content-Type negotiation: validate incoming request Accept / Content-Type headers",
    "R10.7": "No unauthenticated admin endpoints: route middleware protects management routes",
    "R10.8": "Strict HTTP verb mapping: GET requests are read-only and free of side effects",
    # R10.9 (2026-11-13): R10.1 names Python requests only; this one is the fetch and axios half, as its own ID so the
    # stable rule keeps its meaning.
    "R10.9": "Timeouts on fetch and axios: a call with no options argument sets no timeout and no AbortSignal",
    # R11: State Management & Concurrency (8 rules)
    "R11.1": "Atomic read-modify-write: concurrent mutations use compare-and-swap or row locks",
    "R11.2": "No dangling unawaited promises: promise chains either return or await explicitly",
    "R11.3": "Race condition prevention: UI mutations abort pending conflicting requests via AbortController",
    "R11.4": "Deterministic reducer purity: state reducers remain pure functions without side effects",
    "R11.5": "Cache key stability: cache keys use sorted/normalized parameters to avoid fragmentation",
    "R11.6": "Optimistic UI rollback: optimistic state updates retain rollback snapshot on failure",
    "R11.7": "Bounded in-memory queues: memory queues specify max length and overflow policy",
    "R11.8": "Thread-safe singleton initialization: singletons use atomic locks or double-checked init",
    # R12: Memory & Resource Leak Prevention (8 rules)
    "R12.1": "Event listener teardown: addEventListener in useEffect/components pairs with removeEventListener",
    "R12.2": "Timer lifecycle cleanup: setTimeout and setInterval handle IDs cleaned on component unmount",
    "R12.3": "Stream disposal hygiene: open readable/writable streams explicitly call close() or pipe safely",
    "R12.4": "WebSocket cleanup: client socket connections explicitly disconnect on navigation/unmount",
    "R12.5": "Database connection pool hygiene: acquired pool clients release in finally blocks",
    "R12.6": "Worker thread termination: spawn worker instances call terminate() upon task completion",
    "R12.7": "Temp file deterministic deletion: scratch files in /tmp register unlink in finalizer",
    "R12.8": "No unbounded collection growth: caches and registries specify TTL or LRU eviction",
    # R13: Observability & Logging Hygiene (8 rules)
    "R13.1": "No hardcoded secrets: credentials stay out of source code and out of browser bundles",
    "R13.2": "Structured JSON logging: application logs emit valid JSON key-value pairs with timestamps",
    "R13.3": "Trace context propagation: distributed calls forward traceparent / correlation IDs",
    "R13.4": "Metric label cardinality bounding: avoid dynamic user IDs or timestamps in metric labels",
    "R13.5": "Standard log severity: use debug/info/warn/error appropriately without log spam",
    "R13.6": "Audit event journaling: security-critical actions append an immutable audit record",
    "R13.7": "Health check hermeticity: /healthz endpoints execute lightweight non-allocating probes",
    "R13.8": "Exception traceback containment: sanitize public error responses to avoid leaking paths",
    # R14: Auth & Session Hardening (8 rules)
    "R14.1": "Session token entropy: session identifiers contain at least 128 bits of cryptosecure random",
    "R14.2": "Password hash modern algorithms: use Argon2id or bcrypt with appropriate work factor",
    "R14.3": "Token expiration bounding: short-lived access tokens paired with rotating refresh tokens",
    "R14.4": "CSRF token validation: state-changing forms and endpoints validate anti-CSRF token",
    "R14.5": "No credentials in URLs: pass tokens, API keys and passwords outside query strings",
    "R14.6": "Strict redirect URL allowlist: OAuth / login return URLs validated against domain allowlist",
    "R14.7": "Brute-force throttle: login and reset-password routes rate-limited by IP and username",
    "R14.8": "Role-based access control: enforce authorization check at controller/handler boundary",
    # R15: Error Boundaries & Fallback Rendering (8 rules)
    "R15.1": "React error boundary protection: major route segments wrapped in error boundaries",
    "R15.2": "Non-blocking third-party failures: external widgets render graceful placeholder on failure",
    "R15.3": "Form submit recovery: failed form submission preserves user input for retry",
    "R15.4": "Image broken state fallback: img / Image tags provide alt and error state fallback",
    "R15.5": "Network offline banner: offline connectivity state indicated with non-blocking alert",
    "R15.6": "Empty state affordance: data tables and lists display actionable empty state message",
    "R15.7": "Skeleton loader transition: loading states use layout-preserving skeletons to avoid CLS",
    "R15.8": "Recoverable 404 navigation: not-found pages provide search and home navigation links",
    # R16: Supply Chain & Dependency Hygiene (8 rules)
    "R16.1": "Lockfile integrity enforcement: CI installs from frozen lockfiles (pnpm --frozen)",
    "R16.2": "No wildcard dependency versions: package.json does not use '*' or 'latest'",
    "R16.3": "Scoped internal packages: monorepo internal packages use scoped organization prefixes",
    "R16.4": "No install script execution: untrusted packages ignore postinstall lifecycle scripts",
    "R16.5": "Production bundle minimization: devDependencies not imported in production runtime code",
    "R16.6": "License compliance: no copyleft (GPL) dependencies imported into proprietary modules",
    "R16.7": "Single package manager: repo roots maintain exactly one package manager lockfile",
    "R16.8": "Deterministic engine constraints: package.json specifies minimum node/pnpm engines",
    # R17: Modern React & Next.js Patterns (8 rules)
    "R17.1": "Server Component default: components in app directory default to RSC unless 'use client'",
    "R17.2": "No server-only code in client: 'server-only' imports barred from client-marked components",
    "R17.3": "Metadata API compliance: route pages export static or generateMetadata for SEO",
    "R17.4": "Next/Image usage: production UI renders next/image for optimized srcset and layout stability",
    "R17.5": "Next/Font optimization: load Google or local fonts via next/font to avoid layout shift",
    "R17.6": "Client boundary minimization: push 'use client' directives down to leaf interactive nodes",
    "R17.7": "Server action security: server actions validate session authentication before mutating",
    "R17.8": "Route handler return typing: route handlers return typed NextResponse with status",
    # R18: Accessibility (WCAG 2.1 AA) (8 rules)
    "R18.1": "Image alternative text: img elements carry an alt attribute",
    "R18.2": "Form control labels: input, select and textarea elements have a label (not scanned); the scan checks "
             "target=\"_blank\" links for rel=\"noopener noreferrer\"",
    "R18.3": "Keyboard navigation accessibility: interactive elements focusable via Tab and Enter/Space",
    "R18.4": "Contrast ratio minimum: text satisfies 4.5:1 minimum contrast ratio against background",
    "R18.5": "No click handlers on non-interactive elements: a div with onClick and no role is flagged",
    "R18.6": "Aria attributes validity: aria-* attributes use valid names and permissible state values",
    "R18.7": "Visible focus rings: interactive elements retain visible focus outline for keyboard users",
    "R18.8": "Document language attribute: html root element specifies valid lang='en' attribute",
    # R19: Clean Code & Maintainability Guardrails (4 rules)
    "R19.1": "Function length limit: functions remain under 100 lines; extract complex branches",
    "R19.2": "Cyclomatic complexity bounding: functions maintain cyclomatic complexity <= 20",
    "R19.3": "No dead commented-out code: remove obsolete commented code blocks prior to PR merge",
    "R19.4": "Descriptive variable naming: avoid single-character variables outside trivial loops",
    # R20: Test custody (5 rules). `brevio-core check` printed these findings under R6.5 (a different rule) and, for the
    # empty test block, R6.1; they have IDs of their own since 2026-11-12 (improvement plan 3a).
    "R20.1": "Existing test files kept: a test file on the base is not deleted or renamed to a file that is no test",
    "R20.2": "Existing tests and assertions kept: a changed test file does not hold fewer tests or assertions than its base",
    "R20.3": "No test skip or focus marker added: fix the failing test instead of silencing it",
    "R20.4": "No tautological assertion added: assert real output or state",
    "R20.5": "No empty test block: a test whose body is empty, `pass` or `...` tests nothing",
}

# Every rule has a status (ROADMAP E33, 2026-11-05). A stable rule fails a scan. A preview rule reports but never fails;
# a new rule ships as preview for one minor release. A deprecated rule is retired: it keeps its ID, which is never
# reused, and reports nothing. ``rule_ids.json`` records the IDs and titles of the last release.
STABLE, PREVIEW, DEPRECATED = "stable", "preview", "deprecated"
STATUSES = (STABLE, PREVIEW, DEPRECATED)
RULE_STATUS: dict[str, str] = {         # a rule not named here is stable
    # 30% precise in the E35 precision run (docs/benchmarks/precision.md, 2026-11-05): most hits were deliberate waits
    # (a test checking that nothing happens in a given time) or test sources written as strings, and no small pattern
    # change tells those from a sleep that stands in for a condition. It reports, and never blocks, until narrowed.
    "R6.3": PREVIEW,
    # New IDs for the test-custody findings (2026-11-12, improvement plan 3a). Nothing has measured them yet.
    **dict.fromkeys(("R20.1", "R20.2", "R20.3", "R20.4", "R20.5"), PREVIEW),
    # New in 0.10.0 (2026-11-13): a new rule ships as preview first, and nothing has measured this one.
    "R10.9": PREVIEW,
}
# Why a rule is not stable, in one or two sentences; its rule page prints it after the status.
STATUS_REASONS: dict[str, str] = {
    "R6.3": "In a precision run on open-source repositories most of its hits were deliberate waits (a test checking "
            "that nothing happens in a given time) or test code written as a string, so it reports but never fails a "
            "scan until it can tell those from a sleep that stands in for a condition.",
    "R10.9": "New, and not measured on any code yet. It reads one line and flags only a call that has no options argument "
             "at all, which cannot carry a signal or a timeout. It reports but never fails a scan until a precision run "
             "judges it.",
    # Not a scan rule: `brevio-core scan` does not run these, so "never fails a scan" would say too little. What `check`
    # does with them is what it did before they had IDs (2026-11-12, improvement plan 3a).
    **dict.fromkeys(("R20.1", "R20.2", "R20.3", "R20.4", "R20.5"),
                    "New, and not measured on any code yet. A finding is a prompt for a reviewer, not proof of a "
                    "problem: a change can drop or rewrite a test for a good reason. `brevio-core scan` does not run "
                    "this rule, so no scan fails on it. `brevio-core check` reports it and exits 1 when it does."),
}
# A line for the Rules section of the next release notes about a rule, for a change the ID file cannot show: findings
# that moved to another ID before the first release. Emptied when the release that carries them is out: 0.10.0 is out
# (2026-11-13), so the table is empty until a change of the next release needs a line.
RULE_NOTES: dict[str, str] = {}


def rule_status(rule_id: str) -> str:
    """The status of a rule of ``PR_RULES_122``: ``stable``, ``preview`` or ``deprecated``."""
    return RULE_STATUS.get(rule_id, STABLE)


# Pre-compiled regex patterns for common rule violations on added lines
# R13.1 flags a known credential format anywhere on a code line, or a random-looking literal bound to a secret-named
# identifier. Any long quoted string after a key-like name was 0% precise on open-source code: public search keys and
# storage-entry names (E35 precision run, docs/benchmarks/precision.md, 2026-11-05).
_CREDENTIAL_RX = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])"                     # AWS access key ID
    r"|gh[pousr]_[A-Za-z0-9]{36}(?![A-Za-z0-9])"                     # GitHub token
    r"|github_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}(?![A-Za-z0-9])"    # GitHub fine-grained token
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"                                # Slack token
    r"|(?:sk|rk)_live_[A-Za-z0-9]{24,}"                              # Stripe secret or restricted live key
    r"|AIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])"                     # Google API key
    r"|-{5}BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-{5}"          # a private key block
    r")")
_SECRET_NAME = r"[\w$.-]*?(?:secret|token|passw(?:or)?d|pwd|api_?key|private_?key|access_?key|credential)[\w$-]*"
# ``name = '…'``, ``name: '…'`` and TypeScript's ``name: string = '…'``.
_SECRET_ASSIGN_RX = re.compile(
    r"(?<![\w$.-])(?P<name>" + _SECRET_NAME + r")['\"]?\s*(?::\s*[\w$.<>\[\]| ]{1,40}?\s*)?(?:=|:)\s*"
    r"(?P<q>['\"`])(?P<val>[^'\"`\s]{16,512})(?P=q)", re.I)
_JWT_RX = re.compile(r"^eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{10,}$")
# A value with one of these is a placeholder, not a key. Known-public kinds: a name that says public or publishable
# (NEXT_PUBLIC_*, publishableKey), Stripe's publishable pk_ keys, and Algolia search keys, which ship to the browser.
_PLACEHOLDER_RX = re.compile(r"x{4,}|\*{3,}|\.{3}|your[_-]|example|change_?me|placeholder|dummy|sample|fake|redacted"
                             r"|test|mock|<[^>]*>|\$\{|\{\{", re.I)
_PUBLIC_NAME_RX = re.compile(r"public|publishable", re.I)
_PUBLIC_CONTEXT_RX = re.compile(r"algolia|docsearch", re.I)
_SECRET_CONTEXT = 5     # lines before a hit read for a public key kind (an Algolia block names its key `apiKey`)
_CLIENT_BUNDLE_SECRET_ENV_RX = re.compile(
    r"\b(?:NEXT_PUBLIC|VITE|REACT_APP)_[A-Z0-9_]*(?:SECRET|PRIVATE_KEY|SERVICE_ROLE|DATABASE_URL|PASSWORD)[A-Z0-9_]*\b"
)
_DANGEROUS_HTML_RX = re.compile(r"dangerouslySetInnerHTML\s*=\s*\{\s*\{\s*__html\b")
# Sanitizers at the dangerouslySetInnerHTML sink: escapeHtml, DOMPurify.sanitize, sanitizeHtml, etc.
_SANITIZED_HTML_RX = re.compile(r"__html\s*:\s*(?:(?:DOMPurify|dompurify|sanitizeHtml|xss)\.sanitize|escapeHtml|sanitizeHtml|DOMPurify|sanitize|escape)\s*\(", re.I)
# Not after ``$``: Playwright's ``page.$eval`` selector API is not JavaScript eval (E35 precision run, 2026-11-05).
_EVAL_RX = re.compile(r"(?<![\w$])(?:eval|Function)\s*\(")
# In test files, eval with a static quoted string literal (no interpolation) is a test probe (E35 precision run).
_STATIC_EVAL_RX = re.compile(r"\beval\s*\(\s*(?:\"(?:[^\"\\$]|\\.)*\"|'(?:[^'\\$]|\\.)*')\s*\)")
_UNSAFE_SQL_RX = re.compile(
    r"\.execute\s*\(\s*f[\"']\s*(?:SELECT|INSERT|UPDATE|DELETE)\b"
    r"|\.\$(?:query|execute)RawUnsafe\s*\(\s*`[^`]*\$\{",
    re.I,
)
_BIDI_TAG_RX = re.compile(r"[\u202a-\u202e\u2066-\u2069\U000e0000-\U000e007f]")
_UNSAFE_SINK_RX = re.compile(
    r"|".join((_EVAL_RX.pattern, _DANGEROUS_HTML_RX.pattern, _UNSAFE_SQL_RX.pattern, _BIDI_TAG_RX.pattern)),
    re.I,
)
# Test fixture assets (e.g. tests/assets/): vendored builds kept for browser test suites run no user sinks.
_TEST_ASSET_RX = re.compile(r"(?:^|/)(?:tests?|spec)/assets/", re.I)
_NO_ALT_IMG_RX = re.compile(r"<img\b(?!.*?alt=)[^>]*>", re.I)
_CLICK_ON_DIV_RX = re.compile(r"<div\b[^>]*onClick\s*=(?!.*?role=)[^>]*>", re.I)
_TARGET_BLANK_A_RX = re.compile(r"""<a\b[^>]*\btarget\s*=\s*["']_blank["'][^>]*>""", re.I)
_REL_NOOPENER_RX = re.compile(r"""\brel\s*=\s*["'][^"']*\bnoopener\b""", re.I)
_UNBOUNDED_QUERY_RX = re.compile(r"\.(?:findMany|select)\s*\(\s*\{(?![^}]*take\s*:)(?![^}]*limit\s*:)", re.I)
_AS_ANY_RX = re.compile(r"\bas\s+any(?![\w$])|\bz\.any\s*\(")
_ANY_RX = _AS_ANY_RX
_TS_IGNORE_RX = re.compile(r"@ts-ignore\b")
_EMPTY_CATCH_RX = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}")
# ``except``, what it names (empty for a bare except) and the rest of the line, which holds a one-line handler's body.
_EXCEPT_RX = re.compile(r"^(?P<indent>\s*)\+?\s*except\b(?P<what>[^:#]*):(?P<tail>.*)$")
# A handler for ``Exception``, alone or in a tuple. ``BaseException`` is left out. ``except OSError: pass`` names what it
# expects and is a common, deliberate cleanup, so it is not flagged (the standard library holds hundreds of them).
_BROAD_EXCEPT_RX = re.compile(r"(?<![\w.])Exception\b")
_PY_STRING_OR_COMMENT_RX = re.compile(r"""(?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(?P<comment>#.*)""")
# eval or exec called by name: not ``model.eval()``, ``ast.literal_eval(`` or a ``def eval(`` of one's own.
_PY_EVAL_RX = re.compile(r"(?<!def )(?<![\w$.])(?:eval|exec)\s*\(")
_PY_LITERAL_ARG_RX = re.compile(r'\s*[rRbBuU]?""\s*[,)]')       # on a masked line: a plain string, nothing joined to it
_FETCH_CALL_RX = re.compile(r"(?<![\w$.])(?:fetch|axios\.(?P<verb>get|delete|head|options|post|put|patch))\s*\(")
_SIGNAL_OR_TIMEOUT_RX = re.compile(r"\b(?:signal|timeout)\b", re.I)
_SUBPROCESS_SHELL_RX = re.compile(
    r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\s*\([^)]*\bshell\s*=\s*True\b"
    r"|\bos\.system\s*\(\s*f[\"']"
)
_REQUESTS_CALL_RX = re.compile(r"\brequests\.(?:get|post|put|delete|patch|head)\s*\([^)]*\)")
_TIMEOUT_KW_RX = re.compile(r"\btimeout\s*=")
# A key whose whole value is ``*`` or ``latest``, one per line or several on a minified line.
_WILDCARD_DEP_RX = re.compile(r'"(?P<key>[^"]+)"\s*:\s*"(?:\*|latest)"\s*(?=[,}]|$)')
_WAIT_FOR_TIMEOUT_RX = re.compile(r"\bwaitForTimeout\s*\(\s*\d+")
# A path is a test path when a folder or the file name says so by a whole word: test, tests, spec, specs, e2e or testing
# at its start or end (tests/, __tests__/, a.test.ts, test_a.py, a_test.py, conftest.py). The substring "test" read
# latest.py, contest_utils.py and attestation/ as tests and skipped the checks there (2026-11-13).
_TEST_WORDS = frozenset({"test", "tests", "spec", "specs", "e2e", "testing", "unittest", "unittests", "pytest", "vitest",
                         "testdata", "testutil", "testutils", "testhelper", "testhelpers"})
_NAME_WORDS_RX = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")
# JavaScript and TypeScript sources, in both module systems.
_SCRIPT_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
# A line that is only a comment runs nothing: `<img>` or `as any` in prose is not code (E35 precision run, 2026-11-05).
_COMMENT_LINE_RX = re.compile(r"^\s*(?://|/\*|\*)")
# A div whose click handler only stops propagation is a wrapper, not a control; `role="button"` would be wrong (E35).
_STOP_ONLY_RX = re.compile(r"onClick\s*=\s*\{\s*\(?\s*(\w+)\s*\)?\s*=>\s*\1\.stopPropagation\(\)\s*\}")
# A TypeScript declaration file holds types only, no code that runs, so no code rule can be right there: its R2.8 hits
# were prose in doc comments (E35 precision run, 2026-11-06).
_DECLARATION_RX = re.compile(r"\.d\.[cm]?ts$", re.I)

_STRING_OR_COMMENT_RX = re.compile(
    r"""(?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|(?P<comment>//.*|/\*.*?\*/|#.*)"""
)


def _mask_strings_and_trailing_comments(line: str) -> str:
    """Mask string literals and strip trailing comments so regexes match only real code expressions."""
    def _repl(m: re.Match[str]) -> str:
        if m.group("str") is not None:
            return '""'
        return ""
    return _STRING_OR_COMMENT_RX.sub(_repl, line)


def _entropy(s: str) -> float:
    """Bits per character of ``s``."""
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values()) if n else 0.0


def _random_looking(value: str) -> bool:
    """A literal that reads as a generated key: one run of 12 or more characters between separators that mixes
    letters and digits at 3 bits per character or more. Words, slugs, paths, URLs and placeholders do not."""
    if _PLACEHOLDER_RX.search(value) or "://" in value or value.startswith(("/", "./", "#", "data:", "sha", "pk_")):
        return False
    return any(len(seg) >= 12 and re.search(r"\d", seg) and re.search(r"[A-Za-z]", seg) and _entropy(seg) >= 3.0
               for seg in re.split(r"[-_.:]", value))


def _hardcoded_secret(line: str, before: str) -> bool:
    """R13.1 on one code line; ``before`` is the text of the few lines above it, read for a public key kind."""
    return _hardcoded_literal(line, before) or bool(_CLIENT_BUNDLE_SECRET_ENV_RX.search(line))


def _hardcoded_literal(line: str, before: str) -> bool:
    """A credential format or a random-looking literal under a secret name on ``line``: a secret written into the code."""
    if _CREDENTIAL_RX.search(line):
        return True
    for m in _SECRET_ASSIGN_RX.finditer(line):
        name, val = m["name"], m["val"]
        if _PUBLIC_NAME_RX.search(name):
            continue
        if _JWT_RX.match(val):
            return True
        if _random_looking(val) and not _PUBLIC_CONTEXT_RX.search(name + "\n" + line + "\n" + before):
            return True
    return False


def _unsafe_eval(rel_path: str, line: str, in_test: bool) -> bool:
    """True when ``line`` runs eval or Function outside test fixture assets and static literal eval probes."""
    masked = _mask_strings_and_trailing_comments(line)
    if not _EVAL_RX.search(masked) or _TEST_ASSET_RX.search(rel_path):
        return False
    if in_test and _STATIC_EVAL_RX.search(line) and not _EVAL_RX.search(_STATIC_EVAL_RX.sub("", line)):
        return False
    return True


@functools.lru_cache(maxsize=4096)
def is_test_path(rel_path: str) -> bool:
    """``rel_path`` is a test file or lies in a test folder, judged by whole words (see ``_TEST_WORDS``)."""
    parts = rel_path.replace("\\", "/").split("/")
    stem = parts[-1].rpartition(".")[0] or parts[-1]
    if stem.lower() == "conftest":
        return True
    for part in (*parts[:-1], *stem.split(".")):        # a.test.d.ts says test in a middle segment of its name
        words = [w.lower() for w in _NAME_WORDS_RX.split(part) if w]
        if words and (words[0] in _TEST_WORDS or words[-1] in _TEST_WORDS):
            return True
    return False


def _python_unsafe_eval(rel_path: str, line: str) -> bool:
    """True when ``line`` calls eval or exec on something other than a plain string literal (a Python source line)."""
    masked = _PY_STRING_OR_COMMENT_RX.sub(lambda m: '""' if m.group("str") is not None else "", line)
    if _TEST_ASSET_RX.search(rel_path):
        return False
    for m in _PY_EVAL_RX.finditer(masked):
        rest = masked[m.end():]
        if not rest.lstrip().startswith(")") and not _PY_LITERAL_ARG_RX.match(rest):
            return True
    return False


def _call_args(masked: str, start: int) -> list[str] | None:
    """The top-level arguments of the call whose ``(`` is just before ``start``; ``None`` when the call is not closed on
    this line."""
    depth, args, cur = 0, [], ""
    for ch in masked[start:]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return [a.strip() for a in (*args, cur) if a.strip()] if ch == ")" else None
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(cur)
            cur = ""
            continue
        cur += ch
    return None


# ``function fetch(url) {`` or a method ``fetch(url) {`` defines a function; it does not call one.
_FETCH_DEFINITION_RX = re.compile(r"\bfunction\s*\*?\s*fetch\s*\(|^\s*(?:(?:async|static|public|private|protected)\s+)*fetch\s*\([^)]*\)\s*(?::[^;{]*)?\{")


def _fetch_without_options(line: str) -> bool:
    """A ``fetch(url)``, ``axios.get(url)`` or ``axios.post(url, data)`` closed on ``line``: no options argument, so it
    cannot carry a signal or a timeout. A line that names ``signal`` or ``timeout`` is left alone."""
    masked = _mask_strings_and_trailing_comments(line)
    if _SIGNAL_OR_TIMEOUT_RX.search(masked) or _FETCH_DEFINITION_RX.search(masked):
        return False
    for m in _FETCH_CALL_RX.finditer(masked):
        args = _call_args(masked, m.end())
        if args and not args[0].startswith("..."):
            if len(args) <= (2 if m["verb"] in ("post", "put", "patch") else 1):
                return True
    return False


def _statements(text: str) -> list[str]:
    """The ``;``-separated statements on a line, without its comment."""
    return [x.strip() for x in re.sub(r"\s*#.*$", "", text).split(";") if x.strip()]


def _top_level_statements(numbered: list[tuple[int, str]], i: int, indent: int, tail: str) -> list[str]:
    """The statements at the top level of the handler that starts on ``numbered[i]``: its one-line body, or the first
    indented block below it. Reading stops at a gap in the line numbers (a diff) and at the first line that is not
    indented deeper than the ``except``."""
    if _statements(tail):
        return _statements(tail)
    out: list[str] = []
    top, expected = 0, numbered[i][0]
    for lno, text in numbered[i + 1:]:
        if lno != expected + 1:
            break
        expected = lno
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        depth = len(text.expandtabs()) - len(text.expandtabs().lstrip())
        if depth <= indent:
            break
        top = top or depth
        if depth == top:
            out += _statements(text)
    return out


def handler_body(numbered: list[tuple[int, str]], lno: int) -> list[str] | None:
    """The top-level statements of the ``except`` handler that starts on line ``lno``, or ``None`` when that line is not
    an ``except``. The rule and the fixer both read a handler through this, so they cannot disagree."""
    for i, (n, line) in enumerate(numbered):
        if n == lno:
            m = _EXCEPT_RX.match(line)
            return _top_level_statements(numbered, i, len(m["indent"].expandtabs()), m["tail"]) if m else None
    return None


def only_passes(body: list[str]) -> bool:
    """Whether a handler body does nothing: it holds only ``pass`` or ``...``."""
    return bool(body) and all(x in ("pass", "...") for x in body)


def _handler_findings(rel_path: str, numbered: list[tuple[int, str]]) -> dict[int, str]:
    """R5.4 for the ``except`` handlers of a Python file: a bare ``except:`` that does not re-raise, and an
    ``except Exception`` handler that only passes. A bare ``raise`` at the top level of the handler means the error is
    not swallowed."""
    out: dict[int, str] = {}
    for i, (lno, line) in enumerate(numbered):
        m = _EXCEPT_RX.match(line)
        if not m:
            continue
        body = _top_level_statements(numbered, i, len(m["indent"].expandtabs()), m["tail"])
        if not m["what"].strip():
            if "raise" not in body:
                out[lno] = f"{rel_path}:{lno}: [R5.4] Bare `except:` detected; specify explicit exception tuple"
        elif only_passes(body) and _BROAD_EXCEPT_RX.search(m["what"]):
            out[lno] = (f"{rel_path}:{lno}: [R5.4] `except Exception` handler that only passes drops the error; log it, "
                        "narrow it or re-raise it")
    return out


def _check_ui_patterns(rel_path: str, lno: int, line: str, findings: list[str]) -> None:
    """The rules for a code line of a UI component (``.tsx``, ``.jsx``) outside the tests."""
    if _DANGEROUS_HTML_RX.search(line) and not _SANITIZED_HTML_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R2.8] Unsanitized `dangerouslySetInnerHTML` in UI component")
    if _NO_ALT_IMG_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R18.1] Missing `alt` attribute on `<img>` element")
    if _CLICK_ON_DIV_RX.search(line) and not _STOP_ONLY_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R18.5] `onClick` handler on non-interactive `<div>` without `role`")


def _check_python_patterns(rel_path: str, lno: int, line: str, in_test: bool, findings: list[str]) -> None:
    """The rules for a Python source line (``.py``); the ``except`` handlers are read in ``_handler_findings``."""
    if not in_test and not line.lstrip().startswith("#"):
        if _SUBPROCESS_SHELL_RX.search(line):
            findings.append(f"{rel_path}:{lno}: [R5.5] Unsafe `shell=True` subprocess or interpolated `os.system` call")
        if _REQUESTS_CALL_RX.search(line) and not _TIMEOUT_KW_RX.search(line):
            findings.append(f"{rel_path}:{lno}: [R10.1] Outbound `requests` call without explicit `timeout=`")
    if not line.lstrip().startswith("#") and _python_unsafe_eval(rel_path, line):
        findings.append(f"{rel_path}:{lno}: [R2.8] Unsafe `eval` or `exec` on a non-literal detected")


def _check_script_patterns(rel_path: str, lno: int, line: str, in_test: bool, findings: list[str]) -> None:
    """The rules for a JavaScript or TypeScript code line."""
    masked = _mask_strings_and_trailing_comments(line)
    if _ANY_RX.search(masked) and not in_test:
        findings.append(f"{rel_path}:{lno}: [R9.1] Untyped escape hatch `as any` or `z.any()` in production source")
    if _EMPTY_CATCH_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R5.4] Empty catch block detected; handle or log the error")
    if _unsafe_eval(rel_path, line, in_test):
        findings.append(f"{rel_path}:{lno}: [R2.8] Unsafe `eval` or dynamic `Function` constructor detected")
    if not in_test and _fetch_without_options(line):
        findings.append(f"{rel_path}:{lno}: [R10.9] `fetch` or `axios` call without options sets no timeout or "
                        "AbortSignal; pass `signal: AbortSignal.timeout(ms)` or `timeout`")


def _check_security_patterns(rel_path: str, lno: int, line: str, code: bool, ext: str, before: str, findings: list[str]) -> None:
    """The cross-language security and manifest checks outside test files."""
    if _hardcoded_literal(line, before):
        findings.append(f"{rel_path}:{lno}: [R13.1] Hardcoded secret pattern detected in added code")
    elif m := _CLIENT_BUNDLE_SECRET_ENV_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R13.1] Secret-named variable `{m.group(0)}` is sent to the browser; keep the "
                        "secret server-side and expose only a public value")
    if _BIDI_TAG_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R2.8] Hidden directional or Unicode tag control character detected")
    if code and ext in (".py", *_SCRIPT_EXTS) and _UNSAFE_SQL_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R2.8] Raw SQL string interpolation detected; use parameterized queries")
    if ext in (".tsx", ".jsx", ".html", ".vue", ".svelte") and code:
        if any(not _REL_NOOPENER_RX.search(m.group(0)) for m in _TARGET_BLANK_A_RX.finditer(line)):
            findings.append(f'{rel_path}:{lno}: [R18.2] External `target="_blank"` link without `rel="noopener noreferrer"`')


def _check_line_patterns(rel_path: str, lno: int, line: str, findings: list[str], before: str = "") -> None:
    """Helper to evaluate single-line AST and regex rules; ``before`` is the text of the few lines above ``line``."""
    ext = os.path.splitext(rel_path)[1].lower()
    in_test = is_test_path(rel_path)
    code = not _COMMENT_LINE_RX.match(line)     # the @ts-ignore rule still reads comments: the directive is one
    is_js = ext in _SCRIPT_EXTS

    if not in_test:
        _check_security_patterns(rel_path, lno, line, code, ext, before, findings)
    if ext == ".py":
        _check_python_patterns(rel_path, lno, line, in_test, findings)
    if os.path.basename(rel_path) == "package.json":
        if any(m["key"] not in ("version", "private") for m in _WILDCARD_DEP_RX.finditer(line)):
            findings.append(f"{rel_path}:{lno}: [R16.2] Wildcard or `latest` dependency version in `package.json`")
    if is_js and _TS_IGNORE_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R9.2] `@ts-ignore` used; use `@ts-expect-error` with explanatory reason")
    if is_js and code:
        _check_script_patterns(rel_path, lno, line, in_test, findings)
    if ext in (".tsx", ".jsx") and code and not in_test:
        _check_ui_patterns(rel_path, lno, line, findings)
    if in_test and _WAIT_FOR_TIMEOUT_RX.search(line):
        findings.append(f"{rel_path}:{lno}: [R6.3] `waitForTimeout` sleep in test; use deterministic assertions")


def scan_added_lines_for_rules(
    added_lines_by_file: dict[str, list[str]],
) -> list[str]:
    """Scan added lines across changed files against portable zero-token code rules."""
    findings: list[str] = []
    for rel_path, lines in added_lines_by_file.items():
        findings += scan_lines(rel_path, list(enumerate(lines, 1)))
    return findings


def declaration(rel_path: str) -> bool:
    """``rel_path`` names a TypeScript declaration file: types only, no code that runs."""
    return bool(_DECLARATION_RX.search(rel_path))


_UNCLOSED_CONSTRUCTS = (
    (re.compile(r"<img\b", re.I), ">"),
    (re.compile(r"<div\b", re.I), ">"),
    (re.compile(r"dangerouslySetInnerHTML\s*="), "}}"),
    (re.compile(r"\.(?:execute|\$queryRawUnsafe|\$executeRawUnsafe)\s*\(", re.I), ")"),
    (re.compile(r"\brequests\.(?:get|post|put|delete|patch|head)\s*\(", re.I), ")"),
)
_FINDING_KEY_RX = re.compile(r":(\d+):\s*\[(R\d+\.\d+)\]")


def _dedup_findings(findings: list[str]) -> list[str]:
    seen: set[tuple[int, str] | str] = set()
    deduped: list[str] = []
    for f in findings:
        m = _FINDING_KEY_RX.search(f)
        if m:
            key: tuple[int, str] | str = (int(m.group(1)), m.group(2))
        else:
            key = f
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped


def scan_lines(rel_path: str, numbered: list[tuple[int, str]]) -> list[str]:
    """Scan ``(line_no, text)`` pairs of one file, keeping the real line numbers (a diff's added lines).

    A TypeScript declaration file (``.d.ts``, ``.d.mts``, ``.d.cts``) gives no finding, so every scan mode skips it."""
    raw_findings: list[str] = []
    if declaration(rel_path):
        return raw_findings

    ext = os.path.splitext(rel_path)[1].lower()
    in_test = is_test_path(rel_path)
    is_js = ext in _SCRIPT_EXTS

    # R17.2: Bar 'server-only' imports from client components ('use client')
    if is_js and not in_test:
        has_use_client = any(re.search(r"['\"]use client['\"]", text) for _, text in numbered)
        if has_use_client:
            for lno, text in numbered:
                if re.search(r"['\"]server-only['\"]", text):
                    raw_findings.append(f"{rel_path}:{lno}: [R17.2] {PR_RULES_122['R17.2']}")

    handlers = _handler_findings(rel_path, numbered) if ext == ".py" else {}

    for i, (lno, line) in enumerate(numbered):
        if lno in handlers:
            raw_findings.append(handlers[lno])
        before = "\n".join(t for n, t in numbered[max(0, i - _SECRET_CONTEXT):i] if n >= lno - _SECRET_CONTEXT)
        _check_line_patterns(rel_path, lno, line, raw_findings, before)

        for pattern, closer in _UNCLOSED_CONSTRUCTS:
            if pattern.search(line) and closer not in line:
                combined_parts = [line]
                curr_lno = lno
                for j in range(i + 1, min(i + 11, len(numbered))):
                    next_lno, next_line = numbered[j]
                    if next_lno != curr_lno + 1:
                        break
                    combined_parts.append(next_line)
                    curr_lno = next_lno
                    if closer in next_line:
                        break
                combined_line = " ".join(combined_parts)
                _check_line_patterns(rel_path, lno, combined_line, raw_findings, before)
                break

    return _dedup_findings(raw_findings)
