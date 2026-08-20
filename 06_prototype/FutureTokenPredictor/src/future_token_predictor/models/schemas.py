"""Core data models for the Future Token Predictor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# --- Enums ---


class Provider(str, Enum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    MISTRAL = "mistral"
    COHERE = "cohere"
    BEDROCK = "bedrock"
    LOCAL = "local"


class AgentPattern(str, Enum):
    SINGLE_CALL = "single_call"
    TOOL_AGENT = "tool_agent"
    MULTI_AGENT = "multi_agent"
    WORKFLOW = "workflow"
    RAG_PIPELINE = "rag_pipeline"
    CODE_EXEC = "code_exec"

    @classmethod
    def _missing_(cls, value):
        if value == "react_agent":
            return cls.TOOL_AGENT
        return None


class Framework(str, Enum):
    MAF = "maf"
    LANGCHAIN = "langchain"
    CREWAI = "crewai"
    AUTOGEN = "autogen"
    CUSTOM = "custom"


class AgentType(str, Enum):
    """Deployment topology of the AI agent.

    PROMPT: single inference call (prompt → response).
    WORKFLOW: multi-step chain or DAG with deterministic routing.
    HOSTED: autonomous loop (ReAct / multi-agent) that decides its own steps.
    """
    PROMPT = "prompt"
    WORKFLOW = "workflow"
    HOSTED = "hosted"


class Modality(str, Enum):
    TEXT = "text"
    IMAGE_INPUT = "image_input"
    IMAGE_OUTPUT = "image_output"
    DOCUMENT = "document"
    AUDIO_INPUT = "audio_input"
    AUDIO_OUTPUT = "audio_output"


class DeploymentRegion(str, Enum):
    """Azure-specific deployment region (ignored by non-Azure providers)."""
    GLOBAL = "global"
    DATA_ZONE = "data_zone"
    REGIONAL = "regional"


class DeploymentType(str, Enum):
    """Billing tier / deployment mode."""
    STANDARD = "standard"
    BATCH = "batch"
    PROVISIONED = "provisioned"


class Tool(str, Enum):
    FILE_SEARCH = "file_search"
    CODE_INTERPRETER = "code_interpreter"
    WEB_SEARCH = "web_search"
    MCP_SERVER = "mcp_server"
    CUSTOM_FUNCTION = "custom_function"
    FUNCTION_CALLING = "function_calling"
    RAG = "rag"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DetailLevel(str, Enum):
    LOW = "low"
    HIGH = "high"


class RetrievalStrategy(str, Enum):
    DIRECT = "direct"
    FILE_SEARCH = "file_search"
    RAG = "rag"


# --- Input Profile ---


@dataclass
class ImageInputProfile:
    """Describes image inputs for a use case."""

    count_per_call: int = 1
    avg_width: int = 1024
    avg_height: int = 1024
    detail_level: DetailLevel = DetailLevel.HIGH


@dataclass
class DocumentInputProfile:
    """Describes document inputs for a use case."""

    count: int = 1
    avg_pages: int = 5
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.FILE_SEARCH
    top_k: int = 5  # Number of chunks retrieved per query (File Search)


@dataclass
class AudioInputProfile:
    """Describes audio inputs for a use case."""

    avg_duration_seconds: float = 30.0


@dataclass
class AgentModelAssignment:
    """Assigns one model to one logical agent in a multi-agent workload."""

    agent_id: str
    provider: Provider
    model: str
    role: str = ""
    turn_weight: float = 1.0


@dataclass
class AnalysisEvidence:
    """One deterministic evidence span supporting an inferred field."""

    rule: str
    text: str


@dataclass
class TopologyAnalysis:
    """Selected topology plus uncertainty and supporting evidence."""

    selected: AgentPattern
    confidence: str
    alternatives: list[AgentPattern] = field(default_factory=list)
    evidence: list[AnalysisEvidence] = field(default_factory=list)


@dataclass
class AgentCountAnalysis:
    """Agent-count value and provenance."""

    value: int
    source: str
    evidence: list[AnalysisEvidence] = field(default_factory=list)


@dataclass
class QuantityAnalysis:
    """One cost-material quantity plus its provenance."""

    value: float
    source: str
    evidence: list[AnalysisEvidence] = field(default_factory=list)


@dataclass
class WorkloadAnalysis:
    """Versioned deterministic analysis consumed by workload prediction."""

    schema_version: str
    rule_set_version: str
    description_hash: str
    topology: TopologyAnalysis
    agent_count: AgentCountAnalysis
    modalities: list[Modality] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    modality_evidence: dict[str, list[AnalysisEvidence]] = field(default_factory=dict)
    tool_evidence: dict[str, list[AnalysisEvidence]] = field(default_factory=dict)
    quantities: dict[str, QuantityAnalysis] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)


@dataclass
class UseCaseProfile:
    """Complete profile of an agentic use case for token prediction."""

    # Provider & model
    provider: Provider = Provider.OPENAI
    model: str = "gpt-4.1"
    framework: Framework = Framework.CUSTOM

    # Agent pattern (preferred) and legacy agent_type (backward compat)
    agent_pattern: AgentPattern = AgentPattern.SINGLE_CALL
    agent_type: AgentType = AgentType.PROMPT
    deployment_type: DeploymentType = DeploymentType.STANDARD
    deployment_region: DeploymentRegion = DeploymentRegion.GLOBAL
    complexity: Complexity = Complexity.MEDIUM

    # Modalities
    modalities: list[Modality] = field(default_factory=lambda: [Modality.TEXT])
    image_inputs: Optional[ImageInputProfile] = None
    document_inputs: Optional[DocumentInputProfile] = None
    audio_inputs: Optional[AudioInputProfile] = None
    searches_per_call: Optional[int] = None

    # Tools
    tools: list[Tool] = field(default_factory=list)

    # Workflow characteristics
    expected_turns: int = 1
    workflow_steps: Optional[int] = None
    multi_agent_count: int = 1
    agent_models: list[AgentModelAssignment] = field(default_factory=list)
    has_reasoning_tokens: bool = False
    thinking_budget: Optional[int] = None  # Anthropic extended thinking token budget

    # Scale
    users: int = 1
    calls_per_user_per_day: int = 1

    # Optional overrides
    system_prompt_tokens: Optional[int] = None
    avg_user_input_tokens: Optional[int] = None

    # Capability flags (cost levers)
    uses_prompt_caching: bool = False
    uses_batch_api: bool = False
    uses_streaming: bool = False
    uses_retrieval: bool = False

    def __post_init__(self) -> None:
        """Sync agent_type and agent_pattern for backward compatibility."""
        _TYPE_TO_PATTERN = {
            AgentType.PROMPT: AgentPattern.SINGLE_CALL,
            AgentType.WORKFLOW: AgentPattern.WORKFLOW,
            AgentType.HOSTED: AgentPattern.TOOL_AGENT,
        }
        _PATTERN_TO_TYPE = {
            AgentPattern.SINGLE_CALL: AgentType.PROMPT,
            AgentPattern.RAG_PIPELINE: AgentType.PROMPT,
            AgentPattern.CODE_EXEC: AgentType.PROMPT,
            AgentPattern.TOOL_AGENT: AgentType.HOSTED,
            AgentPattern.MULTI_AGENT: AgentType.HOSTED,
            AgentPattern.WORKFLOW: AgentType.WORKFLOW,
        }
        # If legacy agent_type was set to non-default but agent_pattern is default, infer
        if self.agent_type != AgentType.PROMPT and self.agent_pattern == AgentPattern.SINGLE_CALL:
            self.agent_pattern = _TYPE_TO_PATTERN.get(self.agent_type, AgentPattern.SINGLE_CALL)
        # If agent_pattern was set to non-default but agent_type is default, infer
        elif self.agent_pattern != AgentPattern.SINGLE_CALL and self.agent_type == AgentType.PROMPT:
            self.agent_type = _PATTERN_TO_TYPE.get(self.agent_pattern, AgentType.PROMPT)

        # Auto-detect pattern from tools: function_calling/custom_function/mcp_server
        # implies agentic tool-loop, not a single prompt call
        _AGENTIC_TOOLS = {Tool.FUNCTION_CALLING, Tool.CUSTOM_FUNCTION, Tool.MCP_SERVER}
        if self.agent_pattern == AgentPattern.SINGLE_CALL and self.tools and _AGENTIC_TOOLS & set(self.tools):
            self.agent_pattern = AgentPattern.TOOL_AGENT
            self.agent_type = AgentType.HOSTED


# --- Output Models ---


@dataclass
class ModalityBreakdown:
    """Token counts broken down by modality."""

    text_input: float = 0.0
    text_output: float = 0.0
    cached_input: float = 0.0
    image_input: float = 0.0
    image_output: float = 0.0
    document_input: float = 0.0
    audio_input: float = 0.0
    audio_output: float = 0.0
    reasoning: float = 0.0

    @property
    def total_input(self) -> float:
        return (
            self.text_input
            + self.cached_input
            + self.image_input
            + self.document_input
            + self.audio_input
        )

    @property
    def total_output(self) -> float:
        return self.text_output + self.image_output + self.audio_output + self.reasoning

    @property
    def total(self) -> float:
        return self.total_input + self.total_output


@dataclass
class ToolCostBreakdown:
    """Non-token costs from Foundry tools."""

    file_search_calls: int = 0
    file_search_cost_usd: float = 0.0
    code_interpreter_sessions: int = 0
    code_interpreter_cost_usd: float = 0.0
    web_search_calls: int = 0
    web_search_cost_usd: float = 0.0
    storage_gb: float = 0.0
    storage_cost_usd_per_day: float = 0.0
    unpriced_external_tools: list[dict[str, str]] = field(default_factory=list)

    @property
    def total_usd(self) -> float:
        """Return per-invocation tool charges; daily storage is separate."""
        return (
            self.file_search_cost_usd
            + self.code_interpreter_cost_usd
            + self.web_search_cost_usd
        )


@dataclass
class CostEstimate:
    """Cost estimate with confidence intervals."""

    mean: float = 0.0
    ci_95_low: float = 0.0
    ci_95_high: float = 0.0
    worst_case: float = 0.0  # 99th percentile


@dataclass
class PredictionResult:
    """Complete prediction output."""

    # Per-invocation
    tokens_per_call: ModalityBreakdown = field(default_factory=ModalityBreakdown)
    tokens_p5: float = 0.0   # 5th percentile total tokens (low estimate)
    tokens_p50: float = 0.0  # median total tokens
    tokens_p95: float = 0.0  # 95th percentile total tokens (high estimate)
    cost_per_call: CostEstimate = field(default_factory=CostEstimate)
    tool_costs_per_call: ToolCostBreakdown = field(default_factory=ToolCostBreakdown)

    # Scaled projections
    daily_tokens: float = 0.0
    daily_cost_usd: CostEstimate = field(default_factory=CostEstimate)
    monthly_cost_usd: CostEstimate = field(default_factory=CostEstimate)
    annual_cost_usd: CostEstimate = field(default_factory=CostEstimate)

    # Metadata
    model: str = ""
    provider: str = ""
    archetype: str = ""
    prediction_method: str = "tier1_heuristic"
    pricing_verified: bool = False
    pricing_timestamp: Optional[str] = None
    optimizations: list[str] = field(default_factory=list)
    model_warnings: list[str] = field(default_factory=list)
    requested_model: Optional[str] = None  # Original model before validation/substitution
    pricing_url: str = ""  # URL to provider's pricing page
    model_catalog_url: str = ""  # URL to provider's model catalog/docs
    missing_parameters: list[str] = field(default_factory=list)  # Params that were defaulted, not explicitly set
    prediction_id: Optional[int] = None  # History row used to record actual usage
    bound_method: str = "heuristic_multiplier"
    bound_samples: int = 1
    bound_seed: Optional[int] = None
    agent_model_assignments: list[dict] = field(default_factory=list)
    calculation_trace: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ActualRecordingResult:
    """Outcome of recording actual usage for a prediction."""

    prediction_id: int
    status: str  # updated | already_recorded | not_found
