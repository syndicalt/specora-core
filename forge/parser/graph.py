"""Contract dependency graph — resolves references and detects cycles.

The dependency graph is the third stage of the compilation pipeline.
After contracts are loaded and validated, the graph builder:

  1. Creates a node for each contract (identified by FQN)
  2. Creates edges from each contract's `requires` array
  3. Validates that all required contracts exist
  4. Detects circular dependencies
  5. Computes topological sort for compilation order

The graph ensures that when the compiler processes contracts, every
dependency is compiled before the contracts that depend on it.

Usage:
    from forge.parser.graph import build_dependency_graph

    graph = build_dependency_graph(contracts)

    # Check for issues
    unresolved = graph.find_unresolved()
    cycles = graph.detect_cycles()

    # Get compilation order
    order = graph.topological_order()
"""

from __future__ import annotations

import heapq
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from forge.parser.dependencies import merge_dependencies

logger = logging.getLogger(__name__)

# An insertion-ordered set. Membership is O(1) and iteration order is the order
# edges were added, so `dependencies_of` stays deterministic without sorting.
OrderedFqnSet = dict[str, None]


@dataclass
class ContractNode:
    """A node in the contract dependency graph.

    Represents a single contract with its identity and dependency information.

    Attributes:
        fqn: Fully Qualified Name (e.g., "entity/itsm/incident").
        kind: Contract kind (e.g., "Entity", "Workflow").
        domain: Domain namespace (e.g., "itsm", "stdlib").
        name: Contract name (e.g., "incident").
        source_path: File system path to the contract file.
        raw: The raw contract dict (for passing to the compiler).
        requires: List of FQNs this contract depends on.
    """

    fqn: str
    kind: str
    domain: str
    name: str
    source_path: str = ""
    raw: dict = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)


@dataclass
class GraphError:
    """An error found during graph construction or analysis.

    Attributes:
        error_type: Category of error ("unresolved", "cycle", "self_reference").
        message: Human-readable description.
        contract_fqn: The contract where the error was detected.
        details: Additional context (e.g., the list of FQNs in a cycle).
    """

    error_type: str
    message: str
    contract_fqn: str = ""
    details: list[str] = field(default_factory=list)


class DependencyGraph:
    """The contract dependency graph.

    Nodes are contracts identified by FQN. Edges represent `requires`
    relationships (A requires B means A -> B edge).

    The graph provides:
        - Unresolved reference detection
        - Circular dependency detection
        - Topological sort for compilation order
        - Dependency queries (what depends on X, what does X depend on)
    """

    def __init__(self):
        self.nodes: dict[str, ContractNode] = {}
        self.edges: dict[str, OrderedFqnSet] = {}  # fqn -> required fqns
        self.reverse_edges: dict[str, OrderedFqnSet] = {}  # fqn -> dependents
        self.build_errors: list[GraphError] = []  # authoring errors found while building

    def add_node(self, node: ContractNode) -> None:
        """Add a contract node to the graph.

        Args:
            node: The ContractNode to add.
        """
        self.nodes[node.fqn] = node
        self.edges.setdefault(node.fqn, {})
        self.reverse_edges.setdefault(node.fqn, {})

    def add_edge(self, from_fqn: str, to_fqn: str) -> None:
        """Add a dependency edge (from_fqn requires to_fqn).

        Args:
            from_fqn: The FQN of the contract that has the dependency.
            to_fqn: The FQN of the required contract.
        """
        self.edges.setdefault(from_fqn, {})[to_fqn] = None
        self.reverse_edges.setdefault(to_fqn, {})[from_fqn] = None

    def find_unresolved(self) -> list[GraphError]:
        """Find all unresolved references (requires pointing to non-existent contracts).

        Returns:
            List of GraphError objects for each unresolved reference.
        """
        errors = []
        for fqn, deps in self.edges.items():
            for dep in deps:
                if dep not in self.nodes:
                    errors.append(
                        GraphError(
                            error_type="unresolved",
                            message=f"Contract '{fqn}' requires '{dep}' which does not exist",
                            contract_fqn=fqn,
                            details=[dep],
                        )
                    )
        return errors

    def detect_cycles(self) -> list[GraphError]:
        """Detect circular dependencies using an explicit-stack DFS.

        The traversal is iterative rather than recursive: contract chains are
        user-authored and unbounded, and a recursive DFS turns a legitimately
        deep `requires` chain into a RecursionError — a crash that names no
        contract and looks nothing like the "your contracts are fine" answer it
        actually represents.

        Returns:
            List of GraphError objects, one per cycle found.
            Each error's `details` contains the FQNs in the cycle.
        """
        visited: set[str] = set()
        in_stack: set[str] = set()
        path: list[str] = []
        cycles: list[list[str]] = []

        for root in self.nodes:
            if root in visited:
                continue

            # Each frame is (fqn, iterator over its not-yet-walked dependencies).
            # A frame is pushed with a None iterator to mean "not entered yet".
            stack: list[tuple[str, Iterator[str] | None]] = [(root, None)]

            while stack:
                fqn, deps = stack.pop()

                if deps is None:
                    if fqn in in_stack:
                        cycles.append(path[path.index(fqn) :] + [fqn])
                        continue
                    if fqn in visited:
                        continue
                    visited.add(fqn)
                    in_stack.add(fqn)
                    path.append(fqn)
                    # Only follow edges to existing nodes; dangling requires are
                    # reported by find_unresolved, not here.
                    deps = iter([d for d in self.edges.get(fqn, {}) if d in self.nodes])

                next_dep = next(deps, None)
                if next_dep is None:
                    path.pop()
                    in_stack.discard(fqn)
                    continue

                stack.append((fqn, deps))
                stack.append((next_dep, None))

        return [
            GraphError(
                error_type="cycle",
                message=f"Circular dependency: {' -> '.join(cycle)}",
                contract_fqn=cycle[0],
                details=cycle,
            )
            for cycle in cycles
        ]

    def topological_order(self) -> list[str]:
        """Compute topological sort of contracts for compilation order.

        Contracts with no dependencies come first. Each contract appears
        only after all its dependencies.

        Edge direction, stated once: `self.edges[A]` holds what A *requires*,
        and `self.reverse_edges[B]` holds who requires B. Compilation must run
        the other way — B before A — so Kahn's algorithm here treats a node's
        *requires* count as its in-degree and walks `reverse_edges` to relax it.

        Returns:
            List of FQNs in compilation order. Ties are broken lexicographically
            so the order is stable across runs and platforms; generators embed
            this order in provenance comments, and an unstable order would make
            every regeneration produce a spurious diff.

        Raises:
            GraphCycleError: If the graph has cycles (topological sort
                is impossible with cycles).
        """
        pending_deps = {
            fqn: sum(1 for dep in self.edges.get(fqn, {}) if dep in self.nodes)
            for fqn in self.nodes
        }

        # A heap rather than a re-sorted list: the old code called queue.sort()
        # on every iteration and popped from the front, which is O(n^2 log n)
        # for the same result heapq gives in O(n log n).
        ready = [fqn for fqn, count in pending_deps.items() if count == 0]
        heapq.heapify(ready)
        result: list[str] = []

        while ready:
            fqn = heapq.heappop(ready)
            result.append(fqn)

            for dependent in self.reverse_edges.get(fqn, {}):
                if dependent in pending_deps:
                    pending_deps[dependent] -= 1
                    if pending_deps[dependent] == 0:
                        heapq.heappush(ready, dependent)

        if len(result) != len(self.nodes):
            # Cycle detected — some nodes never reached in-degree 0
            remaining = set(self.nodes) - set(result)
            raise GraphCycleError(
                f"Cannot determine compilation order — circular dependencies "
                f"involving: {', '.join(sorted(remaining))}"
            )

        return result

    def dependents_of(self, fqn: str) -> list[str]:
        """Find all contracts that depend on the given contract.

        Args:
            fqn: The FQN to find dependents of.

        Returns:
            List of FQNs that directly require this contract.
        """
        return list(self.reverse_edges.get(fqn, {}))

    def dependencies_of(self, fqn: str) -> list[str]:
        """Find all contracts that the given contract requires.

        Args:
            fqn: The FQN to find dependencies of.

        Returns:
            List of FQNs that this contract directly requires.
        """
        return list(self.edges.get(fqn, {}))

    def summary(self) -> str:
        """Return a human-readable summary of the graph.

        Returns:
            Multi-line string with node counts, edge counts, and compilation order.
        """
        kinds: dict[str, int] = {}
        for node in self.nodes.values():
            kinds[node.kind] = kinds.get(node.kind, 0) + 1

        edge_count = sum(len(deps) for deps in self.edges.values())

        lines = [
            f"Contract Graph: {len(self.nodes)} contracts, {edge_count} dependencies",
            "",
            "By kind:",
        ]
        for kind, count in sorted(kinds.items()):
            lines.append(f"  {kind}: {count}")

        return "\n".join(lines)


def build_dependency_graph(
    contracts: dict[str, dict],
    source_paths: dict[str, str] | None = None,
) -> DependencyGraph:
    """Build the dependency graph from a collection of loaded contracts.

    Creates nodes for each contract and edges from the `requires` arrays.
    Detects self-references as errors.

    Args:
        contracts: Dict mapping FQN -> loaded contract dict. A `ContractSet`
            from `load_all_contracts` carries its own source paths, which are
            used automatically when `source_paths` is not given.
        source_paths: Optional FQN -> file path mapping, used only for error
            reporting. Source paths are deliberately kept out of the contract
            dicts themselves so nothing can write them back to disk.

    Returns:
        A populated DependencyGraph.
    """
    graph = DependencyGraph()
    if source_paths is None:
        source_paths = getattr(contracts, "source_paths", {}) or {}

    # merge_dependencies walks every field of every contract; calling it once
    # per contract and reusing the result keeps that cost linear.
    dependencies = {fqn: merge_dependencies(contract) for fqn, contract in contracts.items()}

    for fqn, contract in contracts.items():
        metadata = contract.get("metadata") or {}
        declared = contract.get("requires")
        if isinstance(declared, list) and fqn in declared:
            # An explicit `requires: [<self>]` is always an authoring mistake:
            # it imposes no ordering the compiler can honour, so it means the
            # author typed the wrong FQN and the dependency they meant to
            # declare is missing.
            graph.build_errors.append(
                GraphError(
                    error_type="self_reference",
                    message=(
                        f"Contract '{fqn}' lists itself in `requires`. A contract "
                        f"cannot be compiled before itself — remove the entry, or "
                        f"correct it to the contract that was meant."
                    ),
                    contract_fqn=fqn,
                    details=[fqn],
                )
            )

        graph.add_node(
            ContractNode(
                fqn=fqn,
                kind=contract.get("kind", ""),
                domain=metadata.get("domain", ""),
                name=metadata.get("name", ""),
                source_path=source_paths.get(fqn, ""),
                raw=contract,
                requires=dependencies[fqn],
            )
        )

    for fqn, requires in dependencies.items():
        for dep in requires:
            if dep == fqn:
                # A self-edge derived from contract *semantics* is legitimate and
                # common: `account.parent_account_id -> account` (a chart-of-
                # accounts hierarchy), `journal.reversal_of_id -> journal` (a
                # reversing entry), threaded comments, org charts. The foreign
                # key, the graph edge and the frontend picker are all compiled
                # from `spec.fields.*.references` directly and are unaffected;
                # this graph only decides compilation *order*, and an entity is
                # compiled once as a whole, so it constrains nothing. Dropping
                # the edge here is exact, not a degradation — hence debug, not
                # a warning. Explicitly declared self-requires are caught above.
                logger.debug("Contract '%s' references itself — no ordering edge needed", fqn)
                continue
            graph.add_edge(fqn, dep)

    logger.info(
        "Built dependency graph: %d nodes, %d edges",
        len(graph.nodes),
        sum(len(deps) for deps in graph.edges.values()),
    )

    return graph


class GraphCycleError(Exception):
    """Raised when the dependency graph contains cycles."""

    pass
