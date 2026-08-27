"""
rag/ingest.py — build the book corpus index (RAG data-plane, one-time).

Pipeline: download 5 Project Gutenberg books -> strip boilerplate -> chunk ->
embed each chunk with the deployed Azure embedding model -> create an Azure AI Search
index (vector + semantic) -> upload the chunks.

Auth is Entra ID everywhere (DefaultAzureCredential), consistent with the rest of the
prototype — no keys. Requires these RBAC roles on the search service for the signed-in user:
  * Search Service Contributor      (create the index)
  * Search Index Data Contributor   (upload documents)
Run from the repository root: `.venv/Scripts/python.exe rag/ingest.py`
"""

from __future__ import annotations
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from costgov.providers import build_client  # reuse the Entra-auth AzureOpenAI client

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SearchField, SearchFieldDataType, SimpleField, SearchableField,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
    SemanticConfiguration, SemanticSearch, SemanticPrioritizedFields, SemanticField,
)

EMBED_DEPLOYMENT = os.environ.get("AZURE_DEPLOYMENT_EMBEDDING", "text-embed")
EMBED_DIM = 1536  # text-embedding-3-small
INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX", "books")
DATA_DIR = os.path.join(HERE, "data")

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200


def _download(book_id: int) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"pg{book_id}.txt")
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    url = f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"
    req = urllib.request.Request(url, headers={"User-Agent": "tokengov-rag/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    open(path, "w", encoding="utf-8").write(text)
    return text


def _strip_boilerplate(text: str) -> str:
    start = re.search(r"\*\*\* START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.I)
    end = re.search(r"\*\*\* END OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.I)
    s = start.end() if start else 0
    e = end.start() if end else len(text)
    return text[s:e].strip()


def _chunk(text: str):
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + CHUNK_CHARS].strip())
        i += CHUNK_CHARS - CHUNK_OVERLAP
    return [c for c in out if len(c) > 100]


def _embed_batch(client, texts):
    resp = client.embeddings.create(model=EMBED_DEPLOYMENT, input=texts)
    return [d.embedding for d in resp.data]


def _upload_batch(search_client: SearchClient, documents: list[dict]) -> None:
    results = search_client.upload_documents(documents=documents)
    failures = [result for result in results if not result.succeeded]
    if failures:
        details = ", ".join(
            f"{result.key}: {result.error_message or 'upload failed'}"
            for result in failures[:5]
        )
        raise RuntimeError(f"Azure AI Search rejected {len(failures)} documents: {details}")


def build_index(index_client: SearchIndexClient):
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="book", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(name="content_vector",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True, vector_search_dimensions=EMBED_DIM,
                    vector_search_profile_name="vprofile"),
    ]
    vs = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="vprofile", algorithm_configuration_name="hnsw")],
    )
    semantic = SemanticSearch(configurations=[
        SemanticConfiguration(
            name="sem",
            prioritized_fields=SemanticPrioritizedFields(
                content_fields=[SemanticField(field_name="content")]))
    ])
    index = SearchIndex(name=INDEX_NAME, fields=fields, vector_search=vs, semantic_search=semantic)
    index_client.create_or_update_index(index)
    print(f"index '{INDEX_NAME}' created/updated")


def main():
    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    cred = DefaultAzureCredential()
    aoai = build_client()

    index_client = SearchIndexClient(endpoint=endpoint, credential=cred)
    build_index(index_client)

    search_client = SearchClient(endpoint=endpoint, index_name=INDEX_NAME, credential=cred)
    books = json.load(open(os.path.join(HERE, "books.json"), encoding="utf-8"))["books"]

    total = 0
    for b in books:
        raw = _download(b["id"])
        body = _strip_boilerplate(raw)
        chunks = _chunk(body)
        print(f"{b['title']}: {len(chunks)} chunks")
        # embed + upload in batches (embeddings API and search upload both like <=~100)
        BATCH = 64
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i:i + BATCH]
            vectors = _embed_batch(aoai, batch)
            docs = [{
                "id": f"{b['id']}-{i + j}",
                "book": b["title"],
                "content": batch[j],
                "content_vector": vectors[j],
            } for j in range(len(batch))]
            _upload_batch(search_client, docs)
            total += len(docs)
        print(f"  uploaded (running total {total})")
    print(f"DONE: {total} chunks indexed into '{INDEX_NAME}'")


if __name__ == "__main__":
    main()
