from typing import Any, Literal

from pydantic import BaseModel, Field

SupportLevel = Literal["yes", "no", "partial", "unknown"]


class ModelCapabilities(BaseModel):
    model: str
    supports_tools: SupportLevel = "unknown"
    supports_tool_choice: SupportLevel = "unknown"
    supports_reasoning_effort: SupportLevel = "unknown"
    supports_thinking: SupportLevel = "unknown"
    execution_agent_compatible: bool = True
    warnings: list[str] = Field(default_factory=list)


def _model_key(model: str) -> str:
    return (model or "").strip().lower()


def infer_model_capabilities(model: str) -> ModelCapabilities:
    key = _model_key(model)
    warnings: list[str] = []
    caps = ModelCapabilities(model=model)

    if not key:
        caps.execution_agent_compatible = False
        caps.warnings.append("Model is empty.")
        return caps

    if "deepseek-reasoner" in key:
        warnings.append(
            "deepseek-reasoner does not support tool_choice and is fragile in multi-step tool loops. "
            "Use it for planning/synthesis, not execution agents."
        )
        return ModelCapabilities(
            model=model,
            supports_tools="partial",
            supports_tool_choice="no",
            supports_reasoning_effort="unknown",
            supports_thinking="yes",
            execution_agent_compatible=False,
            warnings=warnings,
        )

    if key.startswith("deepseek/"):
        return ModelCapabilities(
            model=model,
            supports_tools="yes",
            supports_tool_choice="unknown",
            supports_reasoning_effort="unknown",
            supports_thinking="yes",
        )

    if key.startswith(("openai/", "chatgpt/", "anthropic/")):
        return ModelCapabilities(
            model=model,
            supports_tools="yes",
            supports_tool_choice="yes",
            supports_reasoning_effort="yes" if key.startswith(("openai/", "chatgpt/")) else "unknown",
            supports_thinking="yes" if key.startswith("anthropic/") else "unknown",
        )

    if key.startswith(("ollama/", "github_copilot/")):
        return ModelCapabilities(
            model=model,
            supports_tools="unknown",
            supports_tool_choice="unknown",
            supports_reasoning_effort="unknown",
            supports_thinking="unknown",
        )

    return caps


def supports_tool_choice(model: str, tool_choice: Any) -> bool:
    if not tool_choice:
        return True
    return infer_model_capabilities(model).supports_tool_choice != "no"
