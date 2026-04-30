# Desktop Distribution Plan

AStudio is a local-first agent application. The distribution goal is simple:
non-technical users should download one installer from GitHub Releases, open it,
configure a model provider, and start using the app without running terminal commands.

## Current State

The repository currently supports an Electron sidecar preview:

- `web/electron/main.cjs` starts or reuses the local FastAPI backend.
- In packaged mode, Electron first looks for a backend sidecar under `resources/server-bin/astudio-server`.
- If the sidecar is missing, Electron falls back to `uv run uvicorn` for developer preview packages.
- The backend can read mutable data from `ASTUDIO_DATA_DIR`, or from `ASTUDIO_USER_DATA_DIR/data`.
- Packaged Electron uses a free local port by default unless `ASTUDIO_SERVER_PORT` is explicitly set.
- `electron-builder.yml` can package macOS, Windows, and Linux desktop artifacts.
- `.github/workflows/release.yml` builds the backend sidecar, smoke-tests `/api/health`, packages Electron, and uploads artifacts to a draft prerelease.

This means the current release path is suitable for early public testing. It is close to one-click installation, but should stay prerelease until signing, notarization, update behavior, and cross-platform artifact checks are complete.

## Recommended Path

Keep Electron as the primary distribution path.

Electron is not the smallest option, but it is the lowest-risk option for AStudio right now because:

- the existing frontend already runs in Chromium-class browsers;
- the app needs a stable desktop shell, file access, local process management, and logs;
- GitHub Releases plus `electron-builder` already covers `.dmg`, `.exe`, and `.AppImage`;
- future features such as auto-update, tray, crash logs, and file associations are straightforward.

The main cost is package size. That cost is acceptable for the first public desktop release because AStudio is a power-user local agent tool, not a small utility.

## Release Stages

### Stage 1: Developer Preview

Goal: produce downloadable packages from GitHub Releases for technical users.

Requirements:

- Build frontend into `web/dist`.
- Package Electron shell and server source.
- Start backend with `uv run uvicorn`.
- Mark GitHub Release as draft and prerelease.
- Document that users need `uv` installed.

This stage is already covered by the developer preview path.

### Stage 2: Real One-Click Desktop

Goal: no Node, pnpm, Python, or uv required on the user's machine.

Required implementation:

- Build a backend sidecar binary for each platform.
  - Candidate tools: PyInstaller first; Nuitka later if startup or size becomes a problem.
  - Output examples:
    - macOS: `server-bin/astudio-server`
    - Windows: `server-bin/astudio-server.exe`
    - Linux: `server-bin/astudio-server`
  - The Python runtime architecture must match the Electron artifact architecture.
    - An arm64 macOS app needs an arm64 backend sidecar.
    - An x64 macOS app needs an x64 backend sidecar.
    - Do not package a locally generated x64 sidecar into an arm64 app.
- Put the sidecar binary into Electron `extraResources`.
- Change Electron startup:
  - in development, continue using `uv run uvicorn`;
  - in packaged mode, start the bundled sidecar binary;
  - write logs to `app.getPath('userData')/logs/backend.log`.
- Move runtime data into Electron `userData`.
  - The packaged app must not write databases, config, logs, sandboxes, or uploads into the installed app directory.
  - The backend reads `ASTUDIO_DATA_DIR`, or `ASTUDIO_USER_DATA_DIR/data`.
- Use dynamic or reserved local ports.
  - Packaged Electron uses a free port by default.
  - Development mode still works through the Vite proxy on `127.0.0.1:8000`.
- Add smoke tests in CI:
  - boot backend sidecar;
  - call `/api/health`;
  - verify the packaged Electron artifact exists.

The sidecar startup path, `userData` data directory override, dynamic packaged ports, and CI smoke test are already wired. The remaining work is hardening this across all target platforms and removing platform-specific packaging risks.

Playwright Chromium should not be bundled in the first true one-click build unless required. It adds a lot of size. Prefer this order:

1. normal web search provider;
2. system Chrome/Edge/Chromium fallback;
3. optional bundled Playwright browser as a later "full" build.

### Stage 3: Trusted Public Distribution

Goal: reduce OS security warnings and support upgrades.

Requirements:

- macOS Developer ID signing and notarization.
- Windows code signing.
- Auto-update through GitHub Releases or a custom update feed.
- Export logs from the app UI.
- Add "reset local data" and "open data directory" actions.

## Alternatives

### Tauri

Pros:

- much smaller shell than Electron;
- uses system webview instead of bundled Chromium;
- good long-term fit for polished desktop apps.

Cons for AStudio right now:

- still needs the same Python backend sidecar problem solved;
- introduces Rust toolchain and platform webview differences;
- higher migration cost for no immediate product advantage.

Verdict: worth revisiting later, not better than Electron for the first open-source release.

### PyInstaller/Nuitka + Local Browser

Pros:

- smaller than Electron;
- backend packaging work directly solves the hardest part;
- can open `http://127.0.0.1:<port>` in the user's default browser.

Cons:

- less app-like;
- browser lifecycle is harder to control;
- no integrated window, tray, auto-update, or native desktop affordances by default.

Verdict: good fallback distribution for advanced users, not the best primary UX.

### Docker Desktop / Docker Compose

Pros:

- strong runtime isolation;
- predictable backend dependencies;
- easy reset for developers.

Cons:

- Docker Desktop is a large prerequisite;
- poor fit for non-technical users;
- local file permissions and browser automation can be awkward.

Verdict: useful for contributors or self-hosting, not a one-click desktop path.

### PWA / Local Web App

Pros:

- simplest frontend distribution;
- no desktop shell.

Cons:

- still requires users to install and run the backend;
- weak local process management;
- less suitable for sandbox and file workflows.

Verdict: not enough for the "download and use" goal.

## Decision

Use Electron for the public desktop app.

The immediate next engineering target is hardening the sidecar release in CI: verify macOS/Windows/Linux artifacts, then add signing and notarization. After that, GitHub Releases can become the main download channel.
