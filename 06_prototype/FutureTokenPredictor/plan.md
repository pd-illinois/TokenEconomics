# Plan: Future Token Predictor — Universal LLM Token & Cost Prediction MCP Server

## TL;DR
Build a **local MCP Server** (with Python library core) that predicts total token usage and cost for **any LLM provider** across multimodal agentic workflows. Supports **OpenAI, Azure OpenAI, Anthropic (Claude), Google (Gemini), Mistral, Cohere, Meta (Llama), AWS Bedrock, Google Vertex AI, and local models (Ollama/vLLM)**. Handles all modalities: text, vision, image generation, document/RAG, audio, and video. Works with any agent framework: Microsoft Foundry Agent Service, LangChain, CrewAI, AutoGen, custom. Runs locally first; Azure Functions hosting planned for later.

## Delivery Mechanism

**Phase 1 (now): Local MCP Server**
- Runs as a local stdio/SSE MCP server
- Consumable by VS Code Copilot (via `mcp.json`), Claude Desktop, any MCP client
- Python library core (`future_token_predictor`) usable directly in scripts/notebooks
- No cloud infrastructure needed to start

**Phase 2 (later): Azure Functions MCP hosting**
- Deploy the same MCP server to Azure Functions (`/runtime/webhooks/mcp` endpoint)
- Any agent framework can consume it as a remote MCP tool
- GitHub Copilot coding agent can consume it remotely

## Problem Statement
LLM token consumption is unpredictable, especially in agentic workflows where:
- **Provider diversity**: Each provider (OpenAI, Anthropic, Google, Mistral) has different tokenizers, pricing models, and billing units
- **Modality matters**: Image tokens differ by provider — OpenAI uses tiles, Claude uses base+per-tile, Gemini uses fixed tokens per image
- **Reasoning tokens vary**: OpenAI o-series has hidden reasoning tokens; Claude "extended thinking" has explicit thinking tokens; Gemini has "thinking" mode
- **Agent frameworks abstract calls**: MAF, LangChain, CrewAI, AutoGen all add framework-specific overhead
- **Tool invocations**: RAG tools, code execution, web search — each provider prices these differently or bundles them
- **Multi-agent workflows**: compound estimation error across any orchestration framework
- **Pricing is fragmented**: No single API covers all providers; each has its own pricing page/API

## Supported Providers

| Provider | Models | Pricing Source | Tokenizer |
|----------|--------|---------------|-----------|
| **OpenAI** (direct) | GPT-4.1, GPT-5, o3, o4-mini, GPT-Image-1 | OpenAI API `/models` | tiktoken (o200k_base) |
| **Azure OpenAI** | Same as OpenAI + deployment types | Azure Retail Prices REST API | tiktoken (o200k_base) |
| **Anthropic** | Claude Opus 4, Sonnet 4, Haiku 3.5 | Anthropic pricing page (static) | Anthropic tokenizer / estimate |
| **Google** | Gemini 2.5 Pro, Flash, Ultra | Google AI pricing (static) | SentencePiece-based / estimate |
| **Mistral** | Mistral Large, Medium, Small | Mistral pricing (static) | Mistral tokenizer / tiktoken |
| **Cohere** | Command R+, Command R | Cohere pricing (static) | Cohere tokenizer |
| **Meta (via providers)** | Llama 4 Scout, Maverick | Provider-dependent (Groq, Together, etc.) | Llama tokenizer / tiktoken |
| **AWS Bedrock** | Claude, Llama, Titan, etc. | AWS Pricing API | Model-dependent |
| **Google Vertex AI** | Gemini, Claude, Llama | Vertex pricing | Model-dependent |
| **Local (Ollama/vLLM)** | Any GGUF/safetensors model | Free (compute cost only) | Model-dependent |

## Supported Agent Frameworks

| Framework | Agent Types | Token Overhead Model |
|-----------|-------------|---------------------|
| **Microsoft Foundry Agent Service** | Prompt, Workflow, Hosted agents | Tool costs (File Search $2.50/1K, Code Interpreter $0.033/session) |
| **LangChain/LangGraph** | Chains, Agents, Tools | Tool call serialization + memory window |
| **CrewAI** | Crews, Agents, Tasks | Inter-agent delegation + shared memory |
| **AutoGen / AG2** | Conversable Agents, Group Chat | Multi-turn conversation accumulation |
| **Custom / Direct API** | Any pattern | User-specified iterations and context growth |

## Research Foundation & Citations

| # | Paper / Source | Key Insight | Relevance |
|---|-------|-------------|-----------|
| [1] | PreflightLLMCost (Salar, 2025) — [GitHub](https://github.com/aatakansalar/PreflightLLMCost) | 3-tier cascade: heuristics → regression → hidden state analysis. ≤15% MAPE | Architecture template; single-call prediction baseline |
| [2] | "Response Length Perception and Sequence Scheduling" (Zheng et al., 2023) — [arXiv:2305.13144](https://arxiv.org/abs/2305.13144) | LLMs can predict own response length with minimal overhead; 86% throughput improvement | Validates cheap model → estimate expensive model output length |
| [3] | "Emergent Response Planning in LLMs" (Dong et al., ICML 2025) — [arXiv:2502.06258](https://arxiv.org/abs/2502.06258) | Hidden states encode response length, reasoning steps, structure attributes | LLMs plan ahead in hidden states; prediction is theoretically grounded |
| [4] | "Precise Length Control in Large Language Models" (Butcher et al., 2024) — [arXiv:2412.11937](https://arxiv.org/abs/2412.11937) | LDPE achieves mean token errors <3 tokens | Models CAN be precise about length; informs accuracy bounds |
| [5] | "Zero-Shot Strategies for Length-Controllable Summarization" (Retkowski & Waibel, NAACL 2025) — [ACL Anthology](https://aclanthology.org/2025.findings-naacl.34/) | Length approximation without fine-tuning or architecture changes | Zero-shot heuristic tier—no model modification needed |
| [6] | "Your LLM Knows the Future: Multi-Token Prediction Potential" (Samragh et al., 2025) — [arXiv:2507.11851](https://arxiv.org/abs/2507.11851) | Vanilla LLMs inherently encode knowledge about future tokens | Supports meta-prompting approach for length estimation |
| [7] | Microsoft Foundry Agent Service Docs — [Overview](https://learn.microsoft.com/en-us/azure/foundry/agents/overview) | Prompt agents, Workflow agents, Hosted agents; built-in tools | One of several supported agent frameworks |
| [8] | Azure OpenAI Pricing — [Pricing Page](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/) | Per-model pricing for text/image/audio input/output | One of several pricing sources |
| [9] | Anthropic Pricing — [Pricing Page](https://www.anthropic.com/pricing) | Claude model pricing with thinking tokens | Anthropic provider pricing |
| [10] | Google AI Pricing — [Pricing Page](https://ai.google.dev/pricing) | Gemini model pricing with context caching | Google provider pricing |

## Multimodal Token Accounting (Provider-Specific)

### Text Tokens

| Provider | Tokenizer | Notes |
|----------|-----------|-------|
| OpenAI / Azure OpenAI | tiktoken `o200k_base` | All GPT-4+ and o-series models |
| Anthropic | Claude tokenizer (≈ tiktoken compatible) | ~3.5 chars/token English average |
| Google | SentencePiece variant | ~4 chars/token English average |
| Mistral | SentencePiece (tiktoken compatible) | Similar to OpenAI token counts |
| Local models | Model-specific (Llama, Phi, etc.) | Fallback: 4 chars/token estimate |

### Image Input Tokens (Vision)

| Provider | Method | Example (1024×1024) |
|----------|--------|---------------------|
| **OpenAI/Azure** | Tile-based: 512×512 tiles × 170 + 85 base | 765 tokens (high detail) |
| **Anthropic** | Base 1 token + ceil(w/tile)×ceil(h/tile)×tokens_per_tile | ~1590 tokens (varies) |
| **Google Gemini** | Fixed per-image (258 tokens) regardless of resolution | 258 tokens |
| **Mistral** | Similar to OpenAI tile system | ~765 tokens |

### Reasoning/Thinking Tokens

| Provider | Mechanism | Billing |
|----------|-----------|---------|
| **OpenAI o-series** | Hidden reasoning tokens (not visible) | Billed at output rate |
| **Anthropic Claude** | "Extended thinking" (visible in API, configurable budget) | Billed at output rate |
| **Google Gemini** | "Thinking" mode (visible, configurable) | Billed at output rate (often discounted) |

### Audio Tokens

| Provider | Input Rate | Output Rate |
|----------|-----------|-------------|
| **OpenAI GPT-4o-Audio** | ~43 tokens/second | ~43 tokens/second |
| **Google Gemini** | 32 tokens/second | 32 tokens/second |

## Provider Pricing Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Pricing Registry                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Azure Retail │  │   Static     │  │   Custom     │         │
│  │ Prices API   │  │   Catalog    │  │   Provider   │         │
│  │  (live)      │  │  (fallback)  │  │   (plugin)   │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                  │                  │                 │
│         ▼                  ▼                  ▼                 │
│  ┌─────────────────────────────────────────────────────┐       │
│  │          Unified Pricing Interface                   │       │
│  │  get_price(provider, model, modality, token_type)    │       │
│  │  → USD per 1M tokens                                │       │
│  └─────────────────────────────────────────────────────┘       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Pricing Data Sources
- **Azure OpenAI**: Azure Retail Prices REST API (live, no auth, auto-cached)
- **OpenAI Direct**: Static catalog updated from openai.com/pricing (manual refresh)
- **Anthropic**: Static catalog updated from anthropic.com/pricing
- **Google**: Static catalog updated from ai.google.dev/pricing
- **Mistral**: Static catalog updated from mistral.ai/pricing
- **AWS Bedrock**: AWS Pricing API (optional, requires credentials)
- **Local Models**: Zero cost (user optionally specifies compute cost/hour)

## Framework-Agnostic Agent Archetypes

| Archetype | Pattern | Framework Examples | Token Profile |
|-----------|---------|-------------------|---------------|
| **SingleCall_TextOnly** | One LLM call, no tools | Any direct API call | Predictable, 1 call |
| **SingleCall_Vision** | One call with image input | Any vision model | Image tokens dominate input |
| **SingleCall_ImageGen** | One call producing images | DALL-E, Imagen, Flux | High output cost |
| **RAG_Pipeline** | Retrieve → Augment → Generate | LangChain RAG, MAF File Search, custom | Chunk injection + generation |
| **ReAct_Agent** | Observe → Think → Act loop | LangChain Agent, MAF Hosted, AutoGen | 3-15 iterations, high variance |
| **MultiAgent_Collab** | Multiple agents communicating | CrewAI, AutoGen GroupChat, MAF A2A | M agents × K turns |
| **Workflow_Sequential** | Multi-step pipeline | LangGraph, MAF Workflow, custom chains | N steps, predictable |
| **CodeExec_Loop** | Generate code → execute → refine | Code Interpreter, Open Interpreter | Iteration + code output |
| **Conversational_Multi** | Multi-turn chat with memory | Any chatbot with history | Growing context window |
| **CUA_ScreenAgent** | GUI automation with screenshots | OpenAI CUA, Claude Computer Use | Very high image token volume |

## Architecture

```
User Input (NL description + optional structured params)
    ↓
Use Case Classifier ─── NL → UseCaseProfile
    ├── Detects provider: openai | azure_openai | anthropic | google | mistral | cohere | bedrock | local
    ├── Detects model: gpt-4.1 | claude-opus-4 | gemini-2.5-pro | mistral-large | llama-4 | etc.
    ├── Detects modality: text-only | vision | image-gen | document | audio | multimodal-mix
    ├── Detects agent pattern: single_call | react | multi_agent | workflow | rag | code_exec
    └── Detects tools: rag | code_exec | web_search | function_calling | mcp
    ↓
Structured Profile (UseCaseProfile)
    ├── provider: openai | azure_openai | anthropic | google | mistral | cohere | bedrock | vertex | local
    ├── model: specific model identifier
    ├── agent_pattern: single_call | react_agent | multi_agent | workflow | rag_pipeline | code_exec
    ├── framework: maf | langchain | crewai | autogen | custom
    ├── modalities: [text, image_input, image_output, document, audio_input, audio_output, video]
    ├── deployment_type: standard | batch | provisioned (provider-specific)
    ├── tools: [rag, code_exec, web_search, function_calling, mcp_servers]
    ├── complexity: low | medium | high
    ├── image_inputs: {count_per_call, avg_resolution, detail_level}
    ├── document_inputs: {count, avg_pages, retrieval_strategy, chunk_size, top_k}
    ├── audio_inputs: {avg_duration_seconds}
    ├── thinking_budget: int | None (for reasoning models)
    ├── expected_turns: N
    ├── multi_agent_count: N
    ├── context_window_usage: float (0-1, how full the context gets)
    ├── users: N
    └── calls_per_user_per_day: N
    ↓
Provider-Specific Token Estimator
    ├── Text tokens: provider-appropriate tokenizer or estimation
    ├── Image input tokens: provider-specific formula (tiles vs fixed vs resolution-scaled)
    ├── Image output tokens: provider-specific (OpenAI token-based, others per-image)
    ├── Document tokens: pages × extraction_rate OR chunks × top_k
    ├── Audio tokens: duration × provider_token_rate
    ├── Thinking/reasoning tokens: model-specific multiplier or explicit budget
    └── Context caching discount: provider-specific (OpenAI cached_input, Anthropic context caching, Gemini)
    ↓
Prediction Engine (3-tier cascade, per [1])
    ├── Tier 1: Enhanced Heuristics (per [5])
    │   ├── Modality-aware prompt classification
    │   ├── Archetype-specific iteration/tool-call distribution
    │   └── Complexity-adjusted scaling
    ├── Tier 2: Statistical Regression (when historical data available)
    │   └── Calibrated against actual usage logs
    └── Tier 3: LLM-Assisted Estimation (per [2], [3], [6])
        ├── Meta-prompt a cheap model to estimate output length
        └── Cross-validate against Tier 1
    ↓
Workflow Aggregator (Framework-Agnostic)
    ├── Single Call: 1 model invocation + tool calls
    ├── ReAct Agent: N iterations with growing context
    ├── Multi-Agent: M agents × K turns × context sharing overhead
    ├── Workflow/Pipeline: N sequential steps
    ├── RAG Pipeline: retrieval tokens + generation tokens
    ├── Tool cost accumulation (framework-specific extras)
    └── Monte Carlo simulation over iteration count uncertainty
    ↓
Scale Projector
    ├── users × calls_per_user × days
    ├── Context caching discount (provider-specific)
    ├── Batch API discount (where available)
    └── Growth factor
    ↓
Cost Calculator (Multi-Provider)
    ├── Pricing Registry → per-provider, per-model, per-modality pricing
    ├── Token cost: sum(modality_tokens × provider_modality_price)
    ├── Platform tool costs (if applicable): provider-specific add-ons
    ├── Total = token_cost + tool_cost + platform_cost
    └── Cross-provider comparison (same workload, different providers)
    ↓
Output Report
    ├── Per-modality token breakdown
    ├── Per-provider cost (primary + alternatives)
    ├── Total tokens by type: mean, 95% CI, worst-case
    ├── Total cost: mean, CI, worst-case
    ├── Daily / monthly / annual projections
    ├── Provider comparison table (same task, different models)
    └── Optimization suggestions (model swap, caching, batch, provider switch)
```

## Workflow Archetypes (Framework-Agnostic)

| Archetype | Pattern | Applicable Frameworks | Token Profile |
|-----------|---------|----------------------|---------------|
| **SingleCall_TextOnly** | One LLM call, text in/out, no tools | Any | 1 call, very predictable |
| **SingleCall_Vision** | One call with image inputs | Any with vision model | Image tokens dominate input budget |
| **SingleCall_ImageGen** | One call producing images | OpenAI DALL-E, Vertex Imagen | High output token cost |
| **RAG_Pipeline** | Retrieve chunks → inject → generate | LangChain, MAF File Search, LlamaIndex | Chunk tokens + generation |
| **ReAct_Agent** | Observe → Think → Act iterative loop | LangChain Agent, MAF Hosted, AutoGen | 3-15 iterations; high variance |
| **MultiAgent_Collab** | Multiple agents communicating | CrewAI, AutoGen GroupChat, MAF A2A | M agents × K turns |
| **Workflow_Sequential** | Multi-step pipeline with branching | LangGraph, MAF Workflow, Prefect | N steps; tool calls per step |
| **CodeExec_Loop** | Generate code → execute → refine | Code Interpreter, Open Interpreter | Code gen + iteration loops |
| **Conversational_Multi** | Multi-turn chat with growing history | Any chatbot with memory | Context window fills over turns |
| **CUA_ScreenAgent** | GUI automation with screenshots | OpenAI CUA, Claude Computer Use | Very high image token volume |

## Steps

### Phase 1: Core Foundation (Provider-Agnostic)
1. **Project scaffolding** — Python package: `pyproject.toml`, `src/future_token_predictor/`, tests, data
2. **Provider registry & pricing** — `providers/` module: abstract pricing interface + concrete implementations for OpenAI, Azure, Anthropic, Google, Mistral. Static catalog with live-API override for Azure
3. **Multimodal token calculator** — `token_calculator.py`: provider-specific image token formulas, universal document estimation, audio duration conversion, text tokenization (tiktoken + fallback estimator)
4. **Archetype definitions** — `archetype_profiles.yaml` with framework-agnostic workflow archetypes
5. **Use case classifier** — `classifier.py`: NL → `UseCaseProfile` detecting provider, model, modality, agent pattern, framework, and complexity

### Phase 2: Prediction Engine (*depends on Phase 1*)
6. **Single-call predictor (Tier 1)** — Heuristic: classify prompt + modality → per-modality token estimates using provider-specific formulas
7. **Workflow predictor** — Takes `UseCaseProfile`, uses archetype model → Monte Carlo over iterations and tool calls → total token distribution with CI
8. **Tool cost estimator** — Framework-specific tool costs (MAF File Search, LangChain callbacks, etc.) as fixed-price additions
9. **Scale projector** — Users × frequency × time period; provider-specific caching discounts

### Phase 3: Cost & Output (*depends on Phase 2*)
10. **Multi-provider cost calculator** — Query pricing registry → compute per-provider cost; generate comparison table for alternative providers
11. **Report generator** — Per-modality breakdown, per-provider comparison, totals, projections, optimization hints
12. **Provider comparison** — "Same workload on Claude vs GPT-4.1 vs Gemini" side-by-side cost/quality trade-off

### Phase 4: MCP Server Surface (*depends on Phase 3*)
13. **MCP Server implementation** — `mcp_server.py`: tools for `predict_token_usage`, `get_model_pricing`, `compare_providers`, `estimate_image_tokens`, `estimate_document_tokens`
14. **VS Code integration** — `.vscode/mcp.json` for local MCP server registration
15. **Cross-client support** — Document Claude Desktop, Cursor, and other MCP client configurations

### Phase 5: Future Enhancements
16. **Azure Functions hosting** — Remote MCP server deployment
17. **Tier 2 calibration** — Historical calibration from usage logs
18. **Tier 3 LLM-assisted** — Meta-prompting per [2], [6]
19. **Provider API live pricing** — Auto-fetch from OpenAI, Anthropic, Google pricing APIs when available
20. **PTU/Commitment break-even** — At what usage should you switch to provisioned/committed tiers?

## Project Structure

```
FutureTokenPredictor/
├── pyproject.toml
├── plan.md
├── README.md
├── .vscode/
│   └── mcp.json
├── .github/
│   └── copilot-review-council.md
├── src/
│   └── future_token_predictor/
│       ├── __init__.py
│       ├── predictor.py                 # Main orchestrator: predict()
│       ├── mcp_server.py                # MCP server entry point
│       ├── classifier.py                # NL → UseCaseProfile
│       ├── token_calculator.py          # Multimodal token computation
│       ├── archetypes.py                # Workflow archetype definitions + matching
│       ├── single_call_predictor.py     # Tier 1 heuristic prediction
│       ├── workflow_predictor.py        # Monte Carlo workflow simulation
│       ├── tool_cost_estimator.py       # Framework-specific tool costs
│       ├── cost_calculator.py           # Multi-provider cost computation
│       ├── scale_projector.py           # User × frequency scaling
│       ├── report.py                    # Output report generation
│       ├── models/
│       │   └── schemas.py              # UseCaseProfile, PredictionResult, etc.
│       ├── providers/
│       │   ├── __init__.py             # Provider registry
│       │   ├── base.py                 # Abstract provider interface
│       │   ├── openai_provider.py      # OpenAI + Azure OpenAI
│       │   ├── anthropic_provider.py   # Anthropic Claude
│       │   ├── google_provider.py      # Google Gemini
│       │   ├── mistral_provider.py     # Mistral
│       │   ├── cohere_provider.py      # Cohere
│       │   ├── bedrock_provider.py     # AWS Bedrock
│       │   ├── local_provider.py       # Ollama, vLLM (zero-cost tokens)
│       │   └── catalog.yaml            # Static pricing fallback (all providers)
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── maf_adapter.py          # Microsoft Foundry Agent Service specifics
│       │   ├── langchain_adapter.py    # LangChain overhead model
│       │   └── crewai_adapter.py       # CrewAI overhead model
│       └── history/
│           ├── database.py             # SQLite for calibration
│           └── calibrator.py           # Tier 2 regression
├── data/
│   └── archetype_profiles.yaml
├── tests/
│   ├── test_classifier.py
│   ├── test_token_calculator.py
│   ├── test_workflow_predictor.py
│   ├── test_providers.py
│   └── test_cost_calculator.py
└── examples/
    ├── example_openai_agent.py
    ├── example_claude_rag.py
    ├── example_gemini_vision.py
    └── example_provider_comparison.py
```

## Verification
1. **Image token math (OpenAI)**: 1024×1024 high-detail → 4 tiles × 170 + 85 = 765 tokens
2. **Image token math (Gemini)**: Any resolution → 258 tokens per image
3. **Image token math (Claude)**: 1024×1024 → verify against Anthropic docs
4. **Multi-provider pricing**: Same 1000-token prompt → correct cost for each provider
5. **Classifier accuracy**: "Claude agent analyzing PDFs with RAG" → provider=anthropic, archetype=RAG_Pipeline
6. **Provider comparison**: Same workload produces valid cost comparison across 3+ providers
7. **Monte Carlo convergence**: 1000 samples → CI CV < 5%
8. **End-to-end test**: "200 users, 20 queries/day, GPT-4o vision agent" → complete report with alternatives

## Decisions
- **Provider-agnostic core** — Not locked to any single provider; all major LLM providers supported
- **Framework-agnostic archetypes** — Workflow patterns apply across MAF, LangChain, CrewAI, AutoGen, custom
- **Provider-specific token formulas** — Each provider's image/audio token counting is different; no "one formula fits all"
- **Static pricing with live override** — Static YAML catalog for all providers; Azure gets live API pricing as bonus
- **Unified output** — Reports include cross-provider comparison by default
- **Pluggable provider architecture** — New providers can be added via simple subclass
- **MAF remains a first-class adapter** — Not removed, but one of several supported frameworks

## Further Considerations
1. **Token count accuracy varies by provider**: OpenAI (tiktoken = exact), Anthropic (~95% accurate estimation), Google/Mistral (~90% character-based estimation). Report should flag accuracy confidence.
2. **Pricing staleness**: Static catalogs go stale. Include `pricing_age_days` in reports and warn if >30 days old.
3. **Provider-specific features**: Anthropic prompt caching, Google context caching, OpenAI cached_input — all reduce cost differently. Model each.
4. **Batch/async discounts**: OpenAI (50% off), Anthropic (50% off), Google (varies). Include in optimization suggestions.
5. **Rate limits matter for scale**: At high volume, some providers throttle. Note this in scale projections.
