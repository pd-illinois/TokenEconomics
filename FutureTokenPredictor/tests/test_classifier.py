"""Tests for the classifier module."""

from __future__ import annotations

import pytest

from future_token_predictor.classifier import analyze_workload, classify
from future_token_predictor.models.schemas import (
    AgentPattern,
    AgentType,
    Complexity,
    Modality,
    Provider,
    Tool,
)


# ── Model Detection ─────────────────────────────────────────────────────


class TestModelDetection:
    def test_gpt41(self):
        profile = classify("Use GPT-4.1 for a simple chatbot")
        assert profile.model == "gpt-4.1"
        assert profile.provider == Provider.OPENAI

    def test_claude_sonnet(self):
        profile = classify("Build an agent with Claude Sonnet 4")
        assert profile.model == "claude-sonnet-4"
        assert profile.provider == Provider.ANTHROPIC

    def test_claude_opus(self):
        profile = classify("Use Claude Opus 4 for complex analysis")
        assert profile.model == "claude-opus-4"
        assert profile.provider == Provider.ANTHROPIC

    def test_gemini_pro(self):
        profile = classify("Use Gemini 2.5 Pro for reasoning tasks")
        assert profile.model == "gemini-2.5-pro"
        assert profile.provider == Provider.GOOGLE

    def test_mistral_large(self):
        profile = classify("Deploy Mistral Large for enterprise use")
        assert profile.model == "mistral-large"
        assert profile.provider == Provider.MISTRAL

    def test_command_r_plus(self):
        profile = classify("Build a RAG pipeline with Command R Plus")
        assert profile.model == "command-r-plus"
        assert profile.provider == Provider.COHERE

    def test_deepseek_r1(self):
        profile = classify("Run DeepSeek R1 locally")
        assert profile.model == "deepseek-r1"
        assert profile.provider == Provider.LOCAL

    def test_llama_local(self):
        profile = classify("Run Llama 3.1 8B on local GPU")
        assert profile.model == "llama-3.1-8b"
        assert profile.provider == Provider.LOCAL

    def test_default_model(self):
        profile = classify("Build a chatbot")
        assert profile.model == "gpt-4.1"
        assert profile.provider == Provider.OPENAI

    def test_o3_reasoning(self):
        profile = classify("Use o3 for complex reasoning")
        assert profile.model == "o3"
        assert profile.provider == Provider.OPENAI

    def test_bedrock_claude(self):
        profile = classify("Use Bedrock Claude Sonnet for AWS deployment")
        assert profile.model == "bedrock-claude-sonnet-4"
        assert profile.provider == Provider.BEDROCK


# ── Provider Override ────────────────────────────────────────────────────


class TestProviderOverride:
    def test_azure_openai_override(self):
        profile = classify("Use GPT-4.1 on Azure OpenAI")
        assert profile.model == "gpt-4.1"
        assert profile.provider == Provider.AZURE_OPENAI

    def test_bedrock_override(self):
        profile = classify("Deploy on AWS Bedrock with Claude")
        assert profile.provider == Provider.BEDROCK

    def test_generic_anthropic(self):
        profile = classify("Use Anthropic for this task")
        assert profile.model == "claude-sonnet-4"
        assert profile.provider == Provider.ANTHROPIC

    def test_generic_google(self):
        profile = classify("Use Google for the backend")
        assert profile.provider == Provider.GOOGLE


# ── Modality Detection ───────────────────────────────────────────────────


class TestModalityDetection:
    def test_text_always_present(self):
        profile = classify("Simple chatbot")
        assert Modality.TEXT in profile.modalities

    def test_vision_detected(self):
        profile = classify("Analyze uploaded images with GPT-4.1")
        assert Modality.IMAGE_INPUT in profile.modalities

    def test_image_generation(self):
        profile = classify("Generate images using DALL-E")
        assert Modality.IMAGE_OUTPUT in profile.modalities

    def test_document_detected(self):
        profile = classify("Search through uploaded PDF documents")
        assert Modality.DOCUMENT in profile.modalities

    def test_audio_detected(self):
        profile = classify("Build a voice assistant with speech-to-text")
        assert Modality.AUDIO_INPUT in profile.modalities
        assert Modality.AUDIO_OUTPUT in profile.modalities

    def test_multi_modal(self):
        profile = classify("Analyze images and search documents with RAG")
        assert Modality.IMAGE_INPUT in profile.modalities
        assert Modality.DOCUMENT in profile.modalities


# ── Tool Detection ───────────────────────────────────────────────────────


class TestToolDetection:
    def test_file_search(self):
        profile = classify("Use file search to find relevant documents")
        assert Tool.FILE_SEARCH in profile.tools

    def test_code_interpreter(self):
        profile = classify("Run code interpreter to analyze CSV data")
        assert Tool.CODE_INTERPRETER in profile.tools

    def test_web_search(self):
        profile = classify("Search the web for current information")
        assert Tool.WEB_SEARCH in profile.tools

    def test_rag_implies_file_search(self):
        profile = classify("Build a RAG pipeline over knowledge base")
        assert Tool.FILE_SEARCH in profile.tools


# ── Agent Type Detection ─────────────────────────────────────────────────


class TestAgentTypeDetection:
    def test_prompt_agent(self):
        profile = classify("Simple chatbot for Q&A")
        assert profile.agent_type == AgentType.PROMPT

    def test_workflow_agent(self):
        profile = classify("Multi-step workflow with branching logic")
        assert profile.agent_type == AgentType.WORKFLOW

    def test_hosted_agent(self):
        profile = classify("Autonomous ReAct loop agent with tool use")
        assert profile.agent_type == AgentType.HOSTED

    def test_agent_pattern_mapping(self):
        profile = classify("Simple chatbot")
        assert profile.agent_pattern == AgentPattern.SINGLE_CALL

        profile = classify("Multi-step workflow pipeline")
        assert profile.agent_pattern == AgentPattern.WORKFLOW


# ── Complexity Detection ─────────────────────────────────────────────────


class TestRetrievalTopology:
    @pytest.mark.parametrize("suffix", [
        "",
        " There is no multi-agent delegation.",
        " This runs without multi-agent delegation.",
        " This is not a multi-agent system.",
    ])
    def test_gutenberg_single_retrieval_generation_pipeline(self, suffix, monkeypatch):
        for key in ("CLASSIFIER_API_KEY", "CLASSIFIER_ENDPOINT", "CLASSIFIER_MODEL",
                    "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
                    "AZURE_OPENAI_CLASSIFIER_DEPLOYMENT", "OPENAI_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        description = (
            "A RAG pipeline for a Project Gutenberg book collection. "
            "A reader submits a question. The application embeds the question, "
            "performs hybrid retrieval against the existing Azure AI Search books "
            "index, and passes the retrieved book excerpts and question to "
            "Microsoft Foundry GPT-5.6 Luna. A single answer-generation call "
            "produces a grounded text answer with book citations. The workload "
            "is a retrieval-augmented generation pipeline with one retrieval "
            "stage and one generation stage. Sample questions concern characters, "
            "events, themes and comparisons in the indexed books."
        ) + suffix

        analysis = analyze_workload(description)
        assert analysis.topology.selected == AgentPattern.RAG_PIPELINE
        assert analysis.topology.evidence[0].rule == "retrieval_pipeline"
        assert "workflow_steps" not in analysis.quantities
        assert analysis.agent_count.value == 1
        assert Tool.FILE_SEARCH in analysis.tools
        assert classify(description).agent_pattern == AgentPattern.RAG_PIPELINE

    @pytest.mark.parametrize(("description", "expected"), [
        ("A RAG pipeline over a knowledge base.", AgentPattern.RAG_PIPELINE),
        ("A pipeline with retrieval and a six-step workflow with branching.",
         AgentPattern.WORKFLOW),
        ("A retrieval pipeline ingests documents, extracts facts, and validates results.",
         AgentPattern.WORKFLOW),
        ("An autonomous RAG pipeline retrieves evidence and executes actions.",
         AgentPattern.TOOL_AGENT),
        ("A RAG pipeline retrieves policies and submits a refund request.",
         AgentPattern.TOOL_AGENT),
        ("A multi-agent RAG pipeline delegates to specialists.", AgentPattern.MULTI_AGENT),
        ("No multi-agent delegation in retrieval. A separate multi-agent team reviews answers.",
         AgentPattern.MULTI_AGENT),
        ("A multi-step workflow pipeline.", AgentPattern.WORKFLOW),
    ])
    def test_stronger_topology_signals_are_preserved(self, description, expected):
        assert analyze_workload(description).topology.selected == expected


class TestComplexityDetection:
    def test_high_complexity(self):
        profile = classify("Complex enterprise multi-agent system")
        assert profile.complexity == Complexity.HIGH

    def test_low_complexity(self):
        profile = classify("Simple demo chatbot prototype")
        assert profile.complexity == Complexity.LOW

    def test_medium_default(self):
        profile = classify("Build a customer support chatbot")
        assert profile.complexity == Complexity.MEDIUM


# ── Scale Detection ──────────────────────────────────────────────────────


class TestScaleDetection:
    def test_user_count(self):
        profile = classify("Chatbot for 100 users")
        assert profile.users == 100

    def test_calls_per_day(self):
        profile = classify("Each user makes 10 calls per day")
        assert profile.calls_per_user_per_day == 10

    def test_both_scale_params(self):
        profile = classify("200 users, 5 calls per user per day with GPT-4.1")
        assert profile.users == 200
        assert profile.calls_per_user_per_day == 5


# ── Reasoning Detection ─────────────────────────────────────────────────


class TestReasoningDetection:
    def test_o3_triggers_reasoning(self):
        profile = classify("Use o3 for deep reasoning tasks")
        assert profile.has_reasoning_tokens is True

    def test_regular_model_no_reasoning(self):
        profile = classify("Use GPT-4.1 for chatbot")
        assert profile.has_reasoning_tokens is False


# ── Regex Precedence Edge Cases ──────────────────────────────────────────


class TestRegexPrecedence:
    def test_sonnet_45_not_sonnet_4(self):
        profile = classify("Use Claude Sonnet 4.5 for analysis")
        assert profile.model == "claude-sonnet-4.5"

    def test_gpt41_mini_not_gpt41(self):
        profile = classify("Use GPT-4.1-mini for lightweight tasks")
        assert profile.model == "gpt-4.1-mini"

    def test_gpt41_nano_not_gpt41(self):
        profile = classify("Use GPT-4.1-nano for edge deployment")
        assert profile.model == "gpt-4.1-nano"

    def test_gpt4o_mini_not_gpt4o(self):
        profile = classify("Use GPT-4o-mini for cost savings")
        assert profile.model == "gpt-4o-mini"

    def test_o4_mini_not_o3(self):
        profile = classify("Use o4-mini for reasoning")
        assert profile.model == "o4-mini"


# ── Capability / Cost-Lever Detection ───────────────────────────────────


class TestCapabilityDetection:
    def test_prompt_caching_detected(self):
        profile = classify(
            "Chatbot that uses prompt caching to reuse the system prompt"
        )
        assert profile.uses_prompt_caching is True

    def test_prompt_caching_absent_by_default(self):
        profile = classify("Use GPT-4.1 for a simple chatbot")
        assert profile.uses_prompt_caching is False

    def test_batch_api_detected(self):
        profile = classify("Run nightly summarization via the batch API")
        assert profile.uses_batch_api is True

    def test_batch_api_absent_by_default(self):
        profile = classify("Use GPT-4.1 for a simple chatbot")
        assert profile.uses_batch_api is False

    def test_streaming_detected(self):
        profile = classify("A chatbot that streams tokens to the user")
        assert profile.uses_streaming is True

    def test_streaming_absent_by_default(self):
        profile = classify("Use GPT-4.1 for a simple chatbot")
        assert profile.uses_streaming is False

    def test_retrieval_detected(self):
        profile = classify("A RAG agent over a knowledge base with a vector store")
        assert profile.uses_retrieval is True

    def test_retrieval_absent_by_default(self):
        profile = classify("Use GPT-4.1 for a simple chatbot")
        assert profile.uses_retrieval is False
