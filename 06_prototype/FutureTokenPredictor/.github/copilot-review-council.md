---
description: "LLM Council — Multi-model review protocol for the Future Token Predictor project. Ensures accuracy, correctness, and performance across all supported LLM providers."
---

# LLM Council Review Protocol

## Purpose
This council ensures the Future Token Predictor produces **accurate, performant, and maintainable** predictions across all supported LLM providers (OpenAI, Anthropic, Google, Mistral, Cohere, Meta, AWS Bedrock, local models). Each council member brings a distinct expertise that prevents specific categories of bugs.

## Council Members

Each member has a dedicated agent file in `.github/agents/` that can be invoked directly in VS Code:

| Role | Model | Agent File | Responsibility |
|------|-------|-----------|----------------|
| **Architect** | Claude Opus 4 | `@architect` | System design, provider abstraction, MCP protocol, schema evolution |
| **Precision Engineer** | o3 | `@precision-engineer` | Token formula math, Monte Carlo stats, cost arithmetic |
| **Code Quality & Performance** | GPT-4.1 | `@code-quality` | Python practices, type safety, test coverage, performance |
| **Security & Data Integrity** | Claude Sonnet 4 | `@security` | Input validation, API safety, cache integrity, SSRF prevention |
| **Provider Accuracy Auditor** | Gemini 2.5 Pro | `@provider-auditor` | Cross-check formulas & pricing against official docs |

## Review Checklist

### 1. MATH CHECK (Precision Engineer — o3)
- [ ] **OpenAI image tokens**: `ceil(w/512) * ceil(h/512) * 170 + 85` for high detail; 85 for low
- [ ] **Anthropic image tokens**: Verify against Claude vision docs (resolution-dependent tiers)
- [ ] **Gemini image tokens**: Fixed 258 tokens per image regardless of resolution
- [ ] **Audio tokens**: OpenAI = ~43 tok/sec; Gemini = ~32 tok/sec; verified against docs
- [ ] **Reasoning multipliers**: o3 = 5x, o4-mini = 3x, Claude thinking = explicit budget, Gemini thinking = 2-4x
- [ ] **Cost arithmetic**: All divisions by 1_000_000 for per-million pricing; no off-by-one
- [ ] **Monte Carlo stability**: CV < 5% at N=1000 samples
- [ ] **Caching discounts correct**: OpenAI cached_input = 50-75% off; Anthropic context caching = 90% off read; Gemini = varies
- [ ] **Cross-provider parity**: Same text input → similar token count (±10%) across providers using same tokenizer family

### 2. ARCHITECTURE CHECK (Architect — Claude Opus 4)
- [ ] **Provider interface complete**: All providers implement the same abstract methods
- [ ] **No provider-specific logic in core**: Token calculator dispatches to provider; no `if provider == "openai"` in predictor
- [ ] **Schema backward-compatible**: New fields have defaults; old profiles still work
- [ ] **MCP tool schemas**: Input validation complete, descriptions guide the LLM client
- [ ] **Error responses structured**: Provider unavailable, unknown model, pricing stale — all handled gracefully
- [ ] **Plugin boundary clear**: New provider = new file in `providers/`; no changes to core modules

### 3. PROVIDER ACCURACY CHECK (Provider Accuracy Auditor — Gemini 2.5 Pro)
- [ ] **Pricing catalog current**: All prices within 30 days of provider's published rates
- [ ] **Model list complete**: No major models missing from any supported provider
- [ ] **Token formula matches docs**: Each provider's image/audio formula verified against official documentation
- [ ] **Caching mechanics correct**: Each provider's caching discount applied correctly
- [ ] **Batch/async pricing**: Where available, discounts accurately represented

### 4. SECURITY CHECK (Security — Claude Sonnet 4)
- [ ] **No API keys in code**: Pricing APIs are public or use env vars
- [ ] **HTTP clients parameterized**: No f-string URLs with user input
- [ ] **MCP input validation**: All tool inputs validated (ranges, types, enums) before processing
- [ ] **Cache integrity**: Pricing cache can't be poisoned by malformed API responses
- [ ] **No credential exposure**: MCP tool responses never leak internal paths, keys, or config

### 5. PERFORMANCE CHECK (Code Quality — GPT-4.1)
- [ ] **Startup time < 2s**: MCP server initializes quickly (lazy-load pricing)
- [ ] **Prediction latency < 500ms**: Single prediction completes fast (no blocking API calls on critical path)
- [ ] **Memory < 100MB**: No large data structures held permanently
- [ ] **Test coverage ≥ 80%**: Core math modules at 100%
- [ ] **Type annotations complete**: mypy strict mode passes
- [ ] **No circular imports**: Provider modules don't import from core predictor

## How to Invoke the Council

### Self-Review (During Development)
Before marking any module complete, apply ALL 5 checklists mentally. Key questions:
1. "Would o3 find a math error in my token formula?"
2. "Would the Architect say my abstraction leaks provider details?"
3. "Would Gemini 2.5 Pro say my pricing is stale or my formula doesn't match docs?"
4. "Would Sonnet 4 find an input validation gap?"
5. "Would GPT-4.1 find a performance or type safety issue?"

### PR Review (Multi-Model Protocol)
```
Step 1 (o3):     "Review for mathematical correctness — verify token formulas per provider, 
                  Monte Carlo statistics, and cost arithmetic. Flag any formula that doesn't 
                  match the provider's documentation."

Step 2 (GPT-4.1): "Review for Python best practices, type annotations, test coverage, 
                   performance (startup time, prediction latency), and dependency hygiene."

Step 3 (Gemini):  "Cross-check all pricing values and token formulas against the official 
                   documentation for each provider. Are any values stale or incorrect?"

Step 4 (Default): Architecture review — provider abstraction, schema design, MCP protocol.
```

### Quarterly Pricing Audit
Every 30 days (or when a provider announces pricing changes):
1. Run Gemini 2.5 Pro audit: "Verify all pricing in `catalog.yaml` matches current published rates"
2. Update static catalog with any changes
3. Add regression test for any new pricing tier

## Automated Quality Gates (CI)

```yaml
# .github/workflows/quality.yml
jobs:
  quality:
    steps:
      - name: Unit tests
        run: pytest tests/ -x --cov=src/future_token_predictor --cov-fail-under=80
      
      - name: Type checking
        run: mypy src/future_token_predictor/ --strict
      
      - name: Linting
        run: ruff check src/ tests/
      
      - name: Token formula regression
        run: pytest tests/test_token_calculator.py -k "regression" --tb=short
      
      - name: Provider pricing validation
        run: pytest tests/test_providers.py -k "pricing_sanity" --tb=short
      
      - name: Monte Carlo stability
        run: pytest tests/test_workflow_predictor.py -k "convergence" --tb=short
```

## Quality Thresholds

| Metric | Target | Rationale |
|--------|--------|-----------|
| Test coverage | ≥80% overall, 100% for math modules | Token formulas must be bulletproof |
| Type coverage (mypy strict) | 0 errors | Prevents provider interface drift |
| Token calc regression | Must pass exactly | Catches formula regressions |
| Monte Carlo stability | CV < 5% at N=1000 | Reproducible predictions |
| Pricing staleness | < 30 days | Cost estimates remain actionable |
| MCP response time | < 500ms p95 | Usable in interactive chat |
| Startup time | < 2 seconds | MCP server usable immediately |
| Provider parity | Same text → ±10% token count | Cross-provider comparisons are valid |

## Provider Addition Protocol

When adding a new provider:
1. Create `providers/{name}_provider.py` implementing `BaseProvider`
2. Add pricing to `catalog.yaml` with source URL and date verified
3. Add image token formula (if vision supported) with documentation link
4. Add regression tests with known-value assertions
5. Run full council checklist on the new provider module
6. Update `classifier.py` model patterns to detect the new provider's models
