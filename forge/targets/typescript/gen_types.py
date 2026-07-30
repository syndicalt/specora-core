"""TypeScript type generator — EntityIR -> TypeScript interfaces.

Generates a TypeScript file containing interface definitions for
every entity in the domain. Each entity becomes an exported interface
with typed fields, JSDoc comments, and proper optional/required markers.

Types come from `forge.targets.typemap` and interface names from
`forge.targets.naming`; neither is reimplemented here. The two mappings that
are easy to get wrong, and that the local copy of the table did get wrong:

    decimal  -> string   (a JSON number is a float, which would reintroduce the
                          precision loss `decimal` exists to prevent)
    datetime -> string   (ISO 8601; the wire format is not a Date)
    uuid     -> string

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
from forge.targets.naming import class_name
from forge.targets.typemap import ts_type


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

        # A reference names its target by FQN, and the interface that FQN maps
        # to depends on the target's own domain — not on the referencing
        # entity's — so the mapping is resolved once across the whole build.
        interface_by_fqn = {
            e.fqn: class_name(e.name, e.domain, multi_domain=ir.multi_domain) for e in ir.entities
        }

        interfaces: list[str] = []
        for entity in ir.entities:
            interfaces.append(self._generate_interface(entity, interface_by_fqn, ir.multi_domain))

        content = header + "\n".join(interfaces) + "\n"

        return [
            GeneratedFile(
                path="types.ts",
                content=content,
                provenance=provenance_fqns,
            )
        ]

    def _generate_interface(
        self,
        entity: EntityIR,
        interface_by_fqn: dict[str, str],
        multi_domain: bool,
    ) -> str:
        """Generate a TypeScript interface for a single entity.

        Args:
            entity: The EntityIR to convert.
            interface_by_fqn: Entity FQN -> interface name, for @see links.
            multi_domain: Whether this build spans more than one domain.

        Returns:
            TypeScript interface definition string.
        """
        lines: list[str] = []

        interface_name = class_name(entity.name, entity.domain, multi_domain=multi_domain)
        if entity.description:
            lines.append(f"/** {self._one_line(entity.description)} */")

        lines.append(f"export interface {interface_name} {{")

        for field in entity.fields:
            lines.extend(self._generate_field(field, interface_by_fqn))

        # Add _links for HATEOAS
        lines.append("  /** HATEOAS navigation links */")
        lines.append("  _links?: Record<string, string>;")

        lines.append("}")
        lines.append("")

        return "\n".join(lines)

    def _generate_field(self, field: FieldIR, interface_by_fqn: dict[str, str]) -> list[str]:
        """Generate TypeScript field lines with JSDoc.

        Args:
            field: The FieldIR to convert.
            interface_by_fqn: Entity FQN -> interface name, for @see links.

        Returns:
            List of TypeScript lines (JSDoc + field definition).
        """
        lines: list[str] = []

        # JSDoc comment
        doc_parts: list[str] = []
        if field.description:
            doc_parts.append(self._one_line(field.description))
        if field.reference:
            target = field.reference.target_entity
            doc_parts.append(f"@see {interface_by_fqn.get(target, target)}")
        if field.computed:
            doc_parts.append(f"@computed {field.computed}")
        if field.immutable:
            doc_parts.append("@readonly")

        if doc_parts:
            lines.append(f"  /** {' | '.join(doc_parts)} */")

        # Field name and type
        resolved = self._resolve_type(field)
        optional = "?" if not field.required else ""
        lines.append(f"  {field.name}{optional}: {resolved};")

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
            return f"Array<{ts_type(field.items_type)}>"

        return ts_type(field.type)

    def _one_line(self, text: str) -> str:
        """Flatten text for a single-line JSDoc comment.

        Contract descriptions are often YAML folded scalars, so they carry
        newlines that would break out of `/** ... */`. A literal `*/` in the
        text would close the comment early and leave prose as source.
        """
        return " ".join(text.split()).replace("*/", "*\\/")
