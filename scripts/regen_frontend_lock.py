#!/usr/bin/env python3
"""Regenerate forge/targets/nextjs/frontend_lock.json from npm_deps.py.

The generated frontend ships a committed lockfile so that `npm ci` inside
Dockerfile.frontend installs one exact, hash-verified dependency tree. That
lockfile has to come from somewhere, and "somebody ran npm once" is not an
answer anyone can check. This script is the answer: it builds a package.json
from the pins in forge/targets/nextjs/npm_deps.py, asks npm to resolve it, and
writes the result back.

Run it after editing `_DEPENDENCIES` or `_DEV_DEPENDENCIES`, and commit both
files together. Requires npm and network access — which is exactly why it is a
deliberate, occasional step and not part of the build.

    python scripts/regen_frontend_lock.py           # rewrite the lockfile
    python scripts/regen_frontend_lock.py --check   # verify, change nothing

`--check` is the CI-safe form: it fails if the committed lockfile is not what
the current pins resolve to.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCKFILE = REPO / "forge" / "targets" / "nextjs" / "frontend_lock.json"


def resolve() -> dict:
    """Ask npm to resolve the pinned manifest, and return the lockfile it writes."""
    sys.path.insert(0, str(REPO))
    from forge.targets.nextjs import npm_deps

    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm is not on PATH; this script needs it to resolve the tree.")

    manifest = {
        "name": "specora-frontend",
        "version": npm_deps.FRONTEND_VERSION,
        "private": True,
        "dependencies": npm_deps.dependencies(),
        "devDependencies": npm_deps.dev_dependencies(),
    }

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "package.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # --package-lock-only resolves and records without unpacking anything,
        # so nothing from the registry executes on this machine.
        subprocess.run(
            [npm, "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=work,
            check=True,
        )
        return json.loads((work / "package-lock.json").read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed lockfile differs from what the pins resolve to",
    )
    args = ap.parse_args()

    fresh = json.dumps(resolve(), indent=2) + "\n"

    if args.check:
        current = LOCKFILE.read_text(encoding="utf-8") if LOCKFILE.exists() else ""
        if current != fresh:
            print(f"{LOCKFILE.relative_to(REPO)} is stale — rerun without --check.")
            return 1
        print(f"{LOCKFILE.relative_to(REPO)} matches the pins in npm_deps.py")
        return 0

    LOCKFILE.write_text(fresh, encoding="utf-8")
    print(f"wrote {LOCKFILE.relative_to(REPO)} ({len(fresh)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
