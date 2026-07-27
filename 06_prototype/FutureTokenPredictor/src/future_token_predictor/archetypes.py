"""Archetypes module — loads MAF workflow archetype profiles from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from future_token_predictor.models.schemas import (
    AgentPattern,
    AgentType,
    Complexity,
    Modality,
    Tool,
)

_ARCHETYPE_FILE = Path(__file__).parent.parent.parent / "data" / "archetype_profiles.yaml"
_archetypes: dict[str, dict[str, Any]] | None = None


def _load_archetypes() -> dict[str, dict[str, Any]]:
    global _archetypes
    if _archetypes is None:
        with open(_ARCHETYPE_FILE) as f:
            data = yaml.safe_load(f)
        _archetypes = data.get("archetypes", {})
    return _archetypes


def get_archetype(name: str) -> dict[str, Any] | None:
    """Get a single archetype definition by name."""
    return _load_archetypes().get(name)


def get_archetype_names() -> list[str]:
    """List all available archetype names."""
    return list(_load_archetypes().keys())


def get_token_profile(name: str, complexity: Complexity) -> dict[str, Any]:
    """Get the token profile for an archetype at a given complexity level."""
    archetype = get_archetype(name)
    if not archetype:
        raise ValueError(f"Unknown archetype: {name}")
    profiles = archetype.get("token_profiles", {})
    profile = profiles.get(complexity.value)
    if not profile:
        raise ValueError(f"No {complexity.value} profile for archetype {name}")
    return profile


def match_archetype(
    agent_type: AgentType | None = None,
    modalities: list[Modality] | None = None,
    tools: list[Tool] | None = None,
    agent_pattern: AgentPattern | None = None,
) -> str:
    """Match input parameters to the best-fit archetype name.

    Accepts either agent_pattern (preferred) or agent_type (backward compat).
    """
    archetypes = _load_archetypes()
    modalities = modalities or []
    tools = tools or []

    # Resolve pattern string for matching
    pattern_value = None
    if agent_pattern is not None:
        pattern_value = agent_pattern.value
    elif agent_type is not None:
        # Map old AgentType to AgentPattern for matching
        _type_to_pattern = {
            "prompt": "single_call",
            "workflow": "workflow",
            "hosted": "react_agent",
        }
        pattern_value = _type_to_pattern.get(agent_type.value, agent_type.value)

    best_name = "SingleCall_TextOnly"
    best_score = -1

    for name, arch in archetypes.items():
        score = 0

        # Agent pattern match
        if pattern_value and arch.get("agent_pattern") == pattern_value:
            score += 10

        # Modality overlap
        arch_modalities = set(arch.get("modalities", []))
        input_modalities = {m.value for m in modalities}
        overlap = arch_modalities & input_modalities
        score += len(overlap) * 5

        # Penalize extra modalities in archetype not in input
        extra = arch_modalities - input_modalities
        score -= len(extra) * 2

        # Tool overlap
        arch_tools = set(arch.get("tools", []))
        input_tools = {t.value for t in tools}
        tool_overlap = arch_tools & input_tools
        score += len(tool_overlap) * 3

        if score > best_score:
            best_score = score
            best_name = name

    return best_name
