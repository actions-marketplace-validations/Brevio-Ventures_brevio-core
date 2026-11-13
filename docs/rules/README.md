# Rules

One page per rule: what it catches, why, a bad and a good example, how it is checked and how to silence it. There are 129 rules. `brevio-core scan` checks 14 of them and `brevio-core scan --surface` 9 more; 5 more are checked by `brevio-core check`; 23 more come with a one-line reminder from `brevio-core brief`; the rest are standing rules for authors and reviewers. Generated from the rule definitions; do not edit by hand.


## R1: UI and mobile performance

- [R1.1](R1.1.md) Mobile GPU budget (brevio-core scan --surface)
- [R1.2](R1.2.md) SSR visibility resilience (brevio-core scan --surface)
- [R1.3](R1.3.md) Third-party script deferral (brevio-core scan --surface)
- [R1.4](R1.4.md) Flash-free theme bootstrap
- [R1.5](R1.5.md) No dead controls
- [R1.6](R1.6.md) Accessible switch geometry
- [R1.7](R1.7.md) LCP asset priority
- [R1.8](R1.8.md) Async polling hygiene

## R2: Security and tenancy

- [R2.1](R2.1.md) Tenant context derivation
- [R2.2](R2.2.md) Tenant-scoped data access
- [R2.3](R2.3.md) Dual-key entity resolution
- [R2.4](R2.4.md) Raw-body webhook verification (brevio-core scan --surface)
- [R2.5](R2.5.md) Outbound SSRF & timeout guard
- [R2.6](R2.6.md) Constant-time secret comparison
- [R2.7](R2.7.md) Secure cookie & CORS hygiene
- [R2.8](R2.8.md) No unsafe code or HTML sinks (brevio-core scan)

## R3: Billing and money

- [R3.1](R3.1.md) Idempotent webhook processing
- [R3.2](R3.2.md) Monetary integer representation (brevio-core scan --surface)
- [R3.3](R3.3.md) Gateway ID discrimination
- [R3.4](R3.4.md) Pre-execution quota enforcement
- [R3.5](R3.5.md) Transactional outbox

## R4: Database and migrations

- [R4.1](R4.1.md) Expand-contract migrations
- [R4.2](R4.2.md) Foreign-key & tenant composite indexing
- [R4.3](R4.3.md) N+1 query prevention
- [R4.4](R4.4.md) Ingestion FK pre-resolution
- [R4.5](R4.5.md) Bounded pagination

## R5: Reliability and fallbacks

- [R5.1](R5.1.md) Deterministic graceful fallbacks
- [R5.2](R5.2.md) HTTP empty-body hygiene
- [R5.3](R5.3.md) Bounded retry with jitter
- [R5.4](R5.4.md) No silent exception swallowing (brevio-core scan)
- [R5.5](R5.5.md) Process safety (brevio-core scan)

## R6: Testing and hermeticity

- [R6.1](R6.1.md) Real production import gate (brevio-core scan --surface)
- [R6.2](R6.2.md) Mutation side-effect assertions (brevio-core scan --surface)
- [R6.3](R6.3.md) No fixed-length waitForTimeout in tests (brevio-core scan) (preview)
- [R6.4](R6.4.md) Hermetic test state isolation
- [R6.5](R6.5.md) Post-incident regression crystallization

## R7: Architecture and contracts

- [R7.1](R7.1.md) Contract-first monorepo changes
- [R7.2](R7.2.md) Granular field validation
- [R7.3](R7.3.md) Single canonical route per path
- [R7.4](R7.4.md) Acyclic module layering
- [R7.5](R7.5.md) Universal contracts purity (brevio-core scan --surface)

## R8: Marketing and attribution

- [R8.1](R8.1.md) Anti-cloaking & markdown parity
- [R8.2](R8.2.md) Honest telemetry attribution
- [R8.3](R8.3.md) Consent Mode v2 ordering

## R9: Type safety

- [R9.1](R9.1.md) No untyped escape hatches (brevio-core scan)
- [R9.2](R9.2.md) No silenced type errors (brevio-core scan)
- [R9.3](R9.3.md) Package-name imports (brevio-core scan --surface)

## R10: API and network robustness

- [R10.1](R10.1.md) Explicit HTTP request timeouts (brevio-core scan)
- [R10.2](R10.2.md) Idempotency key transmission
- [R10.3](R10.3.md) Rate-limit header handling
- [R10.4](R10.4.md) Structured error envelope
- [R10.5](R10.5.md) Response size bounding
- [R10.6](R10.6.md) Content-Type negotiation
- [R10.7](R10.7.md) No unauthenticated admin endpoints
- [R10.8](R10.8.md) Strict HTTP verb mapping
- [R10.9](R10.9.md) Timeouts on fetch and axios (brevio-core scan) (preview)

## R11: State and concurrency

- [R11.1](R11.1.md) Atomic read-modify-write
- [R11.2](R11.2.md) No dangling unawaited promises
- [R11.3](R11.3.md) Race condition prevention
- [R11.4](R11.4.md) Deterministic reducer purity
- [R11.5](R11.5.md) Cache key stability
- [R11.6](R11.6.md) Optimistic UI rollback
- [R11.7](R11.7.md) Bounded in-memory queues
- [R11.8](R11.8.md) Thread-safe singleton initialization

## R12: Memory and resource leaks

- [R12.1](R12.1.md) Event listener teardown
- [R12.2](R12.2.md) Timer lifecycle cleanup
- [R12.3](R12.3.md) Stream disposal hygiene
- [R12.4](R12.4.md) WebSocket cleanup
- [R12.5](R12.5.md) Database connection pool hygiene
- [R12.6](R12.6.md) Worker thread termination
- [R12.7](R12.7.md) Temp file deterministic deletion
- [R12.8](R12.8.md) No unbounded collection growth

## R13: Observability and logging

- [R13.1](R13.1.md) No hardcoded secrets (brevio-core scan)
- [R13.2](R13.2.md) Structured JSON logging
- [R13.3](R13.3.md) Trace context propagation
- [R13.4](R13.4.md) Metric label cardinality bounding
- [R13.5](R13.5.md) Standard log severity
- [R13.6](R13.6.md) Audit event journaling
- [R13.7](R13.7.md) Health check hermeticity
- [R13.8](R13.8.md) Exception traceback containment

## R14: Auth and sessions

- [R14.1](R14.1.md) Session token entropy
- [R14.2](R14.2.md) Password hash modern algorithms
- [R14.3](R14.3.md) Token expiration bounding
- [R14.4](R14.4.md) CSRF token validation
- [R14.5](R14.5.md) No credentials in URLs
- [R14.6](R14.6.md) Strict redirect URL allowlist
- [R14.7](R14.7.md) Brute-force throttle
- [R14.8](R14.8.md) Role-based access control

## R15: Error boundaries and fallback rendering

- [R15.1](R15.1.md) React error boundary protection
- [R15.2](R15.2.md) Non-blocking third-party failures
- [R15.3](R15.3.md) Form submit recovery
- [R15.4](R15.4.md) Image broken state fallback
- [R15.5](R15.5.md) Network offline banner
- [R15.6](R15.6.md) Empty state affordance
- [R15.7](R15.7.md) Skeleton loader transition
- [R15.8](R15.8.md) Recoverable 404 navigation

## R16: Supply chain

- [R16.1](R16.1.md) Lockfile integrity enforcement
- [R16.2](R16.2.md) No wildcard dependency versions (brevio-core scan)
- [R16.3](R16.3.md) Scoped internal packages
- [R16.4](R16.4.md) No install script execution
- [R16.5](R16.5.md) Production bundle minimization
- [R16.6](R16.6.md) License compliance
- [R16.7](R16.7.md) Single package manager
- [R16.8](R16.8.md) Deterministic engine constraints

## R17: React and Next.js patterns

- [R17.1](R17.1.md) Server Component default
- [R17.2](R17.2.md) No server-only code in client (brevio-core scan)
- [R17.3](R17.3.md) Metadata API compliance
- [R17.4](R17.4.md) Next/Image usage
- [R17.5](R17.5.md) Next/Font optimization
- [R17.6](R17.6.md) Client boundary minimization
- [R17.7](R17.7.md) Server action security
- [R17.8](R17.8.md) Route handler return typing

## R18: Accessibility (WCAG 2.1 AA)

- [R18.1](R18.1.md) Image alternative text (brevio-core scan)
- [R18.2](R18.2.md) Form control labels (brevio-core scan)
- [R18.3](R18.3.md) Keyboard navigation accessibility
- [R18.4](R18.4.md) Contrast ratio minimum
- [R18.5](R18.5.md) No click handlers on non-interactive elements (brevio-core scan)
- [R18.6](R18.6.md) Aria attributes validity
- [R18.7](R18.7.md) Visible focus rings
- [R18.8](R18.8.md) Document language attribute

## R19: Clean code

- [R19.1](R19.1.md) Function length limit
- [R19.2](R19.2.md) Cyclomatic complexity bounding
- [R19.3](R19.3.md) No dead commented-out code
- [R19.4](R19.4.md) Descriptive variable naming

## R20: Test custody

- [R20.1](R20.1.md) Existing test files kept (brevio-core check) (preview)
- [R20.2](R20.2.md) Existing tests and assertions kept (brevio-core check) (preview)
- [R20.3](R20.3.md) No test skip or focus marker added (brevio-core check) (preview)
- [R20.4](R20.4.md) No tautological assertion added (brevio-core check) (preview)
- [R20.5](R20.5.md) No empty test block (brevio-core check) (preview)
