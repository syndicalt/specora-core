"""Integration test — verify emitted contracts pass the Forge compiler.

This is the critical end-to-end test: Factory emitters produce contracts,
then the Forge compiler validates, resolves, and compiles them into IR.
If this test passes, the Factory → Forge loop works.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from factory.emitters.entity_emitter import emit_entity
from factory.emitters.workflow_emitter import emit_workflow
from factory.emitters.route_emitter import emit_route
from factory.emitters.page_emitter import emit_page
from forge.ir.compiler import Compiler


def test_emitted_contracts_compile():
    """The full Factory → Forge loop: emit contracts, then compile them."""
    with tempfile.TemporaryDirectory() as tmpdir:
        domain_dir = Path(tmpdir)

        # Emit a complete domain
        entity_data = {
            "description": "A test widget",
            "fields": {
                "name": {"type": "string", "required": True, "description": "Widget name"},
                "count": {"type": "integer", "description": "A counter"},
                "active": {"type": "boolean", "default": True},
            },
            "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
            "state_machine": "workflow/test_domain/widget_lifecycle",
        }

        workflow_data = {
            "initial": "draft",
            "states": {
                "draft": {"label": "Draft", "category": "open"},
                "active": {"label": "Active", "category": "open"},
                "archived": {"label": "Archived", "category": "closed"},
            },
            "transitions": {
                "draft": ["active"],
                "active": ["archived"],
                "archived": ["active"],
            },
            "description": "Widget lifecycle",
        }

        # Write contracts
        files = {
            "entities/widget.contract.yaml": emit_entity("widget", "test_domain", entity_data),
            "workflows/widget_lifecycle.contract.yaml": emit_workflow(
                "widget_lifecycle", "test_domain", workflow_data
            ),
            "routes/widgets.contract.yaml": emit_route(
                "widgets", "test_domain", "entity/test_domain/widget",
                "workflow/test_domain/widget_lifecycle",
            ),
            "pages/widgets.contract.yaml": emit_page(
                "widgets", "test_domain", "entity/test_domain/widget",
                ["name", "count", "active", "state"],
            ),
        }

        for rel_path, content in files.items():
            file_path = domain_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        # Compile — this should succeed
        compiler = Compiler(contract_root=domain_dir)
        ir = compiler.compile()

        # Verify IR structure
        assert ir.domain == "test_domain"
        assert len(ir.entities) == 1
        assert ir.entities[0].name == "widget"
        # Widget has 3 own fields + 4 mixin fields (id, number, created_at, updated_at) + state from workflow
        assert len(ir.entities[0].fields) >= 7
        assert len(ir.workflows) >= 1
        assert len(ir.routes) == 1
        assert len(ir.pages) == 1

        # Verify mixin expansion worked
        field_names = {f.name for f in ir.entities[0].fields}
        assert "name" in field_names
        assert "created_at" in field_names  # from timestamped mixin
        assert "id" in field_names  # from identifiable mixin

        # Verify state machine binding
        assert ir.entities[0].state_machine is not None
        assert ir.entities[0].state_machine.initial == "draft"


def test_multi_entity_domain_compiles():
    """Test a domain with multiple entities and cross-references."""
    with tempfile.TemporaryDirectory() as tmpdir:
        domain_dir = Path(tmpdir)

        # Owner entity
        owner_data = {
            "description": "A pet owner",
            "fields": {
                "name": {"type": "string", "required": True},
                "email": {"type": "email"},
            },
            "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
        }

        # Pet entity with reference to owner
        pet_data = {
            "description": "A pet",
            "fields": {
                "name": {"type": "string", "required": True},
                "species": {"type": "string", "enum": ["dog", "cat", "bird"]},
                "owner_id": {
                    "type": "string",
                    "references": {
                        "entity": "entity/pets/owner",
                        "display": "name",
                    },
                },
            },
            "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
        }

        files = {
            "entities/owner.contract.yaml": emit_entity("owner", "pets", owner_data),
            "entities/pet.contract.yaml": emit_entity("pet", "pets", pet_data),
            "routes/owners.contract.yaml": emit_route("owners", "pets", "entity/pets/owner"),
            "routes/pets.contract.yaml": emit_route("pets", "pets", "entity/pets/pet"),
            "pages/owners.contract.yaml": emit_page("owners", "pets", "entity/pets/owner", ["name", "email"]),
            "pages/pets.contract.yaml": emit_page("pets", "pets", "entity/pets/pet", ["name", "species", "owner_id"]),
        }

        for rel_path, content in files.items():
            file_path = domain_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        compiler = Compiler(contract_root=domain_dir)
        ir = compiler.compile()

        assert ir.domain == "pets"
        assert len(ir.entities) == 2
        assert len(ir.routes) == 2
        assert len(ir.pages) == 2

        # Verify reference was preserved
        pet = next(e for e in ir.entities if e.name == "pet")
        owner_field = next(f for f in pet.fields if f.name == "owner_id")
        assert owner_field.reference is not None
        assert owner_field.reference.target_entity == "entity/pets/owner"
