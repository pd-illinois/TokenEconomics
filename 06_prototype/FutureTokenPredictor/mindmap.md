<!-- GENERATED from .weave/state.yaml by scripts/render.py. Do not edit by hand. -->

# <name your product>

**Status:** shaping

_The design view: the idea, the chosen tech stack, and how the blocks fit together. For live status, see `kanban.md`._

## Core idea

Future Token Predictor is an MCP server that forecasts the LLM token usage and dollar cost of an agentic workflow before it runs - treating tokens as "agentic currency." Given a natural-language or structured description of an agent (model, tools, multi-step loops, RAG, multimodal inputs, user/volume scale), it returns predicted token breakdowns (prompt, output, hidden reasoning, context growth) and cost ranges with Monte Carlo confidence intervals across major providers (starting with Microsoft Foundry), using a 3-tier cascade: heuristic+Monte Carlo baseline, optional regression calibration on historical logs, then optional LLM-assisted estimation, always returning a usable range even when later tiers are unavailable. Out of scope for this MVP: live Azure infrastructure pricing and footprint sizing (compute, egress, concurrency) - that is a separate Azure/Foundry MCP server composed on top, not part of this predictor. Done when an MCP client can describe an agentic workload and get a per-call token breakdown plus a scaled (daily/monthly) cost range with confidence intervals, across at least the Foundry provider set.

## Tech stack

The components MVPWeaver assembles:

```text
+- Tech stack -------------------------------------------------------------------------------------------------------------+
| Python 3.11+                      Implementation language and runtime for the predictor pipeline and MCP server          |
| MCP SDK (mcp, stdio)              Model Context Protocol server framework exposing the 6 prediction tools to MCP clients |
| tiktoken                          Provider tokenization / token counting for prompt and output estimation                |
| NumPy                             Monte Carlo simulation and statistical confidence-interval math                        |
| httpx                             Async HTTP client for live model-catalog and provider pricing lookups                  |
| PyYAML                            Loads archetype profiles and config from data YAML                                     |
| azure-identity                    Authentication for Microsoft Foundry / Azure model catalog access                      |
| python-dotenv                     Local environment/config loading                                                       |
| hatchling                         Build backend for the src-layout package                                               |
| pytest + pytest-asyncio + respx   Test runner and HTTP mocking for the verification suite                                |
+--------------------------------------------------------------------------------------------------------------------------+
```

## Block dependency graph

Indented children depend on the block above them.

```text
[x] done/baseline   [~] building/in-review   [ ] todo   [!] unverified   [-] reverted

[x] B0  classifier
[x] B1  llm_classifier
[x] B2  token_calculator
[x] B3  workflow_predictor
[x] B4  cost_calculator
[x] B5  model_validator
[x] B6  tier3_estimator
[x] B7  providers
[x] B8  predictor
[x] B9  mcp_server
```

## Decisions

Why each block is built the way it is -- recorded provenance and purpose, so the source can stay light on comments.

### B0 - classifier
- Pattern: Precompiled-regex detection table + dataclass capability flags, set in the strategy-fallback classify() pipeline (LLM-first, regex always runs for these signals).
- Complexity: O(n) per flag over description length with 4 precompiled patterns; constant extra memory; negligible vs existing detectors.
- Provenance: `authored`
- Rationale: Capability cost-lever detection (prompt caching, batch API, streaming, retrieval) lives in the classifier's existing regex-fallback stage as four module-level precompiled patterns plus four boolean flags on UseCaseProfile, rather than a new module. Keeps all NL->profile signal extraction in one seam and mirrors the existing _MODEL_PATTERNS/_detect_* style.

### B1 - llm_classifier
- Pattern: Fallback parser + structured-output schema extension; first-balanced-object regex extraction guards prose-wrapped replies.
- Complexity: O(n) over the reply text for the single regex scan; no extra LLM calls or allocations on the happy path.
- Provenance: `authored`
- Rationale: Extended the in-file LLMClassification dataclass and _parse_classification rather than touching schemas.py or classifier.py, keeping all changes inside B1's seam. Tolerant extraction added as a fallback only (strict json.loads still tried first) so existing clean-JSON behaviour is unchanged. The four cost-lever booleans mirror B0's UseCaseProfile flags to surface the same signal from the LLM path; they default False and are not yet consumed by classify() (which sets the profile flags via regex) -- wiring them in is a future B0-seam change, deliberately out of scope here.

## Block specs

### B0 - classifier  `[done]`
- Goal: Adopted seam: src/future_token_predictor/classifier.py.
- Depends on: none
- Module: `src/future_token_predictor/classifier.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_classifier.py`
- Checkpoint Tag: `weave/B0`

### B1 - llm_classifier  `[done]`
- Goal: Adopted seam: src/future_token_predictor/llm_classifier.py.
- Depends on: none
- Module: `src/future_token_predictor/llm_classifier.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_llm_classifier.py`
- Checkpoint Tag: `weave/B1`

### B2 - token_calculator  `[baseline]`
- Goal: Adopted seam: src/future_token_predictor/token_calculator.py.
- Depends on: none
- Module: `src/future_token_predictor/token_calculator.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_token_calculator.py`

### B3 - workflow_predictor  `[baseline]`
- Goal: Adopted seam: src/future_token_predictor/workflow_predictor.py.
- Depends on: none
- Module: `src/future_token_predictor/workflow_predictor.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_tier2.py`

### B4 - cost_calculator  `[baseline]`
- Goal: Adopted seam: src/future_token_predictor/cost_calculator.py.
- Depends on: none
- Module: `src/future_token_predictor/cost_calculator.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_cost_calculator.py`

### B5 - model_validator  `[baseline]`
- Goal: Adopted seam: src/future_token_predictor/model_validator.py.
- Depends on: none
- Module: `src/future_token_predictor/model_validator.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_model_validator.py`

### B6 - tier3_estimator  `[baseline]`
- Goal: Adopted seam: src/future_token_predictor/history/tier3_estimator.py.
- Depends on: none
- Module: `src/future_token_predictor/history/tier3_estimator.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_tier3.py`

### B7 - providers  `[baseline]`
- Goal: Adopted seam: src/future_token_predictor/providers.
- Depends on: none
- Module: `src/future_token_predictor/providers`
- Runner: `pytest`
- Acceptance Test: `tests/test_providers.py`

### B8 - predictor  `[baseline]`
- Goal: Adopted seam: src/future_token_predictor/predictor.py.
- Depends on: none
- Module: `src/future_token_predictor/predictor.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_predictor.py`

### B9 - mcp_server  `[baseline]`
- Goal: Adopted seam: src/future_token_predictor/mcp_server.py.
- Depends on: none
- Module: `src/future_token_predictor/mcp_server.py`
- Runner: `pytest`
- Acceptance Test: `tests/test_mcp_server.py`

