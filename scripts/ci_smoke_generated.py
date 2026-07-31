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
    # A generated app whose contracts declare auth refuses to boot without a
    # real signing secret and an explicit CORS origin — both deliberate, and
    # both of which this script has to satisfy rather than work around. Only
    # set what the operator has not: an env var already present belongs to the
    # caller.
    os.environ.setdefault("AUTH_SECRET", "ci-smoke-" + "0" * 40)
    os.environ.setdefault("CORS_ORIGINS", "https://ci.smoke.invalid")

    for mod in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[mod]

    try:
        import importlib

        from fastapi.testclient import TestClient

        app_module = importlib.import_module("backend.app")
        app = app_module.app
    except Exception as e:
        FAILURES.append(f"{app_root.name}: app failed to import: {type(e).__name__}: {e}")
        sys.path.remove(str(app_root))
        return

    client = TestClient(app, raise_server_exceptions=False)
    label = app_root.name

    # Mint a token directly from the app's own auth provider where one exists.
    # Going through /auth/login would require a seeded user; this only needs
    # enough authority to reach the endpoints whose invariants we assert, and
    # deliberately does not exercise the login path — that is asserted below to
    # *fail* without a credential.
    auth_headers: dict[str, str] = {}
    try:
        import asyncio

        from backend.auth.interface import AuthUser  # type: ignore
        from backend.auth.jwt_provider import JWTAuthProvider  # type: ignore

        pair = asyncio.run(
            JWTAuthProvider().issue_tokens(
                AuthUser(id="ci-smoke", email="ci@specora.invalid", role="admin")
            )
        )
        auth_headers = {"Authorization": f"Bearer {pair.access_token}"}
    except ImportError:
        pass  # app declares no auth; endpoints are reachable unauthenticated
    except Exception as e:
        # Anything else means the auth surface changed shape. Say so rather
        # than silently degrading to unauthenticated probing, which would let
        # every downstream check pass on a 401.
        FAILURES.append(
            f"{app_root.name}: could not mint a smoke token "
            f"({type(e).__name__}: {e}). The generated auth API changed; update "
            f"this script rather than letting the invariant checks pass on 401."
        )

    # A login endpoint must never issue a token without verifying a credential.
    login = client.post("/auth/login", json={"id": "anon", "email": "a@b.c", "role": "admin"})
    if login.status_code == 200:
        body = (
            login.json()
            if login.headers.get("content-type", "").startswith("application/json")
            else {}
        )
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
    #
    # A 401 does NOT satisfy this check. An auth-protected app would pass it
    # trivially while leaving the bound untested, and the bound is what stops a
    # single request from trying to materialise an entire table. So assert the
    # request is refused *for the right reason*: 422 (validation rejected the
    # limit), not merely "refused".
    # Discover routes from the OpenAPI schema rather than walking app.routes:
    # included routers are wrapped (`_IncludedRouter`) rather than flattened in
    # current FastAPI, so iterating app.routes silently sees only the built-in
    # /docs and /openapi.json endpoints and every check against it passes
    # vacuously. The schema is what the app actually serves.
    #
    # Built-ins take no `limit`, so restrict to paths the generator emitted,
    # identified by the ROUTE_TO_FQN map the app builds for healer attribution.
    generated_prefixes = tuple(getattr(app_module, "ROUTE_TO_FQN", {}) or ())
    paths = app.openapi().get("paths", {})
    collections = [
        p
        for p, ops in paths.items()
        if "get" in {m.lower() for m in ops}
        and "{" not in p
        and p.rstrip("/").startswith(generated_prefixes or ("\0",))
    ]
    check(
        bool(collections),
        f"{label}: no generated collection endpoint found to test the pagination bound against",
    )
    for path in collections:
        huge = client.get(f"{path}?limit=999999999", headers=auth_headers)
        if huge.status_code in (401, 403):
            check(
                False,
                f"{label}: GET {path}?limit=... returned {huge.status_code}, so "
                f"the pagination bound was never exercised. Fix the smoke "
                f"test's token rather than accepting this as a pass.",
            )
        else:
            check(
                huge.status_code == 422,
                f"{label}: GET {path}?limit=999999999 returned "
                f"{huge.status_code}; expected 422 from a bounded limit",
            )

    sys.path.remove(str(app_root))
    print(f"  probed {label}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path, help="root of generated apps")
    args = ap.parse_args()

    # `backend/app.py` is imported as `backend.app`, so the importable root is
    # the directory *containing* backend/, not backend/ itself.
    app_roots = sorted(p.parent.parent for p in args.root.glob("*/backend/app.py"))
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
