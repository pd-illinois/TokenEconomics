# Azure deployment guidance

This repository is a public reference implementation. It intentionally excludes live
tenant identifiers, subscription identifiers, resource names, endpoints, policy
provenance, deployment receipts, incident records, and authorization evidence.

Use the templates under `infra/` with private parameter files or a protected deployment
pipeline. Do not commit environment-specific values. At minimum, keep these settings
outside the repository:

- Azure tenant, subscription, resource-group, and resource names;
- Foundry, Azure AI Search, App Configuration, storage, and hosted application endpoints;
- application/client/object identifiers and role-assignment evidence;
- policy keys, labels, ETags, approval URLs, and publication receipts;
- Key Vault secret URIs, credentials, tokens, connection strings, and certificate data.

The reference architecture assumes:

1. the data plane executes model, retrieval, and tool operations;
2. the control plane forecasts, compares policy, evaluates, responds, reconciles, and learns;
3. Azure App Configuration is authoritative and read fail-closed;
4. runtime and policy-publication identities are separate and least privileged;
5. operational evidence is retained in private, immutable stores.

Before deploying, review `docs/09_TOKENECONOMICS_CONSTITUTION.md`, use Azure policy and
security tooling appropriate to your tenant, and validate the generated infrastructure
templates with non-secret parameters.
