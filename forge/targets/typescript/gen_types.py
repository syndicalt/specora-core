"""TypeScript type generator — EntityIR -> TypeScript interfaces.

Generates a TypeScript file containing interface definitions for
every entity in the domain. Each entity becomes an exported interface
with typed fields, JSDoc comments, and proper optional/required markers.

Type mapping (IR -> TypeScript):
    string   -> string
    integer  -> number
    number   -> number
    boolean  -> boolean
    text     -> string
    array    -> Array<T>  (T from items_type, default: unknown)
    object   -> Record<string, unknown>
    datetime -> string    (ISO 8601)
    date     -> string    (ISO 8601 date)
    uuid     -> string
    email    -> string

Reference fields get a JSDoc @see annotation pointing to the
referenced entity interface.

Usage:
    from forge.targets.typescript.gen_types import TypeScriptGenerator

    gen = TypeScriptGenerator()
    files = gen.generate(ir)
    # -> [GeneratedFile(path="types.ts", content="...")]
"""

from __future__ import annotations

from forge.ir.model import DomainIR, EntityIR, FieldIR
from forge.targets.base import BaseGenerator, GeneratedFile, provenance_header

# IR type -> TypeScript type
TYPE_MAP: dict[str, str] = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "text": "string",
    "array": "Array<unknown>",
    "object": "Record<string, unknown>",
    "datetime": "string",
    "date": "string",
    "uuid": "string",
    "email": "string",
}

# IR items_type -> TypeScript array element type
ITEMS_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "object": "Record<string, unknown>",
}


class TypeScriptGenerator(BaseGenerator):
    """Generates TypeScript interfaces from entity definitions."""

    def name(self) -> str:
        return "typescript"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        """Generate a single types.ts file with all entity interfaces.

        Args:
            ir: The compiled DomainIR.

        Returns:
            List containing one GeneratedFile (types.ts).
        """
        if not ir.entities:
            return []

        provenance_fqns = ", ".join(e.fqn for e in ir.entities)
        header = provenance_header(
            "typescript",
            provenance_fqns,
            f"TypeScript interfaces for the {ir.domain} domain",
        )

        interfaces: list[str] = []
        for entity in ir.entities:
            interfaces.append(self._generate_interface(entity))

        content = header + "\n".join(interfaces) + "\n"

        return [
            GeneratedFile(
                path="types.ts",
                content=content,
                provenance=provenance_fqns,
            )
        ]

    def _generate_interface(self, entity: EntityIR) -> str:
        """Generate a TypeScript interface for a single entity.

        Args:
            entity: The EntityIR to convert.

        Returns:
            TypeScript interface definition string.
        """
        lines: list[str] = []

        # Interface JSDoc
        interface_name = self._to_interface_name(entity.name)
        if entity.description:
            lines.append(f"/** {entity.description} */")

        lines.append(f"export interface {interface_name} {{")

        for field in entity.fields:
            lines.extend(self._generate_field(field))

        # Add _links for HATEOAS
        lines.append("  /** HATEOAS navigation links */")
        lines.append("  _links?: Record<string, string>;")

        lines.append("}")
        lines.append("")

        return "\n".join(lines)

    def _generate_field(self, field: FieldIR) -> list[str]:
        """Generate TypeScript field lines with JSDoc.

        Args:
            field: The FieldIR to convert.

        Returns:
            List of TypeScript lines (JSDoc + field definition).
        """
        lines: list[str] = []

        # JSDoc comment
        doc_parts: list[str] = []
        if field.description:
            doc_parts.append(field.description)
        if field.reference:
            doc_parts.append(f"@see {self._to_interface_name(field.reference.target_entity.split('/')[-1])}")
        if field.computed:
            doc_parts.append(f"@computed {field.computed}")
        if field.immutable:
            doc_parts.append("@readonly")

        if doc_parts:
            lines.append(f"  /** {' | '.join(doc_parts)} */")

        # Field name and type
        ts_type = self._resolve_type(field)
        optional = "?" if not field.required else ""
        lines.append(f"  {field.name}{optional}: {ts_type};")

        return lines

    def _resolve_type(self, field: FieldIR) -> str:
        """Resolve the TypeScript type for a field.

        Handles enums, arrays with item types, and basic type mapping.
        """
        # Enum -> union type
        if field.enum_values:
            literals = " | ".join(f'"{v}"' for v in field.enum_values)
            return literals

        # Array with known item type
        if field.type == "array" and field.items_type:
            item_ts = ITEMS_TYPE_MAP.get(field.items_type, "unknown")
            return f"Array<{item_ts}>"

        return TYPE_MAP.get(field.type, "unknown")

    def _to_interface_name(self, name: str) -> str:
        """Convert a snake_case entity name to PascalCase interface name.

        Examples:
            "incident"        -> "Incident"
            "assignment_group" -> "AssignmentGroup"
            "knowledge_article" -> "KnowledgeArticle"
        """
        return "".join(part.capitalize() for part in name.split("_"))
