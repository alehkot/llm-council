# AGENTS.md

This document serves as the authoritative guide for agentic coding agents (like Gemini CLI) operating within the `llm-council` repository. It defines the build processes, code style conventions, and operational workflows required to maintain the codebase's integrity.

## 1. Project Overview

LLM Council is a 3-stage deliberation system where multiple LLMs collaboratively answer user questions through anonymized peer review and synthesis.

### Workflow

1.  **Stage 1: Individual Responses**: All council models answer the query in parallel.
2.  **Negative Mode (Optional)**: Models evaluate responses for gaps, risks, and edge cases.
3.  **Stage 2: Anonymized Peer Review**: Models evaluate and rank all anonymized responses.
4.  **Deliberation (Optional)**: Models can revise their answers based on peer feedback.
5.  **Stage 3: Synthesis**: A Chairman model synthesizes the final answer using all prior context.

## 2. Technology Stack

### Backend (`backend/`)

-   **Runtime**: Python 3.10+
-   **Framework**: FastAPI with Uvicorn
-   **Package Manager**: `uv` (Astral)
-   **Workflow Engine**: LangGraph
-   **Storage**: JSON files in `data/conversations/`
-   **Concurrency**: `consultation_manager.py` handles an async thread pool for long-running consultations.

### Frontend (`frontend/`)

-   **Framework**: React 19
-   **Build Tool**: Vite 7.x
-   **Styling**: Tailwind CSS 4.x
-   **State Management**: Zustand (`store/useAppStore.js`) + React Hooks
-   **Communication**: Long-poll state synchronization (SSE removed). The status
    endpoints return full snapshots with a change cursor; clients send the cursor
    back and the server holds the request until the snapshot changes or the wait
    window lapses, so a dropped request never loses state.

## 3. Project Structure

```
llm-council/
├── backend/                    # Python FastAPI backend
│   ├── main.py                # FastAPI entry point & API routes
│   ├── config.py              # Environment configuration
│   ├── council.py             # Deliberation & Synthesis logic
│   ├── consultation_manager.py # Async task orchestration
│   ├── langgraph_flow.py      # LangGraph workflow definition
│   └── storage.py             # JSON file persistence
├── frontend/                   # React + Vite frontend
│   ├── src/
│   │   ├── App.jsx            # Main app orchestration
│   │   ├── store/             # Zustand store for state
│   │   ├── components/        # React components (Stage1-3, etc.)
│   │   └── ui/                # Shadcn-like UI primitives
├── data/                      # Local JSON storage
└── scripts/                   # Utility scripts
```

## 4. Build and Development Commands

### Backend

Always use `uv`. Never run from inside the `backend/` directory; run from the root.

```bash
# Install dependencies
uv sync

# Run development server
uv run python -m backend.main

# Run utility scripts
uv run python scripts/generate_api_key.py

# Format & lint (Ruff)
uv run ruff format .
uv run ruff check --fix .

# Run tests
uv run python -m unittest discover tests
```

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev

# Lint & Build
npm run lint
npm run build

# Format (Biome)
npm run format        # write fixes
npm run format:check  # verify only
```

## 5. Code Style Guidelines

### Python (Backend)

-   **Formatting/Linting**: Ruff is the source of truth (`uv run ruff format .` and
    `uv run ruff check --fix .`); config lives in `pyproject.toml` (rules: E, F, I).
-   **Imports**: Relative imports within the `backend` package (e.g., `from .config import ...`);
    Ruff keeps import blocks sorted.
-   **Typing**: Mandatory type hints for all function signatures.
-   **Async**: All route handlers and external I/O must be `async`.

### JavaScript/React (Frontend)

-   **Formatting**: Biome is the source of truth (`npm run format`); config lives in
    `frontend/biome.json` (tabs, double quotes). CSS is excluded - Tailwind 4 syntax
    is not standard CSS. ESLint remains the linter (`npm run lint`).
-   **Components**: Functional components with Hooks.
-   **State**: Prefer Zustand for global state, `useState` for local component state.
-   **Styling**: Tailwind CSS utility classes. Avoid complex inline styles.
-   **Naming**: PascalCase for files and components, camelCase for variables and functions.

## 6. Development Mandates

### Tool Usage
-   **Absolute Paths**: Always use absolute paths for file operations.
-   **Verification**: Never assume a change works without running `npm run lint` (frontend) or starting the backend server to check for syntax/import errors.

### Commit Protocol
-   **Atomic Changes**: One logical change per commit.
-   **Git State**: Never stage or commit unless explicitly asked. Gather `git status` and `git diff` before proposing a commit.

### Security
-   **Secrets**: Never commit `.env` or log API keys.
-   **Authentication**: All API calls require `X-API-Key`.

## 7. Mandatory Post-Update Checks

After modifying code, you **MUST** execute the following:

**Frontend:**
```bash
cd frontend && npm run format:check && npm run lint && npm run build
```

**Backend:**
```bash
uv run ruff format --check . && uv run ruff check .
uv run python -m unittest discover tests
uv run python -m backend.main  # Run briefly to check for startup errors
```

## 8. Common Gotchas

1.  **Relative Imports**: Running `python backend/main.py` directly will fail. Use `uv run python -m backend.main`.
2.  **ScrollArea Bug**: `ScrollArea` (Radix) can fail in flex containers within Modals. Use `overflow-y-auto` as a fallback for reliable scrolling in Dialogs.
3.  **Polling**: The frontend uses polling to check consultation status. Ensure `consultation_manager.get_status()` returns accurate state.
4.  **Metadata Persistence**: Some metadata is ephemeral (in-memory) during a run, but critical stages (stage1, stage2, stage3) are persisted to JSON.
