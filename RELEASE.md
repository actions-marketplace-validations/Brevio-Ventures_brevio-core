# brevio-core 0.10.0

Exported from harness commit `68b66f7f838335314f5cf01bf044b6dd35698330` of 2026-11-13. Profile: full.

This tree was produced by the harness exporter and checked for leaks before it was written. To reproduce it, check out that commit and run the exporter.

## Rules

Added:

- R10.9 Timeouts on fetch and axios: a call with no options argument sets no timeout and no AbortSignal (preview)
- R20.1 Existing test files kept: a test file on the base is not deleted or renamed to a file that is no test (preview)
- R20.2 Existing tests and assertions kept: a changed test file does not hold fewer tests or assertions than its base (preview)
- R20.3 No test skip or focus marker added: fix the failing test instead of silencing it (preview)
- R20.4 No tautological assertion added: assert real output or state (preview)
- R20.5 No empty test block: a test whose body is empty, `pass` or `...` tests nothing (preview)

Reworded (the ID and the status are unchanged; a note below says where the findings changed):

- R1.2 SSR visibility resilience: leave no [data-reveal] at opacity:0 without a CSS animation fallback
- R1.4 Flash-free theme bootstrap: paired semantic tokens across light and dark modes
- R1.5 No dead controls: an interactive element wires a handler and loading and error feedback
- R2.1 Tenant context derivation: derive tenant context from the verified session, not the client payload
- R2.2 Tenant-scoped data access: a DB read or write filters by the authenticated workspace scope
- R2.8 No unsafe code or HTML sinks: eval or exec on a non-literal, new Function, unsanitised dangerouslySetInnerHTML, interpolated SQL strings and hidden control characters
- R4.5 Bounded pagination: enforce a hard LIMIT (<=100) on list queries
- R5.1 Deterministic graceful fallbacks: return an explicit degraded status, not fabricated metrics
- R5.4 No silent exception swallowing: log or surface errors; empty catch blocks, bare except and except Exception: pass are flagged
- R5.5 Process safety: no subprocess call with shell=True and no os.system call built from an f-string
- R6.3 No fixed-length waitForTimeout in tests: wait for a condition instead
- R6.5 Post-incident regression crystallization: a bug fix comes with a failing-first test
- R8.2 Honest telemetry attribution: label synthetic or demo data and keep it apart from live metrics
- R9.2 No silenced type errors: @ts-ignore is flagged; use @ts-expect-error and say why
- R9.3 Package-name imports: import workspace packages by name, not via ../../packages/ paths
- R10.1 Explicit HTTP request timeouts: Python requests calls pass timeout=
- R10.8 Strict HTTP verb mapping: GET requests are read-only and free of side effects
- R13.1 No hardcoded secrets: credentials stay out of source code and out of browser bundles
- R14.5 No credentials in URLs: pass tokens, API keys and passwords outside query strings
- R16.1 Lockfile integrity enforcement: CI installs from frozen lockfiles (pnpm --frozen)
- R16.2 No wildcard dependency versions: package.json does not use '*' or 'latest'
- R17.5 Next/Font optimization: load Google or local fonts via next/font to avoid layout shift
- R18.1 Image alternative text: img elements carry an alt attribute
- R18.2 Form control labels: input, select and textarea elements have a label (not scanned); the scan checks target="_blank" links for rel="noopener noreferrer"
- R18.5 No click handlers on non-interactive elements: a div with onClick and no role is flagged

Notes:

- R10.1 Explicit HTTP request timeouts: Python requests calls pass timeout=: The fetch and axios check is R10.9, a new preview rule; R10.1 stays the Python requests rule.
- R10.9 Timeouts on fetch and axios: a call with no options argument sets no timeout and no AbortSignal: New, preview: a fetch or axios call with no options argument. R10.1 stays the Python requests rule.
- R13.1 No hardcoded secrets: credentials stay out of source code and out of browser bundles: A secret-named NEXT_PUBLIC_, VITE_ or REACT_APP_ variable now gets its own message; the ID and the finding are the same.
- R16.2 No wildcard dependency versions: package.json does not use '*' or 'latest': A folder scan and a diff scan now read package.json, and a minified package.json is read too.
- R18.2 Form control labels: input, select and textarea elements have a label (not scanned); the scan checks target="_blank" links for rel="noopener noreferrer": The scan checks target="_blank" links without rel="noopener noreferrer", as 0.9.1 did; the form-label rule in the title has no scanner.
- R2.8 No unsafe code or HTML sinks: eval or exec on a non-literal, new Function, unsanitised dangerouslySetInnerHTML, interpolated SQL strings and hidden control characters: New ID for the eval, new Function and dangerouslySetInnerHTML findings, which builds before 0.6.0 reported as R2.5 (the SSRF and timeout guard, which brevio-core scan does not check); name R2.8 in an ignore comment or brevio-core.toml for them. Since 0.10.0 it also flags eval and exec on a non-literal in Python, and eval in .mjs and .cjs files.
- R20.1 Existing test files kept: a test file on the base is not deleted or renamed to a file that is no test: New ID for the findings about a test file that was deleted or renamed to a file that is no test. Builds up to 0.9.1 printed them as R6.5, which names the regression-test rule.
- R20.2 Existing tests and assertions kept: a changed test file does not hold fewer tests or assertions than its base: New ID for the findings about a test file with fewer tests or assertions than its base. Builds up to 0.9.1 printed them as R6.5.
- R20.3 No test skip or focus marker added: fix the failing test instead of silencing it: New ID for the findings about an added skip or focus marker. Builds up to 0.9.1 printed them as R6.5.
- R20.4 No tautological assertion added: assert real output or state: New ID for the findings about an added assertion that is always true. Builds up to 0.9.1 printed them as R6.5.
- R20.5 No empty test block: a test whose body is empty, `pass` or `...` tests nothing: New ID for the findings about an empty test block. Builds up to 0.9.1 printed them as R6.1, which names the rule about tests that import no code under test.
- R5.4 No silent exception swallowing: log or surface errors; empty catch blocks, bare except and except Exception: pass are flagged: Since 0.10.0 a bare except that re-raises is not flagged, and an except Exception handler that only passes is.
