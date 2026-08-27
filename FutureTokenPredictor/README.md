# Future Token Predictor

Future Token Predictor is a prediction engine that estimates LLM token usage and costs before you run a single API call. It works across all major providers using a 3-tier prediction cascade (heuristic → statistical calibration → LLM-assisted), provider-specific formulas, Monte Carlo simulation, and cost calculation.

The system is exposed as an MCP Server (stdio transport) so any MCP-compatible client (Claude Desktop, VS Code Copilot, custom agents) can invoke it as a tool.

A VS Code Copilot Agent (.github/agents/agentic-currency-estimator.agent.md) wraps the MCP server with a conversational workflow — it asks clarifying questions, validates models, predicts costs, and suggests Azure architectures by composing up to 3 MCP servers (future-token-predictor, Azure MCP, Foundry MCP).


The easiest way to use Future Token Predictor is through the **Agentic Currency Estimator** — a VS Code Copilot Agent that wraps the MCP server with a conversational workflow. Just type `@agentic-currency-estimator` in VS Code Copilot Chat, describe your workload, and it will ask clarifying questions, validate your model, predict costs, and suggest Azure architectures.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│               VS Code Copilot Agent (optional)                  │
│  .github/agents/agentic-currency-estimator.agent.md             │
│  Conversational workflow: INTAKE → CLARIFY → VALIDATE →         │
│  PREDICT → SUGGEST ARCHITECTURE → PRESENT                       │
├──────────────┬──────────────────┬───────────────────────────────┤
│ future-token-│ Foundry MCP      │ Azure MCP                     │
│ predictor    │ (model catalog)  │ (resource lookup, pricing)    │
└──────┬───────┴──────────────────┴───────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Server                               │
│  mcp_server.py — 6 tools, stdio transport                       │
│  predict_token_usage │ get_model_pricing │ estimate_image_tokens │
│  estimate_document_tokens │ compare_providers │ refresh_models   │
└──────────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Predictor (Orchestrator)                    │
│  predictor.py — 9-stage pipeline                                │
│  classify → validate → predict → tier2 → tier3 → tool costs →  │
│  cost CI → scale → build result                                 │
└──────┬──────────┬───────────┬───────────┬──────────┬────────────┘
       │          │           │           │          │
       ▼          ▼           ▼           ▼          ▼
  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐
  │Classifier│ │Workflow  │ │  Cost    │ │ Tool   │ │  Scale   │
  │          │ │Predictor │ │Calculator│ │Estimator│ │Projector │
  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┘ └──────────┘
       │             │            │
       ▼             ▼            ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                    Provider Registry                         │
  │  8 providers: OpenAI, Anthropic, Google, Mistral, Cohere,    │
  │  Bedrock, Local, Azure OpenAI                                │
  └──────────────────────────────────────────────────────────────┘
```

See [architecture.md](architecture.md) for the full architecture, dataflow, module reference, and design rationale.

## Supported Providers

See [Model Catalog and Pricing Lifecycle](MODEL_CATALOG.md) for the provider/model offering contract, current limitations, and the recommended discovery, pricing, review, and retirement pipeline.

| Provider | Models | Image | Audio | Reasoning |
|----------|--------|-------|-------|-----------|
| **OpenAI** | GPT-4.1, GPT-4o, GPT-5, o3, o4-mini, GPT-Image-1 | Tile-based (512×512) | 43 tok/s | o3 (5×), o4-mini (3×) |
| **Azure OpenAI** | Same as OpenAI | Same | Same | Same |
| **Anthropic** | Claude Opus 4–4.7, Sonnet 4/4.5/4.6, Haiku 3.5/4.5 | Resolution tiers | — | — |
| **Google** | Gemini 2.5 Pro/Flash, 2.0 Flash | 258 fixed | 32 tok/s | Pro (3×), Flash (2×) |
| **Mistral** | Large, Small, Codestral, Pixtral | 16×16 pixel tiles | — | — |
| **Cohere** | Command R+, R, A | — | — | — |
| **AWS Bedrock** | Claude, Llama, Mistral (Bedrock-hosted) | Anthropic tiers | — | — |
| **Local** | Llama 3.1, DeepSeek R1, Phi-4, Qwen, Mistral 7B | — | — | DeepSeek (4×) |

## MCP Tools

The server exposes 6 tools via the Model Context Protocol:

| Tool | Description |
|------|-------------|
| `predict_token_usage` | Full prediction from natural language or structured params (supports `output_format: "json"`) |
| `get_model_pricing` | Per-modality pricing for any model (USD/1M tokens) |
| `estimate_image_tokens` | Provider-specific image token calculation |
| `estimate_document_tokens` | Document token estimation (direct, RAG, file search) |
| `compare_providers` | Side-by-side cost comparison across providers |
| `refresh_models` | Refresh live model catalogs from provider APIs |

## Installation

```bash
# Clone and set up
git clone <repo-url>
cd FutureTokenPredictor
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate
# Activate (macOS/Linux)
source .venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

**Requirements:** Python 3.11+

## Usage

### As an MCP Server

Add to your MCP client configuration (e.g., Claude Desktop, VS Code):

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

Or run directly:

```bash
future-token-predictor
```

### As a Python Library

```python
from future_token_predictor import predict

# From natural language
result = predict(description="A ReAct agent using Claude Sonnet 4 with RAG over PDFs, 100 users, 10 queries/day")

print(f"Model: {result.model}")              # claude-sonnet-4
print(f"Provider: {result.provider}")         # anthropic
print(f"Tokens/call: {result.tokens_per_call.total:.0f}")
print(f"Cost/call: ${result.cost_per_call.mean:.6f}")
print(f"Monthly: ${result.monthly_cost_usd.mean:.2f}")

# From structured profile
from future_token_predictor.models.schemas import UseCaseProfile, Provider

profile = UseCaseProfile()
profile.model = "gpt-4.1"
profile.provider = Provider.OPENAI
profile.users = 500
profile.calls_per_user_per_day = 20

result = predict(profile=profile)
```

### Formatted Report

```python
from future_token_predictor import predict
from future_token_predictor.report import format_report

result = predict(description="GPT-4.1 chatbot with file search, 200 users, 15 calls per user per day")
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

```python
from future_token_predictor.cost_calculator import calculate_cost
from future_token_predictor.models.schemas import ModalityBreakdown, Provider

tokens = ModalityBreakdown(text_input=1000, text_output=500)

for provider, model in [
    (Provider.OPENAI, "gpt-4.1"),
    (Provider.ANTHROPIC, "claude-sonnet-4"),
    (Provider.GOOGLE, "gemini-2.5-flash"),
    (Provider.LOCAL, "llama-3.1-8b"),
]:
    cost = calculate_cost(tokens, model, provider=provider)
    print(f"{provider.value:15s} {model:20s} ${cost:.6f}")
```

## Key Features

- **3-Tier Prediction Cascade** — Tier 1 heuristic → Tier 2 statistical calibration (history DB) → Tier 3 LLM-assisted estimation
- **5-Stage Model Validation** — Azure Foundry catalog → static provider catalogs → live provider APIs → family fallback → NOT_FOUND
- **Monte Carlo Simulation** — Confidence intervals on all cost estimates
- **Multimodal** — Text, image, audio, document, and reasoning token estimation
- **VS Code Copilot Agent** — Type `@agentic-currency-estimator` in VS Code Copilot Chat for a guided conversational workflow that composes future-token-predictor, Azure MCP, and Foundry MCP servers

## Running Tests

```bash
pytest tests/ -v
```

347 tests covering all providers, token calculator, classifier, cost calculator, model validator, live registry, LLM classifier, MCP server, Tier 2/3 prediction, and end-to-end predictor.

## License

MIT
