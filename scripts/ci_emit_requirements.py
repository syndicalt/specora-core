#!/usr/bin/env python3
"""Emit the union of requirements Specora generates for user applications.

Generated apps inherit whatever `requirements.txt` the Docker generator emits,
and that set is not audited by anything that audits *this* project. It shipped
`python-jose` (unmaintained, algorithm-confusion CVEs) and `passlib` (no release
since 2020, broken against bcrypt 5.x) with no upper bounds — a supply-chain
problem in every generated application, invisible to a scan of this repo.

CI feeds this file to pip-audit so a vulnerable generated dependency fails the
build here rather than in a user's deployment.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--repo", type=Path, default=REPO)
    args = ap.parse_args()

    sys.path.insert(0, str(args.repo))
    from forge.ir.compiler import Compiler
    from forge.targets.fastapi_prod.generator import DockerGenerator

    requirements: set[str] = set()
    domain_dirs = sorted(
        d
        for d in (args.repo / "domains").iterdir()
        if d.is_dir() and any(d.rglob("*.contract.yaml"))
    )

    for domain_dir in domain_dirs:
        ir = Compiler(contract_root=domain_dir).compile()
        for f in DockerGenerator().generate(ir):
            if not Path(f.path).name.startswith("requirements"):
                continue
            for line in f.content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    requirements.add(line)

    if not requirements:
        print("no generated requirements found", file=sys.stderr)
        return 1

    args.out.write_text("\n".join(sorted(requirements)) + "\n", encoding="utf-8")
    print(f"wrote {len(requirements)} requirements from {len(domain_dirs)} domains")
    return 0


if __name__ == "__main__":
    sys.exit(main())
