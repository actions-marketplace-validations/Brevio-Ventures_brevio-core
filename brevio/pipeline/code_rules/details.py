"""The fields of each rule that the one-line catalogue does not hold: why it exists, a bad and a good example.

``PR_RULES_122`` (in ``__init__``) names what a rule catches. This module adds the rest, so
``docs.py`` can write one page per rule (ROADMAP 8.2 item 11, 2026-11-03). Standard library only: the
exported package carries it with the rules.

``SCANS`` names the rules a scanner really checks, the scanner and the path its example needs. A test
runs each bad example through that scanner and expects a finding for the rule, and each good one and
expects none, so an example on a scanned rule cannot drift from the code.
"""
from __future__ import annotations

from typing import NamedTuple


class Detail(NamedTuple):
    why: str
    bad: str
    good: str
    lang: str = "ts"
    exempt: str = ""        # a real exemption in the scanner, in plain words; empty when there is none


def _d(why: str, bad: str, good: str, lang: str = "ts", exempt: str = "") -> Detail:
    return Detail(why, bad, good, lang, exempt)


# rule id -> (scanner, example path). "dev" is brevio.pipeline.dev_rules (``scan --surface``), "code" is code_rules.scan_added_lines_for_rules.
SCANS: dict[str, tuple[str, str]] = {
    "R1.1": ("dev", "src/Hero.tsx"),
    "R1.2": ("dev", "src/reveal.css"),
    "R1.3": ("dev", "src/Layout.tsx"),
    "R2.4": ("dev", "src/webhook/route.ts"),
    "R3.2": ("dev", "src/money.ts"),
    "R5.4": ("code", "src/load.ts"),
    "R5.5": ("code", "src/runner.py"),
    "R6.1": ("dev", "src/a.test.ts"),
    "R6.2": ("dev", "src/a.test.ts"),
    "R6.3": ("code", "src/a.test.ts"),
    "R7.5": ("dev", "packages/contracts/index.ts"),
    "R9.1": ("code", "src/api.ts"),
    "R9.2": ("code", "src/api.ts"),
    "R9.3": ("dev", "src/api.ts"),
    "R10.1": ("code", "src/client.py"),
    "R10.9": ("code", "src/client.ts"),
    "R13.1": ("code", "src/client.ts"),
    "R16.2": ("code", "package.json"),
    "R17.2": ("code", "src/Component.tsx"),
    "R2.8": ("code", "src/run.ts"),
    "R18.1": ("code", "src/Logo.tsx"),
    "R18.2": ("code", "src/Link.tsx"),
    "R18.5": ("code", "src/Menu.tsx"),
}



# What a scanned rule reads, by language, in plain words: a rule page prints it so the reader sees where the rule applies
# and where it does not (2026-11-13). A rule not named here has one language, which its examples show.
SCOPE: dict[str, str] = {
    "R2.8": "JavaScript and TypeScript (.ts, .tsx, .js, .jsx, .mjs, .cjs): eval, new Function and, in .tsx "
            "and .jsx, dangerouslySetInnerHTML. Python: eval and exec on a non-literal. Python and JavaScript and "
            "TypeScript: SQL built by string interpolation. Any code file: hidden control characters.",
    "R5.4": "JavaScript and TypeScript: an empty catch block. Python: a bare except that does not re-raise, and an except "
            "handler for Exception whose body is only pass or `...`. A handler that names a narrower error, such as OSError, "
            "is not flagged.",
    "R5.5": "Python only.",
    "R6.3": "A test file in any language: waitForTimeout with a number. A sleep call is not checked.",
    "R9.2": "JavaScript and TypeScript: @ts-ignore. The reason after @ts-expect-error is not checked.",
    "R10.1": "Python only: calls of requests.get, post, put, delete, patch and head. Not httpx, not fetch and not axios; "
             "R10.9 covers fetch and axios.",
    "R10.9": "JavaScript and TypeScript, outside test files: fetch and axios calls. Python is not read.",
    "R13.1": "Code files outside tests. Where a secret is logged is not checked.",
    "R16.2": "package.json, with one key per line or several on a minified line.",
}


# A line a rule page prints under "How it is checked" for findings that now carry another ID (2026-11-13, D34).
POINTERS: dict[str, str] = {
    "R6.1": "Test-custody findings now print as R20.x. An empty test body, printed as R6.1 up to 0.9.1, is R20.5; see "
            "[R20.5](R20.5.md).",
    "R6.5": "Test-custody findings now print as R20.x. The findings about a deleted, renamed, weakened or skipped test, "
            "printed as R6.5 up to 0.9.1, are R20.1 to R20.4; see [R20.1](R20.1.md).",
}


def scanned_by(engine: str) -> list[str]:
    """The rule IDs the scanner ``engine`` checks, sorted: ``code`` is what ``brevio-core scan`` runs, ``dev`` what
    ``brevio-core scan --surface`` adds."""
    return sorted(rid for rid, (e, _path) in SCANS.items() if e == engine)


DETAILS: dict[str, Detail] = {
    # R1: UI and mobile performance
    "R1.1": _d("A large blur is painted on the GPU for every frame. Phones drop frames and heat up.",
               '<div className="blur-[120px]" />', '<div className="blur-none md:blur-[120px]" />', "tsx",
               "A line that already scopes the blur with a sm:, md:, lg:, xl: or 2xl: prefix is not flagged."),
    "R1.2": _d("If scripts load late, an element that starts at opacity 0 stays invisible for the visitor.",
               "[data-reveal] { opacity: 0; }",
               "[data-reveal] { opacity: 0; animation: show 0s 1s forwards; }\n@keyframes show { to { opacity: 1; } }",
               "css", "A file that holds a @keyframes block is not flagged."),
    "R1.3": _d("A synchronous analytics tag blocks hydration, so the page feels slow on a phone.",
               '<script async src="//www.googletagmanager.com/gtag/js?id=G-1"></script>',
               '<Script strategy="lazyOnload" src="//www.googletagmanager.com/gtag/js?id=G-1" />', "tsx"),
    "R1.4": _d("Without paired tokens the page flashes the wrong theme before the script runs.",
               ":root { --bg: #fff; }  /* no dark value */",
               ":root { --bg: #fff; }\n:root[data-theme=dark] { --bg: #111; }", "css"),
    "R1.5": _d("A button that does nothing, or hides a failure, teaches people the product is broken.",
               "<button>Save</button>",
               "<button onClick={save} disabled={saving}>{saving ? 'Saving' : 'Save'}</button>", "tsx"),
    "R1.6": _d("A toggle track without fixed geometry squashes in a flex row and the knob leaves the track.",
               '<span className="w-10 h-5 rounded-full" />',
               '<span className="inline-flex items-center shrink-0 w-10 h-5 rounded-full" />', "tsx"),
    "R1.7": _d("The browser cannot know the hero image is the largest paint unless you say so.",
               '<img src="/hero.jpg" />', '<Image src="/hero.jpg" priority alt="" width={1200} height={600} />', "tsx"),
    "R1.8": _d("A poll that is never stopped keeps calling the server after the user has left.",
               "useEffect(() => { setInterval(poll, 1000); }, []);",
               "useEffect(() => { const t = setInterval(poll, 1000); return () => clearInterval(t); }, []);"),
    # R2: security and tenancy
    "R2.1": _d("A tenant id in the request body lets one customer read another customer's data.",
               "const tenantId = req.body.tenantId;", "const tenantId = session.tenantId;"),
    "R2.2": _d("One query without the tenant filter leaks every tenant's rows.",
               "db.select().from(orders).where(eq(orders.id, id))",
               "db.select().from(orders).where(and(eq(orders.id, id), eq(orders.tenantId, session.tenantId)))",
               "ts", "Only API code under apps/api/ is checked, and only a filter the change removes."),
    "R2.3": _d("An id that is a UUID in one place and a slug in another is looked up in the wrong column.",
               "findOne({ id: param })",
               "const where = isUuid(param) ? { id: param } : { slug: param };\nfindOne(where)"),
    "R2.4": _d("Parsing the JSON first changes the bytes, so the signature no longer matches and a forged call can pass.",
               "const body = await req.json();\nverifySignature(body, sig);",
               "const raw = await req.text();\nverifySignature(raw, sig);\nconst body = JSON.parse(raw);"),
    "R2.5": _d("A server that fetches any URL can be pointed at your internal network, and a call with no limit can hang.",
               "const res = await fetch(req.query.url);",
               "const url = new URL(input);\nif (!ALLOWED.has(url.host)) throw new Error('host');\nawait fetch(url, { signal: AbortSignal.timeout(5000) });"),
    "R2.6": _d("A normal string compare stops at the first wrong byte, so timing leaks the secret.",
               "if (token === expected) ok();", "if (timingSafeEqual(Buffer.from(token), Buffer.from(expected))) ok();"),
    "R2.7": _d("A cookie without these flags can be read by scripts or sent from another site.",
               "res.cookie('sid', id);", "res.cookie('sid', id, { httpOnly: true, secure: true, sameSite: 'lax' });"),
    "R2.8": _d("Code or HTML built from a string runs whatever the string holds; with any outside input in it, that is an "
               "injection.",
               "eval(userInput);", "const value = JSON.parse(userInput);", "ts",
               "Comment lines and Playwright's $eval and $$eval are not flagged; dangerouslySetInnerHTML is checked "
               "only in .tsx and .jsx files outside tests. In Python, `model.eval()`, `ast.literal_eval` and eval or "
               "exec of a plain string literal are not flagged."),
    # R3: billing
    "R3.1": _d("Gateways send an event more than once. Without a check the customer is charged or upgraded twice.",
               "await applyEvent(event);",
               "if (await seen(event.id)) return;\nawait applyEvent(event);\nawait markSeen(event.id);"),
    "R3.2": _d("A float cannot hold 0.1 exactly, so sums of money drift by a cent.",
               "const price = amountCents / 100;", "const priceCents = amountCents;  // format when you show it", "ts",
               "A file named format* or display*, a component, a function named format*/display* and a line that "
               "calls Intl.NumberFormat, toLocaleString or toFixed are not flagged."),
    "R3.3": _d("The wrong kind of id sent to the gateway fails late, or charges the wrong object.",
               "await gateway.cancel(id);", "if (!id.startsWith('sub_')) throw new Error('not a subscription');\nawait gateway.cancel(id);"),
    "R3.4": _d("A paid call made before the entitlement check costs money for a customer who may not use it.",
               "const out = await model.run(prompt);", "assertEntitled(tenant, 'ai');\nconst out = await model.run(prompt);"),
    "R3.5": _d("If the process dies between the queue and the database, the outside world and your data disagree.",
               "await queue.send(msg);\nawait db.insert(row);", "await db.insert(row);\nawait queue.send(msg);"),
    # R4: database
    "R4.1": _d("A one-step drop breaks the old code that is still running during the deploy.",
               "ALTER TABLE users DROP COLUMN name;",
               "ALTER TABLE users ADD COLUMN full_name text NULL;  -- drop 'name' in a later release", "sql"),
    "R4.2": _d("A foreign key without an index makes every join and every tenant list a full scan.",
               "ALTER TABLE a ADD COLUMN tenant_id uuid REFERENCES t(id);",
               "ALTER TABLE a ADD COLUMN tenant_id uuid REFERENCES t(id);\nCREATE INDEX a_tenant_created ON a (tenant_id, created_at);", "sql"),
    "R4.3": _d("One query per row turns a page of 100 rows into 101 round trips.",
               "for (const u of users) await db.orders.where({ userId: u.id });",
               "await db.orders.where({ userId: { in: users.map((u) => u.id) } });"),
    "R4.4": _d("A child row inserted before its parent exists fails, or points at nothing.",
               "await db.insert(event);", "await upsertWorkspace(event.workspaceId);\nawait db.insert(event);"),
    "R4.5": _d("A list query with no limit returns the whole table as it grows.",
               "db.select().from(orders)", "db.select().from(orders).limit(100)"),
    # R5: reliability
    "R5.1": _d("A made-up number looks real on a dashboard and drives wrong decisions.",
               "return { revenue: lastKnown ?? 0 };", "return { status: 'degraded', revenue: null };"),
    "R5.2": _d("A 204 with a body, or JSON without a Content-Type, breaks clients and proxies.",
               "return new Response('ok', { status: 204 });", "return new Response(null, { status: 204 });"),
    "R5.3": _d("Retrying every error, at once, turns one outage into many.",
               "while (true) { try { return await call(); } catch {} }",
               "retry(call, { on: [429, 502, 503, 504], backoff: 'exponential', jitter: true })"),
    "R5.4": _d("An error that is swallowed leaves no trace, so a failure looks like success.",
               "try { await save(); } catch (e) {}", "try { await save(); } catch (e) { log.error('save failed', e); throw e; }",
               "ts", "A bare `except:` whose handler re-raises with a plain `raise` at its top level is not flagged; a "
               "`raise` inside an `if`, or `raise SomethingElse`, does not count."),
    "R5.5": _d("Passing shell=True or an interpolated command string runs a shell that can execute injected input.",
               "subprocess.run(cmd, shell=True)", "subprocess.run(cmd, timeout=60, check=True)", "py",
               "Test files and comment lines are not flagged."),
    # R6: tests
    "R6.1": _d("A test that mocks the module it is named after never runs the real code.",
               "vi.mock('./a');", "vi.mock('./db');", "ts"),
    "R6.2": _d("A test that only checks a value exists passes when the behaviour is wrong.",
               "expect(result).toBeDefined();\nexpect(row).toBeTruthy();",
               "expect(res.status).toBe(200);\nexpect(row.name).toBe('Ada');"),
    "R6.3": _d("A sleep is either too short (flaky) or too long (slow).",
               "await page.waitForTimeout(2000);", "await expect(page.getByText('Saved')).toBeVisible();",
               "ts", "Only added lines in a test file are checked: a path with a folder or file name that starts or ends with the "
               "word test, tests, spec, specs, e2e or testing."),
    "R6.4": _d("Tests that share a database or a state folder pass or fail depending on their order.",
               "const path = '/tmp/state.json';", "const path = join(mkdtempSync(join(tmpdir(), 't-')), 'state.json');"),
    "R6.5": _d("A bug without a test comes back.",
               "fix: handle empty cart   (no test)", "fix: handle empty cart   (adds a test that fails without the fix)", "text"),
    # R7: architecture and contracts
    "R7.1": _d("If a consumer changes first, the contract and the code disagree until the next release.",
               "change the API handler, then edit the shared schema later",
               "edit the shared schema, then the handler and the client in the same change", "text"),
    "R7.2": _d("One generic error forces the user to guess and to retype the whole form.",
               "return { error: 'invalid' };", "return { errors: { email: 'Enter a valid email' } };"),
    "R7.3": _d("Two route files for one path means one of them is never reached.",
               "app/(a)/pricing/page.tsx and app/(b)/pricing/page.tsx", "app/(marketing)/pricing/page.tsx", "text"),
    "R7.4": _d("A cycle between layers means none of them can be tested or changed alone.",
               "adapters/db.ts imports domain/order.ts, and domain/order.ts imports adapters/db.ts",
               "adapters/db.ts imports domain/order.ts only", "text"),
    "R7.5": _d("The client bundler cannot load Node built-ins, so one import in a shared barrel breaks the web build.",
               "import fs from 'node:fs';", "import { z } from 'zod';", "ts",
               "Server-only files (drift.ts, *-io.ts, *-node.ts, server/) are not flagged."),
    # R8: marketing
    "R8.1": _d("Showing a crawler different facts than a person is cloaking. Search engines penalise it.",
               "if (isBot(ua)) return richPage; else return thinPage;", "return samePage;"),
    "R8.2": _d("Demo numbers mixed into live numbers make every report untrue.",
               "metrics.push(...demoRows, ...liveRows)", "metrics.push(...liveRows)  // demo rows are labelled and kept apart"),
    "R8.3": _d("An analytics tag that runs before consent is set breaks consent law.",
               "gtag('config', 'G-1');\ngtag('consent', 'default', { analytics_storage: 'denied' });",
               "gtag('consent', 'default', { analytics_storage: 'denied' });\ngtag('config', 'G-1');"),
    # R9: type safety
    "R9.1": _d("`any` turns the type checker off for everything it touches.",
               "const user = (await res.json()) as any;", "const user = UserSchema.parse(await res.json());", "ts",
               "Test files, test helpers and setup files, and comment lines are not flagged."),
    "R9.2": _d("A silenced error hides a real bug, and nobody remembers why.",
               "// @ts-ignore\nconst a = b.c;", "// @ts-expect-error -- the library types omit this field\nconst a = b.c;"),
    "R9.3": _d("A path that climbs into packages/ skips the package's exports map and build boundary.",
               "import { Button } from '../../packages/ui/button';", "import { Button } from '@acme/ui';", "ts",
               "Comment lines are not flagged."),
    # R10: API and network
    "R10.1": _d("A request with no timeout can hang a worker for ever.",
                "response = requests.get(url)", "response = requests.get(url, timeout=10)", "py",
                "Test files and calls that pass timeout= on the same line are not flagged."),
    "R10.2": _d("Without an idempotency key a retried POST creates the thing twice.",
                "await fetch(url, { method: 'POST', body });", "await fetch(url, { method: 'POST', body, headers: { 'Idempotency-Key': key } });"),
    "R10.3": _d("Ignoring Retry-After keeps you rate limited for longer.",
                "if (res.status === 429) return call();", "if (res.status === 429) await sleep(retryAfterMs(res));"),
    "R10.4": _d("Every endpoint with its own error shape makes every client write its own parser.",
                "res.status(400).send('bad');", "res.status(400).json({ error: { code: 'bad_request', message: 'name is required' } });"),
    "R10.5": _d("Reading a response without a cap lets one big reply use all the memory.",
                "const text = await res.text();", "const text = await readCapped(res, 1_000_000);"),
    "R10.6": _d("A handler that accepts any Content-Type may parse data it was never meant to.",
                "const body = await req.json();", "if (req.headers.get('content-type') !== 'application/json') return unsupported();"),
    "R10.7": _d("An admin route without a guard is open to the whole internet.",
                "app.post('/admin/reset', reset);", "app.post('/admin/reset', requireAdmin, reset);"),
    "R10.8": _d("A GET that changes data is triggered by crawlers, prefetchers and link previews.",
                "app.get('/delete/:id', remove);", "app.delete('/items/:id', remove);"),
    "R10.9": _d("A fetch with no options has no way to stop waiting, so a slow server holds the request, and the user, for as "
                "long as it likes.",
                "const res = await fetch(url);", "const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });", "ts",
                "A line that names signal or timeout, a call that has an options argument, a call that does not close on "
                "the line, and test files are not flagged. A timeout set once on an axios instance or in axios.defaults "
                "is not seen, so a call that relies on it is flagged."),
    # R11: state and concurrency
    "R11.1": _d("Two writers that read, change and write lose one of the changes.",
                "const n = await get(); await set(n + 1);", "await db.update({ where: { id, version }, data: { n: n + 1, version: version + 1 } });"),
    "R11.2": _d("A promise nobody awaits fails with no one listening.",
                "save();", "await save();"),
    "R11.3": _d("A slow first request can overwrite the result of a newer one.",
                "fetch(url).then(setData);", "ctrl.abort(); ctrl = new AbortController();\nfetch(url, { signal: ctrl.signal }).then(setData);"),
    "R11.4": _d("A reducer with side effects gives a different state each time it is replayed.",
                "case 'add': log(action); state.n++; return state;", "case 'add': return { ...state, n: state.n + 1 };"),
    "R11.5": _d("Keys built from unsorted parameters make the same query miss the cache.",
                "const key = JSON.stringify(params);", "const key = JSON.stringify(Object.entries(params).sort());"),
    "R11.6": _d("An optimistic update with no snapshot cannot be undone when the server says no.",
                "setItems([...items, item]); await save(item);", "const snap = items; setItems([...items, item]);\ntry { await save(item); } catch { setItems(snap); }"),
    "R11.7": _d("A queue with no bound grows until the process runs out of memory.",
                "const queue = [];", "const queue = new BoundedQueue({ max: 1000, onFull: 'drop-oldest' });"),
    "R11.8": _d("Two threads can both create the singleton.",
                "if (!inst) inst = make();", "with lock:\n    if inst is None:\n        inst = make()", "py"),
    # R12: leaks
    "R12.1": _d("A listener that is never removed keeps the component alive and fires after it is gone.",
                "useEffect(() => { window.addEventListener('resize', f); }, []);",
                "useEffect(() => { window.addEventListener('resize', f); return () => window.removeEventListener('resize', f); }, []);"),
    "R12.2": _d("A timer that outlives its component updates state that no longer exists.",
                "useEffect(() => { setTimeout(f, 5000); }, []);", "useEffect(() => { const t = setTimeout(f, 5000); return () => clearTimeout(t); }, []);"),
    "R12.3": _d("A stream that is not closed keeps its file or socket open.",
                "const s = fs.createReadStream(p);", "const s = fs.createReadStream(p);\ns.on('end', () => s.close());"),
    "R12.4": _d("A socket left open after navigation holds a connection and keeps receiving messages.",
                "const ws = new WebSocket(url);", "const ws = new WebSocket(url);\n// on unmount: ws.close();"),
    "R12.5": _d("A pool client that is not released starves the pool, and the app stops answering.",
                "const c = await pool.connect(); await c.query(q);", "const c = await pool.connect();\ntry { await c.query(q); } finally { c.release(); }"),
    "R12.6": _d("A worker that is never terminated keeps a thread and its memory.",
                "const w = new Worker(file);", "const w = new Worker(file);\nw.on('message', done).on('message', () => w.terminate());"),
    "R12.7": _d("Scratch files that are never deleted fill the disk.",
                "tmp = '/tmp/work.json'", "with tempfile.TemporaryDirectory() as d:\n    tmp = os.path.join(d, 'work.json')", "py"),
    "R12.8": _d("A cache with no limit grows for the life of the process.",
                "const cache = new Map();", "const cache = new LRU({ max: 500, ttl: 60_000 });"),
    # R13: observability
    "R13.1": _d("A secret written into code is copied wherever the code goes: clones, builds and logs.",
                "const sessionToken = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.example-signature';",
                "const sessionToken = process.env.SESSION_TOKEN;", "ts",
                "Only a known credential format (AWS, GitHub, Slack, Stripe live, Google API key, a private key block) "
                "or a random-looking value bound to a secret, token, password or API key name is flagged; a JWT bound "
                "to such a name always is. Test files are not checked. A placeholder (example, fake, test, xxxx, "
                "your-...) is not a key. Keys that are public by design are not flagged: a name that says public or "
                "publishable, a Stripe pk_ key, and an Algolia or DocSearch key (named so, or in the five lines below "
                "such a name). A secret-named NEXT_PUBLIC_, VITE_ or REACT_APP_ variable is flagged with its own message: the "
                "build copies it into the browser bundle, so a secret does not belong in it."),
    "R13.2": _d("Plain text logs cannot be searched by field.",
                "console.log('user ' + id + ' failed');", "log.info({ event: 'login.failed', userId: id });"),
    "R13.3": _d("Without the trace id you cannot follow one request across services.",
                "await fetch(url);", "await fetch(url, { headers: { traceparent } });"),
    "R13.4": _d("A user id as a metric label creates a new time series per user and floods the store.",
                "counter.inc({ userId });", "counter.inc({ route });"),
    "R13.5": _d("If everything is an error, nobody reads the errors.",
                "log.error('request received');", "log.debug('request received');"),
    "R13.6": _d("Without an audit record you cannot say who changed a role or a key.",
                "await setRole(user, 'admin');", "await setRole(user, 'admin');\nawait audit.append({ actor, action: 'role.set', target: user });"),
    "R13.7": _d("A heavy health check can take the service down when it is probed often.",
                "app.get('/healthz', async () => await db.query('SELECT count(*) FROM orders'));", "app.get('/healthz', () => ({ ok: true }));"),
    "R13.8": _d("A stack trace in a reply shows file paths and internals to the caller.",
                "res.status(500).send(err.stack);", "log.error(err);\nres.status(500).json({ error: { code: 'internal' } });"),
    # R14: auth and session
    "R14.1": _d("A guessable session id can be guessed by an attacker.",
                "const sid = Math.random().toString(36);", "const sid = crypto.randomBytes(32).toString('hex');"),
    "R14.2": _d("A fast hash can be brute forced. A slow one cannot, at the same cost.",
                "hash = sha256(password)", "hash = argon2id.hash(password)", "py"),
    "R14.3": _d("A long-lived token that leaks stays useful for months.",
                "sign(payload, key, { expiresIn: '365d' })", "sign(payload, key, { expiresIn: '15m' })  // plus a rotating refresh token"),
    "R14.4": _d("Without a token another site can submit your user's form for them.",
                "<form method=\"post\" action=\"/transfer\">", "<form method=\"post\" action=\"/transfer\">\n  <input type=\"hidden\" name=\"csrf\" value={token} />", "html"),
    "R14.5": _d("Anything in a URL ends up in logs, history and referrer headers.",
                "fetch(`/api?token=${token}`)", "fetch('/api', { headers: { Authorization: `Bearer ${token}` } })"),
    "R14.6": _d("An open redirect sends your user to an attacker's page with your login in the URL.",
                "res.redirect(req.query.next);", "res.redirect(ALLOWED.has(host(next)) ? next : '/');"),
    "R14.7": _d("Without a limit, a script can try a million passwords.",
                "app.post('/login', login);", "app.post('/login', rateLimit({ perIp: 10, perUser: 5 }), login);"),
    "R14.8": _d("A check that lives only in the UI is skipped by anyone who calls the API.",
                "app.delete('/users/:id', removeUser);", "app.delete('/users/:id', requireRole('admin'), removeUser);"),
    # R15: error boundaries
    "R15.1": _d("One failing component should not blank the whole page.",
                "<Dashboard />", "<ErrorBoundary fallback={<Retry />}><Dashboard /></ErrorBoundary>", "tsx"),
    "R15.2": _d("If a chat widget fails to load, the rest of the page must still work.",
                "<ChatWidget />", "<ErrorBoundary fallback={null}><ChatWidget /></ErrorBoundary>", "tsx"),
    "R15.3": _d("A form that clears on failure makes people retype everything.",
                "onError: () => form.reset()", "onError: (e) => setErrors(e.fields)  // values stay", "ts"),
    "R15.4": _d("A broken image with no fallback leaves a hole in the layout.",
                '<img src={url} alt="" />', '<img src={url} alt="Team photo" onError={useFallback} />', "tsx"),
    "R15.5": _d("A user offline sees buttons that silently do nothing.",
                "// no offline state", "{!online && <Banner>You are offline. Changes will sync later.</Banner>}", "tsx"),
    "R15.6": _d("An empty table with no message looks broken.",
                "{rows.map(Row)}", "{rows.length ? rows.map(Row) : <Empty action={<AddButton />} />}", "tsx"),
    "R15.7": _d("A spinner that is replaced by content shifts the layout and hurts CLS.",
                "{loading && <Spinner />}", "{loading ? <TableSkeleton rows={5} /> : <Table rows={rows} />}", "tsx"),
    "R15.8": _d("A dead end 404 loses the visitor.",
                "export default () => <h1>404</h1>;", "export default () => <NotFound links={['/', '/search']} />;", "tsx"),
    # R16: supply chain
    "R16.1": _d("Without a frozen lockfile CI installs different versions than you tested.",
                "pnpm install", "pnpm install --frozen-lockfile", "text"),
    "R16.2": _d("A wildcard version takes whatever was published last, including a bad release.",
                '"left-pad": "*"', '"left-pad": "1.3.0"', "json",
                "Only package.json is checked, and the top-level version and private fields are not flagged."),
    "R16.3": _d("An unscoped internal name can be taken by someone on the public registry.",
                '"name": "billing-utils"', '"name": "@acme/billing-utils"', "json"),
    "R16.4": _d("An install script runs code from a package you have not read.",
                "pnpm install", "pnpm install --ignore-scripts", "text"),
    "R16.5": _d("A dev dependency imported in production code is missing in the production image.",
                "import { faker } from '@faker-js/faker';  // in src/", "import { randomUUID } from 'node:crypto';"),
    "R16.6": _d("A copyleft licence can force you to publish your own code.",
                "import gplLib from 'gpl-lib';", "import mitLib from 'mit-lib';"),
    "R16.7": _d("Two lockfiles mean two sets of versions, and nobody knows which is real.",
                "package-lock.json and pnpm-lock.yaml", "pnpm-lock.yaml only", "text"),
    "R16.8": _d("Without an engines field, someone installs with a Node that cannot run the code.",
                '{ "name": "app" }', '{ "name": "app", "engines": { "node": ">=20", "pnpm": ">=9" } }', "json"),
    # R17: React and Next.js
    "R17.1": _d("A Server Component sends no JavaScript for itself. A client one does.",
                "'use client';  // on a page that only renders text", "// no directive: a Server Component", "tsx"),
    "R17.2": _d("A server-only module in client code leaks secrets or fails the build.",
                "'use client';\nimport 'server-only';", "// keep the import in a server file and pass the data down", "tsx"),
    "R17.3": _d("A page with no metadata has no title or description in search results.",
                "export default function Page() {}", "export const metadata = { title: 'Pricing', description: 'Plans and prices' };", "tsx"),
    "R17.4": _d("A plain img has no resizing, no srcset and no reserved space.",
                '<img src="/a.png" />', '<Image src="/a.png" alt="" width={400} height={300} />', "tsx"),
    "R17.5": _d("A font loaded by link shifts the text when it arrives.",
                '<link href="//fonts.googleapis.com/css2?family=Inter" rel="stylesheet" />', "const inter = Inter({ subsets: ['latin'] });  // next/font", "tsx"),
    "R17.6": _d("A directive at the top of a big tree sends all of it to the browser.",
                "'use client';  // on the whole page", "'use client';  // only in <LikeButton />, a leaf", "tsx"),
    "R17.7": _d("A server action is a public endpoint. Anyone can call it.",
                "'use server';\nexport async function remove(id) { await db.delete(id); }", "'use server';\nexport async function remove(id) { await requireSession(); await db.delete(id); }", "tsx"),
    "R17.8": _d("An untyped handler returns whatever it returns, with a status nobody chose.",
                "export async function GET() { return Response.json(data); }", "export async function GET(): Promise<NextResponse<Data>> { return NextResponse.json(data, { status: 200 }); }", "tsx"),
    # R18: accessibility
    "R18.1": _d("A screen reader reads nothing for an image with no alt text.",
                '<img src="/logo.png" />', '<img src="/logo.png" alt="Acme logo" />', "tsx",
                "Only <img> tags written on a single added line are checked."),
    "R18.2": _d("A target=\"_blank\" link without rel=\"noopener noreferrer\" exposes the opener window to the linked page.",
                '<a href="/docs" target="_blank">Docs</a>',
                '<a href="/docs" target="_blank" rel="noopener noreferrer">Docs</a>', "tsx",
                "Test files and links that include noopener in rel= on the same tag are not flagged."),
    "R18.3": _d("If a control cannot be reached with the keyboard, some people cannot use it.",
                "<div onClick={open}>Open</div>", "<button onClick={open}>Open</button>", "tsx"),
    "R18.4": _d("Light grey text on white cannot be read by many people.",
                "color: #aaa; background: #fff;", "color: #595959; background: #fff;  /* 7:1 */", "css"),
    "R18.5": _d("A div with a click handler is not a button for the keyboard or a screen reader.",
                "<div onClick={open}>Open</div>", '<div onClick={open} role="button" tabIndex={0}>Open</div>', "tsx",
                "A div with role= after its onClick on the same line is not flagged."),
    "R18.6": _d("An invalid aria attribute is ignored, so the control has no name.",
                '<div aria-lable="Close" />', '<div aria-label="Close" />', "tsx"),
    "R18.7": _d("People who use a keyboard need to see where the focus is.",
                "button:focus { outline: none; }", "button:focus-visible { outline: 2px solid #1a73e8; }", "css"),
    "R18.8": _d("Without a language, a screen reader reads the page in the wrong voice.",
                "<html>", '<html lang="en">', "html"),
    # R19: clean code
    "R19.1": _d("A long function has too many reasons to change and cannot be read at once.",
                "def run():  # 300 lines", "def run():\n    plan = make_plan()\n    return execute(plan)", "py"),
    "R19.2": _d("Many branches in one function means many cases nobody tests.",
                "if a: ... elif b: ... elif c: ... # 25 branches", "table = {a: fa, b: fb, c: fc}\nreturn table[key]()", "py"),
    "R19.3": _d("Commented-out code goes stale, and git already keeps the old version.",
                "# old_call(x)\nnew_call(x)", "new_call(x)", "py"),
    "R19.4": _d("A one letter name tells the reader nothing.",
                "const d = Date.now() - s;", "const elapsedMs = Date.now() - startedAt;"),
    # R20: test custody (2026-11-12, improvement plan 3a)
    "R20.1": _d("A test file that is deleted, or renamed so that a runner no longer collects it, stops guarding what it "
                "covered. Sometimes that is right, for example when the code it tested is gone, so a person looks.",
                "git rm tests/test_billing.py   # the change that made it fail stays in",
                "restore the test file and fix the code it tests", "text",
                "only Python files named test_*.py or *_test.py, and JavaScript or TypeScript files named *.test.* or "
                "*.spec.*, count as test files."),
    "R20.2": _d("A test file with fewer tests or assertions than before checks less. The count is a plain count of test "
                "functions and assertion calls in the file, so a test moved to another file reads as fewer too.",
                "def test_total():\n    assert total(cart) == 30   # the second assertion was dropped",
                "keep the assertions the file had:\ndef test_total():\n    assert total(cart) == 30\n    assert total([]) == 0", "text",
                "comment lines are not counted."),
    "R20.3": _d("A skipped or focused test stops running, or stops the others, while the build stays green. The failure "
                "it hides is still there.",
                "it.skip('charges the card', () => { chargeCard(); });",
                "// fix the code or the test, and keep it running:\nit('charges the card', () => { chargeCard(); });", "text",
                "comment lines are not flagged, and the markers it reads are `it`, `test` and `describe` with `.skip` "
                "or `.only`, `xit`, `xtest`, `xdescribe`, and the pytest and unittest skip decorators."),
    "R20.4": _d("An assertion that compares a value with itself, or asserts True, passes whatever the code does.",
                "expect(result).toBe(result);", "// assert the real value:\nexpect(result).toBe(30);", "text",
                "comment lines are not flagged."),
    "R20.5": _d("A test with an empty body passes without checking anything, and still counts as a test.",
                "it('charges the card', () => {});",
                "// give the test a body that checks something:\nit('charges the card', () => {\n  expect(charge(card)).toBe('ok');\n});", "text",
                "a Python test function whose body is only pass, a docstring or `...`, and a JavaScript or TypeScript "
                "it or test with an empty body on one line, are flagged; comment lines are not."),
}

# Rules that `brevio-core check` and the agent hook run, not the scan: rule -> (how it is checked, how to silence it). A
# page prints these in place of the scanner lines, so it says what the code does (2026-11-12, improvement plan 3a).
_CUSTODY_BASE = ("`brevio-core check` compares the working tree with `--base` (default `HEAD`). The agent hook runs the "
                 "same comparison when the agent stops, and `brevio-core patch` runs it on an edit before writing it. "
                 "`brevio-core scan` does not run it.")
_CUSTODY_LINES = ("`brevio-core check` reads the lines the change adds. The agent hook runs it when the agent stops, "
                  "and `brevio-core patch` runs it on an edit before writing it. `brevio-core scan` does not run it.")
_NO_IGNORE = ("An ignore comment does not silence this rule: `brevio-core check` does not read one for it, and a "
              "setting in `brevio-core.toml` does not turn it off. A person who means the change keeps it for a "
              "reviewer to accept; an agent restores the test.")
CHECKED: dict[str, tuple[str, str]] = {
    "R20.1": (_CUSTODY_BASE, _NO_IGNORE),
    "R20.2": (_CUSTODY_BASE, _NO_IGNORE),
    "R20.3": (_CUSTODY_LINES, _NO_IGNORE),
    "R20.4": (_CUSTODY_LINES, _NO_IGNORE),
    "R20.5": ("`brevio-core check` reads the lines the change adds. `brevio-core scan`, the agent hook and "
              "`brevio-core patch` do not run it.",
              "A person can silence one finding with `brevio-core: ignore R20.5 -- reason` on the line or the line "
              "above, in `brevio-core check`. Without the reason it does not count. A setting in `brevio-core.toml` does not "
              "turn it off."),
}
