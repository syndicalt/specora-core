"""Tests for factory contract emitters."""
from __future__ import annotations

import yaml
import pytest


class TestEntityEmitter:
    """Tests for emit_entity()."""

    def test_emit_entity_basic(self):
        """Emit entity with 3 fields + mixins, verify YAML structure."""
        from factory.emitters.entity_emitter import emit_entity

        data = {
            "description": "A ticket in the helpdesk",
            "fields": {
                "title": {"type": "string", "required": True},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "description": {"type": "text"},
            },
            "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
            "number_prefix": "TKT",
            "icon": "ticket",
        }

        result = emit_entity("ticket", "helpdesk", data)
        parsed = yaml.safe_load(result)

        # Envelope
        assert parsed["apiVersion"] == "specora.dev/v1"
        assert parsed["kind"] == "Entity"
        assert parsed["metadata"]["name"] == "ticket"
        assert parsed["metadata"]["domain"] == "helpdesk"
        assert parsed["metadata"]["description"] == "A ticket in the helpdesk"

        # Requires includes mixins
        assert "mixin/stdlib/timestamped" in parsed["requires"]
        assert "mixin/stdlib/identifiable" in parsed["requires"]

        # Spec
        spec = parsed["spec"]
        assert "title" in spec["fields"]
        assert spec["fields"]["title"]["type"] == "string"
        assert spec["fields"]["title"]["required"] is True
        assert spec["fields"]["priority"]["enum"] == ["low", "medium", "high"]
        assert spec["mixins"] == ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"]
        assert spec["number_prefix"] == "TKT"
        assert spec["icon"] == "ticket"

    def test_emit_entity_with_references(self):
        """Emit entity with reference field, verify entity FQN in requires."""
        from factory.emitters.entity_emitter import emit_entity

        data = {
            "description": "A task assigned to a user",
            "fields": {
                "title": {"type": "string", "required": True},
                "assigned_to": {
                    "type": "string",
                    "references": {
                        "entity": "entity/helpdesk/user",
                        "display": "name",
                        "graph_edge": "ASSIGNED_TO",
                    },
                },
                "category_id": {
                    "type": "string",
                    "references": {
                        "entity": "entity/helpdesk/category",
                        "display": "name",
                    },
                },
            },
            "mixins": ["mixin/stdlib/timestamped"],
            "state_machine": "workflow/helpdesk/task_lifecycle",
        }

        result = emit_entity("task", "helpdesk", data)
        parsed = yaml.safe_load(result)

        requires = parsed["requires"]
        # Referenced entities must be in requires
        assert "entity/helpdesk/user" in requires
        assert "entity/helpdesk/category" in requires
        # Workflow must be in requires
        assert "workflow/helpdesk/task_lifecycle" in requires
        # Mixin must be in requires
        assert "mixin/stdlib/timestamped" in requires

        # State machine should be in spec
        assert parsed["spec"]["state_machine"] == "workflow/helpdesk/task_lifecycle"


class TestWorkflowEmitter:
    """Tests for emit_workflow()."""

    def test_emit_workflow(self):
        """Emit workflow with states/transitions, verify structure."""
        from factory.emitters.workflow_emitter import emit_workflow

        data = {
            "description": "Task lifecycle for helpdesk tasks",
            "initial": "open",
            "states": {
                "open": {"label": "Open", "category": "open"},
                "in_progress": {"label": "In Progress", "category": "open"},
                "resolved": {"label": "Resolved", "category": "closed", "terminal": True},
            },
            "transitions": {
                "open": ["in_progress"],
                "in_progress": ["resolved", "open"],
            },
            "guards": {
                "in_progress -> resolved": {
                    "require_fields": ["resolution_notes"],
                },
            },
        }

        result = emit_workflow("task_lifecycle", "helpdesk", data)
        parsed = yaml.safe_load(result)

        # Envelope
        assert parsed["apiVersion"] == "specora.dev/v1"
        assert parsed["kind"] == "Workflow"
        assert parsed["metadata"]["name"] == "task_lifecycle"
        assert parsed["metadata"]["domain"] == "helpdesk"
        assert parsed["metadata"]["description"] == "Task lifecycle for helpdesk tasks"

        # Spec
        spec = parsed["spec"]
        assert spec["initial"] == "open"
        assert "open" in spec["states"]
        assert "in_progress" in spec["states"]
        assert "resolved" in spec["states"]
        assert spec["states"]["resolved"]["terminal"] is True
        assert spec["transitions"]["open"] == ["in_progress"]
        assert spec["transitions"]["in_progress"] == ["resolved", "open"]
        assert "in_progress -> resolved" in spec["guards"]
        assert spec["guards"]["in_progress -> resolved"]["require_fields"] == ["resolution_notes"]


class TestRouteEmitter:
    """Tests for emit_route()."""

    def test_emit_route(self):
        """Emit route, verify >= 4 endpoints."""
        from factory.emitters.route_emitter import emit_route

        result = emit_route("tasks", "helpdesk", "entity/helpdesk/task")
        parsed = yaml.safe_load(result)

        # Envelope
        assert parsed["apiVersion"] == "specora.dev/v1"
        assert parsed["kind"] == "Route"
        assert parsed["metadata"]["name"] == "tasks"
        assert parsed["metadata"]["domain"] == "helpdesk"

        # Entity binding
        assert parsed["spec"]["entity"] == "entity/helpdesk/task"
        assert "entity/helpdesk/task" in parsed["requires"]

        # At least 4 endpoints (GET list, POST, GET by id, PATCH, DELETE)
        endpoints = parsed["spec"]["endpoints"]
        assert len(endpoints) >= 4

        # Check methods present
        methods = [(ep["method"], ep["path"]) for ep in endpoints]
        assert ("GET", "/") in methods
        assert ("POST", "/") in methods
        assert ("GET", "/{id}") in methods
        assert ("PATCH", "/{id}") in methods

        # POST should have auto_fields
        post_endpoint = next(ep for ep in endpoints if ep["method"] == "POST")
        assert post_endpoint["auto_fields"]["id"] == "uuid"
        assert post_endpoint["auto_fields"]["created_at"] == "now"

    def test_emit_route_with_workflow(self):
        """Emit route with workflow, verify state endpoint."""
        from factory.emitters.route_emitter import emit_route

        result = emit_route(
            "tasks", "helpdesk", "entity/helpdesk/task",
            workflow_fqn="workflow/helpdesk/task_lifecycle",
        )
        parsed = yaml.safe_load(result)

        # Workflow in requires
        assert "workflow/helpdesk/task_lifecycle" in parsed["requires"]

        # State transition endpoint should exist
        endpoints = parsed["spec"]["endpoints"]
        state_ep = [ep for ep in endpoints if ep["path"] == "/{id}/state"]
        assert len(state_ep) == 1
        assert state_ep[0]["method"] == "PUT"


class TestPageEmitter:
    """Tests for emit_page()."""

    def test_emit_page(self):
        """Emit page, verify route/entity/generation_tier."""
        from factory.emitters.page_emitter import emit_page

        field_names = ["number", "title", "priority", "state", "assigned_to", "created_at", "description", "category"]

        result = emit_page("tasks", "helpdesk", "entity/helpdesk/task", field_names)
        parsed = yaml.safe_load(result)

        # Envelope
        assert parsed["apiVersion"] == "specora.dev/v1"
        assert parsed["kind"] == "Page"
        assert parsed["metadata"]["name"] == "tasks"
        assert parsed["metadata"]["domain"] == "helpdesk"

        # Key spec fields
        spec = parsed["spec"]
        assert spec["route"] == "/tasks"
        assert spec["entity"] == "entity/helpdesk/task"
        assert spec["generation_tier"] == "mechanical"
        assert "entity/helpdesk/task" in parsed["requires"]

        # Views — only table by default (kanban requires a state machine)
        views = spec["views"]
        assert len(views) >= 1

        # Table view: first 6 fields
        table_view = next(v for v in views if v["type"] == "table")
        assert len(table_view["columns"]) == 6
        assert table_view["columns"] == field_names[:6]
