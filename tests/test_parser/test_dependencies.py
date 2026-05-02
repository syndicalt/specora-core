from __future__ import annotations

from forge.parser.dependencies import extract_semantic_dependencies, merge_dependencies
from forge.parser.graph import build_dependency_graph


def test_entity_semantic_dependencies_are_extracted() -> None:
    contract = {
        "kind": "Entity",
        "requires": ["entity/test/declared"],
        "spec": {
            "mixins": ["mixin/stdlib/timestamped"],
            "state_machine": "workflow/test/task_lifecycle",
            "fields": {
                "owner_id": {
                    "type": "string",
                    "references": {"entity": "entity/test/user"},
                },
            },
            "ai_integration": {"on_create": ["agent/test/classifier"]},
        },
    }

    assert extract_semantic_dependencies(contract) == [
        "mixin/stdlib/timestamped",
        "workflow/test/task_lifecycle",
        "entity/test/user",
        "agent/test/classifier",
    ]
    assert merge_dependencies(contract) == [
        "entity/test/declared",
        "mixin/stdlib/timestamped",
        "workflow/test/task_lifecycle",
        "entity/test/user",
        "agent/test/classifier",
    ]


def test_route_page_and_agent_semantic_dependencies_are_extracted() -> None:
    route = {
        "kind": "Route",
        "spec": {
            "entity": "entity/test/task",
            "endpoints": [
                {
                    "side_effects": [
                        {"dispatch": "agent/test/classifier"},
                        {"nested": {"agent": "agent/test/auditor"}},
                    ],
                },
            ],
        },
    }
    page = {"kind": "Page", "spec": {"entity": "entity/test/task"}}
    agent = {"kind": "Agent", "spec": {"input": {"entity": "entity/test/task"}}}

    assert extract_semantic_dependencies(route) == [
        "entity/test/task",
        "agent/test/classifier",
        "agent/test/auditor",
    ]
    assert extract_semantic_dependencies(page) == ["entity/test/task"]
    assert extract_semantic_dependencies(agent) == ["entity/test/task"]


def test_dependency_graph_uses_synthesized_semantic_edges() -> None:
    contracts = {
        "entity/test/user": {
            "kind": "Entity",
            "metadata": {"domain": "test", "name": "user"},
            "requires": [],
            "spec": {"fields": {"name": {"type": "string"}}},
        },
        "workflow/test/task_lifecycle": {
            "kind": "Workflow",
            "metadata": {"domain": "test", "name": "task_lifecycle"},
            "requires": [],
            "spec": {"initial": "new", "states": {}, "transitions": {}},
        },
        "entity/test/task": {
            "kind": "Entity",
            "metadata": {"domain": "test", "name": "task"},
            "requires": [],
            "spec": {
                "fields": {
                    "owner_id": {
                        "type": "string",
                        "references": {"entity": "entity/test/user"},
                    },
                },
                "state_machine": "workflow/test/task_lifecycle",
            },
        },
        "route/test/tasks": {
            "kind": "Route",
            "metadata": {"domain": "test", "name": "tasks"},
            "requires": [],
            "spec": {"entity": "entity/test/task", "endpoints": []},
        },
    }

    graph = build_dependency_graph(contracts)

    assert graph.dependencies_of("entity/test/task") == [
        "workflow/test/task_lifecycle",
        "entity/test/user",
    ]
    assert graph.dependencies_of("route/test/tasks") == ["entity/test/task"]
    assert graph.topological_order().index("entity/test/user") < graph.topological_order().index(
        "entity/test/task"
    )
