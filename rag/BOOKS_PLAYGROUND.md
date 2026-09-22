# Bounded books playground adapter

`rag/books_playground.py` implements a separately authorized **evaluation proof**,
not production admission or policy publication. It runs one lexical Azure AI
Search query and one direct Foundry Responses call per question. It does **not**
invoke the deployed Foundry agent wrapper, its MCP tools, semantic reranker,
vector embeddings, cache, routing controller, or agentic retry loop.

## Server configuration

The configured workload is the existing `books` index with `id`, `book`, `content`
fields. Credentials, arbitrary URLs, corpus selection, deployment selection and
quality results are never accepted from the browser.

| Setting | Default |
| --- | --- |
| `RAG_PLAYGROUND_SEARCH_ENDPOINT` | `https://search-xbk6ickycmp22.search.windows.net` |
| `RAG_PLAYGROUND_MODEL_ENDPOINT` | `https://ai-account-xbk6ickycmp22.cognitiveservices.azure.com` |
| `RAG_PLAYGROUND_DEPLOYMENT` | `gpt-5-6-luna` (only supported deployment) |

These server-only overrides may live in the existing root `.env`. The dedicated
settings intentionally do not inherit the old general-purpose
`AZURE_SEARCH_ENDPOINT` / `AZURE_OPENAI_ENDPOINT`: those currently name older
resources. Authentication uses existing `DefaultAzureCredential` / Entra access,
Search Index Data Reader and the model data-plane role. No new dependencies or
credentials are introduced.

Existing `TOKENGOV_POLICY_SOURCE=azure`, Azure App Configuration endpoint/key/label,
and evaluation authorization settings remain authoritative. Local evaluation
requires `TOKENGOV_EVALUATION_ALLOW_LOCAL=true`; authenticated Container Apps
ingress uses `TOKENGOV_EVALUATION_ALLOWED_PRINCIPAL` and
`TOKENGOV_LIFECYCLE_AUTHENTICATED_INGRESS=container_apps`.
The existing same-origin `Origin` and `X-TokenGov-CSRF` checks apply.

## API and limits

- `GET /api/rag-playground/connection`: nonsecret configuration, sample questions,
  authorization and scope notes. Returns `ready`, `status`, `public_config`,
  structured `limits` and `sample_questions`. Readiness probes only the Search
  document count and model inventory, not inference or retrieval queries. Ready
  proves read-only access, not evaluation authorization or successful inference.
  `message` is always present; connection failures also return a nonsecret `error`
  and `ready:false`.
- `POST /api/plans/{plan_id}/rag-playground`:
  `{"questions":["one question"],"request_id":"<UUID>"}`. The UUID is optional for
  backward compatibility and is persisted in authorization/result evidence.
  A Foundry `rag_pipeline` receipt for
  `gpt-5.6-luna` is required. Response is the persisted run result: `run_id`,
  `execution_status`, and `answers` containing `question`, `answer`, `sources`
  (`id`, `book`, `content`), provider `usage`, task/trajectory/segment identity and
  status. A 201 means evidence persisted; inspect `execution_status` for failures.
  Successful usage includes `input_tokens`, `output_tokens`, and
  `embedding_tokens:0` with an explicit not-applicable lexical-retrieval label.
  Raw provider usage details remain preserved. The persisted `result.json` also
  includes `rag_playground.answers` for reopening via the existing
  `GET /api/runs/{run_id}` response's `result.rag_playground` object.
- One submission of **1–3 questions per report**, each at most 1,200 characters.
  A filesystem-exclusive claim is made before network activity. Concurrent/new
  submissions are blocked. An identical completed submission returns its saved
  result without another model call, even if a browser regenerates its UUID.
  Reusing a UUID with changed questions does not authorize more work.
  Failure/crash does not release the claim.
  Never delete the claim to retry a potentially charged request.
- Each question permits one Search request and one model request, no retries,
  at most four retrieved excerpts, 6,000 conservatively bounded input tokens
  (UTF-8 text bytes plus framing allowance) and 512 output tokens. The model HTTP
  timeout is 45 seconds; Search connect/read timeouts are 10/15 seconds. Use one
  question for the short synchronous browser proof.
- All active Azure receipt admission checks (including required infrastructure
  forecast checks) apply; missing quality evidence is not operational promotion.
  Worst-case model reservations
  use the versioned sourced Luna catalog and must fit both Azure per-call and
  per-run monetary limits. These are **model-spend limits**, not a claim to bound
  unpriced infrastructure or total task charges. Unknown execution controls block.

## Evidence and constitutional alignment

Advances `execute -> evaluate` by consuming the immutable original forecast,
workload/segment contract, model/pricing catalog and current Azure policy
version/hash/label/ETag. Produces append-only authorization, complete **direct
retrieval + response** task envelopes, provider tokens (including returned detail
fields), sources, answers, meter ledger and registered `result.json`. The
existing read-only source preview / attachment / Evaluate APIs consume these
artifacts. Operational-admission state and prior receipts are not changed.

Lexical retrieval invokes no embedding model; embedding usage is explicitly
not applicable, not silently omitted hybrid-search cost. Search operations are
measured but unpriced. Resource/observability allocation remains unavailable.
Model list-price allocation is separate from invoiced cost. No acceptance
outcomes, quality passes, representative sampling or calibrated tail-risk claims
are fabricated. Samples identify easy/hard segments; custom questions use a
separate unclassified playground segment. Evaluate correctly remains
**inconclusive** without compatible acceptance rules/outcomes, sufficient samples
and full priced cost coverage.

Workload logic stays in `rag/`; core evidence stores remain reusable. Runtime only
reads Azure policy, never publishes it. This is measured incremental workload
evidence when actually invoked, not deployed-agent-loop or production-validated
governance. Remaining end-to-end gaps include decision-grade segment quality,
complete-task monetary coverage/calibration, candidate comparisons, authorized
policy response/reversion and subsequent predictor learning.
