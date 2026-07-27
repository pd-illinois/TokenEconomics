# Architecture

## System Overview

Future Token Predictor is a **prediction engine** that estimates LLM token usage and costs *before* you run a single API call. It works across all major providers using a 3-tier prediction cascade (heuristic → statistical calibration → LLM-assisted), provider-specific formulas, Monte Carlo simulation, and cost calculation.

The system is exposed as an **MCP Server** (stdio transport) so any MCP-compatible client (Claude Desktop, VS Code Copilot, custom agents) can invoke it as a tool.

A **VS Code Copilot Agent** (`.github/agents/agentic-currency-estimator.agent.md`) wraps the MCP server with a conversational workflow — it asks clarifying questions, validates models, predicts costs, and suggests Azure architectures by composing up to 3 MCP servers (future-token-predictor, Azure MCP, Foundry MCP).

## Component Architecture

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
│                                                                 │
│  Supports output_format: "markdown" | "json"                    │
│  Tracks missing_parameters for agent-driven clarification       │
└──────────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                Model Validator (5-stage cascade)                 │
│  model_validator.py — validates model exists before pricing      │
│  1. Azure Foundry catalog (hardcoded + live API)                │
│  2. Static provider catalogs                                    │
│  3. Live provider APIs (live_registry.py)                       │
│  4. Family fallback (closest known model)                       │
│  5. NOT_FOUND — never assume                                    │
└──────────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Predictor (Orchestrator)                    │
│  predictor.py — ties all stages together                        │
│                                                                 │
│  1. classify(description) ──► UseCaseProfile                    │
│  2. validate_model(model) ──► ModelValidationResult             │
│  3. predict_workflow(profile) ──► WorkflowPrediction             │
│  4. Tier 2 calibration (history DB) ──► adjusted tokens         │
│  5. Tier 3 LLM-assisted (meta-prompt) ──► adjusted tokens       │
│  6. estimate_tool_costs(profile) ──► ToolCostBreakdown          │
│  7. calculate_cost_with_ci(tokens, model) ──► CostEstimate      │
│  8. project_scale(profile, cost) ──► ScaledProjection           │
│  9. build_result(...) ──► PredictionResult                      │
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
  │  providers/__init__.py — 8 providers, lazy-loaded            │
  │                                                              │
  │  ┌────────┐ ┌─────────┐ ┌──────┐ ┌───────┐ ┌──────┐        │
  │  │ OpenAI │ │Anthropic│ │Google│ │Mistral│ │Cohere│ ...     │
  │  └────────┘ └─────────┘ └──────┘ └───────┘ └──────┘        │
  │                                                              │
  │  Each provider implements: list_models, get_pricing,         │
  │  get_model_info, calculate_image_tokens,                     │
  │  get_audio_tokens_per_second, get_reasoning_multiplier       │
  └──────────────────────────────────────────────────────────────┘
       │
       ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                Token Calculator & Archetypes                 │
  │  token_calculator.py — provider-aware dispatch               │
  │  archetypes.py + archetype_profiles.yaml — 10 patterns       │
  └──────────────────────────────────────────────────────────────┘
```

## Module Reference

| Module | Purpose |
|--------|---------|
| `mcp_server.py` | MCP tool definitions and handlers, stdio transport |
| `predictor.py` | Orchestrator — runs the full 6-stage pipeline |
| `classifier.py` | NLP classifier — regex-based model/provider/modality/tool detection |
| `single_call_predictor.py` | Tier 1 heuristic — per-call token estimation from archetypes |
| `workflow_predictor.py` | Monte Carlo simulation for multi-step workflows |
| `token_calculator.py` | Multimodal token counting — text (tiktoken), image, audio, document |
| `cost_calculator.py` | USD cost from tokens using provider-specific pricing |
| `scale_projector.py` | Daily/monthly/annual projections with caching discounts |
| `tool_cost_estimator.py` | Non-token costs (File Search, Code Interpreter, Web Search) |
| `archetypes.py` | YAML loader for archetype profiles |
| `report.py` | Markdown report formatter + `build_result` assembler |
| `azure_pricing.py` | Azure Retail Prices API client (fallback pricing source) |
| `providers/base.py` | `BaseProvider` ABC + `ModelInfo`, `PricingTier` dataclasses |
| `providers/__init__.py` | Registry: `get_provider`, `resolve_provider_for_model` |
| `providers/*_provider.py` | 7 concrete providers (OpenAI, Anthropic, Google, etc.) |
| `models/schemas.py` | All data models: enums, profiles, results |
| `history/tier3_estimator.py` | Tier 3 LLM-assisted estimation — meta-prompts a cheap model, cross-validates, blends |
| `data/archetype_profiles.yaml` | 10 workflow archetypes with token distributions |

## Data Flow: End-to-End Example

Let's trace what happens when an MCP client sends:

```
"A ReAct agent using Claude Sonnet 4 with RAG over PDFs, 100 users, 10 queries/day"
```

### Stage 1: Classification (`classifier.py`)

The classifier runs 40+ regex patterns against the description to produce a `UseCaseProfile`:

```
Input:  "A ReAct agent using Claude Sonnet 4 with RAG over PDFs, 100 users, 10 queries/day"
                │
                ├── Model patterns    → "claude-sonnet-4"  (matched r"claude.sonnet.4")
                ├── Provider patterns → Provider.ANTHROPIC
                ├── Agent patterns    → AgentType.HOSTED   (matched "react.*loop")
                ├── Modality patterns → [TEXT, DOCUMENT]   (matched "pdf")
                ├── Tool patterns     → [FILE_SEARCH]      (matched "rag")
                ├── Complexity        → Complexity.HIGH     (matched "react")
                ├── Scale: users      → 100                (matched r"\d+\s*user")
                └── Scale: calls/day  → 10                 (matched r"\d+\s*quer.*per.*day")

Output: UseCaseProfile(
    model="claude-sonnet-4",
    provider=Provider.ANTHROPIC,
    agent_type=AgentType.HOSTED,
    agent_pattern=AgentPattern.REACT_AGENT,
    modalities=[Modality.TEXT, Modality.DOCUMENT],
    tools=[Tool.FILE_SEARCH],
    complexity=Complexity.HIGH,
    users=100,
    calls_per_user_per_day=10
)
```

### Stage 2: Archetype Matching (`archetypes.py`)

The profile is matched to the best-fit archetype from `archetype_profiles.yaml`:

```
Agent pattern: REACT_AGENT + tools: [FILE_SEARCH] + modalities: [TEXT, DOCUMENT]
    → Archetype: "ReAct_Agent"

Token profile (complexity=high):
    system_prompt: 1500 tokens
    user_input: 800 tokens
    output_mean: 2000 tokens
    output_std: 800 tokens
    iterations_mean: 8
    iterations_std: 3
    file_search_calls: 3
```

### Stage 3: Single-Call Token Estimation (`single_call_predictor.py`)

Computes base tokens for one agent invocation using the archetype profile:

```
text_input  = system_prompt + user_input = 1500 + 800 = 2,300 tokens
text_output = output_mean = 2,000 tokens
document    = file_search top_k × chunk_size = 5 × 512 = 2,560 tokens
reasoning   = 0 (Claude Sonnet 4 has 1.0× multiplier — no hidden reasoning)
                                                         ──────
                                              Total:     6,860 tokens/call
```

### Stage 4: Monte Carlo Simulation (`workflow_predictor.py`)

Since this is a ReAct agent (not a simple prompt), the workflow predictor runs 1,000 Monte Carlo samples to model iteration uncertainty:

```
For each of 1,000 samples:
    iterations ~ Normal(mean=8, std=3), clipped to [1, 20]
    context_growth = 1.0 + 0.15 × iteration   (context grows each loop)

    total = Σ(base_tokens × context_growth) over all iterations

Distribution of totals:
    P5  (optimistic) = 28,400 tokens
    P50 (median)     = 48,720 tokens
    P95 (pessimistic)= 78,960 tokens
    P99 (worst case) = 96,400 tokens
    Mean             = 49,800 tokens
```

### Stage 5: Cost Calculation (`cost_calculator.py`)

Pricing is resolved from the Anthropic provider registry:

```
Claude Sonnet 4 pricing (Anthropic):
    input:        $3.00 / 1M tokens
    output:      $15.00 / 1M tokens
    cached_input: $0.30 / 1M tokens

Mean cost per call:
    text_input:  2,300 × $3.00/1M  = $0.006900
    text_output: 2,000 × $15.00/1M = $0.030000
    document:    2,560 × $3.00/1M  = $0.007680
                                      ─────────
    Total:                            $0.044580

With Monte Carlo CI:
    mean:     $0.0446
    95% CI:   $0.0283 — $0.0789
    worst:    $0.0964
```

### Stage 6: Tool Cost Estimation (`tool_cost_estimator.py`)

Non-token costs for File Search:

```
File Search: 3 calls/invocation × $2.50/1K calls = $0.0075
Storage: 0.1 GB × $0.11/GB/day = $0.011/day
```

### Stage 7: Scale Projection (`scale_projector.py`)

Projects to daily/monthly/annual with prompt caching:

```
Daily calls = 100 users × 10 calls/user = 1,000 calls/day

With 75% prompt cache hit rate on repeated system prompts:
    Effective input cost reduced by ~15% on cached tokens

Daily:   1,000 × $0.0446 = $44.60  (CI: $28.30 — $78.90)
Monthly: $44.60 × 30     = $1,338  (CI: $849 — $2,367)
Annual:  $1,338 × 12     = $16,056 (CI: $10,188 — $28,404)
```

### Stage 8: Report Assembly (`report.py`)

All components are assembled into a `PredictionResult` and formatted as a markdown report with token breakdown tables, cost CI, tool costs, projections, and optimization suggestions.

## Provider Architecture

Each provider implements the `BaseProvider` abstract class:

```python
class BaseProvider(ABC):
    name: str                          # "openai", "anthropic", etc.
    display_name: str                  # "OpenAI", "Anthropic", etc.

    list_models() -> list[str]
    get_model_info(model) -> ModelInfo
    get_pricing(model, deployment_type) -> PricingTier
    get_reasoning_multiplier(model) -> float
    get_tokenizer_name(model) -> str

    # Optional (raise NotImplementedError if unsupported)
    calculate_image_tokens(width, height, detail, count) -> ImageTokenResult
    get_audio_tokens_per_second() -> int
```

The registry (`providers/__init__.py`) lazy-loads all 7 provider implementations on first access and maps them to the `Provider` enum. OpenAI and Azure OpenAI share the same instance since they use identical models and formulas.

### Image Token Formulas

Each provider has a unique approach to counting image tokens:

| Provider | Method | Example (1024×1024) |
|----------|--------|---------------------|
| OpenAI | Tile-based: `ceil(w/512) × ceil(h/512) × 170 + 85` | 765 tokens |
| Anthropic | 8 resolution tiers mapped to token counts | 1,806 tokens |
| Google | Fixed 258 tokens per image regardless of size | 258 tokens |
| Mistral | Pixel tiles: `ceil(w/16) × ceil(h/16)` | 4,096 tokens |
| Bedrock | Reuses Anthropic's resolution tiers for Claude | 1,806 tokens |

### Pricing Resolution

Cost calculation uses a two-tier pricing strategy:

1. **Provider registry** (primary): Each provider's `get_pricing()` returns a `PricingTier` with hardcoded per-million-token rates
2. **Azure Retail Prices API** (fallback): For Azure/OpenAI models, queries the live API for current pricing

## Archetype System

The system uses 10 workflow archetypes defined in `data/archetype_profiles.yaml`:

| Archetype | Agent Pattern | Description |
|-----------|--------------|-------------|
| `SingleCall_TextOnly` | Single call | One LLM call, text only |
| `SingleCall_Vision` | Single call | One LLM call with image inputs |
| `SingleCall_ImageGen` | Single call | Image generation (GPT-Image-1) |
| `SingleCall_Audio` | Single call | Audio I/O (speech/realtime) |
| `RAG_Pipeline` | Single call | Retrieval-augmented generation |
| `ReAct_Agent` | ReAct | Iterative tool-use loop |
| `Workflow` | Workflow | Multi-step pipeline |
| `MultiAgent` | Multi-agent | Multiple collaborating agents |
| `CodeExec` | ReAct | Code interpreter sandbox |
| `ReAct_CUA` | ReAct | Computer/browser use agent |

Each archetype defines token distributions at three complexity levels (low/medium/high) with parameters like `system_prompt`, `user_input`, `output_mean`, `output_std`, `iterations_mean`, `iterations_std`, `file_search_calls`, and `turns`.

## Monte Carlo Simulation

For workflow and hosted agents, simple heuristics aren't sufficient because the number of iterations is inherently uncertain. A ReAct agent might resolve a query in 2 steps or 15.

The workflow predictor runs N=1,000 samples:

1. **Sample iteration count** from `Normal(iterations_mean, iterations_std)`, clipped to `[1, max]`
2. **Apply context growth**: each iteration adds ~15% more input tokens (growing conversation history)
3. **Sum total tokens** across all iterations per sample
4. **Extract percentiles**: P5, P50, P95, P99 from the distribution

This produces confidence intervals that propagate through cost calculation and scale projection, giving users a realistic range rather than a single point estimate.

## Design Decisions

**Why heuristic-based, not LLM-based?** The classifier uses regex patterns instead of calling an LLM because the tool itself predicts LLM costs — it would be circular (and expensive) to use an LLM for classification. Regex patterns are fast, deterministic, and free.

**Why per-provider image formulas?** Image token counts vary 5× across providers for the same image (258 for Google vs 4,096 for Mistral). Using a single formula would produce wildly inaccurate cost estimates for non-OpenAI providers.

**Why Monte Carlo over closed-form?** ReAct agent iteration counts follow uncertain distributions that compound with context growth. A closed-form solution would require assumptions about the distribution shape. Monte Carlo handles arbitrary distributions and makes it easy to extract any percentile.

**Why MCP?** The Model Context Protocol is the emerging standard for tool integration with LLM agents. Exposing the predictor as an MCP server means any AI coding assistant can estimate costs before generating code that makes API calls.

## Research Foundation & Citations

This project's architecture and prediction approach are grounded in the following research:

| # | Paper / Source | Key Insight | How We Used It |
|---|---------------|-------------|----------------|
| [1] | **PreflightLLMCost** (Salar, 2025) — [GitHub](https://github.com/aatakansalar/PreflightLLMCost) | 3-tier cascade: heuristics → regression → hidden state analysis. ≤15% MAPE | **Tiers 1 & 2 implemented.** `single_call_predictor.py` is the heuristic tier; `history/calibrator.py` is the regression tier. Predictions output `method: "tier1_heuristic"` or `"tier2_calibrated"`. Tier 3 planned. |
| [2] | "Response Length Perception and Sequence Scheduling" (Zheng et al., 2023) — [arXiv:2305.13144](https://arxiv.org/abs/2305.13144) | LLMs can predict their own response length with minimal overhead; 86% throughput improvement | **Implemented.** Tier 3 (`history/tier3_estimator.py`): meta-prompts a cheap model to estimate output tokens for the target model. |
| [3] | "Emergent Response Planning in LLMs" (Dong et al., ICML 2025) — [arXiv:2502.06258](https://arxiv.org/abs/2502.06258) | Hidden states encode response length, reasoning steps, and structure attributes | **Implemented.** Theoretical grounding for Tier 3 — LLMs plan ahead in hidden states; `apply_tier3()` cross-validates and blends estimates. |
| [4] | "Precise Length Control in Large Language Models" (Butcher et al., 2024) — [arXiv:2412.11937](https://arxiv.org/abs/2412.11937) | LDPE achieves mean token errors <3 tokens | **Not yet implemented.** Informs accuracy bounds — sets the theoretical ceiling for how precise token prediction can get. |
| [5] | "Zero-Shot Strategies for Length-Controllable Summarization" (Retkowski & Waibel, NAACL 2025) — [ACL Anthology](https://aclanthology.org/2025.findings-naacl.34/) | Length approximation without fine-tuning or architecture changes | **Implemented.** Our classifier and archetype-based estimation is this zero-shot heuristic approach — no model modification needed. |
| [6] | "Your LLM Knows the Future: Multi-Token Prediction Potential" (Samragh et al., 2025) — [arXiv:2507.11851](https://arxiv.org/abs/2507.11851) | Vanilla LLMs inherently encode knowledge about future tokens | **Implemented.** Tier 3 leverages this insight — `OpenAICompatibleClient` queries any OpenAI-compatible API to meta-estimate token counts. |
| [7] | Microsoft Foundry Agent Service Docs — [Overview](https://learn.microsoft.com/en-us/azure/foundry/agents/overview) | Prompt agents, Workflow agents, Hosted agents; built-in tool pricing | **Implemented.** Tool cost estimator uses MAF pricing: File Search $2.50/1K calls, Code Interpreter $0.033/session. |
| [8] | Azure OpenAI Pricing — [Pricing Page](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/) | Per-model pricing for text/image/audio input/output | **Implemented.** OpenAI provider pricing + Azure Retail Prices API fallback. |
| [9] | Anthropic Pricing — [Pricing Page](https://www.anthropic.com/pricing) | Claude model pricing with thinking tokens | **Implemented.** Anthropic provider with 4 models and resolution-tier image tokens. |
| [10] | Google AI Pricing — [Pricing Page](https://ai.google.dev/pricing) | Gemini model pricing with context caching | **Implemented.** Google provider with 3 models, 258 fixed image tokens, reasoning multipliers. |

### 3-Tier Prediction Cascade (from [1])

The plan specifies a 3-tier cascade architecture inspired by PreflightLLMCost:

```
Tier 1: Enhanced Heuristics (per [5])          ◄── IMPLEMENTED
    ├── Archetype-based token distributions
    ├── Provider-specific modality formulas
    └── Complexity-adjusted scaling

Tier 2: Statistical Regression (per [1])        ◄── IMPLEMENTED
    ├── SQLite history DB (history/database.py)
    ├── Linear regression per model+archetype (history/calibrator.py)
    ├── Per-modality calibration for text_input/text_output
    ├── Activates after ≥10 samples with R² ≥ 0.3
    └── Every prediction auto-recorded; record_actual() feeds back actuals

Tier 3: LLM-Assisted Estimation (per [2],[3],[6])  ◄── IMPLEMENTED
    ├── history/tier3_estimator.py — OpenAICompatibleClient + MockLLMClient
    ├── Meta-prompt a cheap model (default gpt-4.1-nano) to estimate output length
    ├── Cross-validate against Tier 1 (ratio bounds 0.2×–5.0×, min confidence 0.3)
    ├── Confidence-weighted blend: result = (1-w)×tier1 + w×llm_estimate
    ├── Image/document/audio tokens NOT adjusted (formula-based)
    └── Predictions output method: "tier3_llm_assisted"
```

## Phase Tracking

### Phase 1: Core Foundation — COMPLETE ✅

| Step | Task | Status | Artifacts |
|------|------|--------|-----------|
| 1 | Project scaffolding | ✅ Done | `pyproject.toml`, `src/future_token_predictor/`, `tests/` |
| 2 | Provider registry & pricing | ✅ Done | `providers/__init__.py`, `providers/base.py`, 7 concrete providers (OpenAI, Anthropic, Google, Mistral, Cohere, Bedrock, Local) |
| 3 | Multimodal token calculator | ✅ Done | `token_calculator.py` — text (tiktoken), image (provider-specific), audio, document |
| 4 | Archetype definitions | ✅ Done | `archetypes.py` + `data/archetype_profiles.yaml` — 10 workflow archetypes |
| 5 | Use case classifier | ✅ Done | `classifier.py` — 40+ regex patterns, detects model/provider/modality/tools/agent/complexity/scale |

### Phase 2: Prediction Engine — COMPLETE ✅

| Step | Task | Status | Artifacts |
|------|------|--------|-----------|
| 6 | Single-call predictor (Tier 1) | ✅ Done | `single_call_predictor.py` — heuristic per-call token estimation from archetypes |
| 7 | Workflow predictor | ✅ Done | `workflow_predictor.py` — Monte Carlo simulation (1,000 samples) for multi-step workflows |
| 8 | Tool cost estimator | ✅ Done | `tool_cost_estimator.py` — File Search, Code Interpreter, Web Search non-token costs |
| 9 | Scale projector | ✅ Done | `scale_projector.py` — daily/monthly/annual projections with caching discounts |

### Phase 3: Cost & Output — COMPLETE ✅

| Step | Task | Status | Artifacts |
|------|------|--------|-----------|
| 10 | Multi-provider cost calculator | ✅ Done | `cost_calculator.py` — provider registry pricing + Azure API fallback, CI calculation |
| 11 | Report generator | ✅ Done | `report.py` — markdown report formatter + `build_result()` assembler |
| 12 | Provider comparison | ✅ Done | `compare_providers` MCP tool — side-by-side cost for same workload |

### Phase 4: MCP Server Surface — COMPLETE ✅

| Step | Task | Status | Artifacts |
|------|------|--------|-----------|
| 13 | MCP Server implementation | ✅ Done | `mcp_server.py` — 5 tools via stdio transport |
| 14 | VS Code integration | ✅ Done | `.vscode/mcp.json` — local MCP server registration |
| 15 | Cross-client support | ✅ Done | `README.md` — documents MCP client configuration |

### Phase 5: Future Enhancements — OPEN 🔲

| Step | Task | Status | Dependencies | Research Basis |
|------|------|--------|--------------|----------------|
| 16 | Azure Functions hosting | 🔲 Not started | Remote MCP server deployment | — |
| 17 | Tier 2 calibration | ✅ Done | `history/database.py` (SQLite), `history/calibrator.py` (linear regression), `record_actual()` API | [1] |
| 18 | Tier 3 LLM-assisted | ✅ Done | `history/tier3_estimator.py` (OpenAICompatibleClient, MockLLMClient, apply_tier3), predictor.py integration | [2], [3], [6] |
| 19 | Provider API live pricing | 🔲 Not started | Auto-fetch from provider pricing APIs | — |
| 20 | PTU/Commitment break-even | 🔲 Not started | Provisioned tier analysis | — |

### Not in Plan but Created

| Item | Status | Notes |
|------|--------|-------|
| `adapters/` directory | 🔲 Stub only | Empty `__init__.py`. Plan specifies `maf_adapter.py`, `langchain_adapter.py`, `crewai_adapter.py` — not yet implemented |
| `examples/` directory | 🔲 Not created | Plan specifies 4 example scripts — not yet created |

### Test Coverage — COMPLETE ✅

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_providers.py` | 92 | Registry, all 7 providers, PricingTier, parametrized model/pricing combos |
| `test_classifier.py` | 42 | Model detection, provider override, modality/tool/agent/complexity, regex precedence |
| `test_token_calculator.py` | 30 | Text/image/document/audio, reasoning, resize edge cases |
| `test_live_registry.py` | 28 | Live provider API model discovery, caching, error handling |
| `test_tier2.py` | 30 | SQLite CRUD, linear regression, calibrator logic, predictor integration |
| `test_tier3.py` | 30 | Parse estimate, apply_tier3 validation/blending, MockLLMClient, predictor integration |
| `test_model_validator.py` | 21 | Version variants, normalization, Azure catalog checks, family fallback, end-to-end validation |
| `test_mcp_server.py` | 20 | All 6 tools, auto-detection, JSON output, missing_parameters, error handling |
| `test_cost_calculator.py` | 18 | Price resolution, cost calc, CI, cross-provider |
| `test_llm_classifier.py` | 18 | LLM-assisted classification, credential resolution, integration |
| `test_predictor.py` | 12 | End-to-end prediction, report formatting |
| `test_foundry_catalog.py` | 6 | Azure Foundry catalog API, model existence checks |
| **Total** | **347** | **338 passing, 9 environment-dependent** |
