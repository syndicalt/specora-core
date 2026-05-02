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

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from forge.parser.dependencies import merge_dependencies

logger = logging.getLogger(__name__)


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
        self.edges: dict[str, list[str]] = {}  # fqn -> list of required fqns
        self.reverse_edges: dict[str, list[str]] = {}  # fqn -> list of dependents

    def add_node(self, node: ContractNode) -> None:
        """Add a contract node to the graph.

        Args:
            node: The ContractNode to add.
        """
        self.nodes[node.fqn] = node
        self.edges.setdefault(node.fqn, [])
        self.reverse_edges.setdefault(node.fqn, [])

    def add_edge(self, from_fqn: str, to_fqn: str) -> None:
        """Add a dependency edge (from_fqn requires to_fqn).

        Args:
            from_fqn: The FQN of the contract that has the dependency.
            to_fqn: The FQN of the required contract.
        """
        self.edges.setdefault(from_fqn, [])
        if to_fqn not in self.edges[from_fqn]:
            self.edges[from_fqn].append(to_fqn)
        self.reverse_edges.setdefault(to_fqn, [])
        if from_fqn not in self.reverse_edges[to_fqn]:
            self.reverse_edges[to_fqn].append(from_fqn)

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
        """Detect circular dependencies using DFS.

        Returns:
            List of GraphError objects, one per cycle found.
            Each error's `details` contains the FQNs in the cycle.
        """
        visited: set[str] = set()
        in_stack: set[str] = set()
        cycles: list[list[str]] = []

        def dfs(fqn: str, path: list[str]) -> None:
            if fqn in in_stack:
                # Found a cycle — extract just the cycle portion
                cycle_start = path.index(fqn)
                cycle = path[cycle_start:] + [fqn]
                cycles.append(cycle)
                return
            if fqn in visited:
                return

            visited.add(fqn)
            in_stack.add(fqn)
            path.append(fqn)

            for dep in self.edges.get(fqn, []):
                if dep in self.nodes:  # Only follow edges to existing nodes
                    dfs(dep, path)

            path.pop()
            in_stack.remove(fqn)

        for fqn in self.nodes:
            if fqn not in visited:
                dfs(fqn, [])

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

        Returns:
            List of FQNs in compilation order.

        Raises:
            GraphCycleError: If the graph has cycles (topological sort
                is impossible with cycles).
        """
        # Kahn's algorithm
        in_degree: dict[str, int] = {fqn: 0 for fqn in self.nodes}
        for fqn, deps in self.edges.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] = in_degree.get(dep, 0)  # ensure exists

        # Recompute: in_degree[X] = number of edges pointing TO X
        in_degree = {fqn: 0 for fqn in self.nodes}
        for fqn, deps in self.edges.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        # Wait — that's wrong. In Kahn's algorithm for a dependency graph
        # where A requires B, we want B before A. So the edge is A -> B,
        # meaning B has an in-degree from A. But we want to emit B first.
        #
        # Actually, let's think of it differently:
        # - A requires B means "B must be compiled before A"
        # - So the compilation edge is B -> A (B before A)
        # - In Kahn's, we start with nodes that have no incoming compilation edges
        # - A node's incoming compilation edges = its dependencies (requires list)

        # In-degree = number of requires for each node (how many deps it has)
        in_degree = {fqn: 0 for fqn in self.nodes}
        for fqn, deps in self.edges.items():
            # Count only deps that exist as nodes
            in_degree[fqn] = len([d for d in deps if d in self.nodes])

        # Start with nodes that have no dependencies
        queue = [fqn for fqn, deg in sorted(in_degree.items()) if deg == 0]
        result: list[str] = []

        while queue:
            # Sort for deterministic output
            queue.sort()
            fqn = queue.pop(0)
            result.append(fqn)

            # For each node that depends on this one, decrement its in-degree
            for dependent in self.reverse_edges.get(fqn, []):
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

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
        return self.reverse_edges.get(fqn, [])

    def dependencies_of(self, fqn: str) -> list[str]:
        """Find all contracts that the given contract requires.

        Args:
            fqn: The FQN to find dependencies of.

        Returns:
            List of FQNs that this contract directly requires.
        """
        return self.edges.get(fqn, [])

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


def build_dependency_graph(contracts: dict[str, dict]) -> DependencyGraph:
    """Build the dependency graph from a collection of loaded contracts.

    Creates nodes for each contract and edges from the `requires` arrays.
    Detects self-references as errors.

    Args:
        contracts: Dict mapping FQN -> loaded contract dict.

    Returns:
        A populated DependencyGraph.
    """
    graph = DependencyGraph()

    # Create nodes
    for fqn, contract in contracts.items():
        kind = contract.get("kind", "")
        metadata = contract.get("metadata", {})
        requires = merge_dependencies(contract)

        node = ContractNode(
            fqn=fqn,
            kind=kind,
            domain=metadata.get("domain", ""),
            name=metadata.get("name", ""),
            source_path=contract.get("_source_path", ""),
            raw=contract,
            requires=requires,
        )
        graph.add_node(node)

    # Create edges
    for fqn, contract in contracts.items():
        requires = merge_dependencies(contract)
        for dep in requires:
            if dep == fqn:
                logger.warning("Contract '%s' requires itself — ignoring self-reference", fqn)
                continue
            graph.add_edge(fqn, dep)

    logger.info("Built dependency graph: %d nodes, %d edges",
                len(graph.nodes),
                sum(len(deps) for deps in graph.edges.values()))

    return graph


class GraphCycleError(Exception):
    """Raised when the dependency graph contains cycles."""

    pass
