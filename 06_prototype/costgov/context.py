"""
Context management (data plane).

Attacks the "context snowball" from file 02 — the #1 agentic cost driver. Here we
simulate a running agent whose context accumulates prior turns; pruning caps how
many items get re-sent, which lowers input tokens (and, per the Chroma 'context rot'
finding, often *improves* quality by dropping irrelevant history).
"""

from __future__ import annotations


def build_context_tokens(history_len: int, prune: bool, max_items: int) -> int:
    """Return simulated input-context token count for this turn.

    Without pruning, every accumulated history item (each ~120 tokens) is re-sent
    -> super-linear cost as the loop grows. Pruning caps it to max_items.
    """
    items = min(history_len, max_items) if prune else history_len
    per_item_tokens = 120
    base_system_prompt = 200  # stable prefix -> eligible for native prompt caching
    return base_system_prompt + items * per_item_tokens
