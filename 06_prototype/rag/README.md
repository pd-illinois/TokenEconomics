# Grounded RAG benchmark

This folder is the first TokenEconomics reference workload. It uses five public-domain Project Gutenberg books, Azure OpenAI embeddings and generation, and Azure AI Search hybrid vector/keyword retrieval with semantic reranking.

It is currently a grounded batch benchmark, not a hosted agent. Each request performs retrieval followed by one generation call; evaluation is post-hoc. Do not represent this path as a complete agent trajectory.

## Azure boundary

- Search service: `tokengov-rag-6ayi7` in `rg-tokengov-rag`
- Index: `books`
- Model and embedding deployments: Azure AI Services in `rg-tokengov`
- Request telemetry: existing Application Insights resource `tokengov-aoai-ai`
- Authentication: Entra ID through `DefaultAzureCredential`; no Search or model keys

The developer identity needs these roles scoped to the Search service:

- `Search Service Contributor` to create or update the index
- `Search Index Data Contributor` to upload documents
- `Search Index Data Reader` to query documents

The Search service must allow `aadOrApiKey` authentication. Local-key authentication remains enabled for compatibility but is not used by this workload.

## Configuration

Set these values in the gitignored `../.env` file. Do not commit credentials or connection strings.

```dotenv
AZURE_SEARCH_ENDPOINT=https://tokengov-rag-6ayi7.search.windows.net
AZURE_SEARCH_INDEX=books
AZURE_DEPLOYMENT_EMBEDDING=text-embed
RAG_TOP_K=12
RAG_EVALUATION_SAMPLE_RATE=1.0
```

The benchmark also uses the existing Azure OpenAI deployment variables and optional `APPLICATIONINSIGHTS_CONNECTION_STRING` documented in `../.env.example`.

## Install and ingest

From `06_prototype`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe rag\ingest.py
```

Ingestion downloads the books into the ignored `data/` directory, removes Gutenberg boilerplate, creates the vector and semantic index, embeds chunks, and uploads them. Re-running is idempotent because document IDs are stable.

## Run

Use a small cap first because every arm makes real model calls and evaluation adds judge calls:

```powershell
$env:LIVE_MAX_REQUESTS = "2"
$env:LIVE_WORKLOAD_REPEATS = "1"
.\.venv\Scripts\python.exe rag\bench_rag.py
```

The three arms are premium baseline, TokenGov balanced routing/cache, and Azure Model Router. Output is measured live behavior for this benchmark run; it does not establish calibrated tail risk or complete agent economics.

The current five-book index uses `RAG_TOP_K=12`. A 2026-07-22 retrieval check found all required facts for 7/7 easy golden cases, but only 2/5 hard synthesis cases. The hard segment is therefore not decision-grade; improving multi-source retrieval is follow-up work, and generation quality must not conceal missing retrieval evidence.

When Application Insights is configured, request-level `costgov.request` records are exported. Local governed-arm telemetry is written to the ignored `telemetry_rag.jsonl` file.