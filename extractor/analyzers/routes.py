# extractor/analyzers/routes.py
"""Pass 2c: Extract API routes from any framework via LLM."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from extractor.models import Confidence, ExtractedRoute

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a code analysis expert. You read route/controller/view files and extract API endpoints.

For each endpoint, output a JSON array:

```json
[
  {"path": "/api/users", "method": "GET", "entity_name": "user", "summary": "List all users"},
  {"path": "/api/users", "method": "POST", "entity_name": "user", "summary": "Create a user"},
  {"path": "/api/users/{id}", "method": "GET", "entity_name": "user", "summary": "Get user by ID"},
  {"path": "/api/users/{id}", "method": "PATCH", "entity_name": "user", "summary": "Update a user"},
  {"path": "/api/users/{id}", "method": "DELETE", "entity_name": "user", "summary": "Delete a user"}
]
```

entity_name should be the singular snake_case name of the resource (user, not users).
Only output the JSON array. No other text.
"""


def analyze_routes(
    file_paths: list[str],
    root: Path,
) -> list[ExtractedRoute]:
    """Analyze route files and extract API endpoints."""
    all_routes: list[ExtractedRoute] = []

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        routes = _extract_via_llm(content, fp)
        if routes is None:
            routes = _extract_via_regex(content, fp)
        all_routes.extend(routes)

    return all_routes


def _extract_via_llm(content: str, source_file: str) -> Optional[list[ExtractedRoute]]:
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception:
        return None

    prompt = f"Extract all API endpoints from this file:\n\n```\n{content[:6000]}\n```"

    try:
        response = engine.ask(question=prompt, system=SYSTEM_PROMPT)
    except Exception:
        return None

    match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
    raw = match.group(1) if match else response

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list):
        data = [data]

    routes = []
    for item in data:
        if not isinstance(item, dict):
            continue
        routes.append(ExtractedRoute(
            path=item.get("path", ""),
            method=item.get("method", "GET").upper(),
            entity_name=item.get("entity_name", ""),
            source_file=source_file,
            summary=item.get("summary", ""),
            confidence=Confidence.HIGH,
        ))

    return routes


def _extract_via_regex(content: str, source_file: str) -> list[ExtractedRoute]:
    """Fallback: basic regex extraction of routes."""
    routes = []
    patterns = [
        re.compile(r'@(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']'),
        re.compile(r'@api_view\s*\(\s*\[([^\]]+)\]\s*\)'),
        re.compile(r'\.(?:get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']'),
    ]

    for pattern in patterns:
        for match in pattern.finditer(content):
            if len(match.groups()) == 2:
                method, path = match.group(1).upper(), match.group(2)
            else:
                method, path = "GET", match.group(1)

            entity = path.strip("/").split("/")[0].rstrip("s") if "/" in path else ""
            routes.append(ExtractedRoute(
                path=path,
                method=method,
                entity_name=entity,
                source_file=source_file,
                confidence=Confidence.LOW,
            ))

    return routes
