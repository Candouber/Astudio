# AStudio Server

Python 后端服务，基于 FastAPI 构建。

## 安装

```bash
cd server
uv sync
```

Playwright browser search is optional. Install it only if you want the built-in
browser_search tool instead of relying on a system Chrome/Edge/Chromium fallback:

```bash
uv sync --extra browser
uv run python -m playwright install chromium
```

## 运行

```bash
uv run uvicorn main:app --reload --port 8000
```

`web_search` 会先走配置的搜索 provider。普通搜索失败时，会先尝试可选的
Playwright `browser_search`；如果没有安装 Playwright，会尝试调用系统里的
Chrome / Edge / Chromium headless CLI 解析搜索结果。
