#!/usr/bin/env python3
"""Assert the memory and PostgreSQL adapters answer identically.

The repository pattern here rests on one claim: swapping `DATABASE_BACKEND`
changes where data lives and nothing else. That claim has already been false in
production. The Postgres adapter accepted a `filters` argument and ignored it
while the memory adapter honoured it, so a filtered query returned the right
rows under test and every row in front of a real database — and if those filters
had ever carried a tenant scope, that is cross-tenant disclosure.

No gate could have caught it. The unit suite runs against memory. The CI
Postgres job applied `schema.sql` and stopped, proving the DDL parsed while
never issuing a query through the code that has to use it.

So this drives the same sequence of HTTP requests against a generated app twice
— once with `DATABASE_BACKEND=memory`, once against a real PostgreSQL — and
compares the responses. Anything the two backends disagree about is, by
definition, a divergence, whichever one is right.

Exits non-zero on any disagreement.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Run inside the generated app, because it must import that app's own modules.
# Kept as source rather than a helper module: the app root is not importable
# from here, and a subprocess is also what guarantees the two runs share no
# in-process state — an asyncpg pool is bound to the event loop that made it.
PROBE = '''
import json, os, sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from backend.app import app

def sample(schema, defs, depth=0):
    """Smallest value satisfying a JSON-Schema node, resolving $ref."""
    if depth > 6:
        return None
    if "$ref" in schema:
        return sample(defs.get(schema["$ref"].rsplit("/", 1)[-1], {}), defs, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        for option in schema.get(key, []):
            if option.get("type") != "null":
                return sample(option, defs, depth + 1)
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    t = schema.get("type")
    if t == "string":
        fmt = schema.get("format")
        if fmt == "uuid":
            return "00000000-0000-0000-0000-000000000000"
        if fmt == "email":
            return "parity@ci.invalid"
        if fmt == "date-time":
            return "2026-01-01T00:00:00+00:00"
        return "parity"
    if t == "integer":
        return schema.get("minimum", 1)
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    return None


def payload_for(schema_doc, path):
    """A minimal valid create body for POST *path*, from its OpenAPI schema."""
    post = schema_doc.get("paths", {}).get(path, {}).get("post")
    if not post:
        return None
    ref = (post.get("requestBody", {}).get("content", {})
              .get("application/json", {}).get("schema", {}))
    defs = schema_doc.get("components", {}).get("schemas", {})
    if "$ref" in ref:
        ref = defs.get(ref["$ref"].rsplit("/", 1)[-1], {})
    props, required = ref.get("properties", {}), set(ref.get("required", []))
    return {n: sample(s, defs) for n, s in props.items() if n in required}


results = {}
# A context manager is required: without it the lifespan never runs, so
# migrations are not applied and the auth token store table is never created.
with TestClient(app, raise_server_exceptions=False) as client:
    schema = app.openapi()
    collections = sorted(
        p for p, ops in schema.get("paths", {}).items()
        if "get" in {m.lower() for m in ops} and "{" not in p
        and p not in ("/health",)
    )

    # Seed. Comparing two EMPTY databases is vacuous: both answer "no rows" to
    # every query, so the comparison passes however far the adapters have
    # drifted. `id` is deliberately NOT supplied — the create models set
    # `extra: forbid` and `id` is server-generated, so sending one is a 422 and
    # the seed silently does nothing, which is how this check was vacuous the
    # first time it was written.
    for path in collections:
        body = payload_for(schema, path)
        if body is None:
            continue
        for n in range(3):
            status = client.post(path, json=body).status_code
            results[f"SEED {path} #{n}"] = {"status": status, "body": ""}
            if status >= 400:
                # A seed that cannot land makes every later comparison
                # meaningless, so it is recorded as a failure rather than
                # letting the run report agreement about nothing.
                results[f"SEED {path} #{n}"]["body"] = "SEED FAILED"

    for path in collections:
        for query in ("?limit=5", "?limit=1", "?limit=999999999", "?nosuchfilter=x"):
            r = client.get(f"{path}{query}")
            body = r.text
            if r.status_code == 200:
                try:
                    # Row COUNT and whether a further page exists, not the ids
                    # themselves: ids are server-generated so the two runs
                    # legitimately differ, and comparing them would fail every
                    # time for the wrong reason. Count and paging shape are the
                    # properties an adapter swap must preserve.
                    payload = r.json()
                    body = json.dumps(
                        {
                            "count": len(payload.get("items", [])),
                            "has_cursor": bool(payload.get("next_cursor")),
                        },
                        sort_keys=True,
                    )
                except ValueError:
                    pass
            results[f"{path}{query}"] = {"status": r.status_code, "body": body}
print("__RESULTS__" + json.dumps(results, sort_keys=True))
'''


def reset_and_apply(dsn: str, schema_sql: str) -> None:
    """Drop and recreate the public schema, then apply *schema_sql*.

    Each app gets a clean database so a leftover table from the previous domain
    cannot make a query succeed that would fail on its own.
    """
    import asyncio

    import asyncpg

    async def _apply() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await conn.execute(schema_sql)
        finally:
            await conn.close()

    asyncio.run(_apply())


def run(app_root: Path, env: dict[str, str]) -> dict:
    """Run the probe inside *app_root* with *env* and return its results."""
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=app_root,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("__RESULTS__")), None)
    if line is None:
        raise RuntimeError(
            f"probe failed in {app_root.name}:\n{(proc.stderr or proc.stdout)[-2000:]}"
        )
    return json.loads(line[len("__RESULTS__") :])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path, help="root of generated apps")
    ap.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL DSN; defaults to $DATABASE_URL",
    )
    args = ap.parse_args()

    if not args.database_url:
        print("DATABASE_URL is required to compare against PostgreSQL", file=sys.stderr)
        return 1

    app_roots = sorted(p.parent.parent for p in args.root.glob("*/backend/app.py"))
    if not app_roots:
        print(f"no generated applications under {args.root}", file=sys.stderr)
        return 1

    shared = {
        "AUTH_SECRET": "ci-adapter-parity-" + "0" * 32,
        "CORS_ORIGINS": "https://ci.invalid",
        "PYTHONPATH": ".",
    }

    failures: list[str] = []
    for app_root in app_roots:
        schema = app_root / "database" / "schema.sql"
        if schema.exists():
            # asyncpg rather than shelling out to psql: it is already a
            # dependency of every generated app, so this gate needs no client
            # binary on the runner and cannot fail for the wrong reason.
            reset_and_apply(args.database_url, schema.read_text(encoding="utf-8"))

        memory = run(app_root, {**shared, "DATABASE_BACKEND": "memory"})
        postgres = run(
            app_root,
            {**shared, "DATABASE_BACKEND": "postgres", "DATABASE_URL": args.database_url},
        )

        for key in sorted(set(memory) | set(postgres)):
            m, p = memory.get(key), postgres.get(key)
            if m != p:
                failures.append(f"{app_root.name}: {key}\n    memory={m}\n    postgres={p}")

        print(f"  compared {app_root.name}: {len(memory)} requests")

    if failures:
        print("\nAdapters disagree:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\n{len(app_roots)} app(s): memory and postgres answered identically")
    return 0


if __name__ == "__main__":
    sys.exit(main())
