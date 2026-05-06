"""
配置数据模型
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

LEGACY_PROVIDER_ALIASES = {
    "openai_codex": "chatgpt",
}


def resolve_litellm_slug(name: str, litellm_provider: Optional[str] = None) -> str:
    """
    用于 LiteLLM 的路由前缀与 *_API_KEY / *_API_BASE 环境变量名。
    litellm_provider 若填写，必须与 LiteLLM 支持的供应商标识一致（如 openai、anthropic）；
    用于「显示名或配置 id」与 OpenAI 兼容网关等场景可与 name 不同。
    """
    raw = (litellm_provider or "").strip()
    if raw:
        return LEGACY_PROVIDER_ALIASES.get(raw, raw)
    return LEGACY_PROVIDER_ALIASES.get(name, name)


def canonical_litellm_model_id(model: str, slug: str) -> str:
    """将模型规范为 LiteLLM 可调用的 `{slug}/{model}`。"""
    mt = (model or "").strip()
    if not mt:
        return ""
    mt = normalize_model_name(mt)
    if mt.startswith(f"{slug}/"):
        return mt
    return f"{slug}/{mt}"

CHATGPT_DEFAULT_MODELS = [
    "chatgpt/gpt-5.4",
    "chatgpt/codex-latest",
    "chatgpt/gpt-5.3-codex",
    "chatgpt/gpt-5.1-codex-mini",
]

LEGACY_MODEL_ALIASES = {
    "openai-codex/gpt-5.1-codex": "chatgpt/gpt-5.1-codex-max",
    "openai-codex/codex-mini-latest": "chatgpt/gpt-5.1-codex-mini",
    "openai_codex/gpt-5.1-codex": "chatgpt/gpt-5.1-codex-max",
    "openai_codex/codex-mini-latest": "chatgpt/gpt-5.1-codex-mini",
}


class LLMProvider(BaseModel):
    """LLM 供应商配置"""
    name: str
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    models: list[str] = Field(default_factory=list)
    # 本地模型名 -> 实际传给 LiteLLM 的模型名。
    model_aliases: dict[str, str] = Field(default_factory=dict)
    model_display_names: dict[str, str] = Field(default_factory=dict)
    is_oauth: bool = False  # OAuth 类供应商（如 Codex、Copilot）不使用 API Key
    # 若不填则与 name 相同；必须与 LiteLLM 路由一致（openai、anthropic 等），可与展示用 name 区分开。
    litellm_provider: Optional[str] = None


def get_default_providers() -> list[LLMProvider]:
    return [
        LLMProvider(name="openai", models=["gpt-4o", "gpt-4o-mini"]),
        LLMProvider(name="anthropic", models=["claude-3-5-sonnet-20240620", "claude-3-haiku-20240307"]),
        LLMProvider(name="gemini", models=["gemini-1.5-pro", "gemini-1.5-flash"]),
        LLMProvider(name="deepseek", models=["deepseek-chat", "deepseek-coder"]),
        LLMProvider(name="groq", models=["llama3-70b-8192", "llama3-8b-8192"]),
        LLMProvider(name="zhipu", models=["glm-4", "glm-3-turbo"]),
        LLMProvider(name="ollama", endpoint="http://localhost:11434", models=[]),
        # OAuth 类供应商 — 不使用 API Key，改用浏览器授权
        LLMProvider(
            name="chatgpt",
            is_oauth=True,
            models=CHATGPT_DEFAULT_MODELS.copy(),
        ),
        LLMProvider(
            name="github_copilot",
            is_oauth=True,
            models=["github_copilot/claude-sonnet-4-5", "github_copilot/gpt-4o"],
        ),
    ]


class RoleModelConfig(BaseModel):
    """单个角色的模型与执行参数。"""
    model: str = ""
    reasoning_effort: Optional[
        Literal["default", "none", "minimal", "low", "medium", "high", "xhigh"]
    ] = None
    thinking_type: Optional[Literal["default", "disabled", "enabled", "adaptive"]] = None
    thinking_budget_tokens: Optional[int] = None


class ModelAssignment(BaseModel):
    """角色级别的模型分配与推理参数。"""
    agent_zero: RoleModelConfig = Field(default_factory=lambda: RoleModelConfig(model="gpt-4o"))
    sub_agents: RoleModelConfig = Field(default_factory=lambda: RoleModelConfig(model="gpt-4o-mini"))
    distillation: RoleModelConfig = Field(default_factory=lambda: RoleModelConfig(model="gpt-4o-mini"))

    @field_validator("agent_zero", "sub_agents", "distillation", mode="before")
    @classmethod
    def _coerce_role_config(cls, value: object, info) -> RoleModelConfig:
        fallback = {
            "agent_zero": "gpt-4o",
            "sub_agents": "gpt-4o-mini",
            "distillation": "gpt-4o-mini",
        }.get(info.field_name, "gpt-4o-mini")
        return _coerce_role_model_config(value, fallback)


class WebSearchConfig(BaseModel):
    """网络搜索工具配置"""
    # provider: brave | tavily | searxng | jina | duckduckgo
    provider: str = "duckduckgo"
    api_key: str = ""        # Brave / Tavily / Jina 的 API Key
    base_url: str = ""       # SearXNG 实例地址，如 http://localhost:8080
    max_results: int = 5
    # HTTP/SOCKS5 代理，国内必填，如 http://127.0.0.1:7890
    proxy: Optional[str] = None


class AppConfig(BaseModel):
    """应用配置"""
    llm_providers: list[LLMProvider] = Field(default_factory=get_default_providers)
    model_assignment: ModelAssignment = Field(default_factory=ModelAssignment)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)


def _coerce_role_model_config(raw: object, fallback_model: str) -> RoleModelConfig:
    """兼容旧版字符串配置与新版对象配置。"""
    if isinstance(raw, RoleModelConfig):
        return raw
    if isinstance(raw, str):
        return RoleModelConfig(model=raw)
    if isinstance(raw, dict):
        payload = dict(raw)
        payload.setdefault("model", fallback_model)
        return RoleModelConfig(**payload)
    return RoleModelConfig(model=fallback_model)


def normalize_model_name(model: str) -> str:
    if not model:
        return model

    if model in LEGACY_MODEL_ALIASES:
        return LEGACY_MODEL_ALIASES[model]

    if model.startswith("openai-codex/"):
        return f"chatgpt/{model.split('/', 1)[1]}"

    if model.startswith("openai_codex/"):
        return f"chatgpt/{model.split('/', 1)[1]}"

    return model


def _merge_provider(existing: LLMProvider, incoming: LLMProvider) -> LLMProvider:
    merged_models = list(dict.fromkeys(existing.models + incoming.models))
    merged_aliases = {
        **(existing.model_aliases or {}),
        **(incoming.model_aliases or {}),
    }
    merged_display_names = {
        **(existing.model_display_names or {}),
        **(incoming.model_display_names or {}),
    }
    return LLMProvider(
        name=incoming.name,
        api_key=incoming.api_key if incoming.api_key is not None else existing.api_key,
        endpoint=incoming.endpoint if incoming.endpoint is not None else existing.endpoint,
        models=merged_models,
        model_aliases=merged_aliases,
        model_display_names=merged_display_names,
        is_oauth=existing.is_oauth or incoming.is_oauth,
        litellm_provider=(
            incoming.litellm_provider
            if incoming.litellm_provider is not None
            else existing.litellm_provider
        ),
    )


def canonical_local_model_id(model: str, provider_name: str, litellm_slug: str) -> str:
    mt = normalize_model_name((model or "").strip())
    if not mt:
        return ""
    if "/" not in mt:
        return f"{provider_name}/{mt}"
    prefix, rest = mt.split("/", 1)
    if prefix == provider_name:
        return mt
    if prefix == litellm_slug:
        return f"{provider_name}/{rest}"
    return f"{provider_name}/{mt}"


def normalize_provider(provider: LLMProvider) -> LLMProvider:
    name = LEGACY_PROVIDER_ALIASES.get(provider.name, provider.name)
    slug = resolve_litellm_slug(name, provider.litellm_provider)

    merged_models_raw = list(provider.models or [])
    if name == "chatgpt" and not merged_models_raw:
        merged_models_raw = CHATGPT_DEFAULT_MODELS.copy()

    models: list[str] = []
    for model in merged_models_raw:
        mid = canonical_local_model_id(model, name, slug)
        if mid:
            models.append(mid)
    models = list(dict.fromkeys(models))

    aliases: dict[str, str] = {}
    for raw_key, raw_value in (provider.model_aliases or {}).items():
        key = canonical_local_model_id(str(raw_key), name, slug)
        value = normalize_model_name(str(raw_value or "").strip())
        if not key or not value:
            continue
        aliases[key] = canonical_litellm_model_id(value, slug)
        if key not in models:
            models.append(key)

    display_names: dict[str, str] = {}
    for raw_key, raw_value in (provider.model_display_names or {}).items():
        key = canonical_local_model_id(str(raw_key), name, slug)
        value = str(raw_value or "").strip()
        if key and value:
            display_names[key] = value

    return LLMProvider(
        name=name,
        api_key=provider.api_key,
        endpoint=provider.endpoint,
        models=models,
        model_aliases=aliases,
        model_display_names=display_names,
        is_oauth=provider.is_oauth or name in {"chatgpt", "github_copilot"},
        litellm_provider=provider.litellm_provider,
    )


def _expand_assignment_if_needed(fragment: str, providers: list[LLMProvider]) -> str:
    """将仅存 model 后缀的旧配置对齐到 provider/model（若在本机模型列表中能唯一定位则补全前缀）."""
    fragment = normalize_model_name((fragment or "").strip())
    if not fragment or "/" in fragment:
        return fragment

    outs: list[str] = []
    for p in providers:
        for mid in p.models or []:
            parts = mid.split("/")
            if len(parts) < 2:
                continue
            if mid.endswith(f"/{fragment}") or parts[-1] == fragment:
                outs.append(mid)
    uniq = list(dict.fromkeys(outs))
    if len(uniq) == 1:
        return uniq[0]
    return fragment


def normalize_config(config: AppConfig) -> AppConfig:
    providers_by_name: dict[str, LLMProvider] = {}
    for provider in config.llm_providers:
        normalized = normalize_provider(provider)
        if normalized.name in providers_by_name:
            providers_by_name[normalized.name] = _merge_provider(
                providers_by_name[normalized.name],
                normalized,
            )
        else:
            providers_by_name[normalized.name] = normalized

    for default_provider in get_default_providers():
        if default_provider.name not in providers_by_name:
            providers_by_name[default_provider.name] = normalize_provider(default_provider)

    plist = list(providers_by_name.values())
    assignments = config.model_assignment
    return AppConfig(
        llm_providers=plist,
        model_assignment=ModelAssignment(
            agent_zero=RoleModelConfig(
                **{
                    **assignments.agent_zero.model_dump(),
                    "model": normalize_model_name(
                        _expand_assignment_if_needed(assignments.agent_zero.model, plist)
                    ),
                }
            ),
            sub_agents=RoleModelConfig(
                **{
                    **assignments.sub_agents.model_dump(),
                    "model": normalize_model_name(
                        _expand_assignment_if_needed(assignments.sub_agents.model, plist)
                    ),
                }
            ),
            distillation=RoleModelConfig(
                **{
                    **assignments.distillation.model_dump(),
                    "model": normalize_model_name(
                        _expand_assignment_if_needed(assignments.distillation.model, plist)
                    ),
                }
            ),
        ),
        web_search=config.web_search,
    )


def parse_app_config(data: Optional[dict] = None) -> AppConfig:
    raw = dict(data or {})
    # web_search 段单独解析，不干扰 normalize_config 的 LLM 逻辑
    web_search_data = raw.pop("web_search", None)
    model_assignment_raw = raw.get("model_assignment") or {}
    raw["model_assignment"] = {
        "agent_zero": _coerce_role_model_config(
            model_assignment_raw.get("agent_zero"),
            "gpt-4o",
        ).model_dump(),
        "sub_agents": _coerce_role_model_config(
            model_assignment_raw.get("sub_agents"),
            "gpt-4o-mini",
        ).model_dump(),
        "distillation": _coerce_role_model_config(
            model_assignment_raw.get("distillation"),
            "gpt-4o-mini",
        ).model_dump(),
    }
    config = normalize_config(AppConfig(**raw))
    if web_search_data:
        config.web_search = WebSearchConfig(**web_search_data)
    return config
