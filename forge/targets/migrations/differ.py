"""Compare old vs new EntityIR to detect schema changes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from forge.ir.model import EntityIR, FieldIR


@dataclass
class SchemaChange:
    """A single schema change detected between IR versions."""
    change_type: str          # create_table, drop_table, add_column, drop_column, alter_type, set_not_null, drop_not_null, set_default, drop_default, add_index
    table_name: str
    field_name: str = ""
    old_value: Any = None
    new_value: Any = None
    entity: Optional[EntityIR] = field(default=None, repr=False)
    field_ir: Optional[FieldIR] = field(default=None, repr=False)
    destructive: bool = False


def diff_entities(
    old_entities: list[EntityIR],
    new_entities: list[EntityIR],
) -> list[SchemaChange]:
    """Compare old and new entity lists, return schema changes.

    Detects: new tables, dropped tables, added columns, dropped columns,
    type changes, nullability changes, default changes.
    """
    changes: list[SchemaChange] = []

    old_by_fqn = {e.fqn: e for e in old_entities}
    new_by_fqn = {e.fqn: e for e in new_entities}

    # New entities (tables to create)
    for fqn, entity in new_by_fqn.items():
        if fqn not in old_by_fqn:
            changes.append(SchemaChange(
                change_type="create_table",
                table_name=entity.table_name,
                entity=entity,
            ))

    # Removed entities (tables to drop)
    for fqn, entity in old_by_fqn.items():
        if fqn not in new_by_fqn:
            changes.append(SchemaChange(
                change_type="drop_table",
                table_name=entity.table_name,
                destructive=True,
            ))

    # Changed entities (columns to alter)
    for fqn in old_by_fqn:
        if fqn in new_by_fqn:
            changes.extend(_diff_entity_fields(old_by_fqn[fqn], new_by_fqn[fqn]))

    return changes


def _diff_entity_fields(old: EntityIR, new: EntityIR) -> list[SchemaChange]:
    """Compare fields between two versions of the same entity."""
    changes: list[SchemaChange] = []
    table = new.table_name

    old_fields = {f.name: f for f in old.fields}
    new_fields = {f.name: f for f in new.fields}

    # Added fields
    for name, f in new_fields.items():
        if name not in old_fields:
            changes.append(SchemaChange(
                change_type="add_column",
                table_name=table,
                field_name=name,
                field_ir=f,
            ))

    # Removed fields
    for name, f in old_fields.items():
        if name not in new_fields:
            changes.append(SchemaChange(
                change_type="drop_column",
                table_name=table,
                field_name=name,
                destructive=True,
            ))

    # Modified fields
    for name in old_fields:
        if name in new_fields:
            old_f = old_fields[name]
            new_f = new_fields[name]

            # Type change
            if old_f.type != new_f.type:
                changes.append(SchemaChange(
                    change_type="alter_type",
                    table_name=table,
                    field_name=name,
                    old_value=old_f.type,
                    new_value=new_f.type,
                    field_ir=new_f,
                ))

            # Nullability change
            if not old_f.required and new_f.required:
                changes.append(SchemaChange(
                    change_type="set_not_null",
                    table_name=table,
                    field_name=name,
                ))
            elif old_f.required and not new_f.required:
                changes.append(SchemaChange(
                    change_type="drop_not_null",
                    table_name=table,
                    field_name=name,
                ))

            # Default change
            if old_f.default != new_f.default:
                if new_f.default is not None and new_f.default != "":
                    changes.append(SchemaChange(
                        change_type="set_default",
                        table_name=table,
                        field_name=name,
                        new_value=new_f.default,
                    ))
                elif old_f.default is not None and old_f.default != "":
                    changes.append(SchemaChange(
                        change_type="drop_default",
                        table_name=table,
                        field_name=name,
                    ))

    return changes
