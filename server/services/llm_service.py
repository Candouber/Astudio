import asyncio
import os
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import litellm
import yaml
from loguru import logger

from models.config import (
    AppConfig,
    LLMProvider,
    RoleModelConfig,
    canonical_litellm_model_id,
    parse_app_config,
    resolve_litellm_slug,
)
from storage.database import CONFIG_PATH

_RETRY_DELAYS = (3.0, 8.0)
_CONNECT_TIMEOUT = 30.0
_NONSTREAM_CALL_TIMEOUT = 180.0
_STREAM_CHUNK_TIMEOUT = 60.0
_TRANSIENT_MARKERS = (
    "429", "rate limit",
    "500", "502", "503", "504",
    "overloaded", "timeout", "timed out",
    "connection", "connect error", "cannot connect",
    "server error", "temporarily unavailable",
    "ssl", "network",
)


def _is_transient(exc: Exception) -> bool:
    return any(m in str(exc).lower() for m in _TRANSIENT_MARKERS)


class _Func:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id_: str, func: _Func):
        self.id = id_
        self.type = "function"
        self.function = func


class _AggregatedMessage:
    def __init__(self, content: str, tool_calls_data: Dict[int, dict]):
        self.content = content or None
        if tool_calls_data:
            self.tool_calls = [
                _ToolCall(
                    id_=tool_calls_data[i]["id"],
                    func=_Func(
                        name=tool_calls_data[i]["function"]["name"],
                        arguments=tool_calls_data[i]["function"]["arguments"],
                    ),
                )
                for i in sorted(tool_calls_data.keys())
            ]
        else:
            self.tool_calls = None

litellm.drop_params = True


class LLMService:
    def __init__(self):
        self._config: AppConfig = self._load_config()
        self._config_mtime = self._get_config_mtime()
        self._apply_api_keys()

    @staticmethod
    def _get_config_mtime() -> float | None:
        try:
            return CONFIG_PATH.stat().st_mtime
        except FileNotFoundError:
            return None

    def _load_config(self) -> AppConfig:
        if not CONFIG_PATH.exists():
            return AppConfig()

        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return parse_app_config(data)
        except Exception as e:
            logger.warning(f"Error loading config: {e}")
            return AppConfig()

    def _apply_api_keys(self):
        for provider in self._config.llm_providers:
            slug = resolve_litellm_slug(provider.name, getattr(provider, "litellm_provider", None))
            slug_key = slug.upper().replace("-", "_")
            if provider.api_key:
                os.environ[f"{slug_key}_API_KEY"] = provider.api_key
            if provider.endpoint:
                os.environ[f"{slug_key}_API_BASE"] = provider.endpoint

    def reload_config(self):
        """重新加载配置（热加载）"""
        self._config = self._load_config()
        self._config_mtime = self._get_config_mtime()
        self._apply_api_keys()

    def _reload_if_config_changed(self) -> None:
        current_mtime = self._get_config_mtime()
        if current_mtime != self._config_mtime:
            logger.info("LLM config file changed, reloading")
            self.reload_config()

    def _resolve_local_model_id(self, model: str) -> str:
        model = (model or "").strip()
        if not model or "/" in model:
            return model

        exact_matches: list[str] = []
        prefix_matches: list[str] = []
        for provider in self._config.llm_providers:
            slug = resolve_litellm_slug(provider.name, getattr(provider, "litellm_provider", None))
            for candidate in provider.models or []:
                if candidate.endswith(f"/{model}") or candidate.split("/")[-1] == model:
                    exact_matches.append(candidate)
            if model.startswith(f"{provider.name}-") or model.startswith(f"{slug}-"):
                prefix_matches.append(f"{slug}/{model}")

        exact_unique = list(dict.fromkeys(exact_matches))
        if len(exact_unique) == 1:
            return exact_unique[0]

        prefix_unique = list(dict.fromkeys(prefix_matches))
        if len(prefix_unique) == 1:
            return prefix_unique[0]

        return model

    def _resolve_model_ref(self, model: str) -> tuple[str, LLMProvider | None, str]:
        local_model = self._resolve_local_model_id(model)
        if not local_model:
            return "", None, ""

        for provider in self._config.llm_providers:
            slug = resolve_litellm_slug(provider.name, getattr(provider, "litellm_provider", None))
            provider_prefix = f"{provider.name}/"
            slug_prefix = f"{slug}/"

            matched_local = local_model
            if local_model.startswith(provider_prefix):
                alias = local_model[len(provider_prefix):]
            elif provider.name == slug and local_model.startswith(slug_prefix):
                alias = local_model[len(slug_prefix):]
                matched_local = f"{provider.name}/{alias}"
            else:
                continue

            target = (
                (provider.model_aliases or {}).get(matched_local)
                or (provider.model_aliases or {}).get(alias)
            )
            if target:
                return canonical_litellm_model_id(target, slug), provider, matched_local
            return f"{slug}/{alias}", provider, matched_local

        return local_model, None, local_model

    def _resolve_model_id(self, model: str) -> str:
        return self._resolve_model_ref(model)[0]

    async def _call_litellm(self, **kwargs) -> Any:
        """
        带重试和超时的 litellm.acompletion 封装。

        分层超时：
          · 流式（stream=True）：30s 仅保护"建立连接 / 返回首个 chunk"
          · 非流式（stream=False）：180s 覆盖"完整响应返回"
        瞬时错误（429/5xx/超时/网络）最多重试 2 次，间隔 3s / 8s。
        非瞬时错误（认证失败、参数错误等）直接上抛。
        """
        last_exc: Exception | None = None
        all_delays = list(_RETRY_DELAYS)
        is_stream = bool(kwargs.get("stream"))
        timeout = _CONNECT_TIMEOUT if is_stream else _NONSTREAM_CALL_TIMEOUT

        for attempt in range(len(all_delays) + 1):
            try:
                return await asyncio.wait_for(
                    litellm.acompletion(**kwargs),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                last_exc = TimeoutError(
                    f"LLM 调用超时（>{timeout}s, stream={is_stream}），"
                    "请检查网络或代理配置。"
                )
                logger.warning(
                    f"LLM call timeout (attempt {attempt + 1}/{len(all_delays) + 1}, "
                    f"timeout={timeout}s)"
                )
            except Exception as e:
                if not _is_transient(e):
                    logger.error(f"LLM non-transient error model={kwargs.get('model')}: {e}")
                    raise   # 非瞬时错误直接抛出，不重试
                last_exc = e
                logger.warning(
                    f"LLM 瞬时错误 (attempt {attempt + 1}/{len(all_delays) + 1}): "
                    f"{str(e)[:120]}"
                )

            if attempt < len(all_delays):
                delay = all_delays[attempt]
                logger.info(f"LLM 重试等待 {delay}s ...")
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    def get_model_for_role(self, role: str) -> str:
        """根据角色分配模型"""
        return self._resolve_model_id(self.get_role_config(role).model)

    def get_role_config(self, role: str) -> RoleModelConfig:
        """根据角色返回完整的执行配置。"""
        self._reload_if_config_changed()
        assignments = self._config.model_assignment
        if role == "agent_zero":
            return assignments.agent_zero
        elif role == "studio_leader":
            # Leader 使用与 CEO 相同级别的模型，负责规划拆解
            return assignments.agent_zero
        elif role == "sub_agent":
            return assignments.sub_agents
        elif role == "distillation":
            return assignments.distillation
        return assignments.sub_agents

    def get_role_runtime_options(self, role: str) -> Dict[str, Any]:
        """把角色配置转换为 LiteLLM 调用参数。"""
        role_config = self.get_role_config(role)
        model, provider, _local_model = self._resolve_model_ref(role_config.model)
        options: Dict[str, Any] = {"model": model}
        if provider and provider.api_key:
            options["api_key"] = provider.api_key
        if provider and provider.endpoint:
            options["base_url"] = provider.endpoint

        reasoning_effort = (role_config.reasoning_effort or "").strip()
        if reasoning_effort and reasoning_effort != "default":
            options["reasoning_effort"] = reasoning_effort

        thinking_type = (role_config.thinking_type or "").strip()
        if thinking_type and thinking_type != "default" and model.startswith("anthropic/"):
            thinking: Dict[str, Any] = {"type": thinking_type}
            if role_config.thinking_budget_tokens is not None:
                thinking["budget_tokens"] = role_config.thinking_budget_tokens
            options["thinking"] = thinking
        return options

    def get_model_display_name(self, model: str) -> str:
        """返回用户配置的模型显示名；调用 LiteLLM 时仍使用真实 model id。"""
        model = (model or "").strip()
        if not model:
            return ""
        for provider in self._config.llm_providers:
            if model in (provider.model_display_names or {}):
                return provider.model_display_names[model]
            if model in (provider.model_aliases or {}):
                return (provider.model_display_names or {}).get(model) or model
            slug = resolve_litellm_slug(provider.name, getattr(provider, "litellm_provider", None))
            provider_prefix = f"{provider.name}/"
            if model.startswith(provider_prefix):
                alias = model[len(provider_prefix):]
                return (provider.model_display_names or {}).get(model) or alias
            prefix = f"{slug}/"
            if model.startswith(prefix):
                alias = model[len(prefix):]
                for local, target in (provider.model_aliases or {}).items():
                    if target == model:
                        return (provider.model_display_names or {}).get(local) or local
                if provider.name != slug:
                    return f"{provider.name}/{alias}"
        return model

    def get_model_display_name_for_role(self, role: str) -> str:
        return self.get_model_display_name(self.get_role_config(role).model)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        role: str = "sub_agent",
        stream: bool = False,
        response_format: Any = None,
        temperature: Optional[float] = None,
        tools: Any = None,
        reasoning_effort: Optional[str] = None,
        thinking: Optional[Dict[str, Any]] = None,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """发起对话"""
        runtime_options = self.get_role_runtime_options(role)
        model = runtime_options["model"]

        # chatgpt OAuth 提供商使用的是 Responses API：
        # 1. 该 API 不接受 role=system 的消息，需抽取为 instructions 参数
        # 2. 该 API 强制 stream=True，需要以流式方式调用并在内部聚合
        final_messages = messages
        extra_kwargs: Dict[str, Any] = {}
        force_stream_aggregate = False

        if model.startswith("chatgpt/"):
            # 提取 system 消息为 instructions
            system_parts: List[str] = []
            non_system: List[Dict[str, str]] = []
            for msg in messages:
                if msg.get("role") == "system":
                    system_parts.append(msg.get("content", ""))
                else:
                    non_system.append(msg)
            if system_parts:
                extra_kwargs["instructions"] = "\n\n".join(system_parts)
                final_messages = non_system

            # ChatGPT Responses API 强制走流式，非流调用时我们自己聚合
            if not stream:
                force_stream_aggregate = True
                extra_kwargs["stream"] = True

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": final_messages,
            "stream": stream if not force_stream_aggregate else True,
            **extra_kwargs,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        effective_reasoning_effort = reasoning_effort
        if effective_reasoning_effort is None:
            effective_reasoning_effort = runtime_options.get("reasoning_effort")
        if effective_reasoning_effort:
            kwargs["reasoning_effort"] = effective_reasoning_effort

        effective_thinking = thinking
        if effective_thinking is None:
            effective_thinking = runtime_options.get("thinking")
        if effective_thinking:
            kwargs["thinking"] = effective_thinking

        if response_format:
            kwargs["response_format"] = response_format

        if tools:
            kwargs["tools"] = tools

        response = await self._call_litellm(**kwargs)

        if force_stream_aggregate:
            return await self._aggregate_stream(response)

        if stream:
            async def generate():
                async for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            return generate()
        else:
            msg = response.choices[0].message
            # 如果有工具调用，就返回原生的 Litellm Message Dict 对象，让上层决定怎么处理
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                return msg
            return msg.content

    async def chat_with_usage(
        self,
        messages: List[Dict[str, str]],
        role: str = "sub_agent",
        stream: bool = False,
        response_format: Any = None,
        temperature: Optional[float] = None,
        tools: Any = None,
        reasoning_effort: Optional[str] = None,
        thinking: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """
        与 chat() 相同，但额外返回 (result, total_tokens: int) 元组。
        total_tokens 为本次请求消耗的 token 总量（无法获取时返回 0）。
        """
        runtime_options = self.get_role_runtime_options(role)
        model = runtime_options["model"]
        final_messages = messages
        extra_kwargs: Dict[str, Any] = {}
        force_stream_aggregate = False

        if model.startswith("chatgpt/"):
            system_parts: List[str] = []
            non_system: List[Dict[str, str]] = []
            for msg in messages:
                if msg.get("role") == "system":
                    system_parts.append(msg.get("content", ""))
                else:
                    non_system.append(msg)
            if system_parts:
                extra_kwargs["instructions"] = "\n\n".join(system_parts)
                final_messages = non_system
            if not stream:
                force_stream_aggregate = True
                extra_kwargs["stream"] = True

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": final_messages,
            "stream": stream if not force_stream_aggregate else True,
            **extra_kwargs,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        effective_reasoning_effort = reasoning_effort
        if effective_reasoning_effort is None:
            effective_reasoning_effort = runtime_options.get("reasoning_effort")
        if effective_reasoning_effort:
            kwargs["reasoning_effort"] = effective_reasoning_effort

        effective_thinking = thinking
        if effective_thinking is None:
            effective_thinking = runtime_options.get("thinking")
        if effective_thinking:
            kwargs["thinking"] = effective_thinking

        if response_format:
            kwargs["response_format"] = response_format
        if tools:
            kwargs["tools"] = tools

        response = await self._call_litellm(**kwargs)
        total_tokens = 0

        if force_stream_aggregate:
            result, total_tokens = await self._aggregate_stream_with_usage(response)
            return result, total_tokens

        # 非流式：usage 在顶层 response 对象上
        if hasattr(response, "usage") and response.usage:
            total_tokens = getattr(response.usage, "total_tokens", 0) or 0

        msg = response.choices[0].message
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            return msg, total_tokens
        return msg.content, total_tokens


    def _merge_tool_chunk(self, tc_chunk, tool_calls_map: Dict[int, dict], id_to_idx: Dict[str, int]) -> None:
        """把一个流式 tool_call chunk 正确合并进 tool_calls_map。
        兼容缺 index 的 provider：用 id 做回退映射，新 id 自动分配新 index。"""
        raw_idx = getattr(tc_chunk, "index", None)
        tc_id = getattr(tc_chunk, "id", None) or ""
        if raw_idx is None:
            # 没 index：按 id 查 / 新建
            if tc_id and tc_id in id_to_idx:
                idx = id_to_idx[tc_id]
            else:
                idx = len(tool_calls_map)
                if tc_id:
                    id_to_idx[tc_id] = idx
        else:
            idx = int(raw_idx)
            if tc_id:
                id_to_idx.setdefault(tc_id, idx)
        if idx not in tool_calls_map:
            tool_calls_map[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
        if tc_id:
            tool_calls_map[idx]["id"] = tc_id
        func = getattr(tc_chunk, "function", None)
        if func:
            fn_name = getattr(func, "name", None)
            if fn_name:
                tool_calls_map[idx]["function"]["name"] = fn_name
            fn_args = getattr(func, "arguments", None)
            if fn_args:
                tool_calls_map[idx]["function"]["arguments"] += fn_args

    async def _aggregate_stream(self, response) -> Any:
        """
        聚合流式响应，同时收集文本内容和工具调用数据。
        每个 chunk 之间有 _STREAM_CHUNK_TIMEOUT 秒的超时，防止流挂起。
        返回：
          - 若有工具调用 → _AggregatedMessage 对象（含 tool_calls）
          - 否则 → 纯文本字符串
        """
        full_text = ""
        tool_calls_map: Dict[int, dict] = {}
        id_to_idx: Dict[str, int] = {}

        async def _next_chunk(aiter):
            return await asyncio.wait_for(aiter.__anext__(), timeout=_STREAM_CHUNK_TIMEOUT)

        aiter = response.__aiter__()
        while True:
            try:
                chunk = await _next_chunk(aiter)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"流式响应超时：{_STREAM_CHUNK_TIMEOUT}s 内未收到新数据，"
                    "请检查网络连接。"
                )

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "content", None):
                full_text += delta.content

            tc_list = getattr(delta, "tool_calls", None)
            if tc_list:
                for tc_chunk in tc_list:
                    self._merge_tool_chunk(tc_chunk, tool_calls_map, id_to_idx)

        if tool_calls_map:
            return _AggregatedMessage(full_text, tool_calls_map)
        return full_text

    async def _aggregate_stream_with_usage(self, response) -> tuple:
        """同 _aggregate_stream，但额外返回 (result, total_tokens) 元组。"""
        full_text = ""
        tool_calls_map: Dict[int, dict] = {}
        id_to_idx: Dict[str, int] = {}
        total_tokens = 0

        async def _next_chunk(aiter):
            return await asyncio.wait_for(aiter.__anext__(), timeout=_STREAM_CHUNK_TIMEOUT)

        aiter = response.__aiter__()
        while True:
            try:
                chunk = await _next_chunk(aiter)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"流式响应超时：{_STREAM_CHUNK_TIMEOUT}s 内未收到新数据，"
                    "请检查网络连接。"
                )

            if not chunk.choices:
                # usage-only chunk 也可能在末尾
                if hasattr(chunk, "usage") and chunk.usage:
                    total_tokens = getattr(chunk.usage, "total_tokens", 0) or 0
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "content", None):
                full_text += delta.content

            tc_list = getattr(delta, "tool_calls", None)
            if tc_list:
                for tc_chunk in tc_list:
                    self._merge_tool_chunk(tc_chunk, tool_calls_map, id_to_idx)

            if hasattr(chunk, "usage") and chunk.usage:
                total_tokens = getattr(chunk.usage, "total_tokens", 0) or 0

        if tool_calls_map:
            return _AggregatedMessage(full_text, tool_calls_map), total_tokens
        return full_text, total_tokens


llm_service = LLMService()
