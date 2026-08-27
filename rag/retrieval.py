"""
rag/retrieval.py — the RAG retriever (data-plane context management).

Given a question, embed it, run a HYBRID query (keyword + vector) against Azure AI Search
with the semantic reranker, and return the top-k passages as a single grounding block.
This is the real "context management" stage that sits in front of the gateway in the
reference architecture (file 06) — it replaces the synthetic _pad_context in providers.py.

Auth: Entra ID (DefaultAzureCredential). Needs 'Search Index Data Reader' on the service.
"""

from __future__ import annotations
import os

_INDEX = os.environ.get("AZURE_SEARCH_INDEX", "books")
_EMBED = os.environ.get("AZURE_DEPLOYMENT_EMBEDDING", "text-embed")


class Retriever:
    def __init__(self, aoai_client, top_k: int = 4):
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient
        endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        self.aoai = aoai_client
        self.top_k = top_k
        self.client = SearchClient(endpoint=endpoint, index_name=_INDEX,
                                   credential=DefaultAzureCredential())

    def _embed(self, text: str):
        return self.aoai.embeddings.create(model=_EMBED, input=[text]).data[0].embedding

    def passages(self, question: str):
        """Return a list of (book, content) for the top-k retrieved chunks."""
        from azure.search.documents.models import VectorizedQuery
        vq = VectorizedQuery(vector=self._embed(question), k_nearest_neighbors=self.top_k,
                             fields="content_vector")
        results = self.client.search(
            search_text=question,                 # keyword arm of the hybrid query
            vector_queries=[vq],                  # vector arm
            query_type="semantic",                # semantic reranker (Basic + semantic std)
            semantic_configuration_name="sem",
            top=self.top_k,
            select=["book", "content"],
        )
        return [(r["book"], r["content"]) for r in results]

    def context(self, question: str) -> str:
        """Concatenate retrieved passages into one grounding block for the model prompt."""
        chunks = self.passages(question)
        if not chunks:
            return ""
        blocks = [f"[Source: {book}]\n{content}" for book, content in chunks]
        return "Use ONLY the following retrieved passages to answer.\n\n" + "\n\n---\n\n".join(blocks)
