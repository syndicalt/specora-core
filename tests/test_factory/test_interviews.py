"""Tests for the interview layer's parsing and its LLM-failure fallbacks.

The fallbacks are the part that used to degrade silently: an unreachable model
produced an entity with no fields and a workflow with states the user never
mentioned, both written without a diagnostic.
"""

from __future__ import annotations

import pytest

from factory.interviews import entity as entity_interview
from factory.interviews import workflow as workflow_interview
from factory.interviews.base import Interview, InterviewLLMError, InterviewParseError


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeEngine:
    """Returns a canned reply, or raises, in place of a provider call."""

    model_id = "fake"
    strategy = "fake"

    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error

    def chat(self, messages, system="", temperature=0.0, tools=None):
        if self._error is not None:
            raise self._error
        return FakeResponse(self._reply or "")


def _interview(reply: str | None = None, error: Exception | None = None) -> Interview:
    return Interview(FakeEngine(reply, error))


class TestStructuredParsing:
    def test_plain_yaml(self) -> None:
        assert _interview("a: 1\nb: two\n").ask_llm_structured("x", "y") == {"a": 1, "b": "two"}

    def test_fenced_yaml_with_leading_prose(self) -> None:
        # The old fence handling deleted only the fence lines, leaving the
        # prose in front of the payload where it parsed as a scalar.
        reply = "Sure, here is the structure:\n```yaml\nfields:\n  a:\n    type: string\n```\n"
        assert _interview(reply).ask_llm_structured("x", "y") == {
            "fields": {"a": {"type": "string"}}
        }

    def test_fenced_json(self) -> None:
        assert _interview('```json\n{"a": 1}\n```').ask_llm_structured("x", "y") == {"a": 1}

    def test_prose_raises_with_the_parser_reason(self) -> None:
        with pytest.raises(InterviewParseError) as exc:
            _interview("I am not going to answer that.").ask_llm_structured("x", "y")
        # The diagnostics are what distinguish "wrote prose" from "wrote YAML
        # with a tab in it".
        assert "YAML" in str(exc.value) and "JSON" in str(exc.value)

    def test_scalar_response_raises_rather_than_returning_a_string(self) -> None:
        with pytest.raises(InterviewParseError, match="expected a mapping"):
            _interview("just-a-scalar").ask_llm_structured("x", "y")

    def test_provider_failure_is_wrapped(self) -> None:
        with pytest.raises(InterviewLLMError):
            _interview(error=RuntimeError("429 rate limited")).ask_llm("hello")


class TestEntityInterviewFallback:
    def test_typed_fields_survive_a_provider_outage(self, monkeypatch) -> None:
        answers = iter(["A task someone does", "title, due date, assigned to"])
        monkeypatch.setattr(Interview, "ask_user", lambda self, prompt: next(answers))
        monkeypatch.setattr(Interview, "confirm", lambda self, message: False)

        data = entity_interview.run_entity_interview(
            FakeEngine(error=RuntimeError("provider down")), "task", "todo"
        )

        # Previously this returned {"fields": {}} — the user's answer was
        # dropped and the resulting entity could not generate.
        assert list(data["fields"]) == ["title", "due_date", "assigned_to"]
        assert all(f["type"] == "string" for f in data["fields"].values())
        assert "mixin/stdlib/identifiable" in data["mixins"]

    def test_non_mapping_fields_from_the_model_fall_back(self, monkeypatch) -> None:
        answers = iter(["A task", "title, notes"])
        monkeypatch.setattr(Interview, "ask_user", lambda self, prompt: next(answers))
        monkeypatch.setattr(Interview, "confirm", lambda self, message: False)

        data = entity_interview.run_entity_interview(
            FakeEngine("fields:\n  - title\n  - notes\n"), "task", "todo"
        )
        assert list(data["fields"]) == ["title", "notes"]

    def test_unusable_field_labels_are_dropped_not_emitted(self) -> None:
        fields = entity_interview._fields_from_raw_input("title, ../etc, , café, notes")
        assert list(fields) == ["title", "notes"]


class TestWorkflowInterviewFallback:
    def test_user_states_survive_a_provider_outage(self, monkeypatch) -> None:
        monkeypatch.setattr(
            Interview, "ask_user", lambda self, prompt: "todo, in progress, done"
        )

        data = workflow_interview.run_workflow_interview(
            FakeEngine(error=RuntimeError("provider down")), "task_lifecycle", "todo", "task"
        )

        # Previously replaced wholesale with active/inactive.
        assert list(data["states"]) == ["todo", "in_progress", "done"]
        assert data["initial"] == "todo"
        assert data["transitions"] == {"todo": ["in_progress"], "in_progress": ["done"]}
        assert data["states"]["done"]["terminal"] is True

    def test_the_fallback_chain_emits_a_valid_workflow(self, monkeypatch) -> None:
        from factory.emitters.workflow_emitter import emit_workflow

        monkeypatch.setattr(Interview, "ask_user", lambda self, prompt: "new, done")
        data = workflow_interview.run_workflow_interview(
            FakeEngine(error=RuntimeError("down")), "task_lifecycle", "todo", "task"
        )
        # emit_workflow rejects an undeclared initial state or transition
        # target, so this proves the chain is coherent.
        assert emit_workflow("task_lifecycle", "todo", data)

    def test_unusable_input_falls_back_to_a_declared_default(self, monkeypatch) -> None:
        monkeypatch.setattr(Interview, "ask_user", lambda self, prompt: "!!!, ???")
        data = workflow_interview.run_workflow_interview(
            FakeEngine(error=RuntimeError("down")), "task_lifecycle", "todo", "task"
        )
        assert list(data["states"]) == ["active", "inactive"]
