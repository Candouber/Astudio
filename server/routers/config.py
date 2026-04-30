import asyncio
import os
import queue
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from models.config import (
    LEGACY_PROVIDER_ALIASES,
    AppConfig,
    canonical_litellm_model_id,
    normalize_config,
    resolve_litellm_slug,
)
from storage.config_store import ConfigStore

router = APIRouter()
store = ConfigStore()

_oauth_state: dict[str, dict] = {}
_oauth_locks: dict[str, threading.Lock] = {}
_oauth_manual_queues: dict[str, queue.Queue] = {}

SUPPORTED_OAUTH_PROVIDERS = {"chatgpt", "github_copilot"}
SUPPORTED_OAUTH_PROVIDER_NAMES = SUPPORTED_OAUTH_PROVIDERS | set(LEGACY_PROVIDER_ALIASES.keys())


def _get_lock(provider_name: str) -> threading.Lock:
    if provider_name not in _oauth_locks:
        _oauth_locks[provider_name] = threading.Lock()
    return _oauth_locks[provider_name]


def _canonicalize_provider_name(provider_name: str) -> str:
    return LEGACY_PROVIDER_ALIASES.get(provider_name, provider_name)


def _read_chatgpt_auth_status() -> dict[str, Optional[str]]:
    from litellm.llms.chatgpt.authenticator import Authenticator

    auth = Authenticator()
    auth_data = auth._read_auth_file()
    if not auth_data:
        return {"status": "not_started", "account_id": None, "error": None}

    access_token = auth_data.get("access_token")
    refresh_token = auth_data.get("refresh_token")
    account_id = auth_data.get("account_id") or auth.get_account_id()

    if access_token and not auth._is_token_expired(auth_data, access_token):
        return {"status": "authenticated", "account_id": account_id, "error": None}

    if refresh_token:
        return {"status": "authenticated", "account_id": account_id, "error": None}

    return {
        "status": "failed",
        "account_id": account_id,
        "error": "Saved ChatGPT OAuth token is expired and missing refresh_token.",
    }


def _revoke_chatgpt_auth() -> None:
    from litellm.llms.chatgpt.authenticator import Authenticator

    auth = Authenticator()
    if os.path.exists(auth.auth_file):
        os.remove(auth.auth_file)

    try:
        os.rmdir(auth.token_dir)
    except OSError:
        pass

    try:
        from oauth_cli_kit import OPENAI_CODEX_PROVIDER
        from oauth_cli_kit.storage import FileTokenStorage

        legacy_token_path = FileTokenStorage(
            token_filename=OPENAI_CODEX_PROVIDER.token_filename
        ).get_token_path()
        if legacy_token_path.exists():
            legacy_token_path.unlink()
    except Exception:
        pass


def _run_oauth_in_background(provider_name: str):
    provider_name = _canonicalize_provider_name(provider_name)
    try:
        if provider_name == "chatgpt":
            from litellm.llms.chatgpt.authenticator import Authenticator
            from oauth_cli_kit import login_oauth_interactive

            _oauth_manual_queues[provider_name] = queue.Queue()

            def custom_print(message: str) -> None:
                print(message, flush=True)
                if "http://" in message or "https://" in message:
                    with _get_lock(provider_name):
                        if provider_name in _oauth_state:
                            _oauth_state[provider_name]["auth_url"] = message

            def custom_prompt(_prompt_msg: str) -> str:
                try:
                    return _oauth_manual_queues[provider_name].get(timeout=300)
                except queue.Empty:
                    return ""

            token = login_oauth_interactive(
                print_fn=custom_print,
                prompt_fn=custom_prompt,
            )

            auth = Authenticator()
            auth._write_auth_file(
                {
                    "access_token": token.access,
                    "refresh_token": token.refresh,
                    "expires_at": int(token.expires / 1000),
                    "account_id": getattr(token, "account_id", None),
                }
            )

            with _get_lock(provider_name):
                _oauth_state[provider_name] = {
                    "status": "authenticated",
                    "account_id": getattr(token, "account_id", None),
                    "error": None,
                }
        elif provider_name == "github_copilot":
            # GitHub Copilot 使用 litellm 内置的 github_copilot OAuth 流
            import subprocess
            import sys
            result = subprocess.run(
                [sys.executable, "-c", "import litellm; litellm.utils.get_github_copilot_token()"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                with _get_lock(provider_name):
                    _oauth_state[provider_name] = {
                        "status": "authenticated",
                        "account_id": None,
                        "error": None,
                    }
            else:
                raise RuntimeError(result.stderr or "Unknown error")
        else:
            raise ValueError(f"Unsupported OAuth provider: {provider_name}")
    except Exception as e:
        with _get_lock(provider_name):
            _oauth_state[provider_name] = {
                "status": "failed",
                "account_id": None,
                "error": str(e),
            }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/")
async def get_config() -> AppConfig:
    """获取应用配置"""
    return await store.load()


@router.put("/")
async def update_config(config: AppConfig) -> AppConfig:
    """更新应用配置"""
    from services.llm_service import llm_service

    config = normalize_config(config)
    await store.save(config)
    llm_service.reload_config()
    return config


class ProviderTestRequest(BaseModel):
    name: str
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    test_model: str
    litellm_provider: Optional[str] = None
    model_aliases: dict[str, str] = Field(default_factory=dict)


@router.post("/test")
async def test_provider_connection(req: ProviderTestRequest):
    """测试给定供应商配置能否成功接通大模型"""
    try:
        from litellm import completion

        slug = resolve_litellm_slug(req.name, req.litellm_provider)
        test_model = req.test_model.strip()
        alias = test_model.split("/", 1)[1] if test_model.startswith(f"{req.name}/") else test_model
        target = req.model_aliases.get(test_model) or req.model_aliases.get(alias)
        if target:
            test_model = canonical_litellm_model_id(target, slug)
        elif test_model.startswith(f"{req.name}/") and req.name != slug:
            test_model = f"{slug}/{test_model.split('/', 1)[1]}"
        # OAuth 供应商不走此处的 Key/Base 连通性探测；模型 id 常为完整前缀
        if slug not in SUPPORTED_OAUTH_PROVIDER_NAMES and "/" not in test_model:
            test_model = f"{slug}/{test_model}"

        kwargs = {
            "model": test_model,
            "messages": [{"role": "user", "content": "Hello! Reply 'OK' if you receive this."}],
            "max_tokens": 5,
        }

        if req.api_key:
            kwargs["api_key"] = req.api_key
        if req.endpoint:
            kwargs["base_url"] = req.endpoint

        safe_kwargs = {
            key: ("[redacted]" if key in {"api_key", "headers"} else value)
            for key, value in kwargs.items()
        }
        logger.info(f"Testing litellm connection with args: {safe_kwargs}")

        response = completion(**kwargs)

        return {"success": True, "message": "Connection successful", "reply": response.choices[0].message.content}
    except Exception as e:
        logger.warning(f"Connection test error: {e}")
        return {"success": False, "message": str(e)}


@router.post("/oauth/{provider_name}/initiate")
async def initiate_oauth(provider_name: str):
    """
    启动 OAuth 登录流程。
    该端点立刻返回 {"status": "pending"}，
    并在后台线程中运行授权流程（浏览器将自动弹出）。
    使用 GET /oauth/{provider_name}/status 轮询结果。
    """
    if provider_name not in SUPPORTED_OAUTH_PROVIDER_NAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider_name}")

    provider_name = _canonicalize_provider_name(provider_name)

    if provider_name == "chatgpt":
        try:
            import oauth_cli_kit  # noqa: F401
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="oauth-cli-kit package is not installed. Run: pip install oauth-cli-kit"
            )

    with _get_lock(provider_name):
        current = _oauth_state.get(provider_name, {})
        if current.get("status") == "pending":
            return {"status": "pending", "message": "OAuth flow already in progress"}
        # 重置状态并发起新的授权流
        _oauth_state[provider_name] = {"status": "pending", "account_id": None, "error": None, "auth_url": None}

    # 在后台线程运行阻塞式 OAuth 流，避免阻塞 FastAPI 事件循环
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_oauth_in_background, provider_name)

    return {"status": "pending", "message": "OAuth flow started. Please complete authorization in your browser."}


@router.get("/oauth/{provider_name}/status")
async def get_oauth_status(provider_name: str):
    """
    查询 OAuth 流程状态。
    返回 {"status": "pending"|"authenticated"|"failed"|"not_started", "account_id": str | None}
    """
    if provider_name not in SUPPORTED_OAUTH_PROVIDER_NAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider_name}")

    provider_name = _canonicalize_provider_name(provider_name)

    with _get_lock(provider_name):
        state = _oauth_state.get(provider_name)

    if state:
        return {
            "status": state["status"],
            "account_id": state.get("account_id"),
            "error": state.get("error"),
            "auth_url": state.get("auth_url"),
        }

    if provider_name == "chatgpt":
        return _read_chatgpt_auth_status()

    return {"status": "not_started", "account_id": None, "error": None}


@router.delete("/oauth/{provider_name}/revoke")
async def revoke_oauth(provider_name: str):
    """撤销 OAuth 授权（清除本地 Token 缓存）"""
    if provider_name not in SUPPORTED_OAUTH_PROVIDER_NAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider_name}")

    provider_name = _canonicalize_provider_name(provider_name)

    with _get_lock(provider_name):
        _oauth_state.pop(provider_name, None)

    if provider_name == "chatgpt":
        await asyncio.to_thread(_revoke_chatgpt_auth)

    return {"success": True, "message": f"{provider_name} OAuth token revoked"}


class OAuthCallbackPayload(BaseModel):
    url: str

@router.post("/oauth/{provider_name}/callback")
async def oauth_callback(provider_name: str, payload: OAuthCallbackPayload):
    """手动提交流程的回调 URL"""
    if provider_name not in SUPPORTED_OAUTH_PROVIDER_NAMES:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider_name}")

    provider_name = _canonicalize_provider_name(provider_name)

    q = _oauth_manual_queues.get(provider_name)
    if not q:
        raise HTTPException(status_code=400, detail="No active OAuth flow found to accept this callback")

    q.put(payload.url)
    return {"success": True, "message": "Callback submitted"}
