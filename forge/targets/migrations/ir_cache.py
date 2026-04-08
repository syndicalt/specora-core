"""Save and load DomainIR snapshots for migration diffing."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from forge.ir.model import DomainIR

logger = logging.getLogger(__name__)

CACHE_FILENAME = "domain_ir.json"


def save_ir_cache(ir: DomainIR, cache_dir: Path) -> None:
    """Save a DomainIR snapshot to the cache directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / CACHE_FILENAME
    data = ir.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.info("Saved IR cache to %s", path)


def load_ir_cache(cache_dir: Path) -> Optional[DomainIR]:
    """Load a DomainIR snapshot from the cache directory.

    Returns None if no cache exists.
    """
    path = cache_dir / CACHE_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DomainIR(**data)
    except Exception as e:
        logger.warning("Failed to load IR cache: %s", e)
        return None
