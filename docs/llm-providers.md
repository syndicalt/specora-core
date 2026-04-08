# LLM Providers

> **Note**: LLM providers are needed for Healer Tier 2-3 fixes, Factory contract authoring, and interactive chat. The Forge compiler (Tier 1) is purely deterministic and does not require an LLM. Your LLM coding agent (Claude Code, Cursor, Windsurf) is separate from the provider configured here -- the provider here is used by Specora Core's internal features.

Specora Core supports six LLM providers for features that require AI: the Factory (contract authoring), the Healer (Tier 2-3 structural fixes), the Extractor (future enhancements), and interactive chat.

The Forge compiler (Tier 1) does not use LLMs. It is purely deterministic.

---

## Provider Overview

| Provider | Models | API Style | Key Variable | Base URL |
|----------|--------|-----------|-------------|----------|
| **Anthropic** | Claude Opus 4.6, Claude Sonnet 4.6, Claude Haiku 4.5 | Anthropic Messages API | `ANTHROPIC_API_KEY` | Default (Anthropic) |
| **OpenAI** | GPT-4o, GPT-4o Mini, o3-mini | OpenAI Chat Completions | `OPENAI_API_KEY` | Default (OpenAI) |
| **xAI** | Grok 3, Grok 3 Mini | OpenAI-compatible | `XAI_API_KEY` | `https://api.x.ai/v1` |
| **Z.AI** | GLM-5.1, GLM-5, GLM-4.7-flash, GLM-4.5-flash | OpenAI-compatible | `ZAI_API_KEY` | `https://api.z.ai/api/paas/v4/` |
| **Google** | Gemini 2.5 Pro | OpenAI-compatible | `GOOGLE_API_KEY` | Default (Google) |
| **Ollama** | Llama 3.3:70b, Qwen 2.5:32b, Mistral:7b | OpenAI-compatible (local) | `OLLAMA_BASE_URL` | User-configured |

---

## Auto-Detection Priority

When no `SPECORA_AI_MODEL` is set, the engine probes environment variables in this order and uses the first one found:

```
1. ANTHROPIC_API_KEY  ->  claude-sonnet-4-6
2. OPENAI_API_KEY     ->  gpt-4o
3. XAI_API_KEY        ->  grok-3-mini
4. ZAI_API_KEY        ->  glm-4.7-flash
5. OLLAMA_BASE_URL    ->  llama3.3:70b
```

If none are set, the engine raises `EngineConfigError` with:

```
No LLM provider configured. Set one of: SPECORA_AI_MODEL,
ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY, ZAI_API_KEY, or OLLAMA_BASE_URL.
```

---

## Configuration

### Quick Setup (Pick One)

**Anthropic (recommended):**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**OpenAI:**

```bash
export OPENAI_API_KEY=sk-...
```

**xAI (Grok):**

```bash
export XAI_API_KEY=xai-...
```

**Z.AI (GLM -- free tier available):**

```bash
export ZAI_API_KEY=...
```

**Ollama (local, no API key):**

```bash
export OLLAMA_BASE_URL=http://localhost:11434
```

### Model Override

Force a specific model regardless of which keys are set:

```bash
export SPECORA_AI_MODEL=claude-opus-4-6
export ANTHROPIC_API_KEY=sk-ant-...
```

Or use a free Z.AI model:

```bash
export SPECORA_AI_MODEL=glm-4.7-flash
export ZAI_API_KEY=...
```

When `SPECORA_AI_MODEL` is set, the engine looks up the model in the registry, determines its provider, and uses the corresponding API key.

---

## Providers in Detail

### Anthropic (Claude)

**Sign up:** https://console.anthropic.com/

**Environment variable:** `ANTHROPIC_API_KEY`

**Recommended model:** `claude-sonnet-4-6` (best balance of speed and capability)

| Model | Tier | Context | Tools | Structured Output | Notes |
|-------|------|---------|-------|-------------------|-------|
| `claude-opus-4-6` | frontier | 200k | yes | yes | Most capable |
| `claude-sonnet-4-6` | frontier | 200k | yes | yes | Default. Best balance. |
| `claude-haiku-4-5` | mid | 200k | yes | yes | Fast and cheap |

Anthropic is the recommended provider because Claude models have the best understanding of YAML contract structures and produce the most reliable structural fixes.

### OpenAI (GPT)

**Sign up:** https://platform.openai.com/api-keys

**Environment variable:** `OPENAI_API_KEY`

| Model | Tier | Context | Tools | Structured Output | Notes |
|-------|------|---------|-------|-------------------|-------|
| `gpt-4o` | frontier | 128k | yes | yes | Default when OPENAI_API_KEY is set |
| `gpt-4o-mini` | mid | 128k | yes | yes | Cost-effective |
| `o3-mini` | frontier | 200k | yes | yes | Reasoning model |

### xAI (Grok)

**Sign up:** https://console.x.ai/

**Environment variable:** `XAI_API_KEY`

**Base URL:** `https://api.x.ai/v1` (set automatically)

Uses the OpenAI-compatible API. No need to set the base URL manually -- the engine configures it when `XAI_API_KEY` is detected.

| Model | Tier | Context | Tools | Structured Output | Notes |
|-------|------|---------|-------|-------------------|-------|
| `grok-3` | frontier | 131k | yes | yes | xAI flagship |
| `grok-3-mini` | frontier | 131k | yes | yes | Default when XAI_API_KEY is set |

### Z.AI (GLM)

**Sign up:** https://z.ai (get key from Profile > API Keys)

**Environment variable:** `ZAI_API_KEY`

**Base URL:** `https://api.z.ai/api/paas/v4/` (set automatically)

Z.AI offers **free tier models** (`glm-4.7-flash`, `glm-4.5-flash`) that are suitable for basic contract operations. The paid models (`glm-5.1`, `glm-5`) are frontier-class.

| Model | Tier | Context | Tools | Structured Output | Notes |
|-------|------|---------|-------|-------------------|-------|
| `glm-5.1` | frontier | 128k | yes | yes | Flagship (paid) |
| `glm-5` | frontier | 128k | yes | yes | Standard flagship (paid) |
| `glm-4.7-flash` | mid | 128k | yes | yes | **Free.** Default when ZAI_API_KEY is set |
| `glm-4.5-flash` | mid | 128k | yes | yes | **Free.** |

### Google (Gemini)

**Sign up:** https://aistudio.google.com/apikey

**Environment variable:** `GOOGLE_API_KEY`

| Model | Tier | Context | Tools | Structured Output | Notes |
|-------|------|---------|-------|-------------------|-------|
| `gemini-2.5-pro` | frontier | 1M | yes | yes | Largest context window |

### Ollama (Local)

**Install:** https://ollama.com/

**Environment variable:** `OLLAMA_BASE_URL` (e.g., `http://localhost:11434`)

No API key required. Models run on your own hardware.

Setup:

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull llama3.3:70b

# Set the base URL
export OLLAMA_BASE_URL=http://localhost:11434
```

| Model | Tier | Context | Tools | Structured Output | Notes |
|-------|------|---------|-------|-------------------|-------|
| `llama3.3:70b` | local | 128k | no | yes | Default when OLLAMA_BASE_URL is set |
| `qwen2.5:32b` | local | 32k | no | yes | Smaller, faster |
| `mistral:7b` | local | 32k | no | no | Smallest. Prompt-only strategy. |

Local models do not support tool use. The engine falls back to `structured_output` or `prompt` strategy automatically.

---

## Model Registry

The model registry (`engine/registry.py`) maps model IDs to capability metadata. The engine uses this to select the best interaction strategy.

### Capabilities

Each model declares:

| Capability | Description |
|-----------|-------------|
| `provider` | API provider: `anthropic`, `openai`, or `local` |
| `supports_tools` | Can the model use tool/function calling? |
| `supports_structured_output` | Can the model return structured JSON? |
| `max_context` | Maximum context window (tokens) |
| `tier` | Quality tier: `frontier`, `mid`, or `local` |

### Interaction Strategy

The engine picks the strongest strategy the model supports:

```
1. tools              (if supports_tools = true)
2. structured_output  (if supports_structured_output = true)
3. prompt             (fallback)
```

- **Tools strategy** -- Uses function/tool calling to get structured responses. Most reliable.
- **Structured output strategy** -- Uses JSON mode or schema-constrained output. Good reliability.
- **Prompt strategy** -- Raw text output, parsed manually. Least reliable. Used by `mistral:7b`.

### Full Model List

```
ANTHROPIC:
  claude-opus-4-6        frontier  200k ctx  tools+structured
  claude-sonnet-4-6      frontier  200k ctx  tools+structured  (RECOMMENDED)
  claude-haiku-4-5       mid       200k ctx  tools+structured

OPENAI:
  gpt-4o                 frontier  128k ctx  tools+structured
  gpt-4o-mini            mid       128k ctx  tools+structured
  o3-mini                frontier  200k ctx  tools+structured

xAI (Grok):
  grok-3                 frontier  131k ctx  tools+structured
  grok-3-mini            frontier  131k ctx  tools+structured

GOOGLE:
  gemini-2.5-pro         frontier  1M ctx    tools+structured

Z.AI (GLM):
  glm-5.1                frontier  128k ctx  tools+structured
  glm-5                  frontier  128k ctx  tools+structured
  glm-4.7-flash          mid       128k ctx  tools+structured  (FREE)
  glm-4.5-flash          mid       128k ctx  tools+structured  (FREE)

OLLAMA (Local):
  llama3.3:70b           local     128k ctx  structured_output
  qwen2.5:32b            local     32k ctx   structured_output
  mistral:7b             local     32k ctx   prompt only
```

---

## Which Features Need LLM

| Feature | LLM Required | Notes |
|---------|-------------|-------|
| `spc forge validate` | No | Pure JSON Schema validation |
| `spc forge compile` | No | Deterministic IR compilation |
| `spc forge generate` | No | Deterministic code generation |
| `spc forge graph` | No | Dependency graph analysis |
| `spc healer fix` (Tier 1) | No | Deterministic normalization |
| `spc healer fix` (Tier 2-3) | **Yes** | LLM proposes structural fixes |
| `spc factory new` | **Yes** | LLM interviews and writes contracts |
| `spc extract` | No | Static analysis of source files |
| REPL `/chat` | **Yes** | Interactive LLM conversation |
| REPL `/factory` | **Yes** | Interactive contract authoring |

---

## Engine Configuration (`engine/config.py`)

The `EngineConfig.from_env()` method resolves all provider details from environment variables. Callers never need to specify API keys, base URLs, or model capabilities manually.

```python
from engine.config import EngineConfig

config = EngineConfig.from_env()
print(config.model_id)      # "claude-sonnet-4-6"
print(config.strategy)      # "tools"
print(config.api_key)       # "sk-ant-..."
print(config.base_url)      # None (default for Anthropic)
```

### EngineConfig Fields

| Field | Type | Description |
|-------|------|-------------|
| `model_id` | str | Resolved model ID (e.g., `claude-sonnet-4-6`) |
| `capabilities` | ModelCapabilities | What the model supports |
| `api_key` | str or None | API key (None for local models) |
| `base_url` | str or None | Override base URL (None = provider default) |
| `strategy` | str | `tools`, `structured_output`, or `prompt` |

### Error Handling

If no provider is configured:

```python
from engine.config import EngineConfig, EngineConfigError

try:
    config = EngineConfig.from_env()
except EngineConfigError as e:
    print(e)
    # "No LLM provider configured. Set one of: ..."
```

If `SPECORA_AI_MODEL` is set to an unknown model:

```python
# SPECORA_AI_MODEL=gpt-5-turbo
EngineConfig.from_env()
# raises EngineConfigError("Unknown model: gpt-5-turbo")
```

---

## Adding a Custom Model

The registry can be extended at runtime, but currently only supports built-in models. To add a new model, edit `engine/registry.py` and add an entry to `_BUILTIN_MODELS`:

```python
"my-custom-model": ModelCapabilities(
    provider="openai",           # or "anthropic", "local"
    supports_tools=True,
    supports_structured_output=True,
    max_context=128_000,
    tier="frontier",
    notes="My custom model.",
),
```

Then use it:

```bash
export SPECORA_AI_MODEL=my-custom-model
export OPENAI_API_KEY=sk-...  # or whichever provider
```
