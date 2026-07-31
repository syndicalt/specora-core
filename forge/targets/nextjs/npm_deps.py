"""The generated frontend's dependency set, and the lockfile that pins it.

A generated application is supposed to be a function of its contracts. It was
not: `package.json` asked for `"next": "^15.0.0"` and shipped no lockfile, so
the `npm install` inside `Dockerfile.frontend` re-resolved the whole graph on
every build. Two builds of identical contracts could ship different code, and
the only thing standing between a compromised transitive package and a
production image was whichever version happened to be latest that morning.

Four things fix it, and all four are needed — none is sufficient alone:

1.  **Exact versions, not caret ranges** (`_DEPENDENCIES` below). A range is a
    request for "whatever is newest"; that is the opposite of a specification.
    On its own this pins only the seven direct dependencies out of 143 packages.

2.  **A committed lockfile** (`frontend_lock.json`). It is the other 136. Every
    entry carries a Subresource Integrity hash of the exact tarball, which is
    what makes the build verifiable rather than merely repeatable.

3.  **`npm ci`, not `npm install`** in the Dockerfile. `npm install` treats the
    lockfile as a suggestion and rewrites it when it disagrees; `npm ci`
    installs the lockfile exactly, verifies every integrity hash, and *fails*
    when there is no lockfile or when it has drifted from package.json. The
    hard failure is the point — a build that cannot be reproduced should stop,
    not silently produce a different image.

4.  **`--ignore-scripts`** on that install. Otherwise `npm ci` executes
    lifecycle scripts from any of 143 packages, as root, with network access,
    during the image build. Next.js does not need any of them: its native
    binaries and sharp's arrive as prebuilt per-platform optional dependencies,
    already covered by the lockfile's hashes.

To change or update a dependency, edit `_DEPENDENCIES` / `_DEV_DEPENDENCIES`
here and run `python scripts/regen_frontend_lock.py`. Editing one without the
other is caught at generation time by `load_lockfile()`, not at `docker build`
time by a puzzling `npm ci` error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forge.targets.base import GenerationError

# Resolved once, deliberately, and recorded. Read `scripts/regen_frontend_lock.py`
# before changing anything here.
_DEPENDENCIES: dict[str, str] = {
    "next": "15.5.22",
    "react": "18.3.1",
    "react-dom": "18.3.1",
    # lucide-react was listed but never imported. An unused dependency is a
    # supply-chain surface and an install cost for nothing.
    "clsx": "2.1.1",
    "tailwind-merge": "2.6.1",
    "class-variance-authority": "0.7.1",
}

_DEV_DEPENDENCIES: dict[str, str] = {
    "typescript": "5.9.3",
    "@types/react": "18.3.31",
    "@types/react-dom": "18.3.7",
    "@types/node": "22.20.1",
    "tailwindcss": "3.4.19",
    "postcss": "8.5.25",
    "autoprefixer": "10.5.4",
}

FRONTEND_VERSION = "0.2.0"

_LOCKFILE = Path(__file__).with_name("frontend_lock.json")


def dependencies() -> dict[str, str]:
    return dict(_DEPENDENCIES)


def dev_dependencies() -> dict[str, str]:
    return dict(_DEV_DEPENDENCIES)


def load_lockfile(project_name: str) -> dict[str, Any]:
    """Return the committed lockfile, retargeted to `project_name` and verified.

    Args:
        project_name: The `name` the emitted package.json will carry. npm
            tolerates a mismatch here, but a lockfile that names a different
            project is a trap for the next person to read it.

    Raises:
        GenerationError: If the lockfile and the tables above disagree, or if
            any package in it lacks an integrity hash. Emitting a lockfile that
            `npm ci` will reject turns a build error into a deploy-time
            surprise, which rule 5 of docs/CODEGEN_CONTRACT.md exists to stop.
    """
    lock = json.loads(_LOCKFILE.read_text(encoding="utf-8"))
    _verify(lock)

    lock["name"] = project_name
    lock["version"] = FRONTEND_VERSION
    root = lock["packages"][""]
    root["name"] = project_name
    root["version"] = FRONTEND_VERSION
    return lock


def _verify(lock: dict[str, Any]) -> None:
    packages = lock.get("packages") or {}
    root = packages.get("")
    if root is None:
        raise GenerationError(
            f"{_LOCKFILE.name}: no root package entry. Regenerate it with "
            f"scripts/regen_frontend_lock.py."
        )

    for group, expected in (
        ("dependencies", _DEPENDENCIES),
        ("devDependencies", _DEV_DEPENDENCIES),
    ):
        declared = root.get(group) or {}
        if set(declared) != set(expected):
            missing = sorted(set(expected) - set(declared))
            extra = sorted(set(declared) - set(expected))
            raise GenerationError(
                f"{_LOCKFILE.name} is out of step with npm_deps.{group}: "
                f"missing {missing or 'nothing'}, unexpected {extra or 'nothing'}. "
                f"Run scripts/regen_frontend_lock.py."
            )
        # The root entry repeats the *range* from package.json; the installed
        # version lives under node_modules/<name>. Both must equal the pin, or
        # `npm ci` refuses the tree it was handed.
        for name, version in expected.items():
            if declared[name] != version:
                raise GenerationError(
                    f"{_LOCKFILE.name}: root {group}[{name!r}] is "
                    f"{declared[name]!r}, but npm_deps pins {version!r}. "
                    f"Run scripts/regen_frontend_lock.py."
                )
            installed = packages.get(f"node_modules/{name}")
            if installed is None:
                raise GenerationError(
                    f"{_LOCKFILE.name}: {name} is declared but has no resolved "
                    f"entry. Run scripts/regen_frontend_lock.py."
                )
            if installed.get("version") != version:
                raise GenerationError(
                    f"{_LOCKFILE.name}: {name} resolves to "
                    f"{installed.get('version')!r}, but npm_deps pins {version!r}. "
                    f"Run scripts/regen_frontend_lock.py."
                )

    # Pinning a version without a hash still trusts the registry to serve the
    # same bytes under that version forever. The hash is the part `npm ci`
    # actually verifies, so a lockfile missing one is not a lockfile.
    unhashed = sorted(k for k, v in packages.items() if k and not v.get("integrity"))
    if unhashed:
        raise GenerationError(
            f"{_LOCKFILE.name}: {len(unhashed)} package(s) carry no integrity "
            f"hash ({unhashed[:3]}). npm ci would install them unverified."
        )
