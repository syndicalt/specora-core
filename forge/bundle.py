"""Copy the source contracts into a generated bundle.

The generated stack mounts `./domains` into the Healer and `HealerPipeline`
defaults to reading contracts from there. Nothing put them there: a freshly
generated bundle contained zero `.contract.yaml` files, so Docker created an
empty host directory and the Healer started with nothing to heal. It would
accept error reports, classify them, and be unable to propose a fix to any
contract — the self-healing loop, which is the reason the sidecar exists,
was inert on every stock deploy.

This lives outside `forge/targets/` on purpose. Generators consume only the IR
— that firewall is the best structural property this codebase has — and
shipping the original YAML is not generation, it is packaging. So it belongs in
the layer that already knows both the contract root and the output directory.

It is also what makes the product's central claim true. "If all your code is
deleted but the contracts survive, you regenerate everything" requires the
contracts to be somewhere the deployment can survive with.
"""

from __future__ import annotations

import shutil
from pathlib import Path

CONTRACT_EXTENSION = ".contract.yaml"

# Where the generated docker-compose.yml expects to find them.
BUNDLE_SUBDIR = "domains"


def copy_contracts(contract_root: Path, output_root: Path) -> list[str]:
    """Copy every contract under *contract_root* into *output_root*/domains.

    The tree layout is preserved, because a contract's directory carries
    meaning the file name does not (`entities/`, `routes/`, `workflows/`), and
    the Healer resolves paths by walking that structure.

    Args:
        contract_root: The directory the domain was compiled from.
        output_root: The generated bundle's root.

    Returns:
        Bundle-relative paths of the contracts written, sorted.

    Raises:
        ValueError: If the destination would land outside *output_root*. A
            contract path is attacker-influenced in the Extractor's case, and a
            copy that escapes the bundle is never intended.
    """
    contract_root = Path(contract_root).resolve()
    destination_root = Path(output_root).resolve() / BUNDLE_SUBDIR

    written: list[str] = []
    for source in sorted(contract_root.rglob(f"*{CONTRACT_EXTENSION}")):
        if not source.is_file():
            continue
        relative = source.resolve().relative_to(contract_root)
        destination = (destination_root / relative).resolve()

        if not destination.is_relative_to(destination_root):
            raise ValueError(
                f"Refusing to copy {source} to {destination}: it resolves "
                f"outside the bundle's {BUNDLE_SUBDIR}/ directory."
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        written.append(str(Path(BUNDLE_SUBDIR) / relative))

    return written
