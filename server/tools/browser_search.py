import asyncio
import base64
import os
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from loguru import logger

from tools.web_search import _fmt


async def _launch_browser():
    try:
        from playwright.async_api import async_playwright  # noqa: PLC0415
    except ImportError:
        return None, None, (
            "[Browser search failed] Playwright is optional and is not installed. "
            "Run `pnpm setup:browser`, or install a system Chrome/Edge/Chromium browser for web_search fallback."
        )

    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
            ],
        )
        return playwright, browser, ""
    except Exception as e:
        await playwright.stop()
        return None, None, (
            f"[Browser search failed] Chromium launch failed: {type(e).__name__}: {e}\n"
            "Run `pnpm setup:browser`, or install/configure Chrome/Edge/Chromium for web_search fallback."
        )


def _decode_bing_url(url: str) -> str:
    parsed = urlparse(url)
    if "bing.com" not in parsed.netloc or not parsed.path.startswith("/ck/"):
        return url
    raw = parse_qs(parsed.query).get("u", [""])[0]
    if not raw.startswith("a1"):
        return url
    encoded = raw[2:]
    encoded += "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        return url


async def _extract_bing(page, n: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    results = page.locator("li.b_algo")
    count = await results.count()
    for i in range(count):
        result = results.nth(i)
        link = result.locator("h2 a").first
        if await link.count() == 0:
            continue
        title = ((await link.text_content()) or "").strip()
        url = _decode_bing_url((await link.get_attribute("href")) or "")
        snippet = ""
        snippet_locator = result.locator("p").first
        if await snippet_locator.count() > 0:
            snippet = ((await snippet_locator.text_content()) or "").strip()
        if title and url:
            items.append({"title": title, "url": url, "content": snippet})
        if len(items) >= n:
            break
    return items


async def _extract_google(page, n: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    links = page.locator("a:has(h3)")
    count = await links.count()
    for i in range(count):
        link = links.nth(i)
        title = ((await link.locator("h3").first.text_content()) or "").strip()
        url = (await link.get_attribute("href")) or ""
        if title and url.startswith("http"):
            items.append({"title": title, "url": url, "content": ""})
        if len(items) >= n:
            break
    return items


async def browser_search(query: str, max_results: int = 5) -> str:
    n = min(max(max_results, 1), 10)
    if os.environ.get("BROWSER_SEARCH_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
        return "[Browser search failed] BROWSER_SEARCH_DISABLED is enabled."

    playwright, browser, error = await _launch_browser()
    if error:
        return error
    assert playwright is not None and browser is not None

    engines = [
        ("bing", query, f"https://www.bing.com/search?q={quote_plus(query)}&cc=US&setlang=en&ensearch=1"),
    ]
    if os.environ.get("BROWSER_SEARCH_EXTRA_ENGINES", "").strip().lower() in {"1", "true", "yes"}:
        engines.append(("google", query, f"https://www.google.com/search?q={quote_plus(query)}&hl=zh-CN"))

    last_error = ""
    try:
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
        )
        page = await context.new_page()
        page.set_default_timeout(8_000)
        collected: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for engine, search_query, url in engines:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                items = await (_extract_google(page, n) if engine == "google" else _extract_bing(page, n))
                for item in items:
                    item_url = item.get("url", "")
                    if not item_url or item_url in seen_urls:
                        continue
                    seen_urls.add(item_url)
                    collected.append(item)
                if len(collected) >= n:
                    return "[Browser search]\n" + _fmt(query, collected, n)
                last_error = _fmt(search_query, items, n)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(f"browser_search failed via {engine}: {last_error}")
        if collected:
            return "[Browser search]\n" + _fmt(query, collected, n)
        return f"[Browser search failed] {last_error or 'No parseable results found'}"
    finally:
        await browser.close()
        await playwright.stop()


SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_search",
        "description": "Use the built-in browser to search the web and return titles, links, and snippets. Suitable as fallback when the normal web_search provider fails.",
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
