# AStudio Server

Python 后端服务，基于 FastAPI 构建。

## 安装

```bash
cd server
uv sync
uv run python -m playwright install chromium
```

## 运行

```bash
uv run uvicorn main:app --reload --port 8000
```

`web_search` 会先走配置的搜索 provider。普通搜索失败时，会自动降级到内置
`browser_search`，使用 Playwright Chromium 打开搜索结果页并提取标题、链接和摘要。
