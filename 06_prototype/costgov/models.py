"""
Model layer (data plane).

Simulated, zero-dependency models so the prototype runs offline anywhere.
Each model has a per-1k-token price, a latency, and a *competence* profile
(how well it answers easy vs hard questions). This lets the whole closed loop
run deterministically without API keys.

>>> PLUGGING IN A REAL PROVIDER <<<
Replace SimulatedModel.generate() with a call to a real endpoint, e.g.:
  - Azure AI Foundry Model Router deployment (one deployment auto-picks the model)
  - Anthropic:  client.messages.create(model="claude-haiku-4-5" | "claude-opus-4-8", ...)
  - Azure OpenAI: client.chat.completions.create(...)
Keep the same return shape (Answer) and the rest of the architecture is unchanged.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Answer:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    model: str


# Canonical "correct" answers the simulated models draw from. A model's
# competence decides how complete an answer it returns for a given difficulty.
_KNOWLEDGE = {
    "what are your opening hours":
        "We are open Monday to Friday, 9 to 5.",
    "how do i reset my password":
        "Go to Settings > Security > Reset password and follow the email link.",
    "where is my order":
        "Track it under My Orders; a tracking link was emailed to you.",
    "i was double charged and need a refund to a closed card":
        "We refund the duplicate charge to the original card; if that card is closed "
        "your bank routes it to the replacement account, typically within 5 days.",
    "explain how your data retention policy affects gdpr deletion requests":
        "On a GDPR deletion request we delete active data immediately and purge backups "
        "within 30 days, honoring the request while backups age out.",
}


def _match_knowledge(question: str) -> str:
    q = question.lower()
    best, best_overlap = "", 0
    for key, ans in _KNOWLEDGE.items():
        overlap = len(set(q.split()) & set(key.split()))
        if overlap > best_overlap:
            best, best_overlap = ans, overlap
    return best or "I'm not sure, let me connect you to an agent."


@dataclass
class SimulatedModel:
    name: str
    price_per_1k_input: float
    price_per_1k_output: float
    latency_ms: float
    # competence in [0,1] per difficulty: fraction of the correct answer it reliably returns
    competence_easy: float
    competence_hard: float

    def generate(self, question: str, context_tokens: int, difficulty: str) -> Answer:
        full = _match_knowledge(question)
        competence = self.competence_easy if difficulty == "easy" else self.competence_hard
        # A weaker model on a hard question drops trailing detail -> lower eval score later.
        words = full.split()
        keep = max(1, int(round(len(words) * competence)))
        text = " ".join(words[:keep])

        input_tokens = context_tokens + max(1, len(question.split()))
        output_tokens = max(1, len(text.split()))
        cost = (input_tokens / 1000.0) * self.price_per_1k_input \
             + (output_tokens / 1000.0) * self.price_per_1k_output
        return Answer(text, input_tokens, output_tokens, round(cost, 6),
                      self.latency_ms, self.name)


# Two tiers, priced ~10x apart (mirrors the ~order-of-magnitude spread in file 01).
# Premium is competent on everything; cheap is great on easy, weak on hard.
PREMIUM = SimulatedModel(
    name="premium",
    price_per_1k_input=0.015, price_per_1k_output=0.075,
    latency_ms=900, competence_easy=1.0, competence_hard=1.0,
)
CHEAP = SimulatedModel(
    name="cheap",
    price_per_1k_input=0.0008, price_per_1k_output=0.004,
    latency_ms=350, competence_easy=1.0, competence_hard=0.45,
)

MODELS = {"premium": PREMIUM, "cheap": CHEAP}
