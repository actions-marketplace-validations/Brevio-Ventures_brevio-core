"""Progressive-Disclosure Community Guard Packs (Master Growth Plan G14 / S7).

Curated zero-token AST guard packs installable via `brevio-core add <pack>`:
- nextjs-app-router
- stripe-polar-billing
- fastapi-async
- eu-ai-act
- supabase-rls
"""
from __future__ import annotations

import glob
import os
import re
from typing import Any

from brevio.pipeline.agent_host import config
from brevio.pipeline.gates import defect_compiler

PACKS: dict[str, dict[str, Any]] = {
    "nextjs-app-router": {
        "name": "nextjs-app-router",
        "description": "Next.js App Router hydration, client/server boundaries, and metadata rules",
        "requires": "next",
        # A guard matches one line at a time, so the 'use client' line of the file is named in ``file_contains``, which is
        # checked once against the whole file (2026-11-13, packs). The old patterns spanned lines and never matched.
        "guards": [
            {
                "id": "pack-nextjs-no-async-client",
                "title": "Client components must not be async functions",
                "label": "an async component in a file marked 'use client'",
                "category": "robustness",
                "file_patterns": ["**/*.tsx", "**/*.jsx"],
                "file_contains": "use client",
                "patterns": ["export default async function"],
            },
            {
                "id": "pack-nextjs-metadata-server-only",
                "title": "Metadata exports are only valid in server components",
                "label": "a metadata export in a file marked 'use client'",
                "category": "robustness",
                "file_patterns": ["**/*.tsx", "**/*.jsx"],
                "file_contains": "use client",
                "patterns": ["export const metadata"],
            },
        ],
    },
    "stripe-polar-billing": {
        "name": "stripe-polar-billing",
        "description": "Stripe & Polar billing webhook signature verification & idempotency guards",
        "guards": [
            {
                "id": "pack-billing-webhook-raw-body",
                "title": "Billing webhooks must verify signature using raw unparsed body",
                "category": "security",
                "file_patterns": ["**/webhook*.*", "**/api/billing/**", "**/api/stripe/**"],
                "patterns": ["JSON.parse.*constructEvent", "req.body.*constructEvent"],
            },
            {
                "id": "pack-billing-idempotency-key",
                "title": "Payment mutations must pass an idempotency key",
                "category": "finops",
                "file_patterns": ["**/billing/**", "**/payments/**"],
                "patterns": ["re:stripe\\.(?:charges|paymentIntents)\\.create\\(\\s*\\{[^}]*(?!idempotencyKey)[^}]*\\}\\)"],
            },
        ],
    },
    "fastapi-async": {
        "name": "fastapi-async",
        "description": "FastAPI async event-loop safety & lifespan context guards",
        "requires": "fastapi",
        "guards": [
            {
                "id": "pack-fastapi-no-blocking-io",
                "title": "Blocking call in an API module",
                "label": "a blocking sleep or a synchronous HTTP call",
                "category": "robustness",
                # Modules that usually hold route handlers, not every Python file (2026-11-13, packs).
                "file_patterns": ["**/routers/*.py", "**/routes/*.py", "**/api/*.py", "**/main.py", "**/app/*.py"],
                "patterns": ["re:requests\\.(get|post|put|delete)\\(|time\\.sleep\\("],
            },
            {
                "id": "pack-fastapi-lifespan",
                "title": "Use lifespan context manager instead of deprecated on_event",
                "label": "a deprecated on_event startup hook",
                "category": "robustness",
                "file_patterns": ["**/*.py"],
                "patterns": ['@app.on_event("startup")', "@app.on_event('startup')"],
            },
        ],
    },
    "eu-ai-act": {
        "name": "eu-ai-act",
        "description": "EU AI Act statutory transparency headers and consent logging",
        "guards": [
            {
                "id": "pack-ai-transparency-header",
                "title": "AI inference endpoints must return X-AI-Generated transparency header",
                "category": "compliance",
                "file_patterns": ["**/api/chat/**", "**/api/generate/**", "**/api/llm/**"],
                "patterns": ["re:return\\s+(?:NextResponse|Response)\\.json\\((?!.*X-AI-Generated)"],
            },
        ],
    },
    "supabase-rls": {
        "name": "supabase-rls",
        "description": "Supabase client security & Row Level Security (RLS) enforcement",
        "requires": "supabase",
        "guards": [
            {
                "id": "pack-supabase-no-service-key-client",
                "title": "Browser client bundles must not read the Supabase service role key",
                "label": "the service role key read in client code",
                "category": "security",
                "file_patterns": ["apps/web/**", "src/client/**", "src/components/**", "**/components/*", "**/client/*"],
                "patterns": ["process.env.SUPABASE_SERVICE_ROLE_KEY"],
            },
            {
                "id": "pack-supabase-no-public-service-key",
                "title": "The service role key must not be exposed as a public variable",
                "label": "the service role key named as a public variable",
                "category": "security",
                "file_patterns": ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"],
                "patterns": ["NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY"],
            },
            {
                "id": "pack-supabase-service-key-in-client-file",
                "title": "A file marked 'use client' must not read the Supabase service role key",
                "label": "the service role key read in a file marked 'use client'",
                "category": "security",
                "file_patterns": ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"],
                "file_contains": "use client",
                "patterns": ["process.env.SUPABASE_SERVICE_ROLE_KEY"],
            },
        ],
    },
}


def list_packs() -> list[dict[str, Any]]:
    return [
        {"id": k, "name": p["name"], "description": p["description"], "guard_count": len(p["guards"])}
        for k, p in PACKS.items()
    ]


MANIFESTS = ("pyproject.toml", "requirements*.txt", "package.json")
MAX_MANIFEST = 2_000_000    # bytes read of one manifest: the file is the project's own, but a huge one is not read whole


def requirement_note(pack_name: str, cwd: str | None = None) -> str:
    """A sentence for a person when the pack names a dependency (``requires``) and none of the project's manifests
    (``pyproject.toml``, ``requirements*.txt``, ``package.json`` in the folder) holds it; "" when it is found or the pack
    names none. A pack matches by path and text, so it flags matching lines whether the project uses the library or not."""
    need = str(PACKS.get(pack_name, {}).get("requires", ""))
    if not need:
        return ""
    root = cwd or config.WORKSPACE_DIR
    word = re.compile(rf"(?<![\w-]){re.escape(need)}(?![\w-])", re.IGNORECASE)
    for pattern in MANIFESTS:
        for path in glob.glob(os.path.join(glob.escape(root), pattern)):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    if word.search(fh.read(MAX_MANIFEST)):
                        return ""
            except OSError:
                continue
    return (f"no {need} found in pyproject.toml, requirements files or package.json here; the guards of this pack "
            "still flag the lines they match in the files they watch")


def install_pack(pack_name: str, cwd: str | None = None) -> list[str]:
    """Install a curated community guard pack into .brevio/guards.json and .agents/rules/."""
    pack = PACKS.get(pack_name)
    if not pack:
        raise KeyError(f"Unknown community guard pack: '{pack_name}'. Available: {list(PACKS.keys())}")

    workspace = cwd or config.WORKSPACE_DIR
    installed_ids = []

    data = defect_compiler.load_guards(workspace)
    guards = data.setdefault("guards", {})

    for g_spec in pack["guards"]:
        gid = g_spec["id"]
        title = g_spec["title"]
        category = g_spec["category"]
        file_patterns = g_spec["file_patterns"]
        patterns = g_spec["patterns"]

        rule_path = defect_compiler._write_agent_rule(
            guard_id=gid,
            title=title,
            category=category,
            file_globs=file_patterns,
            patterns=patterns,
            cwd=workspace,
        )

        record = {
            "id": gid,
            "title": title,
            "category": category,
            "pack": pack_name,
            "file_patterns": file_patterns,
            "patterns": patterns,
            **{k: g_spec[k] for k in ("label", "file_contains") if k in g_spec},
            "status": "active",
            "runs_evaluated": 0,
            "false_fails": 0,
            "intercepted": 0,
            "rule_file": os.path.relpath(rule_path, workspace),
            "created_at": defect_compiler._now_iso(),
            "updated_at": defect_compiler._now_iso(),
        }
        guards[gid] = record
        installed_ids.append(gid)

    defect_compiler.save_guards(data, workspace)
    return installed_ids
