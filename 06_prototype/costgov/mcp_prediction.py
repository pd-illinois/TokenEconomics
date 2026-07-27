"""Local stdio MCP client for FutureTokenPredictor."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpPredictionError(RuntimeError):
    """Raised when the predictor MCP process cannot return structured output."""


class McpPredictorClient:
    def __init__(self, root: str | Path, timeout_seconds: float = 60.0) -> None:
        self.root = Path(root).resolve()
        self.timeout_seconds = timeout_seconds

    def predict(self, description: str, overrides: dict | None = None) -> dict:
        description = description.strip()
        if not description:
            raise ValueError("description is required")
        arguments, intake = self._build_arguments(description, overrides or {})
        return anyio.run(self._predict, description, arguments, intake)

    def analyze(self, description: str) -> dict:
        description = description.strip()
        if not description:
            raise ValueError("description is required")
        return anyio.run(self._analyze, description)

    def model_catalog(self) -> dict:
        predictor_root = self.root / "FutureTokenPredictor"
        if not predictor_root.exists():
            raise McpPredictionError("FutureTokenPredictor runtime is unavailable")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(predictor_root / "src")
        command = (
            "import json; "
            "from future_token_predictor.model_catalog import build_model_catalog; "
            "print(json.dumps(build_model_catalog()))"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", command],
                cwd=predictor_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=True,
            )
            return json.loads(result.stdout)
        except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise McpPredictionError(f"FutureTokenPredictor catalog failed: {exc}") from exc

    @staticmethod
    def _build_arguments(description: str, overrides: dict) -> tuple[dict, dict]:
        users_match = re.search(r"([\d,]+)\s*users?", description, re.IGNORECASE)
        users = int(users_match.group(1).replace(",", "")) if users_match else 1
        intake = {
            "model": str(overrides.get("model") or "gpt-4.1"),
            "provider": str(overrides.get("provider") or "openai"),
            "users": int(overrides["users"] if "users" in overrides else users),
            "calls_per_user_per_day": int(
                overrides["calls_per_user_per_day"]
                if "calls_per_user_per_day" in overrides
                else 1
            ),
        }
        confirmed_profile = overrides.get("confirmed_profile") or {}
        profile_fields = {
            "agent_pattern", "multi_agent_count", "modalities", "tools", "complexity",
            "image_count", "document_pages", "document_count", "audio_seconds",
            "searches_per_call", "workflow_steps",
        }
        for field in profile_fields:
            if field in confirmed_profile:
                intake[field] = confirmed_profile[field]
            if field in overrides:
                intake[field] = overrides[field]
        if overrides.get("analysis"):
            intake["analysis"] = overrides["analysis"]
        if confirmed_profile:
            intake["confirmed_profile"] = confirmed_profile
        if overrides.get("agent_models"):
            if len(overrides["agent_models"]) < 2:
                raise ValueError("agent_models requires at least two assignments")
            agent_models = []
            seen_agent_ids: set[str] = set()
            for item in overrides["agent_models"]:
                agent_id = str(item.get("agent_id", "")).strip()
                provider = str(item.get("provider", "")).strip()
                model = str(item.get("model", "")).strip()
                turn_weight = float(item.get("turn_weight", 1.0))
                if not agent_id or agent_id in seen_agent_ids:
                    raise ValueError("agent_models requires unique, non-empty agent_id values")
                if not provider or not model:
                    raise ValueError("each agent model requires provider and model")
                if not math.isfinite(turn_weight) or turn_weight <= 0:
                    raise ValueError(
                        "agent model turn_weight must be finite and greater than zero"
                    )
                seen_agent_ids.add(agent_id)
                agent_models.append({
                    "agent_id": agent_id,
                    "role": str(item.get("role", "")).strip(),
                    "provider": provider,
                    "model": model,
                    "turn_weight": turn_weight,
                })
            intake["agent_pattern"] = "multi_agent"
            intake["multi_agent_count"] = len(agent_models)
            intake["agent_models"] = agent_models
        if intake["users"] < 1 or intake["calls_per_user_per_day"] < 1:
            raise ValueError("users and calls per user per day must be at least 1")
        arguments = {
            key: value for key, value in intake.items()
            if key not in {"analysis", "confirmed_profile"}
        }
        return {"description": description, "output_format": "json", **arguments}, intake

    async def _analyze(self, description: str) -> dict:
        return await self._call_json_tool("analyze_workload", {"description": description})

    async def _call_json_tool(self, name: str, arguments: dict) -> dict:
        predictor_root = self.root / "FutureTokenPredictor"
        if not predictor_root.exists():
            raise McpPredictionError("FutureTokenPredictor runtime is unavailable")

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(predictor_root / "src")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "future_token_predictor.mcp_server"],
            cwd=predictor_root,
            env=environment,
        )
        try:
            with anyio.fail_after(self.timeout_seconds):
                async with stdio_client(parameters) as (read_stream, write_stream):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                    ) as session:
                        await session.initialize()
                        result = await session.call_tool(name, arguments)
        except TimeoutError as exc:
            raise McpPredictionError("FutureTokenPredictor timed out") from exc
        except Exception as exc:
            raise McpPredictionError(f"FutureTokenPredictor failed: {exc}") from exc

        if result.isError or not result.content:
            raise McpPredictionError("FutureTokenPredictor returned an error")
        text = getattr(result.content[0], "text", None)
        if not text:
            raise McpPredictionError("FutureTokenPredictor returned no JSON content")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpPredictionError("FutureTokenPredictor returned invalid JSON") from exc

    async def _predict(self, description: str, arguments: dict, intake: dict) -> dict:
        prediction = await self._call_json_tool("predict_token_usage", arguments)
        return {
            "status": "complete",
            "description": description,
            "intake": intake,
            "prediction": prediction,
            "infrastructure": {
                "status": "not_estimated",
                "message": "Azure infrastructure pricing will be added in the next planning stage.",
            },
        }