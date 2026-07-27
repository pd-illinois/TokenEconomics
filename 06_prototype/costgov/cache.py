"""
Semantic cache (data plane).

Stdlib-only stand-in for an embedding + vector store (e.g. APIM llm-semantic-cache
on Redis, or GPTCache). Similarity = cosine over bag-of-words. A real system swaps
_embed()/_similarity() for an embeddings model + ANN index; the gateway logic is identical.

The score_threshold knob is the SAME quality/hit-rate lever discussed in file 05:
higher threshold = stricter match = fewer wrong-answer hits but a lower hit rate.
"""

from __future__ import annotations
import math
import re
from collections import Counter
from dataclasses import dataclass, field


_WORD = re.compile(r"[a-z0-9]+")


def _embed(text: str) -> Counter:
    return Counter(_WORD.findall(text.lower()))


def _cosine(a, b) -> float:
    # local bag-of-words path (Counter) — real-embedding path uses _vec_cosine below
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _vec_cosine(a, b) -> float:
    # dense-vector path for real embeddings (list[float])
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class SemanticCache:
    """Semantic cache. Default: local bag-of-words cosine (zero-dep).
    Live: pass embed_fn (real embeddings deployment) -> dense-vector cosine.
    Same score_threshold knob either way (mirrors APIM llm-semantic-cache)."""
    threshold: float
    embed_fn: object = None                      # text -> vector; None => local bag-of-words
    _store: list = field(default_factory=list)   # list[(embedding, question, answer)]
    hits: int = 0
    misses: int = 0

    def _embed(self, text: str):
        return self.embed_fn(text) if self.embed_fn else _embed(text)

    def _sim(self, a, b) -> float:
        return _vec_cosine(a, b) if self.embed_fn else _cosine(a, b)

    def lookup(self, question: str):
        qe = self._embed(question)
        best, best_sim = None, 0.0
        for emb, q, ans in self._store:
            sim = self._sim(qe, emb)
            if sim > best_sim:
                best, best_sim = ans, sim
        if best is not None and best_sim >= self.threshold:
            self.hits += 1
            return best, best_sim
        self.misses += 1
        return None, best_sim

    def store(self, question: str, answer) -> None:
        self._store.append((self._embed(question), question, answer))

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

