"""Workflow predictor — Monte Carlo simulation for multi-step agent workflows.

Handles iteration uncertainty in ReAct loops, workflow steps, and multi-agent
communication rounds. Produces confidence intervals.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from future_token_predictor.archetypes import get_token_profile, match_archetype
from future_token_predictor.models.schemas import (
    AgentType,
    ModalityBreakdown,
    UseCaseProfile,
)
from future_token_predictor.single_call_predictor import predict_single_call


@dataclass
class WorkflowPrediction:
    """Result of Monte Carlo workflow simulation."""

    mean_tokens: ModalityBreakdown
    p5_total: float
    p50_total: float
    p95_total: float
    p99_total: float
    samples: int


def predict_workflow(
    profile: UseCaseProfile,
    n_samples: int = 1000,
    seed: int | None = 42,
) -> WorkflowPrediction:
    """Run Monte Carlo simulation over a workflow's iteration uncertainty.

    For prompt agents: returns single-call prediction (no simulation needed).
    For workflow/hosted agents: simulates variable step counts and context growth.
    """
    # Simple prompt agents don't need Monte Carlo
    if profile.agent_type == AgentType.PROMPT and profile.multi_agent_count <= 1:
        single = predict_single_call(profile)
        return WorkflowPrediction(
            mean_tokens=single,
            p5_total=single.total * 0.7,
            p50_total=single.total,
            p95_total=single.total * 1.5,
            p99_total=single.total * 2.0,
            samples=1,
        )

    # Get archetype profile for simulation parameters
    archetype = match_archetype(
        profile.agent_type,
        profile.modalities,
        profile.tools,
        profile.agent_pattern,
    )
    token_profile = get_token_profile(archetype, profile.complexity)

    rng = np.random.default_rng(seed)
    totals = np.zeros(n_samples)

    # Per-call base tokens
    single_call = predict_single_call(profile)
    base_input = single_call.text_input
    base_output = single_call.text_output

    for i in range(n_samples):
        total = 0.0

        if profile.agent_type == AgentType.WORKFLOW:
            # Workflow: variable number of steps
            steps_mean = profile.workflow_steps or token_profile.get("steps_mean", 5)
            steps_std = 0 if profile.workflow_steps is not None else token_profile.get("steps_std", 2)
            steps = max(1, int(rng.normal(steps_mean, steps_std)))

            for step in range(steps):
                # Each step has input + output + possible tool overhead
                step_input = base_input + step * 100  # Context grows per step
                step_output = base_output * rng.uniform(0.5, 1.5)
                total += step_input + step_output

        elif profile.agent_type == AgentType.HOSTED:
            if profile.multi_agent_count > 1:
                # Multi-agent simulation
                agents = profile.multi_agent_count
                turns_per_agent = token_profile.get("turns_per_agent", 4)
                overhead = token_profile.get("context_sharing_overhead", 0.4)

                for agent_idx in range(agents):
                    turns = max(1, int(rng.normal(turns_per_agent, turns_per_agent * 0.3)))
                    for turn in range(turns):
                        turn_input = base_input + turn * token_profile.get("context_growth_per_iteration", 500)
                        turn_output = base_output * rng.uniform(0.6, 1.4)
                        total += turn_input + turn_output
                # Context sharing overhead
                total *= (1 + overhead)
            else:
                # ReAct loop simulation
                iter_mean = token_profile.get("iterations_mean", 6)
                iter_std = token_profile.get("iterations_std", 2)
                context_growth = token_profile.get("context_growth_per_iteration", 500)
                iterations = max(1, int(rng.normal(iter_mean, iter_std)))

                for it in range(iterations):
                    iter_input = base_input + it * context_growth
                    iter_output = base_output * rng.uniform(0.5, 1.5)
                    total += iter_input + iter_output

        # Add non-text modality tokens (these are per-workflow, not per-iteration)
        total += single_call.image_input
        total += single_call.image_output
        total += single_call.document_input
        total += single_call.audio_input
        total += single_call.audio_output
        total += single_call.reasoning

        totals[i] = total

    # Build a mean breakdown whose components sum to the simulated mean.
    # Text input/output repeat across workflow steps, while the simulation adds
    # non-text modalities and reasoning once per complete workflow.
    mean_total = float(np.mean(totals))
    repeated_text_total = single_call.text_input + single_call.text_output
    fixed_total = (
        single_call.cached_input
        + single_call.image_input
        + single_call.image_output
        + single_call.document_input
        + single_call.audio_input
        + single_call.audio_output
        + single_call.reasoning
    )
    if repeated_text_total > 0:
        scale_factor = max(0.0, mean_total - fixed_total) / repeated_text_total
    else:
        scale_factor = 1.0

    mean_breakdown = ModalityBreakdown(
        text_input=single_call.text_input * scale_factor,
        text_output=single_call.text_output * scale_factor,
        cached_input=single_call.cached_input,
        image_input=single_call.image_input,
        image_output=single_call.image_output,
        document_input=single_call.document_input,
        audio_input=single_call.audio_input,
        audio_output=single_call.audio_output,
        reasoning=single_call.reasoning,
    )

    return WorkflowPrediction(
        mean_tokens=mean_breakdown,
        p5_total=float(np.percentile(totals, 5)),
        p50_total=float(np.percentile(totals, 50)),
        p95_total=float(np.percentile(totals, 95)),
        p99_total=float(np.percentile(totals, 99)),
        samples=n_samples,
    )
