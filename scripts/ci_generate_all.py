#!/usr/bin/env python3
"""Generate every shipped domain, so CI can assert the output is actually valid.

This repository's unit suite tests that generators *return files*. It never
tested that those files were valid Python, that the schema applied to a real
database, or that the resulting application could import. All three were broken
simultaneously and the suite stayed green.

Exits non-zero if any domain fails to compile or generate.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def domains(root: Path) -> list[Path]:
    """Every directory under domains/ that holds at least one contract."""
    return sorted(
        d
        for d in (root / "domains").iterdir()
        if d.is_dir() and any(d.rglob("*.contract.yaml"))
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path, help="output root")
    ap.add_argument("--repo", type=Path, default=REPO)
    args = ap.parse_args()

    sys.path.insert(0, str(args.repo))
    from forge.ir.compiler import Compiler
    from forge.targets.fastapi_prod.generator import (
        DockerGenerator,
        FastAPIProductionGenerator,
        TestSuiteGenerator,
    )
    from forge.targets.nextjs.generator import NextJSGenerator
    from forge.targets.postgres.gen_ddl import PostgresGenerator
    from forge.targets.typescript.gen_types import TypeScriptGenerator

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    # Every target the `prod` preset in forge/cli/main.py builds. The frontend
    # belongs here: leaving NextJSGenerator out meant the generated React was
    # never produced under CI, so nothing checked that it compiled, that its
    # output paths were unique, or that it agreed with the API it calls — while
    # the pipeline still reported a clean run.
    generators = [
        FastAPIProductionGenerator(),
        PostgresGenerator(),
        DockerGenerator(),
        TypeScriptGenerator(),
        TestSuiteGenerator(),
        NextJSGenerator(),
    ]

    failures: list[str] = []
    for domain_dir in domains(args.repo):
        name = domain_dir.name
        target = args.out / name
        try:
            ir = Compiler(contract_root=domain_dir).compile()
            count = 0
            for gen in generators:
                for f in gen.generate(ir):
                    p = target / f.path
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(f.content, encoding="utf-8")
                    count += 1
            print(f"  ok    {name}: {count} files")
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")

    if failures:
        print(f"\n{len(failures)} domain(s) failed to generate", file=sys.stderr)
        return 1
    print(f"\ngenerated {len(domains(args.repo))} domains into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
