# TokenEconomics Studio user guide

This public guide explains the Studio workflow without exposing a live environment,
endpoint, report identifier, policy ETag, resource name, or deployment configuration.

## Workflow

Studio presents four operator workspaces, with Home outside the governance loop:

```text
Home -> Forecast -> Policy -> Execute & Review -> Performance & Decisions
```

### Home

Open a report from your authorized environment. Report identifiers are environment
specific and must not be copied into public documentation.

### Forecast

Review the versioned workload, model assumptions, native commercial meters,
infrastructure assumptions, exclusions, and immutable forecast receipt. Microsoft
Copilot Credits, GitHub AI Credits, model tokens, subscriptions, and Azure resource
charges remain separate evidence domains.

### Policy

Inspect the exact policy provenance supplied by the configured Azure App Configuration
authority. Missing or invalid authority fails closed. The browser can prepare a reviewed
change request but does not receive policy-publisher credentials.

### Execute & Review

Execute only an admitted policy binding. Review task and trajectory evidence, evaluator
outputs, and explicit human outcomes. Automated scores remain advisory; acceptance is
recorded separately as `accepted`, `rejected`, or `inconclusive`.

### Performance & Decisions

Compare expected and observed economics, segment-level acceptance, coverage, billing
reconciliation, policy decisions, and learning evidence. Unknown cost remains unknown;
a response-model allocation is not presented as a complete task bill.

## Safe demonstration

Use a local or privately deployed environment with synthetic or approved evidence:

1. Open an authorized report from Home.
2. Review forecast assumptions and meter boundaries.
3. Inspect policy provenance without changing authority.
4. Open an existing run; do not trigger billable execution during a read-only demo.
5. Review evaluator and human-acceptance evidence.
6. Show accepted-task economics, reconciliation coverage, and learning status.

Do not publish live hostnames, resource identifiers, report IDs, tenant or subscription
IDs, policy ETags, deployment revisions, screenshots containing operational metadata, or
private runbook details.

## Interpretation boundaries

- Modeled values are not measured usage.
- Provider token usage may not represent complete trajectory cost.
- Automated grader scores are not acceptance probabilities.
- A modeled percentile is not a calibrated breach guarantee.
- A deployed screen is not proof of end-to-end completion.
- The project does not claim guaranteed savings, quality, or production readiness.

The canonical project intent and completion gate are defined in
[`09_TOKENECONOMICS_CONSTITUTION.md`](09_TOKENECONOMICS_CONSTITUTION.md).
