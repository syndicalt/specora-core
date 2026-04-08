"""Pass 2a: Extract entities from Python model files via LLM."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from extractor.models import Confidence, ExtractedEntity, ExtractedField

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a code analysis expert. You read Python model/schema files and extract data models.

For each class/model found, output a JSON array of entities:

```json
[
  {
    "name": "User",
    "description": "A user account",
    "fields": [
      {"name": "email", "type": "email", "required": true, "description": "User email"},
      {"name": "name", "type": "string", "required": true},
      {"name": "role", "type": "string", "enum_values": ["admin", "user", "guest"]},
      {"name": "department_id", "type": "uuid", "reference_entity": "Department", "reference_edge": "BELONGS_TO"}
    ],
    "state_field": "status",
    "state_values": ["active", "inactive", "suspended"]
  }
]
```

Type mapping:
- str/String/VARCHAR/Text → "string" (short) or "text" (long content)
- int/Integer/BigInteger → "integer"
- float/Decimal/Numeric → "number"
- bool/Boolean → "boolean"
- datetime/DateTime/timestamp → "datetime"
- date/Date → "date"
- UUID/uuid → "uuid"
- EmailStr → "email"
- list/List/Array → "array"
- dict/Dict/JSON/JSONB → "object"

Reference detection:
- Fields ending in _id that reference another model → set reference_entity to the target model name
- Set reference_edge to SCREAMING_SNAKE (e.g., BELONGS_TO, CREATED_BY)

State machine detection:
- Enum fields with lifecycle-like values (active/inactive, open/closed, pending/approved) → set state_field and state_values

Only output the JSON array. No other text.
"""


def analyze_python_models(
    file_paths: list[str],
    root: Path,
) -> list[ExtractedEntity]:
    """Analyze Python model files and extract entities.

    Reads each file, sends to LLM, parses the structured response.
    Falls back to basic regex extraction if LLM is unavailable.
    """
    all_entities: list[ExtractedEntity] = []

    # Batch files together if small enough (< 8000 chars total)
    batches = _batch_files(file_paths, root, max_chars=8000)

    for batch_paths, batch_content in batches:
        entities = _extract_via_llm(batch_content, batch_paths)
        if entities is None:
            entities = _extract_via_regex(batch_paths, root)
        all_entities.extend(entities)

    return all_entities


def _batch_files(file_paths: list[str], root: Path, max_chars: int) -> list[tuple[list[str], str]]:
    """Group files into batches that fit within the char limit."""
    batches = []
    current_paths: list[str] = []
    current_content = ""

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        header = f"\n# === {fp} ===\n"
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
    """Use LLM to extract entities from Python code."""
    try:
        from engine.engine import LLMEngine
        engine = LLMEngine.from_env()
    except Exception as e:
        logger.warning("LLM not available: %s", e)
        return None

    prompt = f"Analyze these Python files and extract all data models:\n\n```python\n{content}\n```"

    try:
        response = engine.ask(question=prompt, system=SYSTEM_PROMPT)
    except Exception as e:
        logger.error("LLM request failed: %s", e)
        return None

    return _parse_llm_response(response, source_files)


def _parse_llm_response(response: str, source_files: list[str]) -> Optional[list[ExtractedEntity]]:
    """Parse the LLM's JSON response into ExtractedEntity objects."""
    # Extract JSON from code block or raw response
    match = re.search(r"```(?:json)?\s*\n(.*?)```", response, re.DOTALL)
    raw = match.group(1) if match else response

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse LLM response as JSON")
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
                reference_display=f.get("reference_display", "name"),
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
    """Fallback: basic regex extraction from Python files."""
    entities = []
    class_pattern = re.compile(r"class\s+(\w+)\s*\(.*(?:BaseModel|Base|Model|db\.Model)")

    for fp in file_paths:
        try:
            content = (root / fp).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for match in class_pattern.finditer(content):
            name = match.group(1)
            entities.append(ExtractedEntity(
                name=name,
                source_file=fp,
                fields=[],
                description=f"Extracted from {fp}",
                confidence=Confidence.LOW,
            ))

    return entities
