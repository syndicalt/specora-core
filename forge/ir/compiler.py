"""IR Compiler — transforms validated contracts into the Intermediate Representation.

The compiler is the central orchestrator of the Forge pipeline:

    1. Load all contracts (loader)
    2. Validate against meta-schemas (validator)
    3. Build dependency graph (graph)
    4. Compile each contract into IR nodes (this module)
    5. Run IR passes (mixin expansion, reference resolution, etc.)
    6. Return the completed DomainIR

The compiler integrates with the diff tracking system: when contracts
are loaded, if a manifest exists from a previous compilation, diffs
are automatically computed and stored.

Usage:
    from forge.ir.compiler import Compiler

    compiler = Compiler(contract_root=Path("domains/library"))
    ir = compiler.compile()
    print(ir.summary())
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from forge.diff.store import DiffStore
from forge.ir.model import (
    AgentIR,
    DomainIR,
    EndpointIR,
    EntityIR,
    FieldIR,
    GuardIR,
    InfraIR,
    MixinIR,
    PageIR,
    ReferenceIR,
    RouteIR,
    StateIR,
    StateMachineIR,
)
from forge.ir.passes import run_all_passes
from forge.parser.graph import DependencyGraph, build_dependency_graph
from forge.parser.loader import ContractLoadError, load_all_contracts
from forge.parser.validator import validate_all

logger = logging.getLogger(__name__)


class CompilationError(Exception):
    """Raised when compilation fails due to validation or resolution errors."""

    def __init__(self, errors: list):
        self.errors = errors
        messages = [str(e) if isinstance(e, str) else getattr(e, "message", str(e)) for e in errors]
        super().__init__(f"Compilation failed with {len(errors)} error(s):\n" + "\n".join(messages))


class Compiler:
    """The Forge compiler — contracts in, DomainIR out.

    Attributes:
        contract_root: Path to the directory containing domain contracts.
        diff_store: Optional diff store for tracking contract changes.
        include_stdlib: Whether to include stdlib contracts.
    """

    def __init__(
        self,
        contract_root: Path,
        diff_store: Optional[DiffStore] = None,
        include_stdlib: bool = True,
    ):
        self.contract_root = Path(contract_root)
        self.diff_store = diff_store
        self.include_stdlib = include_stdlib

    def compile(self) -> DomainIR:
        """Run the full compilation pipeline.

        Returns:
            A complete DomainIR ready for generators.

        Raises:
            CompilationError: If validation or resolution fails.
            ContractLoadError: If contracts can't be loaded.
        """
        # 1. Load
        logger.info("Loading contracts from %s", self.contract_root)
        contracts = load_all_contracts(self.contract_root, include_stdlib=self.include_stdlib)
        logger.info("Loaded %d contracts", len(contracts))

        # 2. Validate
        errors = validate_all(contracts)
        real_errors = [e for e in errors if e.severity == "error"]
        if real_errors:
            raise CompilationError(real_errors)
        for warning in [e for e in errors if e.severity == "warning"]:
            logger.warning("Validation warning: %s — %s", warning.contract_fqn, warning.message)

        # 3. Build dependency graph
        graph = build_dependency_graph(contracts)

        # Check for unresolved references
        unresolved = graph.find_unresolved()
        if unresolved:
            raise CompilationError(
                [f"{e.message}" for e in unresolved]
            )

        # Check for cycles
        cycles = graph.detect_cycles()
        if cycles:
            raise CompilationError(
                [f"{e.message}" for e in cycles]
            )

        # 4. Compile in topological order
        order = graph.topological_order()
        logger.info("Compilation order: %s", " -> ".join(order))

        domain = self._detect_domain(contracts)
        ir = DomainIR(domain=domain)

        for fqn in order:
            node = graph.nodes[fqn]
            self._compile_node(node, ir)

        # 5. Run IR passes
        ir = run_all_passes(ir)

        logger.info("Compilation complete:\n%s", ir.summary())
        return ir

    def _detect_domain(self, contracts: dict[str, dict]) -> str:
        """Detect the primary domain from the loaded contracts.

        Looks for the most common non-stdlib domain. Falls back to
        the first domain found.
        """
        domains: dict[str, int] = {}
        for contract in contracts.values():
            d = contract.get("metadata", {}).get("domain", "")
            if d and d != "stdlib":
                domains[d] = domains.get(d, 0) + 1

        if domains:
            return max(domains, key=domains.get)
        return "unknown"

    def _compile_node(self, node, ir: DomainIR) -> None:
        """Compile a single contract node into its IR representation.

        Dispatches to kind-specific compilation methods.
        """
        kind = node.kind
        contract = node.raw

        if kind == "Entity":
            ir.entities.append(self._compile_entity(node.fqn, contract))
        elif kind == "Workflow":
            ir.workflows.append(self._compile_workflow(node.fqn, contract))
        elif kind == "Page":
            ir.pages.append(self._compile_page(node.fqn, contract))
        elif kind == "Route":
            ir.routes.append(self._compile_route(node.fqn, contract))
        elif kind == "Agent":
            ir.agents.append(self._compile_agent(node.fqn, contract))
        elif kind == "Mixin":
            ir.mixins.append(self._compile_mixin(node.fqn, contract))
        elif kind == "Infra":
            ir.infra.append(self._compile_infra(node.fqn, contract))
        else:
            logger.warning("Unknown contract kind '%s' for %s, skipping", kind, node.fqn)

    def _compile_entity(self, fqn: str, contract: dict) -> EntityIR:
        """Compile an Entity contract into EntityIR."""
        metadata = contract.get("metadata", {})
        spec = contract.get("spec", {})

        fields = self._compile_fields(spec.get("fields", {}))

        entity = EntityIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            description=metadata.get("description", ""),
            table_name=spec.get("table", ""),
            fields=fields,
            mixins_applied=[],  # Filled by mixin_expansion pass
            state_machine=None,  # Filled by state_machine_binding pass
            ai_hooks=spec.get("ai_integration", {}),
            number_prefix=spec.get("number_prefix"),
            icon=spec.get("icon"),
        )

        # Store raw mixin refs for the expansion pass
        entity._mixin_refs = spec.get("mixins", [])
        # Store raw workflow ref for the binding pass
        entity._workflow_ref = spec.get("state_machine")

        return entity

    def _compile_fields(self, fields_spec: dict) -> list[FieldIR]:
        """Compile a fields map into a list of FieldIR."""
        fields = []
        for name, definition in fields_spec.items():
            if not isinstance(definition, dict):
                continue

            ref_spec = definition.get("references")
            reference = None
            if ref_spec and isinstance(ref_spec, dict):
                reference = ReferenceIR(
                    target_entity=ref_spec.get("entity", ""),
                    display_field=ref_spec.get("display", "name"),
                    graph_edge=ref_spec.get("graph_edge"),
                    graph_direction=ref_spec.get("graph_direction"),
                )

            fields.append(
                FieldIR(
                    name=name,
                    type=definition.get("type", "string"),
                    description=definition.get("description", ""),
                    required=definition.get("required", False),
                    immutable=definition.get("immutable", False),
                    default=definition.get("default"),
                    format=definition.get("format"),
                    enum_values=definition.get("enum"),
                    items_type=definition.get("items_type"),
                    computed=definition.get("computed"),
                    constraints=definition.get("constraints", {}),
                    reference=reference,
                )
            )
        return fields

    def _compile_workflow(self, fqn: str, contract: dict) -> StateMachineIR:
        """Compile a Workflow contract into StateMachineIR."""
        spec = contract.get("spec", {})
        states_spec = spec.get("states", {})

        states = []
        for name, definition in states_spec.items():
            if isinstance(definition, dict):
                states.append(
                    StateIR(
                        name=name,
                        label=definition.get("label", name.replace("_", " ").title()),
                        category=definition.get("category", "open"),
                        terminal=definition.get("terminal", False),
                        color=definition.get("color"),
                    )
                )
            else:
                states.append(StateIR(name=name))

        guards = []
        for key, guard_spec in spec.get("guards", {}).items():
            parts = key.split("->")
            if len(parts) == 2:
                guards.append(
                    GuardIR(
                        from_state=parts[0].strip(),
                        to_state=parts[1].strip(),
                        require_fields=guard_spec.get("require_fields", []),
                        condition=guard_spec.get("condition"),
                    )
                )

        return StateMachineIR(
            fqn=fqn,
            initial=spec.get("initial", ""),
            states=states,
            transitions=spec.get("transitions", {}),
            guards=guards,
            side_effects=spec.get("side_effects", {}),
            type_overrides=spec.get("type_overrides", {}),
        )

    def _compile_page(self, fqn: str, contract: dict) -> PageIR:
        """Compile a Page contract into PageIR."""
        metadata = contract.get("metadata", {})
        spec = contract.get("spec", {})

        return PageIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            route=spec.get("route", ""),
            title=spec.get("title", ""),
            entity_fqn=spec.get("entity", ""),
            generation_tier=spec.get("generation_tier", "mechanical"),
            data_sources=spec.get("data_sources", []),
            display_rules=spec.get("display_rules", {}),
            views=spec.get("views", []),
            sections=spec.get("sections", []),
            actions=spec.get("actions", {}),
            filters=spec.get("filters", {}),
        )

    def _compile_route(self, fqn: str, contract: dict) -> RouteIR:
        """Compile a Route contract into RouteIR."""
        metadata = contract.get("metadata", {})
        spec = contract.get("spec", {})

        endpoints = []
        for ep_spec in spec.get("endpoints", []):
            endpoints.append(
                EndpointIR(
                    method=ep_spec.get("method", "GET"),
                    path=ep_spec.get("path", "/"),
                    summary=ep_spec.get("summary", ""),
                    required_fields=ep_spec.get("request_body", {}).get("required_fields", []),
                    validation_rules=ep_spec.get("validation", []),
                    auto_fields=ep_spec.get("auto_fields", {}),
                    side_effects=ep_spec.get("side_effects", []),
                    response_status=ep_spec.get("response", {}).get("status", 200),
                    response_shape=ep_spec.get("response", {}),
                    hateoas_links=ep_spec.get("hateoas", {}),
                    roles=ep_spec.get("roles", []),
                )
            )

        return RouteIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            entity_fqn=spec.get("entity", ""),
            base_path=spec.get("base_path", ""),
            endpoints=endpoints,
            global_behaviors=spec.get("global_behaviors", {}),
        )

    def _compile_agent(self, fqn: str, contract: dict) -> AgentIR:
        """Compile an Agent contract into AgentIR."""
        metadata = contract.get("metadata", {})
        spec = contract.get("spec", {})
        input_spec = spec.get("input", {})
        output_spec = spec.get("output", {})

        return AgentIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            trigger=spec.get("trigger", ""),
            threshold=spec.get("threshold", 0.7),
            input_entity=input_spec.get("entity", ""),
            input_fields=input_spec.get("fields", []),
            output_updates=output_spec.get("updates", {}),
            approach=spec.get("approach", ""),
            constraints=spec.get("constraints", []),
            fallback=spec.get("fallback", {}),
        )

    def _compile_mixin(self, fqn: str, contract: dict) -> MixinIR:
        """Compile a Mixin contract into MixinIR."""
        metadata = contract.get("metadata", {})
        spec = contract.get("spec", {})

        return MixinIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            description=metadata.get("description", ""),
            fields=self._compile_fields(spec.get("fields", {})),
        )

    def _compile_infra(self, fqn: str, contract: dict) -> InfraIR:
        """Compile an Infra contract into InfraIR."""
        metadata = contract.get("metadata", {})
        spec = contract.get("spec", {})

        return InfraIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            category=spec.get("category", ""),
            config=spec.get("config", {}),
            env_vars=spec.get("env_vars", {}),
            bootstrap=spec.get("bootstrap", {}),
        )
