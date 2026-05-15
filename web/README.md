# AStudio Web Frontend

This package contains the React, TypeScript, and Vite frontend for AStudio. It is designed to run either against the local FastAPI backend during development or inside the Electron shell.

## Development

From the repository root, install all dependencies first:

```bash
pnpm setup
```

Start the full development environment with both the backend and frontend:

```bash
pnpm dev
```

Open the web UI at http://127.0.0.1:5173. The Vite dev server expects the backend API to be available at http://127.0.0.1:8000.

To run only the frontend package:

```bash
pnpm --dir web dev
```

## Scripts

Run these commands from the repository root unless noted otherwise.

| Command | Description |
| --- | --- |
| `pnpm --dir web dev` | Start the Vite dev server |
| `pnpm --dir web build` | Type-check and build the frontend |
| `pnpm --dir web lint` | Run ESLint for the frontend |
| `pnpm --dir web preview` | Preview the production build locally |
| `pnpm electron:dev` | Start Electron with the Vite dev server |
| `pnpm electron:start` | Build the frontend, then start Electron |

## Project Layout

| Path | Purpose |
| --- | --- |
| `src/api/` | API and SSE clients |
| `src/components/` | Shared UI and task-specific components |
| `src/i18n/` | Locale dictionaries and translation helpers |
| `src/pages/` | Route-level pages |
| `src/stores/` | Zustand stores for client state |
| `electron/` | Electron main and preload scripts |
| `public/` | Static assets served by Vite |

## Build Output

Production assets are written to `web/dist/`. The root `pnpm start` command builds this package and serves the result through FastAPI.
