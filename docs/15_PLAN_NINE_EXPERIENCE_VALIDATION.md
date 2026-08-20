# Studio Plan Nine-Experience Validation

*Measured locally on 2026-08-20. This report validates deterministic software behavior; it is not a tenant invoice, production usage record, accepted-task quality result, calibrated tail-risk claim, or savings guarantee.*

## Purpose and scope

This validation covers every delivery option displayed by Studio Plan:

1. Microsoft Copilot for employees
2. Copilot Cowork
3. Agent Builder
4. Copilot Studio
5. Custom agent using Work IQ APIs
6. Microsoft Foundry
7. GitHub Copilot
8. Copilot Studio with a Foundry model
9. Microsoft Foundry with Work IQ APIs

The change advances control-plane `predict`. The Microsoft Foundry cases validate model-call forecast composition, while the subscription, native-credit, and token-derived-credit cases validate their own commercial observation units. None of the cases represents a complete executed task or trajectory.

The versioned case fixture is [`../06_prototype/tests/fixtures/plan_experience_cases.v1.json`](../06_prototype/tests/fixtures/plan_experience_cases.v1.json). The executable proof is [`../06_prototype/tests/test_plan_experience_matrix.py`](../06_prototype/tests/test_plan_experience_matrix.py).

## Validation method

Each case traverses the Plan-only HTTP boundary:

```text
create report -> submit Plan case -> resolve meter stack -> calculate route
-> persist schema-4.0 receipt -> reopen receipt -> compare immutable evidence
```

Expected values are not accepted from the rendered UI. The test independently recomputes:

- seat allocation as `seats * allocated monthly cost per user`;
- Copilot Studio credits as `quantity / unit size * credits per unit`;
- zero-variance scenario totals as `task count * credits per task`;
- purchase drawdown, unfunded demand, incremental PAYG, retail, and amortized views;
- GitHub model cost from each token class and its source-pinned per-million rate;
- GitHub AI Credits from `model cost * credits per USD`;
- hybrid totals after normalizing monthly commercial amortized cost to the annual period of the unchanged model subforecast.

Scenario cases deliberately set P50 equal to P95. This makes the modeled lognormal variance zero, so every sample has the same independently knowable value. It proves task scaling and purchase composition without treating a stochastic snapshot as an oracle.

Model-route tests use a deterministic predictor seam to prove that Plan invokes FutureTokenPredictor only for the three model-backed routes and does not alter its returned annual model cost. FutureTokenPredictor's internal token and price arithmetic is separately governed by its mandated test runner.

## Case matrix and expected arithmetic

| Case | Consumption proof | Independent expected result |
|---|---|---:|
| Microsoft Copilot for employees | 10 seats at $30 allocated monthly | $300 fixed and amortized allocation |
| Copilot Cowork | 10 tasks at 7 modeled credits; 50 committed; $0.02 PAYG; $5 commitment; $20 fixed | 70 credits; $0.40 incremental; $25.40 amortized |
| Agent Builder | 5 seats at $25 allocated monthly | $125 fixed and amortized allocation |
| Copilot Studio | 3 generative answers at 2 credits; 5 committed; $0.01 PAYG; $1 commitment; $10 fixed | 6 credits; $0.01 incremental; $11.01 amortized |
| Work IQ APIs | 12 tasks at 3 modeled credits; 100 committed; $2 commitment; $10 fixed | 36 credits; 64 unused; $12 amortized |
| Microsoft Foundry | deterministic predictor result | $0.01 per call and $365 annual model cost retained unchanged |
| GitHub Copilot | 10M input at $2.50/M plus 1M output at $15/M; 100 credits/$; 1,900 included; $19 seats | $40 model usage; 4,000 gross credits; 2,100 additional; $40 modeled total |
| Copilot Studio + Foundry | 4 actions at 5 credits; commercial amortized $6.10/month; model annual $365 | 20 Copilot Credits; $438.20 annual hybrid total |
| Foundry + Work IQ | 5 tasks at 4 credits; commercial amortized $2.20/month; model annual $365 | 20 Copilot Credits; $391.40 annual hybrid total |

Microsoft Copilot Credits and GitHub AI Credits remain distinct currencies. No test converts one to the other or infers either from unrelated token evidence.

## Defect found and corrected

The first nine-case run failed the Foundry + Work IQ case. `forecast_variable_scenario()` omitted an explicit `status: complete`, causing valid scenario evidence to be treated as unresolved during hybrid composition. The resulting `hybrid.total_usd` was `null`.

The scenario contract now emits `status: complete`. Independent release review then found that the original hybrid sum mixed annual model cost with monthly commercial cost. Hybrid composition now requires explicit billing periods and normalizes monthly commercial cost to annual before summation. The rerun proves the corrected Foundry + Work IQ total is:

```text
$365.00 annual model subforecast + ($2.20 monthly commercial amortized cost * 12)
= $391.40 annual hybrid total
```

The same review added fail-closed evidence for four additional boundaries:

- capacity-risk GitHub forecasts return `needs_clarification` and create no completed receipt;
- purchase-portfolio scope mismatches return resumable clarification and create no completed receipt;
- GPT-5.4 and GPT-5.6 Luna select documented long-context rates from maximum input tokens per request;
- disabled GitHub overages retain known fixed-seat cost when usage remains within the included allowance;
- unknown receipt schemas are rejected, and the historical public `react_agent` input remains compatible by normalizing to `tool_agent`.

## Measured results

| Gate | Result |
|---|---|
| Nine-experience executable matrix | 10 passed: one catalog-coverage contract plus nine individually named route cases |
| Complete Studio suite | 105 passed |
| FutureTokenPredictor mandated runner | 485 passed; evidence verdict `pass`, exit 0 |
| UI route/card behavior | 9/9 cards selected the expected guided field group and `Build forecast` or `Analyze workload` action |
| Browser console | 0 errors and 0 warnings during the route-card matrix |
| Allowlisted release build | 53 files built; inventory hashes verified; isolated `plan_studio` import succeeded |
| Patch integrity | `git diff --check` completed with no whitespace errors |

The Python 3.14 environment emitted existing `pytest_asyncio` deprecation warnings. They are environment debt, not arithmetic failures.

## Evidence consumed and produced

**Consumed**

- `consumption-models.v1` product meter stacks;
- Copilot Studio rate card `2026-08-03.1`;
- GitHub Copilot usage rate card `2026-08-20.1`;
- versioned entitlement, scenario, purchase, seat, token, and confirmed-profile inputs;
- deterministic FutureTokenPredictor seam for Plan composition;
- the mandated FutureTokenPredictor suite for predictor-owned arithmetic.

**Produced**

- individually named test outcomes for every Plan option;
- independently recomputed expected values;
- corrected scenario completion status;
- schema-4.0 immutable receipts whose stack, commercial result, and prediction reopen unchanged;
- browser evidence for all nine guided-input paths;
- a 53-file hash-verified release package.

## Constitutional alignment and remaining gap

The validation preserves two-plane separation, fail-closed evidence loading, immutable receipts, explicit model-call versus commercial-meter scope, and honest modeled/measured labels. It introduces no runtime quality claim, so it does not weaken segment-level quality controls.

TE-001.5's software, arithmetic, compatibility, browser, immutable-evidence, and release-isolation gates are satisfied. The reviewed scoped release change set captures the implementation, versioned fixtures, tests, decisions, and this evidence report in Git; the unrelated local Studio screenshot is explicitly excluded.

The end-to-end TokenEconomics completion definition also remains open beyond this release slice: no case proves `compare policy -> admit -> execute -> evaluate -> respond -> reconcile -> learn`, segment-level accepted-task quality, calibrated budget-breach probability, tenant billing reconciliation, or learned subsequent forecasting.
