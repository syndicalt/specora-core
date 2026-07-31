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
from forge.ir.semantic import validate_semantics
from forge.parser.graph import build_dependency_graph
from forge.parser.loader import load_all_contracts
from forge.parser.validator import validate_all

logger = logging.getLogger(__name__)


class CompilationError(Exception):
    """Raised when compilation fails due to validation or resolution errors."""

    def __init__(self, errors: list):
        self.errors = errors
        messages = [str(e) if isinstance(e, str) else getattr(e, "message", str(e)) for e in errors]
        super().__init__(f"Compilation failed with {len(errors)} error(s):\n" + "\n".join(messages))


def _as_dict(value) -> dict:
    """Coerce a possibly-absent, possibly-null contract mapping to a dict.

    `spec.get("fields", {})` returns the default only when the key is *missing*.
    A key that is present but empty — the extremely ordinary YAML

        spec:
          fields:

    — parses to None, and the default never applies, so the next `.get()` or
    `.items()` raises AttributeError from deep inside the compiler with no
    mention of the contract or the key at fault. Every mapping read from a
    contract goes through here instead.
    """
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    """Coerce a possibly-absent, possibly-null contract sequence to a list.

    The list counterpart of `_as_dict`; see there for why the `{}`/`[]` default
    on `.get()` is not enough.
    """
    return value if isinstance(value, list) else []


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
        # Errors found while translating contracts into IR. Collected rather
        # than raised one at a time so a build reports every bad construct in
        # one pass instead of one per re-run.
        self._errors: list[str] = []

    def compile(self) -> DomainIR:
        """Run the full compilation pipeline.

        Returns:
            A complete DomainIR ready for generators.

        Raises:
            CompilationError: If validation or resolution fails.
            ContractLoadError: If contracts can't be loaded.
        """
        self._errors = []

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

        graph_errors = graph.build_errors + graph.find_unresolved() + graph.detect_cycles()
        if graph_errors:
            raise CompilationError([e.message for e in graph_errors])

        # 4. Compile in topological order
        order = graph.topological_order()
        logger.info("Compilation order: %s", " -> ".join(order))

        domains = self._collect_domains(contracts)
        ir = DomainIR(domain=domains[0] if domains else "unknown", domains=domains)

        for fqn in order:
            node = graph.nodes[fqn]
            self._compile_node(node, ir)

        if self._errors:
            raise CompilationError(self._errors)

        # 5. Run IR passes
        ir = run_all_passes(ir)

        semantic_errors = validate_semantics(ir)
        if semantic_errors:
            raise CompilationError(semantic_errors)

        logger.info("Compilation complete:\n%s", ir.summary())
        return ir

    def _collect_domains(self, contracts: dict[str, dict]) -> list[str]:
        """Collect every non-stdlib domain in the build, sorted.

        This replaces an earlier `_detect_domain` that returned only the modal
        domain via `max(domains, key=domains.get)`, discarding the rest. That
        was lossy in a way nothing downstream could detect: a build spanning
        two domains produced a `DomainIR` labelled with one of them (chosen by
        insertion order on a tie) while carrying entities from both, so every
        generator derived colliding identifiers from bare entity names.

        Returning the full set lets `DomainIR.multi_domain` drive namespacing
        in `forge.targets.naming`, and lets `validate_semantics` reject genuine
        collisions rather than letting generators overwrite each other.
        """
        return sorted(
            {
                d
                for contract in contracts.values()
                if (d := _as_dict(contract.get("metadata")).get("domain", ""))
                and d != "stdlib"
            }
        )

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
            # A kind with no compile path contributes nothing to the IR, so the
            # whole contract would vanish from the generated app.
            self._errors.append(
                f"{node.fqn}: unknown contract kind '{kind}' — nothing would be "
                f"generated from this contract."
            )

    def _compile_entity(self, fqn: str, contract: dict) -> EntityIR:
        """Compile an Entity contract into EntityIR."""
        metadata = _as_dict(contract.get("metadata"))
        spec = _as_dict(contract.get("spec"))

        return EntityIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            description=metadata.get("description", ""),
            table_name=spec.get("table", ""),
            fields=self._compile_fields(_as_dict(spec.get("fields")), fqn),
            mixin_refs=_as_list(spec.get("mixins")),
            mixins_applied=[],  # Filled by mixin_expansion pass
            workflow_ref=spec.get("state_machine"),
            state_machine=None,  # Filled by state_machine_binding pass
            ai_hooks=_as_dict(spec.get("ai_integration")),
            number_prefix=spec.get("number_prefix"),
            icon=spec.get("icon"),
        )

    def _compile_fields(self, fields_spec: dict, fqn: str) -> list[FieldIR]:
        """Compile a fields map into a list of FieldIR.

        A field definition must be a mapping. `name: string` — the shorthand
        every author reaches for first — used to be skipped silently, so the
        field disappeared from the models, the DDL and the API with no
        diagnostic anywhere; the first sign of it was a 500 at runtime or a
        column that was never created. It is now a compilation error.
        """
        fields = []
        for name, definition in fields_spec.items():
            if not isinstance(definition, dict):
                message = (
                    f"{fqn}: spec.fields.{name} must be a mapping of field properties, "
                    f"got {type(definition).__name__} ({definition!r})."
                )
                if isinstance(definition, str):
                    message += f" Write `{name}: {{type: {definition}}}`."
                self._errors.append(message)
                continue

            ref_spec = definition.get("references")
            reference = None
            if isinstance(ref_spec, dict) and ref_spec:
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
                    # Write-only. Omitting this hop is silent and total: the
                    # contract and meta-schema accept `sensitive: true`, every
                    # generator honours FieldIR.sensitive, and the flag still
                    # arrives False for every field — so a password hash is
                    # published by the API while the contract says it must not
                    # be. There is no error anywhere along that path.
                    sensitive=definition.get("sensitive", False),
                    default=definition.get("default"),
                    format=definition.get("format"),
                    enum_values=definition.get("enum"),
                    items_type=definition.get("items_type"),
                    computed=definition.get("computed"),
                    constraints=_as_dict(definition.get("constraints")),
                    reference=reference,
                )
            )
        return fields

    def _compile_workflow(self, fqn: str, contract: dict) -> StateMachineIR:
        """Compile a Workflow contract into StateMachineIR."""
        spec = _as_dict(contract.get("spec"))

        states = []
        for name, definition in _as_dict(spec.get("states")).items():
            definition = _as_dict(definition)
            states.append(
                StateIR(
                    name=name,
                    label=definition.get("label", name.replace("_", " ").title()),
                    category=definition.get("category", "open"),
                    terminal=definition.get("terminal", False),
                    color=definition.get("color"),
                )
            )

        guards = []
        for key, guard_spec in _as_dict(spec.get("guards")).items():
            parts = [part.strip() for part in str(key).split("->")]
            if len(parts) != 2 or not all(parts):
                # A guard is the only pre-condition on a state transition —
                # "you may not resolve without a resolution". Dropping one
                # because its key was mistyped removes an authorization check
                # from the generated API and leaves nothing behind to notice.
                self._errors.append(
                    f"{fqn}: guard key {key!r} is not a transition. Guard keys must "
                    f"read 'source_state -> target_state'."
                )
                continue

            guard_spec = _as_dict(guard_spec)
            guards.append(
                GuardIR(
                    from_state=parts[0],
                    to_state=parts[1],
                    require_fields=_as_list(guard_spec.get("require_fields")),
                    condition=guard_spec.get("condition"),
                )
            )

        return StateMachineIR(
            fqn=fqn,
            initial=spec.get("initial", ""),
            states=states,
            transitions=_as_dict(spec.get("transitions")),
            guards=guards,
            side_effects=_as_dict(spec.get("side_effects")),
            type_overrides=_as_dict(spec.get("type_overrides")),
        )

    def _compile_page(self, fqn: str, contract: dict) -> PageIR:
        """Compile a Page contract into PageIR."""
        metadata = _as_dict(contract.get("metadata"))
        spec = _as_dict(contract.get("spec"))

        return PageIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            route=spec.get("route", ""),
            title=spec.get("title", ""),
            entity_fqn=spec.get("entity", ""),
            generation_tier=spec.get("generation_tier", "mechanical"),
            data_sources=_as_list(spec.get("data_sources")),
            display_rules=_as_dict(spec.get("display_rules")),
            views=_as_list(spec.get("views")),
            sections=_as_list(spec.get("sections")),
            actions=_as_dict(spec.get("actions")),
            filters=_as_dict(spec.get("filters")),
        )

    def _compile_route(self, fqn: str, contract: dict) -> RouteIR:
        """Compile a Route contract into RouteIR."""
        metadata = _as_dict(contract.get("metadata"))
        spec = _as_dict(contract.get("spec"))

        endpoints = []
        for index, ep_spec in enumerate(_as_list(spec.get("endpoints"))):
            if not isinstance(ep_spec, dict):
                self._errors.append(
                    f"{fqn}: spec.endpoints[{index}] must be a mapping, "
                    f"got {type(ep_spec).__name__} ({ep_spec!r})."
                )
                continue

            response = _as_dict(ep_spec.get("response"))
            endpoints.append(
                EndpointIR(
                    method=ep_spec.get("method", "GET"),
                    path=ep_spec.get("path", "/"),
                    summary=ep_spec.get("summary", ""),
                    required_fields=_as_list(
                        _as_dict(ep_spec.get("request_body")).get("required_fields")
                    ),
                    validation_rules=_as_list(ep_spec.get("validation")),
                    auto_fields=_as_dict(ep_spec.get("auto_fields")),
                    side_effects=_as_list(ep_spec.get("side_effects")),
                    response_status=response.get("status", 200),
                    response_shape=response,
                    hateoas_links=_as_dict(ep_spec.get("hateoas")),
                    roles=_as_list(ep_spec.get("roles")),
                )
            )

        return RouteIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            entity_fqn=spec.get("entity", ""),
            base_path=spec.get("base_path", ""),
            endpoints=endpoints,
            global_behaviors=_as_dict(spec.get("global_behaviors")),
        )

    def _compile_agent(self, fqn: str, contract: dict) -> AgentIR:
        """Compile an Agent contract into AgentIR."""
        metadata = _as_dict(contract.get("metadata"))
        spec = _as_dict(contract.get("spec"))
        input_spec = _as_dict(spec.get("input"))
        output_spec = _as_dict(spec.get("output"))

        return AgentIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            trigger=spec.get("trigger", ""),
            threshold=spec.get("threshold", 0.7),
            input_entity=input_spec.get("entity", ""),
            input_fields=_as_list(input_spec.get("fields")),
            output_updates=_as_dict(output_spec.get("updates")),
            approach=spec.get("approach", ""),
            constraints=_as_list(spec.get("constraints")),
            fallback=_as_dict(spec.get("fallback")),
        )

    def _compile_mixin(self, fqn: str, contract: dict) -> MixinIR:
        """Compile a Mixin contract into MixinIR."""
        metadata = _as_dict(contract.get("metadata"))
        spec = _as_dict(contract.get("spec"))

        return MixinIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            description=metadata.get("description", ""),
            fields=self._compile_fields(_as_dict(spec.get("fields")), fqn),
        )

    def _compile_infra(self, fqn: str, contract: dict) -> InfraIR:
        """Compile an Infra contract into InfraIR."""
        metadata = _as_dict(contract.get("metadata"))
        spec = _as_dict(contract.get("spec"))

        return InfraIR(
            fqn=fqn,
            name=metadata.get("name", ""),
            domain=metadata.get("domain", ""),
            category=spec.get("category", ""),
            config=_as_dict(spec.get("config")),
            env_vars=_as_dict(spec.get("env_vars")),
            bootstrap=_as_dict(spec.get("bootstrap")),
        )
