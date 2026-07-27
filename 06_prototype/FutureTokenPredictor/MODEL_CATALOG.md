# Model Catalog and Pricing Lifecycle

## Product rule

The selectable unit is a **provider/model offering**, not a model name. `gpt-4.1` on OpenAI and `gpt-4.1` on Azure OpenAI are separate offerings even when they share token formulas. Provider choice controls price lookup, deployment options, source provenance, and policy admission.

Studio obtains offerings from `build_model_catalog()`. The Plan dropdown includes only entries that have both `ModelInfo` and `PricingTier`. Models discovered from a live API without complete pricing remain visible to operators through the API's `unavailable` collection, but cannot be selected for a cost forecast.

Each current offering contains:

- provider and model ID;
- input, output, cached, image, audio, and batch prices where known;
- context and output limits;
- vision, audio, reasoning, and caching capabilities;
- tokenizer and reasoning multiplier;
- official pricing and model-catalog URLs.

This prevents a discovered model ID from being presented as economically supported before its billing behavior is understood.

## Current limitations

The registry is useful but is not yet a complete production catalog:

1. Provider modules keep model metadata and prices in separate static dictionaries, so drift is possible.
2. Live model APIs usually return IDs and partial capabilities, not authoritative prices.
3. Static prices do not carry `effective_from`, `effective_to`, retrieval time, currency, region, deployment mode, or source evidence.
4. Azure prices can vary by Global, Data Zone, Regional, Batch, and Provisioned deployment. The current dropdown shows the provider adapter's baseline token rates.
5. Aliases, dated model revisions, preview status, deprecation, retirement, and replacement chains are not first-class fields.
6. Reasoning is represented by a multiplier. Some providers instead expose effort levels, explicit thinking budgets, or billed reasoning tokens.
7. If upstream validation is bypassed, `AzurePricingClient` can still fall back to GPT-4.1 prices for an unknown model. Forecast paths should eventually fail closed at price resolution too.
8. Provider availability does not imply Govern approval. Studio intentionally shows priced offerings and separately warns when the active policy would reject one.
9. Local/Ollama offerings have zero marginal token API price; their GPU, hosting, energy, and operations cost belongs in the separate infrastructure ledger and must not be interpreted as zero total cost.

## Recommended canonical record

Maintain one versioned record per commercial offering with a stable key such as:

```text
provider / model / API surface / deployment mode / region / currency / price effective date
```

The record should contain four independently sourced sections:

| Section | Required fields |
|---|---|
| Identity | canonical ID, aliases, dated revisions, provider, API surface, lifecycle status, replacement |
| Capabilities | modalities, context/output limits, tool use, structured output, reasoning modes, tokenizer/formula version |
| Pricing | billing unit, input/output/cached/reasoning/image/audio prices, tiers, currency, region, deployment mode |
| Provenance | source URL/API, retrieved time, effective interval, parser version, checksum, reviewer |

Do not overwrite old prices. Close the previous effective interval and append a new revision so historical receipts can reproduce the rate used at prediction time.

## Maintenance pipeline

1. **Discover** models from official provider and Azure/Foundry catalogs on a schedule.
2. **Normalize** aliases and provider-specific identifiers into candidate offering records.
3. **Join** each candidate to official pricing, capabilities, tokenizer/formula, and lifecycle evidence.
4. **Quarantine** incomplete or conflicting candidates as `discovered_unpriced` or `needs_review`; never apply nearest-model pricing automatically.
5. **Validate** schema, non-negative rates, required input/output units, capability/price consistency, duplicate keys, and effective intervals.
6. **Diff and review** additions, price changes, capability changes, and retirements through source control.
7. **Publish** a signed, immutable catalog revision. Pin its revision and price revision into every Plan receipt.
8. **Monitor** source age and discovery coverage. Alert on stale prices, newly discovered unpriced models, removed models, and parser failures.

Suggested service levels:

- discover daily;
- refresh dynamic Azure prices at least daily;
- alert when a price source is older than 48 hours;
- require human review for price changes beyond a configured threshold;
- retain retired offerings for historical replay but prevent them from new Plans.

## Studio evolution

The current dropdown is the first safe slice. Next iterations should add provider, deployment mode, region, and currency filters; display pricing provenance and age; expose unavailable candidates to catalog administrators; and pin `catalog_revision`, `offering_key`, and `price_revision` into the immutable Plan receipt. Govern policy should then allow or deny offering keys and deployment dimensions rather than ambiguous model names alone.