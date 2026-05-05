import asyncio
import html as html_module
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from loguru import logger


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html_module.unescape(text).strip()


def _norm(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _fmt(query: str, items: list[dict[str, Any]], n: int) -> str:
    if not items:
        return f"No relevant results found for '{query}'."
    lines = [f"Search results for '{query}':\n"]
    for i, item in enumerate(items[:n], 1):
        title = _norm(_strip_tags(item.get("title", "")))
        snippet = _norm(_strip_tags(item.get("content", "")))
        url = item.get("url", "")
        lines.append(f"{i}. {title}")
        if url:
            lines.append(f"   Source: {url}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


def _is_search_failure(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return True
    return (
        normalized.startswith("[Search failed]")
        or normalized.startswith("[Browser search failed]")
        or normalized.startswith("No relevant results found")
        or "Missing dependency" in normalized
        or "configure web_search" in normalized
    )


def _get_config():
    try:
        from services.llm_service import llm_service  # noqa: PLC0415
        return llm_service._config.web_search
    except Exception:
        from models.config import WebSearchConfig  # noqa: PLC0415
        return WebSearchConfig()


def _make_httpx_client(proxy: str | None, timeout: float = 12.0) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout, connect=8.0),
        "trust_env": False,
        "follow_redirects": True,
        "headers": {"User-Agent": "Mozilla/5.0"},
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


def _find_browser_executable() -> str:
    explicit = os.environ.get("WEB_SEARCH_BROWSER_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "msedge",
    ]
    for candidate in candidates:
        if "/" in candidate and Path(candidate).exists():
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return ""


async def _browser_dump_dom(url: str) -> str:
    browser = _find_browser_executable()
    if not browser:
        raise RuntimeError("No available browser found. Install Chrome/Edge/Chromium or configure WEB_SEARCH_BROWSER_PATH.")

    tmp_dir = tempfile.mkdtemp(prefix="astudio-web-search-")
    try:
        proc = await asyncio.create_subprocess_exec(
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--virtual-time-budget=8000",
            "--timeout=12000",
            "--window-size=1280,900",
            f"--user-data-dir={tmp_dir}",
            "--dump-dom",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=6)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[-800:]
            raise RuntimeError(err or f"Browser exit code {proc.returncode}")
        return stdout.decode("utf-8", errors="replace")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _open_visible_browser(url: str) -> None:
    if os.environ.get("WEB_SEARCH_OPEN_BROWSER_ON_FAIL", "1").strip().lower() in {"0", "false", "no"}:
        raise RuntimeError("WEB_SEARCH_OPEN_BROWSER_ON_FAIL is disabled.")

    if sys.platform == "darwin":
        cmd = ["open", url]
    elif sys.platform.startswith("win"):
        cmd = ["cmd", "/c", "start", "", url]
    else:
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError("xdg-open was not found; cannot open a browser.")
        cmd = [opener, url]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc.wait(), timeout=5)


def _parse_browser_results(query: str, html: str, n: int) -> str:
    try:
        from lxml import html as lxml_html  # noqa: PLC0415
    except ImportError as e:
        return f"[Browser search failed] Missing dependency: {e}"

    tree = lxml_html.fromstring(html)
    items: list[dict[str, str]] = []

    # Bing: <li class="b_algo"><h2><a ...>...</a></h2><p>...</p></li>
    for result in tree.xpath("//li[contains(concat(' ', normalize-space(@class), ' '), ' b_algo ')]"):
        title = " ".join(result.xpath(".//h2/a//text()")).strip()
        urls = result.xpath(".//h2/a/@href")
        snippet = " ".join(result.xpath(".//p//text()")).strip()
        url = urls[0].strip() if urls else ""
        if title and url:
            items.append({"title": title, "url": url, "content": snippet})
        if len(items) >= n:
            break

    # Google fallback: anchor containing h3.
    if not items:
        for link in tree.xpath("//a[.//h3]"):
            title = " ".join(link.xpath(".//h3//text()")).strip()
            url = (link.get("href") or "").strip()
            if title and url.startswith("http"):
                items.append({"title": title, "url": url, "content": ""})
            if len(items) >= n:
                break

    return _fmt(query, items, n)


async def _browser_search(query: str, n: int) -> str:
    if os.environ.get("WEB_SEARCH_BROWSER_FALLBACK", "1").strip().lower() in {"0", "false", "no"}:
        return "[Browser search failed] WEB_SEARCH_BROWSER_FALLBACK is disabled."

    try:
        from tools.browser_search import browser_search  # noqa: PLC0415

        result = await browser_search(query, n)
        if not _is_search_failure(result):
            return result
        last_error = result
    except asyncio.CancelledError:
        raise
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
        logger.warning(f"browser_search tool fallback failed: {last_error}")

    if os.environ.get("WEB_SEARCH_BROWSER_CLI_FALLBACK", "1").strip().lower() in {"0", "false", "no"}:
        return last_error

    search_urls = [f"https://www.bing.com/search?q={quote_plus(query)}"]
    if os.environ.get("WEB_SEARCH_BROWSER_EXTRA_ENGINES", "").strip().lower() in {"1", "true", "yes"}:
        search_urls.append(f"https://www.google.com/search?q={quote_plus(query)}&hl=zh-CN")
    for url in search_urls:
        try:
            html = await _browser_dump_dom(url)
            result = _parse_browser_results(query, html, n)
            if not _is_search_failure(result):
                logger.info(f"browser web_search fallback succeeded via {url}")
                return "[Browser search fallback]\n" + result
            last_error = result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"browser web_search fallback failed via {url}: {last_error}")

    try:
        await _open_visible_browser(search_urls[0])
        return (
            "[Browser search opened]\n"
            f"Regular search and headless browser parsing both failed. Opened the search page in the local default browser: {search_urls[0]}\n"
            f"Original error: {last_error or 'No parseable results found'}"
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return f"[Browser search failed] {last_error or 'No parseable results found'}; opening a visible browser also failed: {type(e).__name__}: {e}"


# ── DuckDuckGo：primp.AsyncClient（Rust 原生 async，浏览器指纹，可正确取消）──────

async def _duckduckgo(query: str, n: int, proxy: str | None) -> str:
    """
    使用 primp.AsyncClient 进行 DuckDuckGo 搜索。

    primp 是 ddgs 底层使用的 Rust HTTP 客户端，支持浏览器指纹伪装，
    能绕过 DDG 的 bot 检测。AsyncClient 是原生 async，
    asyncio.wait_for 可正确取消，不会泄漏阻塞线程（原 asyncio.to_thread 方案的根本缺陷）。
    """
    try:
        import primp  # noqa: PLC0415
        from lxml import html as lxml_html  # noqa: PLC0415
    except ImportError as e:
        logger.error(f"缺少依赖: {e}，请检查 pyproject.toml")
        return f"[Search failed] Missing dependency: {e}"

    # 默认启用 TLS 校验，只有在显式设置 WEB_SEARCH_INSECURE=1 时关闭
    verify_tls = os.environ.get("WEB_SEARCH_INSECURE", "").strip() not in ("1", "true", "yes")
    try:
        async with primp.AsyncClient(
            impersonate="random",
            timeout=12,
            verify=verify_tls,
            proxy=proxy,
        ) as c:
            resp = await c.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "b": "", "l": "us-en"},
            )

        if resp.status_code != 200:
            logger.warning(f"DDG 返回非 200 状态: {resp.status_code}")
            return f"No relevant results found for '{query}'."

        # 使用 ddgs 相同的 XPath 提取结果
        tree = lxml_html.fromstring(resp.text)
        items_el = tree.xpath("//div[contains(@class, 'body')]")

        results: list[dict] = []
        for item in items_el:
            title_parts = item.xpath(".//h2//text()")
            hrefs = item.xpath("./a/@href")
            body_parts = item.xpath("./a//text()")

            title = " ".join("".join(title_parts).split())
            url = hrefs[0] if hrefs else ""
            body = " ".join("".join(body_parts).split())

            # 过滤 DDG 内部跳转链接
            if title and url and not url.startswith("https://duckduckgo.com/y.js?"):
                results.append({"title": title, "url": url, "content": body})

        if results:
            logger.debug(f"DDG primp 搜索成功，返回 {len(results)} 条结果")
            return _fmt(query, results, n)

        logger.warning("DDG primp 返回空结果")
        return f"No relevant results found for '{query}'."

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"DuckDuckGo 搜索失败: {type(e).__name__}: {e}")
        return (
            f"[Search failed] {type(e).__name__}: {e}\n"
            "Suggestion: configure web_search.provider as brave/tavily with an api_key in config.yaml, "
            "or configure web_search.proxy."
        )


# ── Provider 实现（带 API Key 的 provider 继续用 httpx）─────────────────────────

async def _brave(query: str, n: int, api_key: str, proxy: str | None) -> str:
    api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        logger.warning("BRAVE_API_KEY 未配置，降级到 DuckDuckGo")
        return await _duckduckgo(query, n, proxy)
    try:
        async with _make_httpx_client(proxy, timeout=10.0) as c:
            r = await c.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": n},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            )
            r.raise_for_status()
        items = [
            {"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("description", "")}
            for x in r.json().get("web", {}).get("results", [])
        ]
        return _fmt(query, items, n)
    except Exception as e:
        logger.warning(f"Brave 搜索失败 ({e})，降级到 DuckDuckGo")
        return await _duckduckgo(query, n, proxy)


async def _tavily(query: str, n: int, api_key: str, proxy: str | None) -> str:
    api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        logger.warning("TAVILY_API_KEY 未配置，降级到 DuckDuckGo")
        return await _duckduckgo(query, n, proxy)
    try:
        async with _make_httpx_client(proxy, timeout=15.0) as c:
            r = await c.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "max_results": n},
            )
            r.raise_for_status()
        return _fmt(query, r.json().get("results", []), n)
    except Exception as e:
        logger.warning(f"Tavily 搜索失败 ({e})，降级到 DuckDuckGo")
        return await _duckduckgo(query, n, proxy)


async def _searxng(query: str, n: int, base_url: str, proxy: str | None) -> str:
    base_url = (base_url or os.environ.get("SEARXNG_BASE_URL", "")).strip()
    if not base_url:
        logger.warning("SEARXNG_BASE_URL 未配置，降级到 DuckDuckGo")
        return await _duckduckgo(query, n, proxy)
    try:
        endpoint = f"{base_url.rstrip('/')}/search"
        async with _make_httpx_client(proxy, timeout=10.0) as c:
            r = await c.get(endpoint, params={"q": query, "format": "json"})
            r.raise_for_status()
        return _fmt(query, r.json().get("results", []), n)
    except Exception as e:
        logger.warning(f"SearXNG 搜索失败 ({e})，降级到 DuckDuckGo")
        return await _duckduckgo(query, n, proxy)


async def _jina(query: str, n: int, api_key: str, proxy: str | None) -> str:
    api_key = api_key or os.environ.get("JINA_API_KEY", "")
    if not api_key:
        logger.warning("JINA_API_KEY 未配置，降级到 DuckDuckGo")
        return await _duckduckgo(query, n, proxy)
    try:
        async with _make_httpx_client(proxy, timeout=15.0) as c:
            r = await c.get(
                "https://s.jina.ai/",
                params={"q": query},
                headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
        data = r.json().get("data", [])[:n]
        items = [
            {"title": d.get("title", ""), "url": d.get("url", ""), "content": d.get("content", "")[:500]}
            for d in data
        ]
        return _fmt(query, items, n)
    except Exception as e:
        logger.warning(f"Jina 搜索失败 ({e})，降级到 DuckDuckGo")
        return await _duckduckgo(query, n, proxy)


# ── 公共接口 ───────────────────────────────────────────────────────────────────

async def web_search(query: str, max_results: int = 5) -> str:
    """搜索网络，返回摘要文本。"""
    cfg = _get_config()
    provider = (cfg.provider or os.environ.get("WEB_SEARCH_PROVIDER", "duckduckgo")).strip().lower()
    api_key = cfg.api_key
    base_url = cfg.base_url
    proxy = cfg.proxy or os.environ.get("WEB_PROXY")
    n = min(max(max_results, 1), 10)

    logger.info(f"web_search: provider={provider} proxy={'set' if proxy else 'none'} query={query!r}")

    result: str
    if provider == "brave":
        result = await _brave(query, n, api_key, proxy)
    elif provider == "tavily":
        result = await _tavily(query, n, api_key, proxy)
    elif provider == "searxng":
        result = await _searxng(query, n, base_url, proxy)
    elif provider == "jina":
        result = await _jina(query, n, api_key, proxy)
    else:
        result = await _duckduckgo(query, n, proxy)

    if _is_search_failure(result):
        logger.warning("web_search normal provider failed or returned empty result, trying browser fallback")
        browser_result = await _browser_search(query, n)
        if not _is_search_failure(browser_result):
            return browser_result
        return f"{result}\n\n{browser_result}"
    return result


SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for information, suitable for current data, news, and documentation lookup. Falls back to local browser search when the normal provider fails.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords or question; be as specific as possible",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of returned results, default 5, maximum 10",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}
