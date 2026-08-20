# Future Token Predictor — User Guide

Predict LLM token usage and costs **before** making a single API call. Works across OpenAI, Anthropic, Google, Mistral, Cohere, AWS Bedrock, and local models.

---

## Table of Contents

1. [Installation](#installation)
2. [Quick Start](#quick-start)
3. [Using as an MCP Server](#using-as-an-mcp-server)
   - [VS Code / GitHub Copilot](#vs-code--github-copilot)
   - [Claude Desktop](#claude-desktop)
   - [Other MCP Clients](#other-mcp-clients)
4. [Using as a Python Library](#using-as-a-python-library)
   - [Natural Language Predictions](#natural-language-predictions)
   - [Structured Profile Predictions](#structured-profile-predictions)
   - [Formatted Reports](#formatted-reports)
   - [Cross-Provider Comparison](#cross-provider-comparison)
   - [Image Token Estimation](#image-token-estimation)
   - [Document Token Estimation](#document-token-estimation)
5. [MCP Tools Reference](#mcp-tools-reference)
6. [Self-Improving Predictions (Tier 2 & 3)](#self-improving-predictions-tier-2--3)
   - [Tier 2: Automatic Calibration](#tier-2-automatic-calibration)
   - [Tier 3: LLM-Assisted Estimation](#tier-3-llm-assisted-estimation)
7. [Supported Models & Providers](#supported-models--providers)
8. [Configuration Reference](#configuration-reference)
9. [Examples by Use Case](#examples-by-use-case)
10. [Troubleshooting](#troubleshooting)

---

## Installation

**Requirements:** Python 3.11+

```bash
# Clone the repository
git clone <repo-url>
cd FutureTokenPredictor

# Create a virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate
# Activate (macOS/Linux)
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"
```

Verify the install:

```bash
future-token-predictor --help
# or
python -m future_token_predictor.mcp_server
```

---

## Quick Start

**30-second version** — open a Python shell and run:

```python
from future_token_predictor import predict
from future_token_predictor.report import format_report

result = predict(description="GPT-4.1 chatbot, 100 users, 10 calls/day")
print(format_report(result))
```

You'll get a full report with per-call token breakdown, cost estimates with confidence intervals, and monthly/annual projections.

---

## Using as an MCP Server

The tool runs as an MCP (Model Context Protocol) server, so any MCP-compatible AI assistant can call it directly as a tool.

### VS Code / GitHub Copilot

A `.vscode/mcp.json` file is already included. To use it:

1. Open this project folder in VS Code
2. The MCP server is auto-registered — no extra configuration needed
3. In Copilot Chat (Agent mode), ask something like:

> "How many tokens and how much will it cost to run a ReAct agent with Claude Sonnet 4 and RAG over PDFs for 500 users?"

Copilot will call the `predict_token_usage` tool and return a full report.

**Manual VS Code configuration** (if working from a different project):

Create or edit `.vscode/mcp.json`:

```json
{
  "servers": {
    "future-token-predictor": {
      "type": "stdio",
      "command": "future-token-predictor"
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "future-token-predictor": {
      "command": "future-token-predictor",
      "transport": "stdio"
    }
  }
}
```

Then restart Claude Desktop. You can ask Claude:

> "Use the token predictor to estimate costs for a multi-agent workflow using GPT-4.1 with code interpreter, 200 users, 5 queries each per day"

### Other MCP Clients

Any MCP client that supports stdio transport can connect. The server command is:

```bash
future-token-predictor
```

Or if not installed globally:

```bash
python -m future_token_predictor.mcp_server
```

---

## Using as a Python Library

### Natural Language Predictions

The simplest way — just describe your use case in plain English:

```python
from future_token_predictor import predict

result = predict(description="A ReAct agent using Claude Sonnet 4 with RAG over PDFs, 100 users, 10 queries/day")

# Access results
print(f"Model: {result.model}")                           # claude-sonnet-4
print(f"Provider: {result.provider}")                      # anthropic
print(f"Method: {result.prediction_method}")               # tier1_heuristic
print(f"Tokens/call: {result.tokens_per_call.total:.0f}")  # ~6,860
print(f"Cost/call: ${result.cost_per_call.mean:.6f}")      # $0.044580
print(f"Daily: ${result.daily_cost_usd.mean:.2f}")         # $44.60
print(f"Monthly: ${result.monthly_cost_usd.mean:.2f}")     # $1,338.00
print(f"Annual: ${result.annual_cost_usd.mean:.2f}")       # $16,056.00
```

The classifier auto-detects from your description:
- **Model** — "GPT-4.1", "Claude Sonnet 4", "Gemini 2.5 Pro", etc.
- **Provider** — OpenAI, Anthropic, Google, etc. (inferred from model)
- **Agent pattern** — single call, ReAct, multi-agent, workflow, RAG
- **Modalities** — text, images, documents, audio
- **Tools** — file search, code interpreter, web search
- **Complexity** — low, medium, high
- **Scale** — users, calls per day

### Structured Profile Predictions

For precise control, build a `UseCaseProfile` directly:

```python
from future_token_predictor import predict, UseCaseProfile
from future_token_predictor.models.schemas import (
    Provider,
    AgentPattern,
    Modality,
    Tool,
    Complexity,
    ImageInputProfile,
    DocumentInputProfile,
    DetailLevel,
    RetrievalStrategy,
)

profile = UseCaseProfile(
    model="gpt-4.1",
    provider=Provider.OPENAI,
    agent_pattern=AgentPattern.TOOL_AGENT,
    complexity=Complexity.HIGH,
    modalities=[Modality.TEXT, Modality.DOCUMENT],
    tools=[Tool.FILE_SEARCH, Tool.CODE_INTERPRETER],
    document_inputs=DocumentInputProfile(
        count=3,
        avg_pages=10,
        retrieval_strategy=RetrievalStrategy.FILE_SEARCH,
        top_k=5,
    ),
    users=500,
    calls_per_user_per_day=20,
)

result = predict(profile=profile)
```

#### Key Profile Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | `str` | `"gpt-4.1"` | Model name |
| `provider` | `Provider` | `OPENAI` | LLM provider |
| `agent_pattern` | `AgentPattern` | `SINGLE_CALL` | Workflow pattern |
| `complexity` | `Complexity` | `MEDIUM` | Task complexity (affects token estimates) |
| `modalities` | `list[Modality]` | `[TEXT]` | Input/output modalities |
| `tools` | `list[Tool]` | `[]` | Tools the agent uses |
| `users` | `int` | `1` | Number of users |
| `calls_per_user_per_day` | `int` | `1` | API calls per user per day |
| `image_inputs` | `ImageInputProfile` | `None` | Image details (count, size, detail) |
| `document_inputs` | `DocumentInputProfile` | `None` | Document details (count, pages, strategy) |
| `audio_inputs` | `AudioInputProfile` | `None` | Audio details (duration in seconds) |
| `expected_turns` | `int` | `1` | Conversation turns per session |
| `system_prompt_tokens` | `int` | `None` | Override auto-estimated system prompt size |
| `avg_user_input_tokens` | `int` | `None` | Override auto-estimated user input size |

#### Agent Patterns

| Pattern | Use When |
|---------|----------|
| `SINGLE_CALL` | One prompt → one response (chatbots, completions) |
| `RAG_PIPELINE` | Retrieval-augmented generation with document search |
| `TOOL_AGENT` | Single tool-using agent (reason-act loop, function calling) |
| `WORKFLOW` | Multi-step pipeline with sequential stages |
| `MULTI_AGENT` | Multiple LLM agents collaborating |
| `CODE_EXEC` | Code interpreter / sandbox execution |

### Formatted Reports

Generate a human-readable markdown report:

```python
from future_token_predictor import predict
from future_token_predictor.report import format_report

result = predict(description="GPT-4.1 chatbot with file search, 200 users, 15 calls/day")
print(format_report(result))
```

Output:

```
# Token & Cost Prediction Report

**Model:** gpt-4.1
**Provider:** openai
**Archetype:** RAG_Pipeline
**Method:** tier1_heuristic

## Per-Call Token Breakdown
| Modality | Tokens |
|----------|--------|
| Text Input | 900 |
| Text Output | 600 |
| Document Input | 2,560 |
| **Total** | **4,060** |

## Per-Call Cost
- Mean: $0.009320
- 95% CI: $0.006524 — $0.013980

## Scaled Projections
- Daily cost: $27.96
- Monthly cost: $838.80
- Annual cost: $10,205.40
```

### Cross-Provider Comparison

Compare the same workload across providers:

```python
from future_token_predictor.cost_calculator import calculate_cost
from future_token_predictor.models.schemas import ModalityBreakdown, Provider

tokens = ModalityBreakdown(text_input=1000, text_output=500)

for provider, model in [
    (Provider.OPENAI, "gpt-4.1"),
    (Provider.ANTHROPIC, "claude-sonnet-4"),
    (Provider.GOOGLE, "gemini-2.5-flash"),
    (Provider.MISTRAL, "mistral-large"),
    (Provider.LOCAL, "llama-3.1-70b"),
]:
    cost = calculate_cost(tokens, model, provider=provider)
    print(f"{provider.value:15s} {model:20s} ${cost:.6f}")
```

### Image Token Estimation

Image token counts vary wildly across providers. Calculate them directly:

```python
from future_token_predictor.token_calculator import image_input_tokens
from future_token_predictor.models.schemas import Provider

# Same 1024×1024 image, different providers
for provider in [Provider.OPENAI, Provider.ANTHROPIC, Provider.GOOGLE, Provider.MISTRAL]:
    tokens = image_input_tokens(1024, 1024, provider=provider)
    print(f"{provider.value:12s} → {tokens:.0f} tokens")

# OpenAI:    765 tokens  (tile-based)
# Anthropic: 1,806 tokens (resolution tiers)
# Google:    258 tokens  (fixed)
# Mistral:   4,096 tokens (pixel tiles)
```

### Document Token Estimation

```python
from future_token_predictor.token_calculator import document_tokens

# Direct ingestion: all pages go into context
tokens_direct = document_tokens(pages=20, strategy="direct")  # ~13,000 tokens

# File Search (RAG): only top-k chunks
tokens_rag = document_tokens(pages=20, strategy="file_search", top_k=5)  # ~2,560 tokens
```

---

## MCP Tools Reference

When used as an MCP server, 5 tools are available:

### `predict_token_usage`

Full prediction from natural language or structured parameters.

**Natural language:**
```json
{
  "description": "A ReAct agent using GPT-4.1 with web search, 50 users, 20 queries/day"
}
```

**Structured:**
```json
{
  "model": "claude-sonnet-4",
  "provider": "anthropic",
  "agent_pattern": "tool_agent",
  "modalities": ["text", "document"],
  "tools": ["file_search"],
  "complexity": "high",
  "users": 100,
  "calls_per_user_per_day": 10,
  "document_pages": 15,
  "document_count": 3
}
```

**Mixed** (natural language + overrides):
```json
{
  "description": "GPT-4.1 chatbot",
  "users": 500,
  "calls_per_user_per_day": 30
}
```

### `get_model_pricing`

Returns per-modality pricing in USD per 1M tokens.

```json
{ "model": "gpt-4.1" }
```

Returns input, output, cached input, image, and audio pricing.

### `estimate_image_tokens`

Provider-specific image token calculation.

```json
{
  "width": 1920,
  "height": 1080,
  "detail": "high",
  "count": 3,
  "provider": "openai"
}
```

### `estimate_document_tokens`

Document token estimation with strategy selection.

```json
{
  "pages": 25,
  "document_count": 5,
  "strategy": "file_search",
  "top_k": 10
}
```

### `compare_providers`

Side-by-side cost comparison.

```json
{
  "description": "Simple text chatbot, 1000 calls",
  "providers": ["openai", "anthropic", "google"]
}
```

Or with explicit token counts:
```json
{
  "input_tokens": 2000,
  "output_tokens": 1000,
  "calls": 5000,
  "providers": ["openai", "anthropic", "google", "mistral"]
}
```

---

## Self-Improving Predictions (Tier 2 & 3)

The predictor uses a 3-tier cascade — each tier is optional and improves accuracy when available:

```
Tier 1: Heuristic (always available)
   └─► Tier 2: Regression calibration (auto-improves with usage)
        └─► Tier 3: LLM-assisted estimation (requires API key)
```

### Tier 2: Automatic Calibration

Tier 2 learns from your actual usage. Every prediction is auto-recorded to a local SQLite database (`~/.future_token_predictor/history.db`). When you feed back actual token counts, the system builds per-model regression models.

**How to use:**

```python
from future_token_predictor import predict, record_actual

# Step 1: Make a prediction (automatically recorded)
result = predict(description="GPT-4.1 chatbot")
prediction_id = result.prediction_id

# Step 2: After running your actual LLM call, record the real token counts
record_actual(
    prediction_id=prediction_id,
    actual_input_tokens=920,
    actual_output_tokens=580,
)
```

After **10+ recorded actuals** for a model+archetype pair (with R² ≥ 0.3), Tier 2 automatically activates. Future predictions for that model will be calibrated using linear regression, and `prediction_method` will show `"tier2_calibrated"`.

**Disabling Tier 2:**

```python
result = predict(description="...", enable_tier2=False)
```

### Tier 3: LLM-Assisted Estimation

Tier 3 meta-prompts a cheap/fast model to estimate how many tokens the target model will produce. This adds intelligence beyond pure heuristics.

**Requirements:** An OpenAI-compatible API key.

**Setup:**

```bash
# Set your API key
export OPENAI_API_KEY="sk-..."

# Optional: use a different API endpoint (Ollama, vLLM, Azure, etc.)
export TIER3_BASE_URL="http://localhost:11434/v1"
```

**Usage:**

```python
result = predict(
    description="GPT-4.1 agent analyzing legal contracts with RAG",
    enable_tier3=True,
)
print(result.prediction_method)  # "tier3_llm_assisted" if successful
```

**How it works:**
1. Tier 1 produces a heuristic estimate
2. Tier 2 calibrates it (if available)
3. Tier 3 asks a cheap model (default: `gpt-4.1-nano`) to estimate output tokens
4. The LLM estimate is cross-validated against Tier 1 (must be within 0.2×–5.0× range)
5. If valid, the estimate is blended: `result = (1 - confidence) × tier1 + confidence × llm_estimate`
6. If the LLM estimate fails validation, Tier 1/2 results are used unchanged

**Using a custom LLM client:**

```python
from future_token_predictor.history import OpenAICompatibleClient

client = OpenAICompatibleClient(
    api_key="sk-...",
    base_url="https://my-endpoint.com/v1",
    model="gpt-4.1-nano",
    timeout=10.0,
)

result = predict(
    description="...",
    enable_tier3=True,
    tier3_client=client,
)
```

**Cost note:** Tier 3 makes one additional API call per prediction using a cheap model. At `gpt-4.1-nano` pricing ($0.10/1M input, $0.40/1M output), each meta-estimation costs roughly $0.00005 (~200 input tokens, ~50 output tokens).

---

## Supported Models & Providers

### OpenAI / Azure OpenAI
`gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`, `gpt-4o`, `gpt-4o-mini`, `gpt-5`, `o3`, `o4-mini`, `gpt-image-1`, `gpt-4o-audio`

### Anthropic
`claude-opus-4`, `claude-sonnet-4`, `claude-sonnet-4.5`, `claude-haiku-3.5`

### Google
`gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash`

### Mistral
`mistral-large`, `mistral-small`, `codestral`, `pixtral-large`

### Cohere
`command-r-plus`, `command-r`, `command-a`

### Local
`llama-3.1-8b`, `llama-3.1-70b`, `deepseek-r1`, `phi-4`

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (none) | Required for Tier 3 LLM-assisted estimation |
| `TIER3_BASE_URL` | `https://api.openai.com/v1` | Tier 3 API endpoint (any OpenAI-compatible API) |

### Database Location

Tier 2 history is stored at `~/.future_token_predictor/history.db` (SQLite). To use a custom path:

```python
result = predict(description="...", db_path="/path/to/my/history.db")
```

### Prediction Method Indicator

Every result includes `prediction_method` telling you which tier was used:

| Value | Meaning |
|-------|---------|
| `tier1_heuristic` | Archetype-based heuristic only |
| `tier2_calibrated` | Calibrated with linear regression from your history |
| `tier3_llm_assisted` | Refined with LLM meta-estimation |

---

## Examples by Use Case

### Simple Chatbot

```python
result = predict(description="GPT-4.1-mini chatbot, low complexity")
# ~1,500 tokens/call, ~$0.0004/call
```

### RAG Pipeline

```python
result = predict(description="Claude Sonnet 4 with file search over 50-page PDFs, 200 users, 5 queries/day")
# Includes document tokens, file search tool costs
```

### ReAct Agent with Tools

```python
result = predict(description="GPT-4.1 ReAct agent with web search and code interpreter, high complexity, 50 users, 10 calls/day")
# Monte Carlo simulation for iteration uncertainty
# Includes tool costs (Code Interpreter: $0.033/session)
```

### Multi-Agent System

```python
result = predict(description="Multi-agent workflow with 3 GPT-4.1 agents collaborating, high complexity, 100 users")
# Models inter-agent communication overhead
```

### Image Analysis

```python
result = predict(description="GPT-4o analyzing 5 product photos per request, 1000 users, 3 calls/day")
# Provider-specific image token calculation (tile-based for OpenAI)
```

### Image Generation

```python
result = predict(description="GPT-Image-1 generating 2 images per request, 500 users, 1 call/day")
# Image output token estimation
```

### Audio Transcription

```python
result = predict(description="Gemini 2.5 Flash processing 60-second audio clips, 300 users, 2 calls/day")
# Audio tokens at 32 tok/s for Google
```

### Reasoning Model

```python
result = predict(description="o3 solving complex math problems, high complexity, 20 users, 5 calls/day")
# Includes 5× reasoning token multiplier
```

### Budget Planning

```python
result = predict(description="Claude Opus 4 for enterprise document analysis, 1000 users, 50 calls/day")
print(f"Monthly budget needed: ${result.monthly_cost_usd.ci_95_high:.2f}")
print(f"Annual worst case:     ${result.annual_cost_usd.worst_case:.2f}")
```

---

## Troubleshooting

### "Unknown model" or wrong provider

The classifier maps model names using regex patterns. Use the exact model names listed in [Supported Models](#supported-models--providers), or specify the model and provider explicitly:

```python
result = predict(profile=UseCaseProfile(model="gpt-4.1", provider=Provider.OPENAI))
```

### MCP server won't start

Check that the package is installed:

```bash
pip install -e .
future-token-predictor  # Should start the stdio server
```

If using VS Code, ensure `.vscode/mcp.json` points to the right Python:

```json
{
  "servers": {
    "future-token-predictor": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "future_token_predictor.mcp_server"],
      "cwd": "${workspaceFolder}",
      "env": {
        "PYTHONPATH": "${workspaceFolder}/src"
      }
    }
  }
}
```

### Tier 2 not activating

Tier 2 requires **at least 10 prediction/actual pairs** for a given model+archetype, and the regression must achieve R² ≥ 0.3. Check your history:

```python
from future_token_predictor.history import HistoryDatabase

db = HistoryDatabase()
print(f"Total records: {db.count_calibration_records()}")
print(f"Recent: {db.get_recent_predictions(5)}")
```

### Tier 3 returning None / falling back

Common reasons:
- `OPENAI_API_KEY` not set
- API endpoint unreachable (check `TIER3_BASE_URL`)
- LLM estimate too divergent from Tier 1 (outside 0.2×–5.0× range)
- LLM self-reported confidence below 0.3

### Costs seem too high/low

- Check if the right model was detected: `result.model`
- Check complexity: high complexity = more tokens per call
- For ReAct agents, iterations are simulated via Monte Carlo — the P95 can be 2–3× the mean
- Use `enable_tier2=True` and feed back actuals to improve accuracy over time
