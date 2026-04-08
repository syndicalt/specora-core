# Factory CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Factory CLI (Tier 2) so `specora factory new` can bootstrap a complete domain from a conversational interview — LLM-powered, resumable, with $EDITOR preview.

**Architecture:** The Factory is an LLM-orchestrated conversation engine that interviews a developer about their domain, emits `.contract.yaml` files matching the Specora contract envelope format, opens them in `$EDITOR` for review, and writes them atomically. A model registry maps model IDs to capability profiles (tool calling, structured output, prompt-based) so the engine adapts its strategy per model. Session state is persisted to `.factory/session.json` for resume-on-quit.

**Tech Stack:** Python 3.10+, Click (CLI), Rich (terminal UI), anthropic SDK, openai SDK, pyyaml, pydantic

**Tracks:** syndicalt/specora-core#1

---

## File Map

```
engine/
├── __init__.py               # (exists, empty)
├── registry.py               # NEW — Model registry: model ID → capabilities
├── config.py                 # NEW — Engine configuration (API keys, model selection)
├── engine.py                 # NEW — Provider-agnostic LLM interface
├── tools.py                  # NEW — Tool definitions for function calling
├── context.py                # NEW — Contract-aware prompt builder
└── providers/
    ├── __init__.py            # (exists, empty)
    ├── base.py                # NEW — Provider interface + capability flags
    ├── anthropic.py           # NEW — Claude adapter
    └── openai.py              # NEW — OpenAI adapter

factory/
├── __init__.py               # (exists, empty)
├── session.py                # NEW — Re-entrant session state
├── interviews/
│   ├── __init__.py            # (exists, empty)
│   ├── base.py                # NEW — Interview framework
│   ├── domain.py              # NEW — Domain discovery interview
│   ├── entity.py              # NEW — Entity interview
│   └── workflow.py            # NEW — Workflow interview
├── emitters/
│   ├── __init__.py            # (exists, empty)
│   ├── entity_emitter.py      # NEW — Entity interview → .contract.yaml
│   ├── workflow_emitter.py    # NEW — Workflow interview → .contract.yaml
│   ├── route_emitter.py       # NEW — Auto-generate route contracts
│   └── page_emitter.py        # NEW — Auto-generate page contracts
└── preview/
    └── editor.py              # NEW — Open contracts in $EDITOR

forge/cli/main.py              # MODIFY — Add `factory` command group

pyproject.toml                 # MODIFY — Add anthropic, openai deps

tests/
├── test_engine/
│   ├── __init__.py            # NEW
│   ├── test_registry.py       # NEW
│   └── test_engine.py         # NEW
├── test_factory/
│   ├── __init__.py            # NEW
│   ├── test_session.py        # NEW
│   ├── test_emitters.py       # NEW
│   └── test_interviews.py     # NEW
```

---

### Task 1: Model Registry and Provider Base

**Files:**
- Create: `engine/registry.py`
- Create: `engine/config.py`
- Create: `engine/providers/base.py`
- Modify: `pyproject.toml`
- Test: `tests/test_engine/test_registry.py`

- [ ] **Step 1: Add LLM dependencies to pyproject.toml**

```toml
# In pyproject.toml, change the [project.optional-dependencies] section:
[project.optional-dependencies]
dev = [
    "pytest>=8",
    "ruff>=0.4",
]
llm = [
    "anthropic>=0.25",
    "openai>=1.0",
    "httpx>=0.27",
]
all = [
    "specora-core[dev,llm]",
]
```

Run: `pip install -e ".[llm]"`

- [ ] **Step 2: Write the failing test for the model registry**

```python
# tests/test_engine/__init__.py — empty

# tests/test_engine/test_registry.py
"""Tests for the model registry — maps model IDs to capability profiles."""

from engine.registry import ModelRegistry, ModelCapabilities


def test_registry_has_claude_models():
    reg = ModelRegistry()
    caps = reg.get("claude-sonnet-4-6")
    assert caps is not None
    assert caps.provider == "anthropic"
    assert caps.supports_tools is True
    assert caps.supports_structured_output is True
    assert caps.max_context >= 200_000


def test_registry_returns_none_for_unknown():
    reg = ModelRegistry()
    assert reg.get("nonexistent-model-xyz") is None


def test_registry_lists_all_models():
    reg = ModelRegistry()
    models = reg.list_models()
    assert len(models) > 0
    assert "claude-sonnet-4-6" in models


def test_registry_lists_by_provider():
    reg = ModelRegistry()
    anthropic_models = reg.list_models(provider="anthropic")
    assert all("claude" in m or "anthropic" in m.lower() for m in anthropic_models)


def test_capabilities_best_strategy_with_tools():
    caps = ModelCapabilities(
        provider="anthropic",
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
    )
    assert caps.best_strategy() == "tools"


def test_capabilities_best_strategy_structured_fallback():
    caps = ModelCapabilities(
        provider="openai",
        supports_tools=False,
        supports_structured_output=True,
        max_context=128_000,
    )
    assert caps.best_strategy() == "structured_output"


def test_capabilities_best_strategy_prompt_fallback():
    caps = ModelCapabilities(
        provider="local",
        supports_tools=False,
        supports_structured_output=False,
        max_context=8_000,
    )
    assert caps.best_strategy() == "prompt"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd C:\Users\cheap\OneDrive\Documents\projects\specora-core && pytest tests/test_engine/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.registry'`

- [ ] **Step 4: Implement ModelCapabilities and ModelRegistry**

```python
# engine/registry.py
"""Model registry — maps model IDs to capability profiles.

The registry tells the LLM engine what each model can do:
  - Tool calling (preferred): structured function calls
  - Structured output: JSON matching a schema
  - Prompt-based (last resort): parse YAML from freeform response

Usage:
    from engine.registry import ModelRegistry

    reg = ModelRegistry()
    caps = reg.get("claude-sonnet-4-6")
    strategy = caps.best_strategy()  # "tools"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelCapabilities:
    """Capability profile for a specific model.

    Attributes:
        provider: Which API to use ("anthropic", "openai", "local").
        supports_tools: Can the model call tools/functions?
        supports_structured_output: Can it return JSON matching a schema?
        max_context: Maximum context window in tokens.
        tier: "recommended", "supported", or "community".
        notes: Any model-specific quirks or limitations.
    """

    provider: str
    supports_tools: bool = False
    supports_structured_output: bool = False
    max_context: int = 8_000
    tier: str = "community"
    notes: str = ""

    def best_strategy(self) -> str:
        """Determine the best interaction strategy for this model.

        Returns:
            "tools" — model supports function/tool calling (most reliable)
            "structured_output" — model can return JSON matching a schema
            "prompt" — freeform text, engine parses YAML from response
        """
        if self.supports_tools:
            return "tools"
        if self.supports_structured_output:
            return "structured_output"
        return "prompt"


# Built-in model registry. New models can be added here or via config.
_BUILTIN_MODELS: dict[str, ModelCapabilities] = {
    # ── Anthropic (Claude) ──────────────────────────────────────────
    "claude-opus-4-6": ModelCapabilities(
        provider="anthropic",
        supports_tools=True,
        supports_structured_output=True,
        max_context=1_000_000,
        tier="recommended",
        notes="Most capable. Best for complex domain interviews.",
    ),
    "claude-sonnet-4-6": ModelCapabilities(
        provider="anthropic",
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
        tier="recommended",
        notes="Fast and capable. Good default for most Factory tasks.",
    ),
    "claude-haiku-4-5": ModelCapabilities(
        provider="anthropic",
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
        tier="supported",
        notes="Fastest Claude. Good for explain and simple add operations.",
    ),
    # ── OpenAI ──────────────────────────────────────────────────────
    "gpt-4o": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=128_000,
        tier="supported",
    ),
    "gpt-4o-mini": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=128_000,
        tier="supported",
        notes="Cost-effective for simpler tasks.",
    ),
    "o3-mini": ModelCapabilities(
        provider="openai",
        supports_tools=True,
        supports_structured_output=True,
        max_context=200_000,
        tier="supported",
        notes="Reasoning model. Good for complex workflow design.",
    ),
    # ── Google ──────────────────────────────────────────────────────
    "gemini-2.5-pro": ModelCapabilities(
        provider="openai",  # Uses OpenAI-compatible API
        supports_tools=True,
        supports_structured_output=True,
        max_context=1_000_000,
        tier="supported",
        notes="Use with GOOGLE_API_KEY and OpenAI-compatible endpoint.",
    ),
    # ── Local (Ollama / LM Studio) ─────────────────────────────────
    "llama3.3:70b": ModelCapabilities(
        provider="local",
        supports_tools=True,
        supports_structured_output=False,
        max_context=128_000,
        tier="community",
        notes="Local via Ollama. Tool support varies by quantization.",
    ),
    "qwen2.5:32b": ModelCapabilities(
        provider="local",
        supports_tools=True,
        supports_structured_output=False,
        max_context=32_000,
        tier="community",
    ),
    "mistral:7b": ModelCapabilities(
        provider="local",
        supports_tools=False,
        supports_structured_output=False,
        max_context=8_000,
        tier="community",
        notes="Prompt-based only. Smallest viable model for Factory.",
    ),
}


class ModelRegistry:
    """Registry of known models and their capabilities.

    Combines built-in models with any user-configured additions.
    """

    def __init__(self, extra_models: Optional[dict[str, ModelCapabilities]] = None):
        self._models = dict(_BUILTIN_MODELS)
        if extra_models:
            self._models.update(extra_models)

    def get(self, model_id: str) -> Optional[ModelCapabilities]:
        """Look up a model's capabilities.

        Args:
            model_id: The model identifier (e.g., "claude-sonnet-4-6").

        Returns:
            ModelCapabilities if found, None otherwise.
        """
        return self._models.get(model_id)

    def list_models(self, provider: Optional[str] = None) -> list[str]:
        """List all registered model IDs.

        Args:
            provider: Optional filter by provider name.

        Returns:
            Sorted list of model ID strings.
        """
        if provider:
            return sorted(k for k, v in self._models.items() if v.provider == provider)
        return sorted(self._models.keys())

    def recommended(self) -> list[str]:
        """List models with tier='recommended'."""
        return sorted(k for k, v in self._models.items() if v.tier == "recommended")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_engine/test_registry.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Implement engine config**

```python
# engine/config.py
"""Engine configuration — resolves API keys and model selection.

Reads from environment variables and .env file. The model selection
priority is:
  1. SPECORA_AI_MODEL env var (explicit override)
  2. Auto-detect from available API keys (first available recommended model)

Usage:
    from engine.config import EngineConfig

    config = EngineConfig.from_env()
    print(config.model_id)       # "claude-sonnet-4-6"
    print(config.api_key)        # "sk-ant-..."
    print(config.strategy)       # "tools"
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional

from engine.registry import ModelRegistry, ModelCapabilities

logger = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    """Resolved engine configuration.

    Attributes:
        model_id: The selected model identifier.
        capabilities: The model's capability profile.
        api_key: The API key for the model's provider.
        base_url: Optional base URL override (for local models).
        strategy: The interaction strategy ("tools", "structured_output", "prompt").
    """

    model_id: str
    capabilities: ModelCapabilities
    api_key: str = ""
    base_url: Optional[str] = None
    strategy: str = ""

    def __post_init__(self):
        if not self.strategy:
            self.strategy = self.capabilities.best_strategy()

    @classmethod
    def from_env(cls, registry: Optional[ModelRegistry] = None) -> "EngineConfig":
        """Build config from environment variables.

        Checks:
          1. SPECORA_AI_MODEL — explicit model choice
          2. ANTHROPIC_API_KEY — auto-select Claude
          3. OPENAI_API_KEY — auto-select GPT-4o
          4. OLLAMA_BASE_URL — auto-select local model

        Raises:
            EngineConfigError: If no model can be resolved.
        """
        reg = registry or ModelRegistry()

        # Try explicit model selection
        explicit_model = os.environ.get("SPECORA_AI_MODEL", "").strip()
        if explicit_model:
            caps = reg.get(explicit_model)
            if caps is None:
                raise EngineConfigError(
                    f"Model '{explicit_model}' not found in registry. "
                    f"Available: {', '.join(reg.list_models())}"
                )
            api_key = _resolve_api_key(caps.provider)
            base_url = _resolve_base_url(caps.provider)
            return cls(
                model_id=explicit_model,
                capabilities=caps,
                api_key=api_key,
                base_url=base_url,
            )

        # Auto-detect from available API keys
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if anthropic_key:
            return cls(
                model_id="claude-sonnet-4-6",
                capabilities=reg.get("claude-sonnet-4-6"),
                api_key=anthropic_key,
            )

        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            return cls(
                model_id="gpt-4o",
                capabilities=reg.get("gpt-4o"),
                api_key=openai_key,
            )

        xai_key = os.environ.get("XAI_API_KEY", "").strip()
        if xai_key:
            return cls(
                model_id="gpt-4o",
                capabilities=reg.get("gpt-4o"),
                api_key=xai_key,
                base_url="https://api.x.ai/v1",
            )

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "").strip()
        if ollama_url:
            return cls(
                model_id="llama3.3:70b",
                capabilities=reg.get("llama3.3:70b"),
                base_url=ollama_url,
            )

        raise EngineConfigError(
            "No AI provider configured. Set one of: "
            "ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY, OLLAMA_BASE_URL"
        )


def _resolve_api_key(provider: str) -> str:
    """Resolve the API key for a provider."""
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    env_var = key_map.get(provider, "")
    return os.environ.get(env_var, "").strip()


def _resolve_base_url(provider: str) -> Optional[str]:
    """Resolve the base URL for a provider (used for local models)."""
    if provider == "local":
        return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()
    return None


class EngineConfigError(Exception):
    """Raised when engine configuration cannot be resolved."""

    pass
```

- [ ] **Step 7: Implement provider base class**

```python
# engine/providers/base.py
"""Provider interface — all LLM providers implement this.

Providers handle the actual API communication. The engine calls
providers through this interface, never directly.

Usage:
    from engine.providers.base import Provider, Message

    provider = AnthropicProvider(api_key="...", model="claude-sonnet-4-6")
    response = provider.chat(messages=[Message(role="user", content="...")])
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Message:
    """A single message in a conversation.

    Attributes:
        role: "system", "user", or "assistant".
        content: The message text.
        tool_calls: Tool calls made by the assistant (if any).
        tool_results: Results from tool execution (if any).
    """

    role: str
    content: str
    tool_calls: Optional[list[dict]] = None
    tool_results: Optional[list[dict]] = None


@dataclass
class ToolDefinition:
    """A tool the LLM can call.

    Attributes:
        name: Tool name (e.g., "create_entity_field").
        description: What the tool does.
        parameters: JSON Schema for the tool's parameters.
    """

    name: str
    description: str
    parameters: dict


@dataclass
class LLMResponse:
    """Response from an LLM provider.

    Attributes:
        content: The text response (may be empty if tool calls present).
        tool_calls: Tool calls requested by the model.
        stop_reason: Why the model stopped ("end_turn", "tool_use", "max_tokens").
        usage: Token usage stats.
    """

    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: dict = field(default_factory=dict)


class Provider(ABC):
    """Abstract LLM provider interface."""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a conversation to the LLM and get a response.

        Args:
            messages: Conversation history.
            system: System prompt.
            tools: Available tools for function calling.
            temperature: Sampling temperature (0.0-1.0).
            max_tokens: Maximum response tokens.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'anthropic', 'openai')."""
        ...
```

- [ ] **Step 8: Commit**

```bash
git add engine/ tests/test_engine/ pyproject.toml
git commit -m "feat(#1/T1): model registry, engine config, provider base"
```

---

### Task 2: Anthropic and OpenAI Providers

**Files:**
- Create: `engine/providers/anthropic.py`
- Create: `engine/providers/openai.py`
- Create: `engine/engine.py`
- Test: `tests/test_engine/test_engine.py`

- [ ] **Step 1: Write the failing test for engine initialization**

```python
# tests/test_engine/test_engine.py
"""Tests for the LLM engine — provider-agnostic interface."""

import os
import pytest
from unittest.mock import patch, MagicMock

from engine.engine import LLMEngine
from engine.config import EngineConfig, EngineConfigError
from engine.registry import ModelRegistry, ModelCapabilities
from engine.providers.base import Message, LLMResponse


def test_engine_creates_from_config():
    caps = ModelCapabilities(provider="anthropic", supports_tools=True, max_context=200_000)
    config = EngineConfig(model_id="claude-sonnet-4-6", capabilities=caps, api_key="test-key")
    engine = LLMEngine(config)
    assert engine.model_id == "claude-sonnet-4-6"
    assert engine.strategy == "tools"


def test_engine_from_env_raises_without_keys():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(EngineConfigError):
            LLMEngine.from_env()


def test_engine_ask_returns_string():
    """Engine.ask() is the simple interface — send text, get text back."""
    caps = ModelCapabilities(provider="anthropic", supports_tools=True, max_context=200_000)
    config = EngineConfig(model_id="test", capabilities=caps, api_key="key")
    engine = LLMEngine(config)

    # Mock the provider
    mock_response = LLMResponse(content="The answer is 42.")
    engine._provider = MagicMock()
    engine._provider.chat.return_value = mock_response

    result = engine.ask("What is the meaning of life?")
    assert result == "The answer is 42."


def test_engine_ask_with_system_prompt():
    caps = ModelCapabilities(provider="anthropic", supports_tools=True, max_context=200_000)
    config = EngineConfig(model_id="test", capabilities=caps, api_key="key")
    engine = LLMEngine(config)

    mock_response = LLMResponse(content="I am a contract author.")
    engine._provider = MagicMock()
    engine._provider.chat.return_value = mock_response

    result = engine.ask("Who are you?", system="You are a contract author.")
    assert result == "I am a contract author."
    # Verify system prompt was passed
    call_kwargs = engine._provider.chat.call_args
    assert call_kwargs.kwargs.get("system") == "You are a contract author." or \
           call_kwargs[1].get("system") == "You are a contract author."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_engine/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.engine'`

- [ ] **Step 3: Implement Anthropic provider**

```python
# engine/providers/anthropic.py
"""Anthropic (Claude) provider.

Handles tool calling natively via the Anthropic SDK's tool_use feature.
Claude models are the recommended provider for the Factory.

Usage:
    from engine.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(api_key="sk-ant-...", model="claude-sonnet-4-6")
    response = provider.chat(messages=[Message(role="user", content="hello")])
"""

from __future__ import annotations

import logging
from typing import Optional

from engine.providers.base import LLMResponse, Message, Provider, ToolDefinition

logger = logging.getLogger(__name__)


class AnthropicProvider(Provider):
    """Claude provider via the Anthropic SDK."""

    def __init__(self, api_key: str, model: str):
        self._model = model
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError("anthropic package required: pip install anthropic")

    def provider_name(self) -> str:
        return "anthropic"

    def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # Convert messages to Anthropic format
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                continue  # Anthropic uses a separate system parameter
            api_messages.append({"role": msg.role, "content": msg.content})

        kwargs = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as e:
            logger.error("Anthropic API error: %s", e)
            raise

        # Parse response
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
```

- [ ] **Step 4: Implement OpenAI provider**

```python
# engine/providers/openai.py
"""OpenAI-compatible provider.

Handles GPT models, plus any OpenAI-compatible API (xAI Grok, Google Gemini
via compatibility layer, local models via LM Studio/Ollama).

Usage:
    from engine.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-...", model="gpt-4o")
    response = provider.chat(messages=[Message(role="user", content="hello")])
"""

from __future__ import annotations

import logging
from typing import Optional

from engine.providers.base import LLMResponse, Message, Provider, ToolDefinition

logger = logging.getLogger(__name__)


class OpenAIProvider(Provider):
    """OpenAI-compatible provider."""

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        self._model = model
        try:
            import openai
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = openai.OpenAI(**kwargs)
        except ImportError:
            raise ImportError("openai package required: pip install openai")

    def provider_name(self) -> str:
        return "openai"

    def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        for msg in messages:
            if msg.role != "system":
                api_messages.append({"role": msg.role, "content": msg.content})

        kwargs = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("OpenAI API error: %s", e)
            raise

        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = []

        if choice.message.tool_calls:
            import json
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "stop",
            usage={
                "input_tokens": getattr(response.usage, "prompt_tokens", 0),
                "output_tokens": getattr(response.usage, "completion_tokens", 0),
            },
        )
```

- [ ] **Step 5: Implement the LLM engine**

```python
# engine/engine.py
"""LLM Engine — provider-agnostic interface for the Factory.

The engine is the single entry point for all LLM interactions. It:
  1. Resolves configuration (model, API key, strategy)
  2. Instantiates the correct provider
  3. Provides simple interfaces: ask(), chat(), tool_call()

Usage:
    from engine.engine import LLMEngine

    engine = LLMEngine.from_env()
    answer = engine.ask("What fields should a patient entity have?")

    # Or with conversation history
    response = engine.chat(messages, system="You are a contract author.")
"""

from __future__ import annotations

import logging
from typing import Optional

from engine.config import EngineConfig
from engine.providers.base import LLMResponse, Message, Provider, ToolDefinition
from engine.registry import ModelRegistry

logger = logging.getLogger(__name__)


class LLMEngine:
    """Provider-agnostic LLM interface.

    Attributes:
        model_id: The selected model identifier.
        strategy: Interaction strategy ("tools", "structured_output", "prompt").
    """

    def __init__(self, config: EngineConfig):
        self._config = config
        self.model_id = config.model_id
        self.strategy = config.strategy
        self._provider = self._create_provider(config)

    @classmethod
    def from_env(cls, registry: Optional[ModelRegistry] = None) -> "LLMEngine":
        """Create engine from environment variables.

        Raises:
            EngineConfigError: If no AI provider is configured.
        """
        config = EngineConfig.from_env(registry)
        logger.info("LLM Engine: %s (%s strategy)", config.model_id, config.strategy)
        return cls(config)

    def ask(self, question: str, system: str = "") -> str:
        """Simple interface — send a question, get a text answer.

        Args:
            question: The question or prompt.
            system: Optional system prompt.

        Returns:
            The model's text response.
        """
        messages = [Message(role="user", content=question)]
        response = self._provider.chat(messages=messages, system=system)
        return response.content

    def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Full conversation interface with optional tool calling.

        Args:
            messages: Conversation history.
            system: System prompt.
            tools: Available tools (if strategy supports them).
            temperature: Sampling temperature.
            max_tokens: Maximum response tokens.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        # Only pass tools if the model supports them
        effective_tools = tools if self.strategy == "tools" else None

        return self._provider.chat(
            messages=messages,
            system=system,
            tools=effective_tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _create_provider(self, config: EngineConfig) -> Provider:
        """Instantiate the correct provider for the configured model."""
        if config.capabilities.provider == "anthropic":
            from engine.providers.anthropic import AnthropicProvider
            return AnthropicProvider(api_key=config.api_key, model=config.model_id)
        elif config.capabilities.provider in ("openai", "local"):
            from engine.providers.openai import OpenAIProvider
            return OpenAIProvider(
                api_key=config.api_key or "not-needed",
                model=config.model_id,
                base_url=config.base_url,
            )
        else:
            raise ValueError(f"Unknown provider: {config.capabilities.provider}")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_engine/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add engine/ tests/test_engine/
git commit -m "feat(#1/T1): LLM engine with Anthropic and OpenAI providers"
```

---

### Task 3: Session Persistence

**Files:**
- Create: `factory/session.py`
- Test: `tests/test_factory/test_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_factory/__init__.py — empty

# tests/test_factory/test_session.py
"""Tests for session persistence — save/resume mid-interview."""

import json
from pathlib import Path

from factory.session import Session, SessionState


def test_session_create(tmp_path):
    session = Session(root=tmp_path)
    session.start("veterinary", "Veterinary clinic management")
    assert session.state.domain == "veterinary"
    assert session.state.description == "Veterinary clinic management"
    assert session.state.phase == "domain_discovery"


def test_session_save_and_load(tmp_path):
    # Save
    session = Session(root=tmp_path)
    session.start("veterinary", "Vet clinic")
    session.state.entities_discovered = ["patient", "owner"]
    session.state.current_entity = "patient"
    session.state.phase = "entity_interview"
    session.save()

    # Load
    session2 = Session(root=tmp_path)
    assert session2.can_resume()
    session2.resume()
    assert session2.state.domain == "veterinary"
    assert session2.state.entities_discovered == ["patient", "owner"]
    assert session2.state.current_entity == "patient"
    assert session2.state.phase == "entity_interview"


def test_session_cleanup(tmp_path):
    session = Session(root=tmp_path)
    session.start("vet", "Vet clinic")
    session.save()
    assert session.can_resume()
    session.cleanup()
    assert not session.can_resume()


def test_session_add_entity_data(tmp_path):
    session = Session(root=tmp_path)
    session.start("vet", "Vet clinic")
    session.add_entity("patient", {
        "fields": {"name": {"type": "string", "required": True}},
        "description": "An animal patient",
    })
    assert "patient" in session.state.entity_data
    assert session.state.entity_data["patient"]["description"] == "An animal patient"


def test_session_add_workflow_data(tmp_path):
    session = Session(root=tmp_path)
    session.start("vet", "Vet clinic")
    session.add_workflow("patient_lifecycle", {
        "initial": "active",
        "states": {"active": {}, "inactive": {}},
    })
    assert "patient_lifecycle" in session.state.workflow_data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_factory/test_session.py -v`
Expected: FAIL

- [ ] **Step 3: Implement session persistence**

```python
# factory/session.py
"""Session persistence — save and resume Factory interviews.

When a user quits mid-interview (Ctrl+C, closes terminal), the session
state is saved to `.factory/session.json`. On next invocation, the
Factory detects the saved session and offers to resume.

Session state tracks:
  - Domain name and description
  - Current phase (domain_discovery, entity_interview, workflow_interview, etc.)
  - Entities discovered so far
  - Entity field data collected so far
  - Workflow data collected so far
  - Conversation history (for LLM context continuity)

Usage:
    from factory.session import Session

    session = Session()
    if session.can_resume():
        session.resume()
    else:
        session.start("veterinary", "Vet clinic management")

    # During interview...
    session.add_entity("patient", {"fields": {...}})
    session.save()  # Periodic saves

    # On completion
    session.cleanup()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SESSION_FILE = "session.json"
SESSION_DIR = ".factory"


@dataclass
class SessionState:
    """Serializable state of a Factory interview session.

    Attributes:
        domain: The domain name being created.
        description: Domain description.
        phase: Current interview phase.
        entities_discovered: Entity names found during domain discovery.
        current_entity: Entity currently being interviewed (if any).
        entity_data: Collected entity data keyed by name.
        workflow_data: Collected workflow data keyed by name.
        conversation_history: Messages for LLM context continuity.
        created_at: When the session started.
        updated_at: When the session was last saved.
    """

    domain: str = ""
    description: str = ""
    phase: str = "domain_discovery"
    entities_discovered: list[str] = field(default_factory=list)
    current_entity: Optional[str] = None
    entity_data: dict[str, dict] = field(default_factory=dict)
    workflow_data: dict[str, dict] = field(default_factory=dict)
    conversation_history: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict."""
        return {
            "domain": self.domain,
            "description": self.description,
            "phase": self.phase,
            "entities_discovered": self.entities_discovered,
            "current_entity": self.current_entity,
            "entity_data": self.entity_data,
            "workflow_data": self.workflow_data,
            "conversation_history": self.conversation_history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionState":
        """Deserialize from a dict."""
        return cls(
            domain=data.get("domain", ""),
            description=data.get("description", ""),
            phase=data.get("phase", "domain_discovery"),
            entities_discovered=data.get("entities_discovered", []),
            current_entity=data.get("current_entity"),
            entity_data=data.get("entity_data", {}),
            workflow_data=data.get("workflow_data", {}),
            conversation_history=data.get("conversation_history", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


class Session:
    """Manages Factory session persistence.

    Sessions are stored in `.factory/session.json` relative to the
    given root directory (defaults to current working directory).
    """

    def __init__(self, root: Optional[Path] = None):
        self._root = Path(root) if root else Path.cwd()
        self._session_dir = self._root / SESSION_DIR
        self._session_path = self._session_dir / SESSION_FILE
        self.state = SessionState()

    def start(self, domain: str, description: str) -> None:
        """Start a new session."""
        now = datetime.now(timezone.utc).isoformat()
        self.state = SessionState(
            domain=domain,
            description=description,
            phase="domain_discovery",
            created_at=now,
            updated_at=now,
        )
        logger.info("Started new Factory session for domain '%s'", domain)

    def can_resume(self) -> bool:
        """Check if a saved session exists."""
        return self._session_path.exists()

    def resume(self) -> None:
        """Resume a saved session."""
        if not self._session_path.exists():
            raise SessionError("No saved session found")
        try:
            data = json.loads(self._session_path.read_text(encoding="utf-8"))
            self.state = SessionState.from_dict(data)
            logger.info(
                "Resumed session for domain '%s' (phase: %s)",
                self.state.domain, self.state.phase,
            )
        except (json.JSONDecodeError, KeyError) as e:
            raise SessionError(f"Corrupt session file: {e}") from e

    def save(self) -> None:
        """Persist the current session state to disk."""
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        self._session_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        logger.debug("Session saved to %s", self._session_path)

    def cleanup(self) -> None:
        """Remove the saved session (called on successful completion)."""
        if self._session_path.exists():
            self._session_path.unlink()
        logger.info("Session cleaned up")

    def add_entity(self, name: str, data: dict) -> None:
        """Record entity data from an interview."""
        self.state.entity_data[name] = data
        if name not in self.state.entities_discovered:
            self.state.entities_discovered.append(name)

    def add_workflow(self, name: str, data: dict) -> None:
        """Record workflow data from an interview."""
        self.state.workflow_data[name] = data

    def add_message(self, role: str, content: str) -> None:
        """Append a message to the conversation history."""
        self.state.conversation_history.append({"role": role, "content": content})


class SessionError(Exception):
    """Raised when session operations fail."""

    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_factory/test_session.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add factory/session.py tests/test_factory/
git commit -m "feat(#1/T3): session persistence with save/resume/cleanup"
```

---

### Task 4: Contract Emitters

**Files:**
- Create: `factory/emitters/entity_emitter.py`
- Create: `factory/emitters/workflow_emitter.py`
- Create: `factory/emitters/route_emitter.py`
- Create: `factory/emitters/page_emitter.py`
- Test: `tests/test_factory/test_emitters.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_factory/test_emitters.py
"""Tests for contract emitters — interview data → .contract.yaml files."""

import yaml

from factory.emitters.entity_emitter import emit_entity
from factory.emitters.workflow_emitter import emit_workflow
from factory.emitters.route_emitter import emit_route
from factory.emitters.page_emitter import emit_page


def test_emit_entity_basic():
    data = {
        "description": "An animal patient at the clinic",
        "fields": {
            "name": {"type": "string", "required": True, "description": "Patient name"},
            "species": {"type": "string", "enum": ["dog", "cat", "bird"]},
            "weight": {"type": "number", "description": "Weight in kg"},
        },
        "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"],
    }
    result = emit_entity("patient", "veterinary", data)
    contract = yaml.safe_load(result)

    assert contract["apiVersion"] == "specora.dev/v1"
    assert contract["kind"] == "Entity"
    assert contract["metadata"]["name"] == "patient"
    assert contract["metadata"]["domain"] == "veterinary"
    assert contract["spec"]["fields"]["name"]["required"] is True
    assert contract["spec"]["fields"]["species"]["enum"] == ["dog", "cat", "bird"]
    assert "mixin/stdlib/timestamped" in contract["requires"]


def test_emit_entity_with_references():
    data = {
        "description": "An appointment",
        "fields": {
            "patient_id": {
                "type": "string",
                "references": {
                    "entity": "entity/veterinary/patient",
                    "display": "name",
                    "graph_edge": "SCHEDULED_FOR",
                },
            },
        },
    }
    result = emit_entity("appointment", "veterinary", data)
    contract = yaml.safe_load(result)
    ref = contract["spec"]["fields"]["patient_id"]["references"]
    assert ref["entity"] == "entity/veterinary/patient"
    assert "entity/veterinary/patient" in contract["requires"]


def test_emit_workflow():
    data = {
        "initial": "active",
        "states": {
            "active": {"label": "Active", "category": "open"},
            "inactive": {"label": "Inactive", "category": "closed"},
        },
        "transitions": {
            "active": ["inactive"],
            "inactive": ["active"],
        },
    }
    result = emit_workflow("patient_lifecycle", "veterinary", data)
    contract = yaml.safe_load(result)

    assert contract["kind"] == "Workflow"
    assert contract["spec"]["initial"] == "active"
    assert "inactive" in contract["spec"]["transitions"]["active"]


def test_emit_route():
    result = emit_route("patients", "veterinary", "entity/veterinary/patient")
    contract = yaml.safe_load(result)

    assert contract["kind"] == "Route"
    assert contract["spec"]["entity"] == "entity/veterinary/patient"
    assert len(contract["spec"]["endpoints"]) >= 4  # GET list, POST, GET id, PATCH/DELETE


def test_emit_page():
    fields = ["name", "species", "weight", "state"]
    result = emit_page("patients", "veterinary", "entity/veterinary/patient", fields)
    contract = yaml.safe_load(result)

    assert contract["kind"] == "Page"
    assert contract["spec"]["route"] == "/patients"
    assert contract["spec"]["entity"] == "entity/veterinary/patient"
    assert contract["spec"]["generation_tier"] == "mechanical"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_factory/test_emitters.py -v`
Expected: FAIL

- [ ] **Step 3: Implement entity emitter**

```python
# factory/emitters/entity_emitter.py
"""Entity emitter — interview data → entity .contract.yaml content.

Takes the structured data collected during an entity interview and
emits valid YAML matching the Entity meta-schema.

Usage:
    from factory.emitters.entity_emitter import emit_entity

    yaml_str = emit_entity("patient", "veterinary", {
        "description": "An animal patient",
        "fields": {"name": {"type": "string", "required": True}},
        "mixins": ["mixin/stdlib/timestamped"],
    })
"""

from __future__ import annotations

import yaml
from typing import Any


def emit_entity(name: str, domain: str, data: dict) -> str:
    """Emit an Entity contract as YAML.

    Args:
        name: Entity name (snake_case).
        domain: Domain name.
        data: Collected interview data with keys:
            - description: str
            - fields: dict of field definitions
            - mixins: list of mixin FQNs (optional)
            - state_machine: workflow FQN (optional)
            - number_prefix: str (optional)
            - icon: str (optional)

    Returns:
        YAML string of the complete entity contract.
    """
    requires = list(data.get("mixins", []))

    # Auto-detect references and add to requires
    for field_name, field_def in data.get("fields", {}).items():
        if isinstance(field_def, dict) and "references" in field_def:
            ref_entity = field_def["references"].get("entity", "")
            if ref_entity and ref_entity not in requires:
                requires.append(ref_entity)

    # Add workflow to requires
    workflow = data.get("state_machine")
    if workflow and workflow not in requires:
        requires.append(workflow)

    contract: dict[str, Any] = {
        "apiVersion": "specora.dev/v1",
        "kind": "Entity",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": data.get("description", f"A {name} record"),
        },
        "requires": requires,
        "spec": {
            "fields": data.get("fields", {}),
        },
    }

    if data.get("mixins"):
        contract["spec"]["mixins"] = data["mixins"]
    if workflow:
        contract["spec"]["state_machine"] = workflow
    if data.get("number_prefix"):
        contract["spec"]["number_prefix"] = data["number_prefix"]
    if data.get("icon"):
        contract["spec"]["icon"] = data["icon"]

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)
```

- [ ] **Step 4: Implement workflow emitter**

```python
# factory/emitters/workflow_emitter.py
"""Workflow emitter — interview data → workflow .contract.yaml content."""

from __future__ import annotations

import yaml
from typing import Any


def emit_workflow(name: str, domain: str, data: dict) -> str:
    """Emit a Workflow contract as YAML.

    Args:
        name: Workflow name (snake_case).
        domain: Domain name.
        data: Collected interview data with keys:
            - initial: str (initial state)
            - states: dict of state definitions
            - transitions: dict of state → [target states]
            - guards: dict (optional)
            - description: str (optional)

    Returns:
        YAML string of the complete workflow contract.
    """
    contract: dict[str, Any] = {
        "apiVersion": "specora.dev/v1",
        "kind": "Workflow",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": data.get("description", f"Lifecycle for {name.replace('_lifecycle', '').replace('_', ' ')}"),
        },
        "requires": [],
        "spec": {
            "initial": data.get("initial", ""),
            "states": data.get("states", {}),
            "transitions": data.get("transitions", {}),
        },
    }

    if data.get("guards"):
        contract["spec"]["guards"] = data["guards"]

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)
```

- [ ] **Step 5: Implement route emitter**

```python
# factory/emitters/route_emitter.py
"""Route emitter — auto-generates CRUD route contracts for entities."""

from __future__ import annotations

import yaml
from typing import Any


def emit_route(name: str, domain: str, entity_fqn: str, workflow_fqn: str = "") -> str:
    """Emit a Route contract with standard CRUD endpoints.

    Args:
        name: Route name (snake_case, usually plural entity name).
        domain: Domain name.
        entity_fqn: FQN of the entity this route manages.
        workflow_fqn: Optional workflow FQN for state transitions.

    Returns:
        YAML string of the complete route contract.
    """
    requires = [entity_fqn]
    if workflow_fqn:
        requires.append(workflow_fqn)

    entity_name = entity_fqn.split("/")[-1]

    endpoints = [
        {
            "method": "GET",
            "path": "/",
            "summary": f"List all {name}",
            "response": {"status": 200, "shape": "list"},
        },
        {
            "method": "POST",
            "path": "/",
            "summary": f"Create a new {entity_name}",
            "auto_fields": {"id": "uuid", "created_at": "now"},
            "response": {"status": 201, "shape": "entity"},
        },
        {
            "method": "GET",
            "path": "/{id}",
            "summary": f"Get a {entity_name} by ID",
            "response": {"status": 200, "shape": "entity"},
        },
        {
            "method": "PATCH",
            "path": "/{id}",
            "summary": f"Update a {entity_name}",
            "response": {"status": 200, "shape": "entity"},
        },
        {
            "method": "DELETE",
            "path": "/{id}",
            "summary": f"Delete a {entity_name}",
            "response": {"status": 204},
        },
    ]

    if workflow_fqn:
        endpoints.append({
            "method": "PUT",
            "path": "/{id}/state",
            "summary": f"Transition {entity_name} state",
            "request_body": {"required_fields": ["state"]},
            "response": {"status": 200, "shape": "entity"},
        })

    contract: dict[str, Any] = {
        "apiVersion": "specora.dev/v1",
        "kind": "Route",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": f"CRUD API for {name}",
        },
        "requires": requires,
        "spec": {
            "entity": entity_fqn,
            "base_path": f"/{name}",
            "endpoints": endpoints,
        },
    }

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)
```

- [ ] **Step 6: Implement page emitter**

```python
# factory/emitters/page_emitter.py
"""Page emitter — auto-generates list page contracts for entities."""

from __future__ import annotations

import yaml
from typing import Any


def emit_page(name: str, domain: str, entity_fqn: str, field_names: list[str]) -> str:
    """Emit a Page contract with standard list views.

    Args:
        name: Page name (snake_case, usually plural entity name).
        domain: Domain name.
        entity_fqn: FQN of the entity this page displays.
        field_names: List of field names for table columns and card fields.

    Returns:
        YAML string of the complete page contract.
    """
    # Pick sensible defaults for table columns (first 6 fields)
    table_columns = field_names[:6]
    card_fields = field_names[:4]

    contract: dict[str, Any] = {
        "apiVersion": "specora.dev/v1",
        "kind": "Page",
        "metadata": {
            "name": name,
            "domain": domain,
            "description": f"Browse and manage {name}",
        },
        "requires": [entity_fqn],
        "spec": {
            "route": f"/{name}",
            "title": name.replace("_", " ").title(),
            "entity": entity_fqn,
            "generation_tier": "mechanical",
            "data_sources": [{"endpoint": f"/{name}", "alias": name}],
            "views": [
                {
                    "type": "table",
                    "default": True,
                    "columns": table_columns,
                },
                {
                    "type": "kanban",
                    "group_by": "state",
                    "card_fields": card_fields,
                },
            ],
            "actions": {
                "create": {"form": "auto"},
            },
        },
    }

    return yaml.dump(contract, default_flow_style=False, sort_keys=False, allow_unicode=True)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_factory/test_emitters.py -v`
Expected: All 5 tests PASS

- [ ] **Step 8: Commit**

```bash
git add factory/emitters/ tests/test_factory/test_emitters.py
git commit -m "feat(#1/T6): contract emitters — entity, workflow, route, page"
```

---

### Task 5: Editor Preview

**Files:**
- Create: `factory/preview/editor.py`
- Create: `factory/preview/__init__.py`

- [ ] **Step 1: Create the preview directory**

```bash
mkdir -p factory/preview
touch factory/preview/__init__.py
```

- [ ] **Step 2: Implement the editor preview**

```python
# factory/preview/editor.py
"""Editor preview — write contracts to temp dir, open in $EDITOR.

After the Factory generates contracts, it writes them to a temporary
directory and opens them in the user's preferred editor. The user
reviews, makes any edits, then returns to the Factory which reads
back the (possibly modified) files.

If $EDITOR is not set, falls back to terminal preview using Rich.

Usage:
    from factory.preview.editor import preview_contracts

    accepted, files = preview_contracts({
        "entities/patient.contract.yaml": yaml_content,
        "workflows/patient_lifecycle.contract.yaml": yaml_content,
    })
    if accepted:
        for path, content in files.items():
            # Write to actual domain directory
"""

from __future__ import annotations

import os
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.syntax import Syntax

logger = logging.getLogger(__name__)
console = Console()


def preview_contracts(
    contracts: dict[str, str],
    domain: str = "",
) -> tuple[bool, dict[str, str]]:
    """Preview generated contracts in $EDITOR or terminal.

    Writes contracts to a temp directory, opens the editor, then reads
    them back. The user can modify contracts in the editor before accepting.

    Args:
        contracts: Map of relative path → YAML content.
        domain: Domain name (for display).

    Returns:
        Tuple of (accepted: bool, files: dict[path, content]).
        If accepted, files contains the (possibly edited) content.
    """
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))

    if editor:
        return _preview_in_editor(contracts, editor)
    else:
        return _preview_in_terminal(contracts)


def _preview_in_editor(
    contracts: dict[str, str],
    editor: str,
) -> tuple[bool, dict[str, str]]:
    """Write to temp dir, open editor, read back."""
    with tempfile.TemporaryDirectory(prefix="specora-preview-") as tmpdir:
        tmp = Path(tmpdir)

        # Write all contracts
        for rel_path, content in contracts.items():
            file_path = tmp / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        # Show summary
        console.print()
        console.print(f"[bold]Generated {len(contracts)} contracts:[/bold]")
        for path in sorted(contracts.keys()):
            console.print(f"  [green]+[/green] {path}")
        console.print()
        console.print(f"[dim]Opening in {editor}... Review and close the editor to continue.[/dim]")

        # Open editor on the directory
        try:
            subprocess.run([editor, str(tmp)], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning("Editor failed: %s. Falling back to terminal preview.", e)
            return _preview_in_terminal(contracts)

        # Read back (possibly modified) content
        result = {}
        for rel_path in contracts.keys():
            file_path = tmp / rel_path
            if file_path.exists():
                result[rel_path] = file_path.read_text(encoding="utf-8")
            else:
                # User deleted the file — skip it
                logger.info("File removed during preview: %s", rel_path)

        # Ask for confirmation
        console.print()
        response = console.input("[bold]Write these contracts? [Y/n] [/bold]").strip().lower()
        accepted = response in ("", "y", "yes")

        return accepted, result


def _preview_in_terminal(contracts: dict[str, str]) -> tuple[bool, dict[str, str]]:
    """Display contracts in the terminal using Rich syntax highlighting."""
    console.print()
    console.print(f"[bold]Generated {len(contracts)} contracts:[/bold]")
    console.print()

    for path, content in sorted(contracts.items()):
        console.print(f"[bold cyan]── {path} ──[/bold cyan]")
        syntax = Syntax(content, "yaml", theme="monokai", line_numbers=True)
        console.print(syntax)
        console.print()

    response = console.input("[bold]Write these contracts? [Y/n] [/bold]").strip().lower()
    accepted = response in ("", "y", "yes")

    return accepted, dict(contracts)
```

- [ ] **Step 3: Commit**

```bash
git add factory/preview/
git commit -m "feat(#1/T7): editor preview — $EDITOR with Rich terminal fallback"
```

---

### Task 6: Interview Framework + Entity Interview

**Files:**
- Create: `factory/interviews/base.py`
- Create: `factory/interviews/entity.py`
- Create: `factory/interviews/workflow.py`
- Create: `factory/interviews/domain.py`
- Create: `engine/context.py`

- [ ] **Step 1: Implement the interview base framework**

```python
# factory/interviews/base.py
"""Interview framework — the conversational loop that powers the Factory.

An Interview is a multi-turn conversation between the user and an LLM.
The framework handles:
  - Sending user input to the LLM with contract-aware system prompts
  - Parsing structured data from LLM responses
  - Maintaining conversation history
  - Integrating with session persistence for resume

Usage:
    from factory.interviews.base import Interview
    from engine.engine import LLMEngine

    interview = Interview(engine, system_prompt="You are a domain analyst.")
    response = interview.ask_user("What entities does your system have?")
    # User types their answer
    structured = interview.ask_llm_to_parse(user_answer, parse_schema)
"""

from __future__ import annotations

import json
import logging
import yaml
from typing import Any, Optional

from rich.console import Console

from engine.engine import LLMEngine
from engine.providers.base import Message

logger = logging.getLogger(__name__)
console = Console()


class Interview:
    """A multi-turn conversation between user and LLM.

    The interview manages the conversation flow: prompting the user,
    sending their input to the LLM, and extracting structured data.

    Attributes:
        engine: The LLM engine for AI responses.
        system_prompt: System prompt that sets the LLM's role.
        history: Conversation history for context continuity.
    """

    def __init__(self, engine: LLMEngine, system_prompt: str = ""):
        self.engine = engine
        self.system_prompt = system_prompt
        self.history: list[Message] = []

    def ask_user(self, prompt: str) -> str:
        """Display a prompt and get user input.

        Args:
            prompt: The question to display.

        Returns:
            The user's text input.
        """
        console.print()
        response = console.input(f"  [bold]{prompt}[/bold]\n  [green]>[/green] ").strip()
        self.history.append(Message(role="user", content=response))
        return response

    def show(self, message: str) -> None:
        """Display a message to the user (from the Factory)."""
        console.print(f"  {message}")

    def ask_llm(self, user_input: str, instruction: str = "") -> str:
        """Send user input to the LLM and get a text response.

        Args:
            user_input: What the user said.
            instruction: Additional instruction appended to the user message.

        Returns:
            The LLM's text response.
        """
        full_input = user_input
        if instruction:
            full_input += f"\n\n[Internal instruction: {instruction}]"

        messages = list(self.history)
        messages.append(Message(role="user", content=full_input))

        response = self.engine.chat(
            messages=messages,
            system=self.system_prompt,
            temperature=0.3,  # Lower temp for more consistent structured output
        )

        self.history.append(Message(role="assistant", content=response.content))
        return response.content

    def ask_llm_structured(self, user_input: str, instruction: str) -> dict:
        """Ask the LLM and parse the response as YAML or JSON.

        The LLM is instructed to respond with structured data.
        Tries YAML parsing first, then JSON.

        Args:
            user_input: What the user said.
            instruction: What structure we want back.

        Returns:
            Parsed dict from the LLM's response.

        Raises:
            InterviewParseError: If the response can't be parsed.
        """
        full_instruction = (
            f"{instruction}\n\n"
            "Respond with ONLY valid YAML (no markdown fences, no explanation). "
            "Start your response with the YAML directly."
        )
        raw = self.ask_llm(user_input, full_instruction)

        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (fences)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            return yaml.safe_load(cleaned)
        except yaml.YAMLError:
            pass

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        raise InterviewParseError(
            f"Could not parse LLM response as YAML or JSON:\n{raw[:500]}"
        )

    def confirm(self, message: str) -> bool:
        """Ask a yes/no confirmation question.

        Args:
            message: The confirmation prompt.

        Returns:
            True if user confirms, False otherwise.
        """
        response = console.input(f"  [bold]{message} [Y/n][/bold] ").strip().lower()
        return response in ("", "y", "yes")


class InterviewParseError(Exception):
    """Raised when an LLM response cannot be parsed into structured data."""

    pass
```

- [ ] **Step 2: Implement the contract-aware context builder**

```python
# engine/context.py
"""Contract-aware prompt builder — constructs system prompts with domain context.

Builds system prompts that give the LLM awareness of:
  - The Specora contract format
  - Valid field types and annotations
  - The stdlib mixins and workflows available
  - Existing entities in the domain (for reference detection)

Usage:
    from engine.context import build_system_prompt

    prompt = build_system_prompt("entity_interview", domain="veterinary",
                                  existing_entities=["patient", "owner"])
"""

from __future__ import annotations

FIELD_TYPES_REFERENCE = """
Valid field types:
  string   — Short text (names, codes, identifiers)
  integer  — Whole numbers (counts, years, IDs)
  number   — Decimal numbers (weights, prices, percentages)
  boolean  — True/false flags
  text     — Long text (descriptions, notes, content)
  array    — Lists (tags, items)
  object   — Nested structures
  datetime — Timestamps (ISO 8601)
  date     — Dates only (ISO 8601)
  uuid     — Unique identifiers
  email    — Email addresses

Special field features:
  required: true    — Must be provided on creation
  immutable: true   — Cannot change after creation
  enum: [a, b, c]   — Fixed set of allowed values
  computed: "now"    — Auto-set to current timestamp
  computed: "uuid"   — Auto-generated UUID
  references:        — Link to another entity
    entity: entity/domain/name
    display: field_name
    graph_edge: EDGE_NAME
""".strip()

STDLIB_REFERENCE = """
Available standard library mixins (add via mixins list):
  mixin/stdlib/timestamped   — created_at, updated_at
  mixin/stdlib/identifiable  — id (UUID), number (sequential)
  mixin/stdlib/auditable     — created_at, updated_at, created_by, updated_by
  mixin/stdlib/taggable      — tags array
  mixin/stdlib/commentable   — comments array
  mixin/stdlib/soft_deletable — deleted_at, deleted_by, is_deleted

Available standard library workflows:
  workflow/stdlib/crud_lifecycle — active / archived
  workflow/stdlib/approval       — draft / submitted / approved / rejected
  workflow/stdlib/ticket         — new / assigned / in_progress / resolved / closed
""".strip()


def build_system_prompt(
    task: str,
    domain: str = "",
    existing_entities: list[str] | None = None,
) -> str:
    """Build a system prompt for a specific Factory task.

    Args:
        task: The interview type ("domain_discovery", "entity_interview",
              "workflow_interview", "explain").
        domain: The domain being built.
        existing_entities: Entity names already defined in this domain.

    Returns:
        Complete system prompt string.
    """
    entities_ctx = ""
    if existing_entities:
        entity_list = ", ".join(existing_entities)
        entities_ctx = f"\nExisting entities in this domain: {entity_list}\n"

    prompts = {
        "domain_discovery": f"""You are a domain analyst helping a developer define their software domain.

Your job is to discover the core entities (data models) their system needs.

Ask about:
1. What the system does (one sentence)
2. What are the main things being tracked/managed
3. How those things relate to each other
4. Whether any have lifecycles (state machines)

When you have a clear picture, output a YAML list of entity names with brief descriptions.

Domain being built: {domain}
{entities_ctx}""",

        "entity_interview": f"""You are a data modeling expert helping define entity fields for the Specora contract system.

Given a description of an entity, determine:
1. What fields it needs (name, type, description, required, constraints)
2. Whether any fields reference other entities
3. Whether it needs a state machine
4. Which stdlib mixins to include

{FIELD_TYPES_REFERENCE}

{STDLIB_REFERENCE}

Domain: {domain}
{entities_ctx}

Output structured YAML for the entity's fields, references, and mixins.
Always include mixin/stdlib/timestamped and mixin/stdlib/identifiable unless explicitly unwanted.""",

        "workflow_interview": f"""You are a workflow designer helping define state machines for entities.

Given a description of an entity's lifecycle, determine:
1. What states it has (with labels and categories: open/hold/closed)
2. What transitions are valid
3. What guards (required fields) exist for transitions
4. Which states are terminal (no outgoing transitions)

Output structured YAML with initial, states, transitions, and guards.

Domain: {domain}""",

        "explain": f"""You are a technical documentation expert. Explain the given Specora contract in clear, plain English.

Cover:
- What the entity/workflow/page represents
- Its fields and their purposes
- Relationships to other entities
- State machine (if any)
- Which mixins are included

Be concise but thorough. Use bullet points.""",
    }

    return prompts.get(task, f"You are a helpful assistant working on the {domain} domain.")
```

- [ ] **Step 3: Implement the entity interview**

```python
# factory/interviews/entity.py
"""Entity interview — conversational entity field discovery.

Interviews the user about a single entity: its purpose, fields,
references, enums, and lifecycle. Uses the LLM to infer field types
and detect references to other entities.

Usage:
    from factory.interviews.entity import run_entity_interview

    data = run_entity_interview(engine, "patient", "veterinary",
                                 existing_entities=["owner"])
"""

from __future__ import annotations

import logging
from typing import Any

from engine.context import build_system_prompt
from engine.engine import LLMEngine
from factory.interviews.base import Interview, InterviewParseError

logger = logging.getLogger(__name__)


def run_entity_interview(
    engine: LLMEngine,
    entity_name: str,
    domain: str,
    description: str = "",
    existing_entities: list[str] | None = None,
) -> dict:
    """Run an interactive interview to define an entity's fields.

    Args:
        engine: The LLM engine.
        entity_name: Name of the entity being defined.
        domain: Domain name.
        description: Brief description of the entity (from domain discovery).
        existing_entities: Other entities in this domain (for reference detection).

    Returns:
        Dict with keys: description, fields, mixins, state_machine, number_prefix, icon
    """
    system = build_system_prompt(
        "entity_interview",
        domain=domain,
        existing_entities=existing_entities or [],
    )
    interview = Interview(engine, system_prompt=system)

    interview.show(f"[bold cyan]── Entity: {entity_name.replace('_', ' ').title()} ──[/bold cyan]")

    if not description:
        description = interview.ask_user(f"Describe what a '{entity_name}' is:")

    # Ask about fields
    fields_input = interview.ask_user(f"What fields does a {entity_name} have?")

    # Ask the LLM to structure the fields
    instruction = f"""
The user described a '{entity_name}' entity: {description}
They said it has these fields: {fields_input}

Generate a YAML mapping of field definitions. For each field, include:
- type (from the valid types list)
- description (brief)
- required: true if it seems essential
- enum: [...] if the field has a fixed set of values
- references: if the field points to another entity (check existing entities: {existing_entities or []})

Also include:
- mixins: list of mixin FQNs to include
- state_machine_needed: true/false
- description: one-sentence entity description

Format:
fields:
  field_name:
    type: string
    required: true
    description: "..."
mixins:
  - mixin/stdlib/timestamped
  - mixin/stdlib/identifiable
state_machine_needed: false
description: "..."
"""
    try:
        structured = interview.ask_llm_structured(fields_input, instruction)
    except InterviewParseError:
        interview.show("[yellow]Couldn't parse the field structure. Let me try again...[/yellow]")
        structured = {"fields": {}, "mixins": ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"]}

    fields = structured.get("fields", {})
    mixins = structured.get("mixins", ["mixin/stdlib/timestamped", "mixin/stdlib/identifiable"])
    needs_workflow = structured.get("state_machine_needed", False)
    entity_desc = structured.get("description", description)

    # Ask about lifecycle if the LLM thinks it needs one, or ask the user
    workflow_fqn = None
    if needs_workflow:
        interview.show(f"[dim]It looks like {entity_name} has a lifecycle.[/dim]")
        if interview.confirm(f"Does {entity_name} have a state machine (lifecycle)?"):
            workflow_fqn = f"workflow/{domain}/{entity_name}_lifecycle"
    else:
        if interview.confirm(f"Does {entity_name} have a lifecycle (state machine)?"):
            workflow_fqn = f"workflow/{domain}/{entity_name}_lifecycle"

    result = {
        "description": entity_desc,
        "fields": fields,
        "mixins": mixins,
    }
    if workflow_fqn:
        result["state_machine"] = workflow_fqn

    return result
```

- [ ] **Step 4: Implement the workflow interview**

```python
# factory/interviews/workflow.py
"""Workflow interview — conversational state machine discovery.

Interviews the user about an entity's lifecycle: states, transitions,
guards, and terminal states. Uses the LLM to structure the responses.

Usage:
    from factory.interviews.workflow import run_workflow_interview

    data = run_workflow_interview(engine, "patient_lifecycle", "veterinary",
                                   entity_name="patient")
"""

from __future__ import annotations

import logging

from engine.context import build_system_prompt
from engine.engine import LLMEngine
from factory.interviews.base import Interview, InterviewParseError

logger = logging.getLogger(__name__)


def run_workflow_interview(
    engine: LLMEngine,
    workflow_name: str,
    domain: str,
    entity_name: str = "",
) -> dict:
    """Run an interactive interview to define a state machine.

    Args:
        engine: The LLM engine.
        workflow_name: Name of the workflow.
        domain: Domain name.
        entity_name: The entity this workflow is for.

    Returns:
        Dict with keys: initial, states, transitions, guards, description
    """
    system = build_system_prompt("workflow_interview", domain=domain)
    interview = Interview(engine, system_prompt=system)

    interview.show(f"[bold cyan]── Workflow: {entity_name} lifecycle ──[/bold cyan]")

    states_input = interview.ask_user(
        f"What states can a {entity_name} be in? (e.g., active, inactive, archived)"
    )

    instruction = f"""
The user is defining a lifecycle for '{entity_name}'.
They said the states are: {states_input}

Generate a YAML workflow with:
- initial: the starting state
- states: each state with label, category (open/hold/closed), and terminal flag
- transitions: valid state transitions
- guards: required fields for transitions (if any)
- description: one-sentence workflow description

Format:
initial: state_name
states:
  state_name:
    label: "Human Label"
    category: open
    terminal: false
transitions:
  state_name:
    - other_state
guards:
  "from_state -> to_state":
    require_fields: [field_name]
description: "..."
"""

    try:
        structured = interview.ask_llm_structured(states_input, instruction)
    except InterviewParseError:
        interview.show("[yellow]Couldn't parse the workflow structure. Using defaults.[/yellow]")
        structured = {
            "initial": "active",
            "states": {"active": {"label": "Active", "category": "open"}, "inactive": {"label": "Inactive", "category": "closed"}},
            "transitions": {"active": ["inactive"], "inactive": ["active"]},
        }

    return {
        "initial": structured.get("initial", "active"),
        "states": structured.get("states", {}),
        "transitions": structured.get("transitions", {}),
        "guards": structured.get("guards", {}),
        "description": structured.get("description", f"Lifecycle for {entity_name}"),
    }
```

- [ ] **Step 5: Implement the domain discovery interview**

```python
# factory/interviews/domain.py
"""Domain discovery interview — the opening conversation.

Discovers what the user is building: the domain name, description,
and initial set of entities. This is the first phase of `specora factory new`.

Usage:
    from factory.interviews.domain import run_domain_interview

    domain, description, entities = run_domain_interview(engine)
"""

from __future__ import annotations

import logging

from engine.context import build_system_prompt
from engine.engine import LLMEngine
from factory.interviews.base import Interview, InterviewParseError

logger = logging.getLogger(__name__)


def run_domain_interview(engine: LLMEngine) -> tuple[str, str, list[dict]]:
    """Run the domain discovery interview.

    Args:
        engine: The LLM engine.

    Returns:
        Tuple of (domain_name, description, entities) where entities
        is a list of dicts with 'name' and 'description' keys.
    """
    system = build_system_prompt("domain_discovery")
    interview = Interview(engine, system_prompt=system)

    interview.show("[bold]Welcome to the Specora Factory.[/bold]")
    interview.show("[dim]I'll help you define your domain through conversation.[/dim]")

    # Get domain description
    purpose = interview.ask_user("What are you building?")

    # Infer domain name from description
    instruction = """
Based on the user's description, suggest:
1. A short snake_case domain name (e.g., "veterinary", "logistics", "healthcare")
2. A one-sentence description
3. The core entities (3-8) with brief descriptions

Format as YAML:
domain: name
description: "one sentence"
entities:
  - name: entity_name
    description: "brief description"
  - name: entity_name
    description: "brief description"
"""

    try:
        structured = interview.ask_llm_structured(purpose, instruction)
    except InterviewParseError:
        interview.show("[yellow]Let me ask more specifically...[/yellow]")
        domain_name = interview.ask_user("What should we call this domain? (one word, snake_case)")
        desc = interview.ask_user("Describe it in one sentence:")
        entities_raw = interview.ask_user("What are the main things you need to track? (comma-separated)")
        entities = [{"name": e.strip().lower().replace(" ", "_"), "description": ""} for e in entities_raw.split(",")]
        return domain_name, desc, entities

    domain_name = structured.get("domain", "my_domain")
    description = structured.get("description", purpose)
    entities = structured.get("entities", [])

    # Confirm with user
    interview.show(f"\n  [bold]Domain:[/bold] {domain_name}")
    interview.show(f"  [bold]Description:[/bold] {description}")
    interview.show(f"  [bold]Entities:[/bold]")
    for e in entities:
        interview.show(f"    - {e['name']}: {e.get('description', '')}")

    if not interview.confirm("\n  Does this look right?"):
        domain_name = interview.ask_user("Domain name (snake_case):")
        description = interview.ask_user("Description:")
        entities_raw = interview.ask_user("Entities (comma-separated):")
        entities = [{"name": e.strip().lower().replace(" ", "_"), "description": ""} for e in entities_raw.split(",")]

    return domain_name, description, entities
```

- [ ] **Step 6: Commit**

```bash
git add factory/interviews/ engine/context.py
git commit -m "feat(#1/T2,T4,T5): interview framework, entity + workflow + domain interviews"
```

---

### Task 7: `specora factory new` Command

**Files:**
- Create: `factory/cli/__init__.py`
- Create: `factory/cli/new.py`
- Modify: `forge/cli/main.py`

- [ ] **Step 1: Create factory CLI directory**

```bash
mkdir -p factory/cli
touch factory/cli/__init__.py
```

- [ ] **Step 2: Implement the `factory new` command**

```python
# factory/cli/new.py
"""specora factory new — full domain bootstrap from conversation.

The showstopper command. Interviews the user about their domain,
generates all contracts (entities, workflows, routes, pages),
opens them in $EDITOR for review, and writes them atomically.

Usage:
    specora factory new
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path

import click
from rich.console import Console

from engine.engine import LLMEngine
from engine.config import EngineConfigError
from factory.session import Session
from factory.interviews.domain import run_domain_interview
from factory.interviews.entity import run_entity_interview
from factory.interviews.workflow import run_workflow_interview
from factory.emitters.entity_emitter import emit_entity
from factory.emitters.workflow_emitter import emit_workflow
from factory.emitters.route_emitter import emit_route
from factory.emitters.page_emitter import emit_page
from factory.preview.editor import preview_contracts

logger = logging.getLogger(__name__)
console = Console()


@click.command("new")
def factory_new() -> None:
    """Bootstrap a new domain from a conversational interview."""

    # Initialize LLM engine
    try:
        engine = LLMEngine.from_env()
        console.print(f"[dim]Using model: {engine.model_id} ({engine.strategy} strategy)[/dim]")
    except EngineConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Check for resumable session
    session = Session()
    if session.can_resume():
        console.print("[yellow]Found a saved session.[/yellow]")
        if click.confirm("  Resume previous session?", default=True):
            session.resume()
            console.print(f"[green]Resumed:[/green] domain '{session.state.domain}' "
                         f"(phase: {session.state.phase})")
        else:
            session.cleanup()

    # Phase 1: Domain discovery
    if session.state.phase == "domain_discovery" or not session.state.domain:
        try:
            domain, description, entities = run_domain_interview(engine)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Session saved. Run 'specora factory new' to resume.[/yellow]")
            session.save()
            sys.exit(0)

        session.start(domain, description)
        session.state.entities_discovered = [e["name"] for e in entities]
        # Store descriptions for later
        for e in entities:
            session.state.entity_data[e["name"]] = {"description": e.get("description", "")}
        session.state.phase = "entity_interview"
        session.save()

    domain = session.state.domain
    console.print(f"\n[bold]Building domain: {domain}[/bold]")

    # Phase 2: Entity interviews
    if session.state.phase == "entity_interview":
        existing = list(session.state.entity_data.keys())
        for entity_name in session.state.entities_discovered:
            # Skip if already fully interviewed
            if session.state.entity_data.get(entity_name, {}).get("fields"):
                continue

            try:
                desc = session.state.entity_data.get(entity_name, {}).get("description", "")
                data = run_entity_interview(
                    engine, entity_name, domain,
                    description=desc,
                    existing_entities=existing,
                )
                session.add_entity(entity_name, data)
                session.save()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[yellow]Session saved. Resume with 'specora factory new'.[/yellow]")
                session.save()
                sys.exit(0)

        session.state.phase = "workflow_interview"
        session.save()

    # Phase 3: Workflow interviews for entities that need them
    if session.state.phase == "workflow_interview":
        for entity_name, data in session.state.entity_data.items():
            workflow_fqn = data.get("state_machine")
            if not workflow_fqn:
                continue
            workflow_name = workflow_fqn.split("/")[-1]

            # Skip if already interviewed
            if workflow_name in session.state.workflow_data:
                continue

            try:
                wf_data = run_workflow_interview(
                    engine, workflow_name, domain, entity_name=entity_name,
                )
                session.add_workflow(workflow_name, wf_data)
                session.save()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[yellow]Session saved. Resume with 'specora factory new'.[/yellow]")
                session.save()
                sys.exit(0)

        session.state.phase = "emit"
        session.save()

    # Phase 4: Emit contracts
    if session.state.phase == "emit":
        contracts: dict[str, str] = {}

        # Emit entities
        for entity_name, data in session.state.entity_data.items():
            yaml_str = emit_entity(entity_name, domain, data)
            contracts[f"entities/{entity_name}.contract.yaml"] = yaml_str

        # Emit workflows
        for wf_name, wf_data in session.state.workflow_data.items():
            yaml_str = emit_workflow(wf_name, domain, wf_data)
            contracts[f"workflows/{wf_name}.contract.yaml"] = yaml_str

        # Emit routes and pages for each entity
        for entity_name, data in session.state.entity_data.items():
            entity_fqn = f"entity/{domain}/{entity_name}"
            workflow_fqn = data.get("state_machine", "")
            plural = entity_name + "s"  # simple pluralization

            # Route
            route_yaml = emit_route(plural, domain, entity_fqn, workflow_fqn)
            contracts[f"routes/{plural}.contract.yaml"] = route_yaml

            # Page
            field_names = list(data.get("fields", {}).keys())
            page_yaml = emit_page(plural, domain, entity_fqn, field_names)
            contracts[f"pages/{plural}.contract.yaml"] = page_yaml

        # Preview
        console.print(f"\n[bold]Generated {len(contracts)} contracts for domain '{domain}'[/bold]")
        accepted, final_contracts = preview_contracts(contracts, domain=domain)

        if not accepted:
            console.print("[yellow]Cancelled. Session saved for later.[/yellow]")
            session.save()
            return

        # Write atomically
        domain_path = Path("domains") / domain
        for rel_path, content in final_contracts.items():
            file_path = domain_path / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            console.print(f"  [green]wrote[/green] {file_path}")

        # Cleanup session
        session.cleanup()

        console.print(f"\n[bold green]Domain '{domain}' created with {len(final_contracts)} contracts.[/bold green]")
        console.print()
        console.print("Next steps:")
        console.print(f"  specora forge validate domains/{domain}")
        console.print(f"  specora forge generate domains/{domain}")
```

- [ ] **Step 3: Wire the factory commands into the main CLI**

Add the factory command group to `forge/cli/main.py`. Insert this after the existing `diff` group:

```python
# Add to forge/cli/main.py — after the diff group, before _get_generators

# =============================================================================
# Factory commands
# =============================================================================

@cli.group()
def factory() -> None:
    """The Factory — conversational contract authoring (LLM-powered)."""
    pass

# Import and register factory commands
from factory.cli.new import factory_new
factory.add_command(factory_new, "new")
```

- [ ] **Step 4: Run the full CLI to verify wiring**

Run: `cd C:\Users\cheap\OneDrive\Documents\projects\specora-core && python -m forge.cli.main factory --help`
Expected:
```
Usage: main.py factory [OPTIONS] COMMAND [COMMANDS]

  The Factory — conversational contract authoring (LLM-powered).

Commands:
  new  Bootstrap a new domain from a conversational interview.
```

- [ ] **Step 5: Commit**

```bash
git add factory/cli/ forge/cli/main.py
git commit -m "feat(#1/T8): specora factory new — full domain bootstrap from conversation"
```

---

### Task 8: Integration Test — End-to-End Validation

**Files:**
- Test: `tests/test_factory/test_integration.py`

- [ ] **Step 1: Write an integration test that validates emitted contracts compile**

```python
# tests/test_factory/test_integration.py
"""Integration test — verify emitted contracts pass the Forge compiler."""

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
        domain_dir = Path(tmpdir) / "domains" / "test_domain"

        # Emit a complete domain
        entity_data = {
            "description": "A test entity",
            "fields": {
                "name": {"type": "string", "required": True, "description": "The name"},
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
            "workflows/widget_lifecycle.contract.yaml": emit_workflow("widget_lifecycle", "test_domain", workflow_data),
            "routes/widgets.contract.yaml": emit_route("widgets", "test_domain", "entity/test_domain/widget", "workflow/test_domain/widget_lifecycle"),
            "pages/widgets.contract.yaml": emit_page("widgets", "test_domain", "entity/test_domain/widget", ["name", "count", "active", "state"]),
        }

        for rel_path, content in files.items():
            file_path = domain_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        # Compile — this should succeed
        compiler = Compiler(contract_root=domain_dir)
        ir = compiler.compile()

        assert ir.domain == "test_domain"
        assert len(ir.entities) == 1
        assert ir.entities[0].name == "widget"
        assert len(ir.entities[0].fields) >= 3  # widget fields + mixin fields
        assert len(ir.workflows) >= 1
        assert len(ir.routes) == 1
        assert len(ir.pages) == 1
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/test_factory/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run ALL tests to verify nothing is broken**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_factory/test_integration.py
git commit -m "test(#1): integration test — Factory emitters → Forge compiler"
```

---

## Verification Checklist

After all tasks are complete:

1. `pip install -e ".[llm]"` — installs cleanly with LLM deps
2. `pytest tests/ -v` — all tests pass
3. `python -m forge.cli.main factory --help` — shows `new` subcommand
4. `python -m forge.cli.main factory new` — (with API key set) runs the full interview
5. Emitted contracts pass `specora forge validate`
6. Emitted contracts compile with `specora forge compile`
7. Generated code is produced by `specora forge generate`
8. Ctrl+C mid-interview saves session; re-running `factory new` offers to resume
