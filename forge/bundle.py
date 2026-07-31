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

import yaml

CONTRACT_EXTENSION = ".contract.yaml"

# Where the generated docker-compose.yml expects to find them.
BUNDLE_SUBDIR = "domains"


def copy_contracts(contract_root: Path, output_root: Path) -> list[str]:
    """Copy every contract under *contract_root* into *output_root*/domains.

    The destination is `domains/<declared domain>/<kind dir>/<file>`, taken from
    the contract's own `metadata.domain` rather than from where the file
    happened to sit. That matters because `HealerPipeline._find_contract_path`
    reconstructs a path from the FQN — `domains_root / domain / subdir /
    name.contract.yaml` — so a bundle laid out any other way is one the Healer
    can load but cannot locate.

    An earlier version mirrored the source tree relative to `contract_root`,
    which for the usual `domains/saas_platform` root produced
    `domains/entities/...` and dropped the domain segment entirely. The failure
    was quiet and asymmetric: `load_all_contracts` rglobs, so the Healer
    reported all 39 contracts loaded, while every attempt to actually repair one
    resolved to a path that did not exist. "Finds them" and "can fix them" had
    different answers.

    The kind directory (`entities/`, `routes/`, `workflows/`) is preserved from
    the source, since that is the convention the resolver walks.

    Args:
        contract_root: The directory the domain was compiled from.
        output_root: The generated bundle's root.

    Returns:
        Bundle-relative paths of the contracts written, sorted.

    Raises:
        ValueError: If a destination would land outside the bundle. A contract
            path is attacker-influenced in the Extractor's case, and a copy that
            escapes the bundle is never intended.
    """
    contract_root = Path(contract_root).resolve()
    destination_root = Path(output_root).resolve() / BUNDLE_SUBDIR

    written: list[str] = []
    for source in sorted(contract_root.rglob(f"*{CONTRACT_EXTENSION}")):
        if not source.is_file():
            continue

        domain = _declared_domain(source)
        if domain is None:
            # Unreadable or domain-less: the compiler has already validated
            # everything reachable from this root, so this is a stray file
            # rather than a contract in the build. Skipping it silently would
            # be the pattern this whole effort removes, so it is surfaced.
            raise ValueError(
                f"{source} has no readable metadata.domain, so it cannot be "
                f"placed in the bundle where the Healer will look for it."
            )

        relative = Path(domain) / source.parent.name / source.name
        destination = (destination_root / relative).resolve()

        if not destination.is_relative_to(destination_root):
            raise ValueError(
                f"Refusing to copy {source} to {destination}: it resolves "
                f"outside the bundle's {BUNDLE_SUBDIR}/ directory."
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        written.append(str(Path(BUNDLE_SUBDIR) / relative))

    return sorted(written)


def _declared_domain(source: Path) -> str | None:
    """Read `metadata.domain` from a contract file, or None if unreadable."""
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    domain = data.get("metadata", {}).get("domain") if isinstance(
        data.get("metadata"), dict
    ) else None
    return domain if isinstance(domain, str) and domain else None
