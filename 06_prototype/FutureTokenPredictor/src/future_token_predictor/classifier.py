"""Use case classifier — maps natural language descriptions to UseCaseProfile.

Classification strategy (ordered):
1. **LLM-based**: If an API key is available (CLASSIFIER_API_KEY,
   AZURE_OPENAI_API_KEY, or OPENAI_API_KEY), calls a cheap model to extract
   model name, provider, agent type, modalities, and complexity.
2. **Regex fallback**: When no API key is set or the LLM call fails, uses
   keyword/pattern-based classification (the original approach).

Modality, tool, and scale detection still use regex patterns in both paths
because the LLM is only needed for the ambiguous model/provider extraction.
"""

from __future__ import annotations

import logging
import hashlib
import re

from future_token_predictor.models.schemas import (
    AgentPattern,
    AgentCountAnalysis,
    AgentType,
    AnalysisEvidence,
    AudioInputProfile,
    Complexity,
    DetailLevel,
    DocumentInputProfile,
    ImageInputProfile,
    Modality,
    Provider,
    QuantityAnalysis,
    RetrievalStrategy,
    Tool,
    TopologyAnalysis,
    UseCaseProfile,
    WorkloadAnalysis,
)

logger = logging.getLogger(__name__)

ANALYSIS_SCHEMA_VERSION = "1.0"
RULE_SET_VERSION = "enterprise-semantics-2026-07-28.1"

# --- Pattern Definitions ---

_VISION_PATTERNS = re.compile(
    r"image|photo|picture|screenshot|visual|vision|scan|diagram|chart|upload.*image|"
    r"analyze.*image|look at|see the|camera|ocr|multimodal|any format",
    re.IGNORECASE,
)

_IMAGE_GEN_PATTERNS = re.compile(
    r"generate.*image|create.*image|draw|illustration|art|dalle|dall-e|"
    r"gpt-image|image generation|create.*picture|design.*visual",
    re.IGNORECASE,
)

_DOCUMENT_PATTERNS = re.compile(
    r"document|pdf|docx|file.*search|rag|retrieval|knowledge base|"
    r"uploaded.*file|analyze.*document|read.*pdf|extract.*from.*document|"
    r"search.*files|vector.*store|invoice|purchase order|contract|redline|"
    r"clinical documentation|medical observations|\brecords?\b|\breports?\b|"
    r"meeting notes|emails?|tickets?|requirements|deployment packages|"
    r"applications|audit evidence|treatment plans|briefings|timesheets|"
    r"campaign materials|internal (?:and|or) external sources|share context|"
    r"case details|transaction history|financial tracking|reporting agents",
    re.IGNORECASE,
)

_AUDIO_INPUT_PATTERNS = re.compile(
    r"\baudio\b|\bvoice\b|\bspeech\b|\btranscri|\blisten\b|\brealtime\b|\bwhisper\b|"
    r"speech.to.text|conversation.*voice|physician.patient conversation|"
    r"(?:phone|voice|video)\s*call|\bcall\s*center",
    re.IGNORECASE,
)

_AUDIO_OUTPUT_PATTERNS = re.compile(
    r"\bspeak\b|text.to.speech|spoken (?:answer|response)|voice assistant",
    re.IGNORECASE,
)

_CODE_INTERPRETER_PATTERNS = re.compile(
    r"code interpreter|execute.*code|run.*python|data analysis|"
    r"calculate|compute|plot|chart.*data|csv|excel|spreadsheet|"
    r"jupyter|notebook|sandbox|generates? code fixes|coding agents|"
    r"runs? tests|execute tests",
    re.IGNORECASE,
)

_WEB_SEARCH_PATTERNS = re.compile(
    r"web.*search|browse|internet|bing|google|current.*information|"
    r"latest.*news|real.time.*data|up.to.date|grounding|public data sources|"
    r"external sources|threat feeds|news updates|researches?.*public",
    re.IGNORECASE,
)

_FILE_SEARCH_PATTERNS = re.compile(
    r"file.*search|rag|retrieval|knowledge|vector.*store|"
    r"search.*document|find.*in.*files|look.*up.*in|internal sources|"
    r"internal and external sources|"
    r"organizational policies|product documentation",
    re.IGNORECASE,
)

_ENTERPRISE_ACTION_PATTERNS = re.compile(
    r"\berp\b|\bcrm\b|inventory systems?|refunds?|shipment updates?|"
    r"ci/cd|production telemetry|pull requests?|enterprise data sources?|"
    r"security alerts?|multiple tools|predefined response actions?|"
    r"calendars?|development activity|transactions? automatically|"
    r"deployment packages?|billing agents?|alternative suppliers?|"
    r"transportation routes?|fulfillment schedules?|isolate affected systems?|"
    r"downstream systems?|cloud environments?|corrective actions?|"
    r"isolate compromised assets?|block malicious activity|reroute workloads?|"
    r"remediate violations?|reroute traffic|order replacement parts?|"
    r"adjust procurement|shut down unused resources?|right.size infrastructure|"
    r"adjust machine settings?|suspend suspicious activities?|"
    r"customer verification|coordinating actions across systems|"
    r"resource allocation|appointments?|staffing adjustments|"
    r"manage launch readiness|credit evaluations?|check regulations|"
    r"lending recommendations?|execute actions|optimize enterprise.wide|"
    r"coordinate large.scale transformation programs",
    re.IGNORECASE,
)

_WORKFLOW_PATTERNS = re.compile(
    r"workflow|multi.step|pipeline|chain|sequence.*of|orchestrat|"
    r"step.*1.*step.*2|yaml|branching|conditional",
    re.IGNORECASE,
)

_HOSTED_PATTERNS = re.compile(
    r"hosted|container|react.*loop|iterative|autonomous|"
    r"agent.*loop|multi.agent|a2a|group.*chat|collaborate|"
    r"computer.*use|gui.*automat|browser.*automat",
    re.IGNORECASE,
)

_REASONING_MODELS = re.compile(
    r"o3|o4-mini|o1|reasoning|deepseek.r1|thinking|extended.thinking",
    re.IGNORECASE,
)

_COMPLEXITY_HIGH_PATTERNS = re.compile(
    r"complex|advanced|multi.step|enterprise|production|large.scale|"
    r"multi.agent|autonomous|reasoning|research|deep.*analysis",
    re.IGNORECASE,
)

_COMPLEXITY_LOW_PATTERNS = re.compile(
    r"simple|basic|quick|single|straightforward|hello.*world|"
    r"test|demo|prototype|trivial",
    re.IGNORECASE,
)

# Capability cost-lever patterns
_PROMPT_CACHING_PATTERNS = re.compile(
    r"prompt cach|cached prompt|context cach|reuse.*system prompt|cache.*prompt",
    re.IGNORECASE,
)

_BATCH_API_PATTERNS = re.compile(
    r"batch api|batch mode|offline batch|async batch|batch.*processing|nightly.*batch|"
    r"via.*batch|in.*batch",
    re.IGNORECASE,
)

_STREAMING_PATTERNS = re.compile(
    r"streaming|stream.*tokens?|stream.*response|streamed",
    re.IGNORECASE,
)

_RETRIEVAL_PATTERNS = re.compile(
    r"\brag\b|retrieval|knowledge base|vector\s*(?:store|db|database)|semantic search",
    re.IGNORECASE,
)

_ROLE_AGENT_PATTERN = re.compile(
    r"(?=(?:^|[:,]|\band\b)\s*([a-z][a-z\-/ ]{1,30}?)\s+agents?\b)",
    re.IGNORECASE,
)

_GENERIC_ROLE_PREFIXES = {
    "an", "a", "the", "autonomous", "specialized", "llm powered",
    "llm-powered", "operations", "financial operations",
    "network", "data", "manufacturing", "fraud detection",
}

_AUTONOMOUS_LOOP_PATTERNS = re.compile(
    r"\bautonomous\b|\bcontinuously\b|\bwithout human intervention\b|"
    r"\bautomatically remediate\b|\brestore\b.*\bautomatically\b|"
    r"\bdynamically adjust\b|\bcontinuously adapt\b|\bself.heal",
    re.IGNORECASE,
)

_BOUNDED_WORKFLOW_PATTERNS = re.compile(
    r"\bingest\w*\b.*\bextract\w*\b.*\bvalidat\w*\b|"
    r"\blisten\w*\b.*\bgenerat\w*\b.*\bvalidat\w*\b|"
    r"\banaly[sz]\w*\b.*\bhighlight\w*\b.*\bgenerat\w*\b|"
    r"\breview\w*\b.*\bdraft\w*\b.*\bapprov\w*\b",
    re.IGNORECASE | re.DOTALL,
)

_SINGLE_AGENT_ACTION_PATTERNS = re.compile(
    r"\bexecutes?\b|\bresolves?\b|\benrich\w*\b|\brecommend\w*\b|"
    r"\btriage\b|\bsubmits?\b|\bcreates?\s+transactions?\b|"
    r"\bgathers?\b.*\b(?:create|synthesi[sz])\w*\b",
    re.IGNORECASE,
)


def _evidence(rule: str, match: re.Match[str] | None) -> AnalysisEvidence:
    return AnalysisEvidence(rule=rule, text=match.group(0).strip() if match else rule)


def _infer_role_agents(description: str) -> tuple[list[str], list[AnalysisEvidence]]:
    """Return distinct named role groups, excluding generic one-actor wording."""
    roles: list[str] = []
    evidence: list[AnalysisEvidence] = []
    for match in _ROLE_AGENT_PATTERN.finditer(description):
        role = re.sub(r"\s+", " ", match.group(1).lower()).strip(" :-")
        role = re.sub(r"^and\s+", "", role)
        if role in _GENERIC_ROLE_PREFIXES or len(role.split()) > 3:
            continue
        if role not in roles:
            roles.append(role)
            evidence.append(AnalysisEvidence("named_agent_role", f"{role} agents"))

    # A shared trailing "agents" can qualify every role in a comma list:
    # "planning, execution, risk, and reporting agents coordinate ...".
    shared_suffix = re.search(
        r":\s*([a-z][a-z ,\-/]{4,120}?)\s+agents?\s+(?:coordinate|collaborate)",
        description,
        re.IGNORECASE,
    )
    if shared_suffix:
        for raw_role in re.split(r",|\band\b", shared_suffix.group(1)):
            role = raw_role.strip(" :-").lower()
            if role and role not in roles:
                roles.append(role)
                evidence.append(AnalysisEvidence("shared_agent_role_suffix", role))

    # Domain lists following "agents across" imply one specialist role per domain.
    domain_list = re.search(
        r"agents?\s+across\s+([a-z][a-z ,&\-/]{4,120}?)\s+collaborate",
        description,
        re.IGNORECASE,
    )
    if domain_list:
        for raw_role in re.split(r",|&|\band\b", domain_list.group(1)):
            role = raw_role.strip(" :-").lower()
            if role and role not in roles:
                roles.append(role)
                evidence.append(AnalysisEvidence("agent_domain_role", role))
    return roles, evidence


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUMBER_TOKEN = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"


def _number(text: str) -> float:
    normalized = text.lower()
    return float(_NUMBER_WORDS.get(normalized, normalized))


def _quantity(
    description: str,
    rule: str,
    pattern: str,
    *,
    default: float,
    multiplier: float = 1.0,
) -> QuantityAnalysis:
    match = re.search(pattern, description, re.IGNORECASE)
    if match:
        return QuantityAnalysis(
            value=_number(match.group("value")) * multiplier,
            source="explicit",
            evidence=[_evidence(rule, match)],
        )
    return QuantityAnalysis(
        value=default,
        source="defaulted",
        evidence=[AnalysisEvidence(f"{rule}_default", str(default))],
    )


def _field_evidence(
    values: list[Modality] | list[Tool],
    patterns: dict[Modality | Tool, re.Pattern[str]],
    description: str,
) -> dict[str, list[AnalysisEvidence]]:
    evidence: dict[str, list[AnalysisEvidence]] = {}
    for value in values:
        match = patterns[value].search(description)
        evidence[value.value] = [_evidence(f"detected_{value.value}", match)]
    return evidence


def _analyze_quantities(
    description: str,
    modalities: list[Modality],
    tools: list[Tool],
    topology: AgentPattern,
) -> tuple[dict[str, QuantityAnalysis], list[str], list[str]]:
    quantities: dict[str, QuantityAnalysis] = {}
    if topology == AgentPattern.WORKFLOW:
        quantities["workflow_steps"] = _quantity(
            description, "explicit_workflow_steps",
            rf"(?P<value>{_NUMBER_TOKEN})[ -]step", default=5,
        )
    if Modality.DOCUMENT in modalities:
        quantities["document_count"] = _quantity(
            description, "explicit_document_count",
            rf"(?P<value>{_NUMBER_TOKEN})\s*(?:documents?|pdfs?|files?|contracts?)",
            default=1,
        )
        quantities["pages_per_document"] = _quantity(
            description, "explicit_pages_per_document",
            rf"(?P<value>{_NUMBER_TOKEN})\s*pages?", default=5,
        )
    if Modality.IMAGE_INPUT in modalities:
        quantities["image_count"] = _quantity(
            description, "explicit_image_count",
            rf"(?P<value>{_NUMBER_TOKEN})\s*(?:scanned\s+)?(?:images?|photos?|screenshots?|pictures?|scans?)",
            default=1,
        )
    if Modality.AUDIO_INPUT in modalities:
        minutes = re.search(
            rf"(?P<value>{_NUMBER_TOKEN})\s*(?:minutes?|mins?)\b",
            description, re.IGNORECASE,
        )
        quantities["audio_duration_seconds"] = (
            QuantityAnalysis(
                value=_number(minutes.group("value")) * 60,
                source="explicit",
                evidence=[_evidence("explicit_audio_minutes", minutes)],
            )
            if minutes else _quantity(
                description, "explicit_audio_seconds",
                rf"(?P<value>{_NUMBER_TOKEN})\s*(?:seconds?|secs?)\b",
                default=30,
            )
        )
    if Tool.FILE_SEARCH in tools or Tool.WEB_SEARCH in tools:
        quantities["searches_per_call"] = _quantity(
            description, "explicit_search_count",
            rf"(?P<value>{_NUMBER_TOKEN})\s*(?:retrieval\s+|web\s+)?search(?:es)?",
            default=1,
        )

    defaults = [name for name, item in quantities.items() if item.source == "defaulted"]
    assumptions = [
        f"{name} defaulted to {quantities[name].value:g} for modeled invocation economics."
        for name in defaults
    ]
    clarifications = (
        ["Confirm defaulted cost-material quantity assumptions before estimation."]
        if defaults else []
    )
    return quantities, assumptions, clarifications


def analyze_workload(description: str) -> WorkloadAnalysis:
    """Analyze topology deterministically and retain rule evidence/provenance."""
    normalized = description.strip()
    description_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    roles, role_evidence = _infer_role_agents(normalized)
    autonomous = _AUTONOMOUS_LOOP_PATTERNS.search(normalized)
    workflow = _BOUNDED_WORKFLOW_PATTERNS.search(normalized) or _WORKFLOW_PATTERNS.search(normalized)
    retrieval = _RETRIEVAL_PATTERNS.search(normalized)
    action = _SINGLE_AGENT_ACTION_PATTERNS.search(normalized)
    explicit_multi = re.search(
        r"\bmulti[ -]?agent\b|\bagent.to.agent\b|\bnetwork of autonomous agents\b",
        normalized,
        re.IGNORECASE,
    )

    alternatives: list[AgentPattern] = []
    clarifications: list[str] = []
    topology_evidence: list[AnalysisEvidence] = []

    if explicit_multi or len(roles) >= 2:
        selected = AgentPattern.MULTI_AGENT
        topology_evidence = role_evidence or [_evidence("explicit_multi_agent", explicit_multi)]
        confidence = "high"
        if autonomous:
            alternatives.append(AgentPattern.REACT_AGENT)
            clarifications.append(
                "Confirm whether named agents are independently orchestrated or one autonomous actor."
            )
            confidence = "medium"
    elif autonomous:
        selected = AgentPattern.REACT_AGENT
        topology_evidence = [_evidence("autonomous_control_loop", autonomous)]
        confidence = "high"
        if workflow:
            alternatives.append(AgentPattern.WORKFLOW)
            confidence = "medium"
    elif workflow:
        selected = AgentPattern.WORKFLOW
        topology_evidence = [_evidence("bounded_workflow", workflow)]
        confidence = "medium"
    elif retrieval and action:
        selected = AgentPattern.REACT_AGENT
        topology_evidence = [
            _evidence("retrieval_capability", retrieval),
            _evidence("agent_action", action),
        ]
        alternatives.append(AgentPattern.RAG_PIPELINE)
        confidence = "medium"
    elif retrieval:
        selected = AgentPattern.RAG_PIPELINE
        topology_evidence = [_evidence("retrieval_pipeline", retrieval)]
        confidence = "medium"
    elif action:
        selected = AgentPattern.REACT_AGENT
        topology_evidence = [_evidence("agent_action", action)]
        confidence = "medium"
    else:
        selected = AgentPattern.SINGLE_CALL
        topology_evidence = [AnalysisEvidence("no_agentic_signal", "No stronger topology signal")]
        confidence = "low"

    explicit_counts = [
        int(value) for value in re.findall(
            r"(\d+)\s*(?:main|sub|helper|worker|specialist|orchestrat\w*)?\s*(?:-\s*)?agents?",
            normalized,
            re.IGNORECASE,
        )
    ]
    if explicit_counts:
        count = sum(explicit_counts)
        count_source = "explicit"
        count_evidence = [AnalysisEvidence("explicit_agent_count", str(count))]
    elif selected == AgentPattern.MULTI_AGENT:
        count = max(2, len(roles))
        count_source = "inferred_roles"
        count_evidence = role_evidence or topology_evidence
    else:
        count = 1
        count_source = "defaulted"
        count_evidence = [AnalysisEvidence("single_actor_default", "1")]

    modalities = _detect_modalities(normalized)
    tools = _detect_tools(normalized)
    quantities, assumptions, quantity_clarifications = _analyze_quantities(
        normalized, modalities, tools, selected
    )

    return WorkloadAnalysis(
        schema_version=ANALYSIS_SCHEMA_VERSION,
        rule_set_version=RULE_SET_VERSION,
        description_hash=description_hash,
        topology=TopologyAnalysis(
            selected=selected,
            confidence=confidence,
            alternatives=alternatives,
            evidence=topology_evidence,
        ),
        agent_count=AgentCountAnalysis(
            value=count,
            source=count_source,
            evidence=count_evidence,
        ),
        modalities=modalities,
        tools=tools,
        modality_evidence=_field_evidence(
            modalities,
            {
                Modality.TEXT: re.compile(r".+", re.DOTALL),
                Modality.IMAGE_INPUT: _VISION_PATTERNS,
                Modality.IMAGE_OUTPUT: _IMAGE_GEN_PATTERNS,
                Modality.DOCUMENT: _DOCUMENT_PATTERNS,
                Modality.AUDIO_INPUT: _AUDIO_INPUT_PATTERNS,
                Modality.AUDIO_OUTPUT: _AUDIO_OUTPUT_PATTERNS,
            },
            normalized,
        ),
        tool_evidence=_field_evidence(
            tools,
            {
                Tool.FILE_SEARCH: _FILE_SEARCH_PATTERNS,
                Tool.CODE_INTERPRETER: _CODE_INTERPRETER_PATTERNS,
                Tool.WEB_SEARCH: _WEB_SEARCH_PATTERNS,
                Tool.CUSTOM_FUNCTION: _ENTERPRISE_ACTION_PATTERNS,
                Tool.MCP_SERVER: re.compile(r"\bmcp\b", re.IGNORECASE),
                Tool.FUNCTION_CALLING: re.compile(r"function call", re.IGNORECASE),
                Tool.RAG: _RETRIEVAL_PATTERNS,
            },
            normalized,
        ),
        quantities=quantities,
        assumptions=assumptions,
        clarifications=clarifications + quantity_clarifications,
    )

_MODEL_PATTERNS: list[tuple[str, Provider, re.Pattern[str]]] = [
    # OpenAI
    (
        "gpt-image-1",
        Provider.OPENAI,
        re.compile(r"gpt-image|dall-e|image.generation.model", re.IGNORECASE),
    ),
    (
        "gpt-4o-audio",
        Provider.OPENAI,
        re.compile(r"gpt-4o.audio|realtime|voice.model", re.IGNORECASE),
    ),
    ("o3", Provider.OPENAI, re.compile(r"\bo3\b", re.IGNORECASE)),
    ("o4-mini", Provider.OPENAI, re.compile(r"o4-mini|o4.mini", re.IGNORECASE)),
    ("gpt-5", Provider.OPENAI, re.compile(r"gpt-5|gpt.5", re.IGNORECASE)),
    (
        "gpt-4.1-nano",
        Provider.OPENAI,
        re.compile(r"gpt-4\.1-nano|gpt.4\.1.nano|nano", re.IGNORECASE),
    ),
    (
        "gpt-4.1-mini",
        Provider.OPENAI,
        re.compile(r"gpt-4\.1-mini|gpt.4\.1.mini", re.IGNORECASE),
    ),
    ("gpt-4.1", Provider.OPENAI, re.compile(r"gpt-4\.1|gpt.4\.1", re.IGNORECASE)),
    (
        "gpt-4o-mini",
        Provider.OPENAI,
        re.compile(r"gpt-4o-mini|gpt.4o.mini", re.IGNORECASE),
    ),
    ("gpt-4o", Provider.OPENAI, re.compile(r"gpt-4o|gpt.4o", re.IGNORECASE)),
    # Anthropic
    (
        "claude-opus-4",
        Provider.ANTHROPIC,
        re.compile(r"claude.opus.4|opus.4", re.IGNORECASE),
    ),
    (
        "claude-sonnet-4.5",
        Provider.ANTHROPIC,
        re.compile(r"claude.sonnet.4\.5|sonnet.4\.5", re.IGNORECASE),
    ),
    (
        "claude-sonnet-4",
        Provider.ANTHROPIC,
        re.compile(r"claude.sonnet.4|sonnet.4(?!\.)", re.IGNORECASE),
    ),
    (
        "claude-haiku-3.5",
        Provider.ANTHROPIC,
        re.compile(r"claude.haiku|haiku.3\.5|haiku", re.IGNORECASE),
    ),
    # Google
    (
        "gemini-2.5-pro",
        Provider.GOOGLE,
        re.compile(r"gemini.2\.5.pro|gemini.pro", re.IGNORECASE),
    ),
    (
        "gemini-2.5-flash",
        Provider.GOOGLE,
        re.compile(r"gemini.2\.5.flash|gemini.flash", re.IGNORECASE),
    ),
    (
        "gemini-2.0-flash",
        Provider.GOOGLE,
        re.compile(r"gemini.2\.0.flash", re.IGNORECASE),
    ),
    # Mistral
    ("mistral-large", Provider.MISTRAL, re.compile(r"mistral.large", re.IGNORECASE)),
    ("mistral-small", Provider.MISTRAL, re.compile(r"mistral.small", re.IGNORECASE)),
    ("codestral", Provider.MISTRAL, re.compile(r"codestral", re.IGNORECASE)),
    ("pixtral-large", Provider.MISTRAL, re.compile(r"pixtral", re.IGNORECASE)),
    # Cohere
    (
        "command-r-plus",
        Provider.COHERE,
        re.compile(r"command.r.plus|command.r\+", re.IGNORECASE),
    ),
    ("command-a", Provider.COHERE, re.compile(r"command.a\b", re.IGNORECASE)),
    ("command-r", Provider.COHERE, re.compile(r"command.r\b", re.IGNORECASE)),
    # Bedrock
    (
        "bedrock-claude-sonnet-4",
        Provider.BEDROCK,
        re.compile(r"bedrock.*claude.*sonnet", re.IGNORECASE),
    ),
    (
        "bedrock-claude-haiku-3.5",
        Provider.BEDROCK,
        re.compile(r"bedrock.*claude.*haiku", re.IGNORECASE),
    ),
    (
        "bedrock-llama-3.1-70b",
        Provider.BEDROCK,
        re.compile(r"bedrock.*llama.*70", re.IGNORECASE),
    ),
    (
        "bedrock-llama-3.1-8b",
        Provider.BEDROCK,
        re.compile(r"bedrock.*llama.*8b", re.IGNORECASE),
    ),
    (
        "bedrock-mistral-large",
        Provider.BEDROCK,
        re.compile(r"bedrock.*mistral", re.IGNORECASE),
    ),
    # Local
    ("deepseek-r1", Provider.LOCAL, re.compile(r"deepseek.r1|deepseek", re.IGNORECASE)),
    (
        "llama-3.1-70b",
        Provider.LOCAL,
        re.compile(r"llama.3\.1.70|llama.70", re.IGNORECASE),
    ),
    (
        "llama-3.1-8b",
        Provider.LOCAL,
        re.compile(r"llama.3\.1.8|llama.8", re.IGNORECASE),
    ),
    ("phi-4", Provider.LOCAL, re.compile(r"phi-4|phi.4", re.IGNORECASE)),
    ("qwen-2.5-72b", Provider.LOCAL, re.compile(r"qwen", re.IGNORECASE)),
    ("mistral-7b", Provider.LOCAL, re.compile(r"mistral.7b", re.IGNORECASE)),
    # Generic provider mentions (no specific model)
    ("gpt-4.1", Provider.OPENAI, re.compile(r"\bopenai\b", re.IGNORECASE)),
    (
        "claude-sonnet-4",
        Provider.ANTHROPIC,
        re.compile(r"\banthropic\b|\bclaude\b", re.IGNORECASE),
    ),
    (
        "gemini-2.5-flash",
        Provider.GOOGLE,
        re.compile(r"\bgemini\b|\bgoogle\b", re.IGNORECASE),
    ),
    (
        "mistral-large",
        Provider.MISTRAL,
        re.compile(r"\bmistral\b(?!.7b)", re.IGNORECASE),
    ),
    ("command-r-plus", Provider.COHERE, re.compile(r"\bcohere\b", re.IGNORECASE)),
    (
        "llama-3.1-8b",
        Provider.LOCAL,
        re.compile(r"\bollama\b|\blocal\b|\bvllm\b", re.IGNORECASE),
    ),
]

# Provider detection patterns (explicit provider mentions)
_PROVIDER_PATTERNS: list[tuple[Provider, re.Pattern[str]]] = [
    (Provider.AZURE_OPENAI, re.compile(r"azure|azure.openai", re.IGNORECASE)),
    (Provider.BEDROCK, re.compile(r"bedrock|aws", re.IGNORECASE)),
]


def classify(description: str) -> UseCaseProfile:
    """Classify a natural language use case description into a UseCaseProfile.

    Tries LLM-based classification first (for model/provider/agent_type/
    complexity/modalities). Falls back to regex on failure or missing API key.
    """
    profile = UseCaseProfile()
    analysis = analyze_workload(description)

    # ── Try LLM-based classification first ──
    llm_result = _try_llm_classification(description)

    if llm_result is not None:
        profile.model = llm_result.model
        profile.provider = _str_to_provider(llm_result.provider)
        profile.complexity = _str_to_complexity(llm_result.complexity)
        profile.has_reasoning_tokens = llm_result.reasoning

        # LLM evidence may add valid modalities but cannot erase deterministic evidence.
        deterministic_modalities = analysis.modalities
        if llm_result.modalities:
            profile.modalities = deterministic_modalities.copy()
            for modality in _str_list_to_modalities(llm_result.modalities):
                if modality not in profile.modalities:
                    profile.modalities.append(modality)
        else:
            profile.modalities = deterministic_modalities

        # Tools are still regex-based (LLM doesn't detect these)
        profile.tools = _detect_tools(description)
    else:
        # ── Regex fallback ──
        profile.model, profile.provider = _detect_model_and_provider(description)
        profile.modalities = _detect_modalities(description)
        profile.tools = _detect_tools(description)
        profile.complexity = _detect_complexity(description)
        profile.has_reasoning_tokens = bool(_REASONING_MODELS.search(description))

    profile.agent_pattern = analysis.topology.selected
    profile.agent_type = {
        AgentPattern.SINGLE_CALL: AgentType.PROMPT,
        AgentPattern.RAG_PIPELINE: AgentType.PROMPT,
        AgentPattern.CODE_EXEC: AgentType.PROMPT,
        AgentPattern.WORKFLOW: AgentType.WORKFLOW,
        AgentPattern.REACT_AGENT: AgentType.HOSTED,
        AgentPattern.MULTI_AGENT: AgentType.HOSTED,
    }[profile.agent_pattern]
    profile.multi_agent_count = analysis.agent_count.value

    # Build modality-specific profiles
    if Modality.IMAGE_INPUT in profile.modalities:
        profile.image_inputs = _build_image_profile(description)
        quantity = analysis.quantities.get("image_count")
        if quantity:
            profile.image_inputs.count_per_call = int(quantity.value)

    if Modality.DOCUMENT in profile.modalities:
        profile.document_inputs = _build_document_profile(description, profile.tools)
        count = analysis.quantities.get("document_count")
        pages = analysis.quantities.get("pages_per_document")
        if count:
            profile.document_inputs.count = int(count.value)
        if pages:
            profile.document_inputs.avg_pages = int(pages.value)

    if Modality.AUDIO_INPUT in profile.modalities:
        duration = analysis.quantities.get("audio_duration_seconds")
        profile.audio_inputs = AudioInputProfile(
            avg_duration_seconds=duration.value if duration else 30.0
        )

    searches = analysis.quantities.get("searches_per_call")
    if searches:
        profile.searches_per_call = int(searches.value)

    steps = analysis.quantities.get("workflow_steps")
    if steps:
        profile.workflow_steps = int(steps.value)

    # Detect scale
    users_match = re.search(r"([\d,]+)\s*user", description, re.IGNORECASE)
    if users_match:
        profile.users = int(users_match.group(1).replace(",", ""))

    calls_match = re.search(
        r"([\d,]+)\s*(?:calls?|queries|requests?|messages?|interactions?)\s*(?:per|/)\s*(?:user\s*(?:per|/)\s*)?day",
        description,
        re.IGNORECASE,
    )
    if calls_match:
        profile.calls_per_user_per_day = int(calls_match.group(1).replace(",", ""))

    # Detect capability cost levers (regex on the raw description)
    profile.uses_prompt_caching = bool(_PROMPT_CACHING_PATTERNS.search(description))
    profile.uses_batch_api = bool(_BATCH_API_PATTERNS.search(description))
    profile.uses_streaming = bool(_STREAMING_PATTERNS.search(description))
    profile.uses_retrieval = bool(_RETRIEVAL_PATTERNS.search(description))

    return profile


# ── LLM classification helpers ───────────────────────────────────────

_PROVIDER_MAP: dict[str, Provider] = {
    "openai": Provider.OPENAI,
    "azure_openai": Provider.AZURE_OPENAI,
    "azure": Provider.AZURE_OPENAI,
    "anthropic": Provider.ANTHROPIC,
    "google": Provider.GOOGLE,
    "mistral": Provider.MISTRAL,
    "cohere": Provider.COHERE,
    "bedrock": Provider.BEDROCK,
    "local": Provider.LOCAL,
}

_AGENT_TYPE_MAP: dict[str, AgentType] = {
    "prompt": AgentType.PROMPT,
    "workflow": AgentType.WORKFLOW,
    "hosted": AgentType.HOSTED,
}

_COMPLEXITY_MAP: dict[str, Complexity] = {
    "low": Complexity.LOW,
    "medium": Complexity.MEDIUM,
    "high": Complexity.HIGH,
}

_MODALITY_MAP: dict[str, Modality] = {
    "text": Modality.TEXT,
    "image_input": Modality.IMAGE_INPUT,
    "image_output": Modality.IMAGE_OUTPUT,
    "document": Modality.DOCUMENT,
    "audio_input": Modality.AUDIO_INPUT,
    "audio_output": Modality.AUDIO_OUTPUT,
}


def _try_llm_classification(description: str):
    """Attempt LLM-based classification; returns LLMClassification or None."""
    try:
        from future_token_predictor.llm_classifier import classify_with_llm

        return classify_with_llm(description)
    except Exception as exc:
        logger.debug("LLM classifier unavailable: %s", exc)
        return None


def _str_to_provider(s: str) -> Provider:
    return _PROVIDER_MAP.get(s.lower(), Provider.OPENAI)


def _str_to_agent_type(s: str) -> AgentType:
    return _AGENT_TYPE_MAP.get(s.lower(), AgentType.PROMPT)


def _str_to_complexity(s: str) -> Complexity:
    return _COMPLEXITY_MAP.get(s.lower(), Complexity.MEDIUM)


def _str_list_to_modalities(items: list[str]) -> list[Modality]:
    result = []
    for s in items:
        m = _MODALITY_MAP.get(s.lower())
        if m and m not in result:
            result.append(m)
    if Modality.TEXT not in result:
        result.insert(0, Modality.TEXT)
    return result


def _detect_model_and_provider(description: str) -> tuple[str, Provider]:
    """Detect the target model and provider from the description."""
    # Check for explicit provider override (e.g., "using Azure OpenAI")
    explicit_provider = None
    for prov, pattern in _PROVIDER_PATTERNS:
        if pattern.search(description):
            explicit_provider = prov
            break

    for model_name, model_provider, pattern in _MODEL_PATTERNS:
        m = pattern.search(description)
        if m:
            provider = explicit_provider or model_provider
            # Try to extract the exact model string the user typed.
            # E.g., "gpt-5.4" should be preserved, not truncated to "gpt-5".
            resolved = _extract_full_model_id(description, m, model_name)
            return resolved, provider

    provider = explicit_provider or Provider.OPENAI
    return "gpt-4.1", provider


# Pattern to grab a full model identifier after a match (e.g., "gpt-5.4", "gpt-4.1-nano-2025")
_MODEL_ID_RE = re.compile(
    r"((?:gpt|claude|gemini|mistral|o|llama|phi|qwen|codestral|pixtral|"
    r"command|deepseek)[\w.\-]*)",
    re.IGNORECASE,
)


def _extract_full_model_id(description: str, match: re.Match, canonical: str) -> str:
    """Try to extract the full model ID the user typed from the description.

    If the user wrote "gpt-5.4", the regex for gpt-5 matches at the start but
    we should capture the full "gpt-5.4" token rather than truncating.

    Only overrides canonical when the extracted ID is strictly longer (i.e., the
    user typed a more specific version like "gpt-5.4" vs canonical "gpt-5").
    Falls back to canonical otherwise.
    """
    start = max(0, match.start() - 2)
    region = description[start : match.end() + 30]
    full_match = _MODEL_ID_RE.search(region)
    if full_match:
        raw = full_match.group(1)
        end_pos = full_match.end()
        # Check if there's a space-separated version number right after
        remainder = description[start + end_pos :]
        version_ext = re.match(r"[\s\-]+(\d[\w.\-]*)", remainder)
        if version_ext:
            raw = raw + "-" + version_ext.group(1)
        extracted = raw.strip(".").lower()
        extracted = re.sub(r"\s+", "-", extracted)
        # Only use extracted if it starts with the canonical base AND is
        # strictly longer (meaning the user provided a more specific ID)
        if extracted.startswith(canonical.lower()) and len(extracted) > len(canonical):
            return extracted
    return canonical


def _agent_type_to_pattern(agent_type: AgentType) -> AgentPattern:
    """Map AgentType to AgentPattern."""
    mapping = {
        AgentType.PROMPT: AgentPattern.SINGLE_CALL,
        AgentType.WORKFLOW: AgentPattern.WORKFLOW,
        AgentType.HOSTED: AgentPattern.REACT_AGENT,
    }
    return mapping.get(agent_type, AgentPattern.SINGLE_CALL)


def _detect_modalities(description: str) -> list[Modality]:
    """Detect all modalities present in the description."""
    modalities = [Modality.TEXT]  # Always present

    if _IMAGE_GEN_PATTERNS.search(description):
        modalities.append(Modality.IMAGE_OUTPUT)
    if _VISION_PATTERNS.search(description) and Modality.IMAGE_OUTPUT not in modalities:
        modalities.append(Modality.IMAGE_INPUT)
    if _DOCUMENT_PATTERNS.search(description):
        modalities.append(Modality.DOCUMENT)
    if _AUDIO_INPUT_PATTERNS.search(description):
        modalities.append(Modality.AUDIO_INPUT)
    if _AUDIO_OUTPUT_PATTERNS.search(description):
        modalities.append(Modality.AUDIO_OUTPUT)

    return modalities


def _detect_tools(description: str) -> list[Tool]:
    """Detect tools from the description."""
    tools: list[Tool] = []

    if _FILE_SEARCH_PATTERNS.search(description):
        tools.append(Tool.FILE_SEARCH)
    if _CODE_INTERPRETER_PATTERNS.search(description):
        tools.append(Tool.CODE_INTERPRETER)
    if _WEB_SEARCH_PATTERNS.search(description):
        tools.append(Tool.WEB_SEARCH)
    if _ENTERPRISE_ACTION_PATTERNS.search(description):
        tools.append(Tool.CUSTOM_FUNCTION)

    return tools


def _detect_agent_type(description: str, tools: list[Tool]) -> AgentType:
    """Detect agent type from the description."""
    if _HOSTED_PATTERNS.search(description):
        return AgentType.HOSTED
    if _WORKFLOW_PATTERNS.search(description):
        return AgentType.WORKFLOW
    # Many tools suggests more than a simple prompt agent
    if len(tools) >= 3:
        return AgentType.HOSTED
    return AgentType.PROMPT


def _detect_complexity(description: str) -> Complexity:
    """Detect task complexity from the description."""
    if _COMPLEXITY_HIGH_PATTERNS.search(description):
        return Complexity.HIGH
    if _COMPLEXITY_LOW_PATTERNS.search(description):
        return Complexity.LOW
    return Complexity.MEDIUM


def _build_image_profile(description: str) -> ImageInputProfile:
    """Build image input profile from description hints."""
    profile = ImageInputProfile()

    # Try to detect image count
    count_match = re.search(
        r"(\d+)\s*(?:image|photo|screenshot|picture)", description, re.IGNORECASE
    )
    if count_match:
        profile.count_per_call = int(count_match.group(1))

    # Detect detail level
    if re.search(r"low.detail|thumbnail|small|preview", description, re.IGNORECASE):
        profile.detail_level = DetailLevel.LOW

    return profile


def _build_document_profile(
    description: str, tools: list[Tool]
) -> DocumentInputProfile:
    """Build document input profile from description hints."""
    profile = DocumentInputProfile()

    # If File Search tool is present, use that retrieval strategy
    if Tool.FILE_SEARCH in tools:
        profile.retrieval_strategy = RetrievalStrategy.FILE_SEARCH
    else:
        profile.retrieval_strategy = RetrievalStrategy.DIRECT

    # Try to detect page count
    pages_match = re.search(r"(\d+)\s*page", description, re.IGNORECASE)
    if pages_match:
        profile.avg_pages = int(pages_match.group(1))

    # Try to detect document count
    doc_match = re.search(r"(\d+)\s*(?:document|pdf|file)", description, re.IGNORECASE)
    if doc_match:
        profile.count = int(doc_match.group(1))

    return profile
