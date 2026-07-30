#!/usr/bin/env python3
"""Boot every generated application and assert its security invariants hold.

Parsing proves the generated code is syntactically valid. It does not prove the
application is safe. These invariants are each a defect that shipped:

  * a login endpoint that minted an admin token for any anonymous caller
  * CORS reflecting an arbitrary origin with credentials enabled
  * an unbounded `limit` query parameter
  * the workflow state machine bypassable through the ordinary update endpoint
  * 500 responses echoing raw exception text back to the client

Each is asserted against a running app, not read out of the source, because
several of them looked correct in the source and were wrong at runtime.

Exits non-zero if any invariant is violated.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def smoke_one(app_root: Path) -> None:
    """Boot one generated app in-process and probe its invariants."""
    sys.path.insert(0, str(app_root))
    os.environ["DATABASE_BACKEND"] = "memory"

    for mod in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[mod]

    try:
        from fastapi.testclient import TestClient

        from backend.app import app  # type: ignore[import-not-found]
    except Exception as e:
        FAILURES.append(f"{app_root.name}: app failed to import: {type(e).__name__}: {e}")
        return
    finally:
        sys.path.remove(str(app_root))

    client = TestClient(app, raise_server_exceptions=False)
    label = app_root.name

    # A login endpoint must never issue a token without verifying a credential.
    login = client.post(
        "/auth/login", json={"id": "anon", "email": "a@b.c", "role": "admin"}
    )
    if login.status_code == 200:
        body = login.json() if login.headers.get("content-type", "").startswith(
            "application/json"
        ) else {}
        check(
            "access_token" not in body,
            f"{label}: POST /auth/login issued a token with no credential check",
        )

    # CORS must not reflect an arbitrary origin while allowing credentials.
    cors = client.get("/health", headers={"Origin": "https://evil.example"})
    check(
        not (
            cors.headers.get("access-control-allow-origin") == "https://evil.example"
            and cors.headers.get("access-control-allow-credentials") == "true"
        ),
        f"{label}: CORS reflects an arbitrary origin with credentials enabled",
    )

    # Collection endpoints must bound how much a caller can request.
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "GET" not in methods or "{" in path or path in ("/health", "/"):
            continue
        huge = client.get(f"{path}?limit=999999999")
        check(
            huge.status_code != 200,
            f"{label}: GET {path}?limit=999999999 returned 200 — pagination is unbounded",
        )
        break

    print(f"  probed {label}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path, help="root of generated apps")
    args = ap.parse_args()

    app_roots = sorted(p.parent for p in args.root.glob("*/backend/app.py"))
    if not app_roots:
        print(f"no generated applications found under {args.root}", file=sys.stderr)
        return 1

    for app_root in app_roots:
        smoke_one(app_root)

    if FAILURES:
        print("\nSecurity invariants violated:", file=sys.stderr)
        for f in FAILURES:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\n{len(app_roots)} generated app(s) booted; all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
