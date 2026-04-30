# Contributing

Thanks for taking time to improve AStudio.

## Local setup

```bash
corepack enable
pnpm setup
pnpm dev
```

Use `pnpm start` for the stable local web runtime and `pnpm electron:start` for the local Electron runtime.

## Configuration

Runtime data and secrets live in `data/` and are ignored by git. To start from an example config:

```bash
mkdir -p data
cp config.example.yaml data/config.yaml
```

Then edit `data/config.yaml` or use the settings UI.

## Checks

Run the focused checks before opening a pull request:

```bash
pnpm --dir web lint
pnpm build:web
cd server
uv run ruff check .
```

## Pull requests

- Keep changes scoped to one behavior or feature.
- Do not commit local task data, API keys, databases, logs, or generated builds.
- Include screenshots or short recordings for UI changes.
- Mention any migration or local data compatibility impact.
