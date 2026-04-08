# extractor/analyzers/typescript_types.py
"""Pass 2b: Extract entities from TypeScript type files via LLM."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from extractor.models import Confidence, ExtractedEntity, ExtractedField

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a code analysis expert. You read TypeScript/JavaScript type files and extract data models.

For each interface/type/class found, output a JSON array:

```json
[
  {
    "name": "User",
    "description": "A user account",
    "fields": [
      {"name": "email", "type": "email", "required": true},
      {"name": "name", "type": "string", "required": true},
      {"name": "role", "type": "string", "enum_values": ["admin", "user"]},
      {"name": "departmentId", "type": "uuid", "reference_entity": "Department", "reference_edge": "BELONGS_TO"}
    ],
    "state_field": "status",
    "state_values": ["active", "inactive"]
  }
]
```

Type mapping:
- string → "string" or "text" (if long content)
- number → "number" or "integer" (if whole numbers)
- boolean → "boolean"
- Date → "datetime"
- string[] / Array<string> → "array"
- Record<string, any> / object → "object"
- Fields with "email" in name → "email"
- Fields with "id"/"Id"/"ID" suffix → "uuid"

Only output the JSON array. No other text.
"""


def analyze_typescript_types(
    file_paths: list[str],
    root: Path,
) -> list[ExtractedEntity]:
    """Analyze TypeScript type files and extract entities."""
    all_entities: list[ExtractedEntity] = []

    batches = _batch_files(file_paths, root, max_chars=8000)

    for batch_paths, batch_content in batches:
        entities = _extract_via_llm(batch_content, batch_paths)
        if entities is None:
            entities = _extract_via_regex(batch_paths, root)
        all_entities.extend(entities)

    return all_entities


def _batch_files(file_paths: list[str], root: Path, max_chars: int) -> list[tuple[list[str], str]]:
    batches = []
    current_paths: list[str] = []
    current_content = ""

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        header = f"\n// === {fp} ===\n"
        if len(current_content) + len(header) + len(content) > max_chars and current_paths:
            batches.append((current_paths, current_content))
            current_paths = []
            current_content = ""

        current_paths.append(fp)
        current_content += header + content

    if current_paths:
        batches.append((current_paths, current_content))

    return batches


def _extract_via_llm(content: str, source_files: list[str]) -> Optional[list[ExtractedEntity]]:
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception:
        return None

    prompt = f"Analyze these TypeScript files and extract all data models:\n\n```typescript\n{content}\n```"

    try:
        response = engine.ask(question=prompt, system=SYSTEM_PROMPT)
    except Exception:
        return None

    return _parse_response(response, source_files)


def _parse_response(response: str, source_files: list[str]) -> Optional[list[ExtractedEntity]]:
    match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
    raw = match.group(1) if match else response

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list):
        data = [data]

    entities = []
    source = source_files[0] if source_files else "unknown"

    for item in data:
        if not isinstance(item, dict) or "name" not in item:
            continue

        fields = []
        for f in item.get("fields", []):
            fields.append(ExtractedField(
                name=f.get("name", ""),
                type=f.get("type", "string"),
                required=f.get("required", False),
                description=f.get("description", ""),
                enum_values=f.get("enum_values", []),
                reference_entity=f.get("reference_entity", ""),
                reference_edge=f.get("reference_edge", ""),
            ))

        entities.append(ExtractedEntity(
            name=item["name"],
            source_file=source,
            fields=fields,
            description=item.get("description", ""),
            confidence=Confidence.HIGH,
            state_field=item.get("state_field", ""),
            state_values=item.get("state_values", []),
        ))

    return entities


def _extract_via_regex(file_paths: list[str], root: Path) -> list[ExtractedEntity]:
    entities = []
    patterns = [
        re.compile(r"(?:export\s+)?interface\s+(\w+)"),
        re.compile(r"(?:export\s+)?type\s+(\w+)\s*="),
        re.compile(r"(?:export\s+)?class\s+(\w+)"),
    ]

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for pattern in patterns:
            for match in pattern.finditer(content):
                name = match.group(1)
                if name[0].isupper():
                    entities.append(ExtractedEntity(
                        name=name,
                        source_file=fp,
                        fields=[],
                        confidence=Confidence.LOW,
                    ))

    return entities
