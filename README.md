# LLM Council

A 3-stage deliberation system where multiple LLMs collaboratively answer
questions through anonymized peer review and synthesis.

Inspired by [karpathy/llm-council](https://github.com/karpathy/llm-council).

![THE LLM COUNCIL OF ELDERS CONVENES TO DECIDE IF 'CHEESEBURGER' IS A VEGETABLE. THEY'RE STILL DEBATING](header.jpg)

## Overview

LLM Council orchestrates multiple large language models to provide more
thoughtful, balanced answers. The key innovation is **anonymized peer review**
in Stage 2—models evaluate responses without knowing which model generated
them, preventing favoritism and ensuring honest assessment.

## How It Works

```text
User Query
    ↓
┌─────────────────────────────────────────────────────────┐
│ Stage 0: Prompt Refinement (Optional)                   │
│ Iteratively clarify intent and revise user prompt       │
└─────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────┐
│ Stage 1: Individual Responses                           │
│ All council models answer the query in parallel         │
└─────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────┐
│ Stage 1.5: Extension Modes (Optional)                   │
│ - Negative Mode: Identify gaps and blind spots          │
│ - Deliberation: Models revise answers based on feedback │
│ - Interactive Response Review                           │
└─────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────┐
│ Stage 2: Anonymized Peer Review                         │
│ Responses anonymized as "Response A, B, C..."           │
│ Each model evaluates and ranks all responses            │
│ Aggregate rankings calculated                           │
└─────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────┐
│ Stage 3: Synthesis                                      │
│ Chairman model synthesizes final answer. Options:       │
│ - Synthesis Prefs (Optional): User selects mode         │
│ - Combines responses, rankings, and negative evaluation │
└─────────────────────────────────────────────────────────┘
    ↓
Final Answer
```

## Features

- **Multi-model consensus**: Leverage diverse perspectives from GPT, Claude,
  Gemini, Grok, and more.
- **Anonymized evaluation**: Unbiased peer review without model identification.
- **Invalid ranking handling**: Peer orders marked invalid by Stage 2 (for example,
  unknown labels, including labels that only share a prefix with a valid response,
  a truncated order missing a response) are shown to the chairman as invalid,
  not as usable ranking lines, and do not contribute deliberation position counts.
  Where a synthesis mode includes reviewer rationale, an invalid review's excerpt
  remains available but is explicitly labeled as rationale only. Valid rankings,
  aggregate voting rules, and legacy reviews without validation metadata are unchanged.
- **Transparent reasoning**: View raw evaluations, debug traces, and validate ranking
  extraction.
- **Provider cooldowns**: Retryable provider errors honor valid `Retry-After`
  seconds or HTTP dates before retrying. The configured retry count is unchanged;
  `OPENROUTER_RETRY_MAX_SECONDS` caps exponential backoff, not a longer provider
  cooldown. Stop can interrupt the wait. Missing or invalid hints use normal backoff.
- **Graceful degradation**: Continues with successful responses if some models
  fail. A completed synthesis can still be re-run (one or all modes) when every
  peer reviewer failed; reruns use the saved responses without peer rankings.
  Empty or whitespace-only provider completions use the existing bounded retry
  budget; if all attempts fail, deliberation keeps the original answer instead
  of replacing it with blank text. Nonblank string formatting is preserved.
- **Polling-based sync**: Robust state synchronization between frontend and backend.
  Switching consultations while a message is being submitted keeps the selected
  consultation’s progress, queue, and errors separate. Late submission results
  update only the original conversation’s cached messages; returning to it
  reloads its status and queue from the backend.
- **Cancellation**: Stopping a consultation cancels its local in-flight async work
  and clears queued messages, freeing the worker for a new request. This does not
  guarantee that an already-submitted provider request stops or avoids billing.
- **Conversation history**: Persistent JSON storage with full deliberation records.
- **Durable recovery**: Restart resumes unfinished consultations. Jobs whose
  conversation file is missing or unreadable are skipped without blocking other
  conversations. Their journals are retained; repair or restore the conversation
  file and restart to retry recovery. Deleted conversations are not recreated.
- **Markdown Export**: Export full conversation context and reasoning to Markdown.
- **Focus View**: Dedicated modal for deep reading of the synthesized answer.

### Advanced Features (Optional)

- **Negative Mode**: Models identify gaps, risks, and blind spots in responses.
- **Deliberation**: Models revise their answers after seeing peer feedback.
- **Interactive Mode**: Inline prompts for user guidance during processing.
- **Prompt Refinement**: Iteratively refine your initial query for better results.
  Re-running one or all synthesis modes preserves the accepted refined prompt;
  choosing the original prompt keeps reruns on that original question.

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- [OpenRouter API key](https://openrouter.ai/)

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/yourusername/llm-council.git
   cd llm-council
   ```

2. Configure environment:

   ```bash
   cp .env.sample .env
   # Edit .env and add your OPENROUTER_API_KEY and API_AUTH_KEY
   ```

3. Install dependencies:

   ```bash
   # Backend
   uv sync

   # Frontend
   cd frontend
   npm install
   cd ..
   ```

4. Start the application:

   ```bash
   ./start.sh
   ```

   Or run separately:

   ```bash
   # Terminal 1 - Backend (port 8001)
   uv run python -m backend.main

   # Terminal 2 - Frontend (port 5173)
   cd frontend && npm run dev
   ```

5. Open <http://localhost:5173>

## Configuration

All configuration is managed via `.env`. See `.env.sample` for full options.

### Required

| Variable             | Description                  |
|----------------------|------------------------------|
| `OPENROUTER_API_KEY` | Your OpenRouter API key      |
| `API_AUTH_KEY`       | Local API authentication key |

### Models

| Variable          | Description              | Default                    |
|-------------------|--------------------------|----------------------------|
| `COUNCIL_MODELS`  | Comma-separated model IDs| GPT-4o, Gemini-1.5, Claude-3.5, etc. |
| `CHAIRMAN_MODEL`  | Synthesis model          | google/gemini-pro-1.5      |
| `TITLE_MODEL`     | Title generation model   | google/gemini-flash-1.5    |

### Feature Flags

| Variable                   | Description                    | Default |
|----------------------------|--------------------------------|---------|
| `NEGATIVE_MODE_ENABLED`    | Enable gap-finding evaluations | false   |
| `DELIBERATION_ENABLED`     | Enable revised responses       | false   |
| `INTERACTIVE_MODE_ENABLED` | Enable inline user prompts     | false   |
| `PROMPT_REFINEMENT_ENABLED`| Enable query refinement stage  | false   |

## Project Structure

```text
llm-council/
├── backend/                 # Python FastAPI backend
│   ├── main.py             # FastAPI app entry point & routes
│   ├── config.py           # Configuration management
│   ├── council.py          # Deliberation & Synthesis logic
│   ├── langgraph_flow.py   # LangGraph workflow orchestration
│   ├── consultation_manager.py # Async task orchestration
│   └── storage.py          # JSON conversation persistence
├── frontend/               # React + Vite + Tailwind
│   ├── src/
│   │   ├── App.jsx         # Main orchestration
│   │   ├── store/          # Zustand global state
│   │   ├── api.js          # Backend API client
│   │   └── components/     # UI components (Stage1-3, etc.)
│   └── package.json
├── data/                   # Conversation storage
│   └── conversations/      # JSON files per conversation
├── AGENTS.md               # Development guidelines
├── start.sh                # Combined startup script
└── pyproject.toml          # Python dependencies
```

## API Reference

### Conversations

| Endpoint                           | Method | Description             |
|------------------------------------|--------|-------------------------|
| `/api/conversations`               | GET    | List all conversations  |
| `/api/conversations`               | POST   | Create new conversation |
| `/api/conversations/{id}`          | GET    | Get conversation by ID  |
| `/api/conversations/{id}`          | DELETE | Delete conversation     |
| `/api/conversations/{id}/message`  | POST   | Send message (async)    |
| `/api/conversations/{id}/queue`    | GET    | Get queued messages     |
| `.../export-markdown`              | GET    | Export as Markdown      |

### Consultation Management

| Endpoint                           | Method | Description             |
|------------------------------------|--------|-------------------------|
| `/api/consultations/status`        | GET    | Global system status    |
| `.../consultation/status`          | GET    | Check processing status |
| `.../consultation/cancel`          | POST   | Cancel consultation     |

### Config & Interactive

| Endpoint                    | Method | Description              |
|-----------------------------|--------|--------------------------|
| `/api/config`               | GET    | Get runtime configuration|
| `/api/interactive-response` | POST   | Submit user feedback     |
| `/`                         | GET    | Health check             |

## Architecture

- **Backend**: FastAPI with async orchestration via `consultation_manager`.
- **Workflow**: LangGraph for complex 3-stage deliberation state management.
- **Frontend**: React 19, Vite 7, Tailwind CSS 4, and Zustand.
- **Storage**: Lightweight JSON file persistence in `data/conversations/`.
- **Sync**: Robust polling mechanism replaces SSE for state consistency.

## Backend Regression Checks

```bash
uv run python -m unittest discover -s tests -v
```

Provider-response tests use mocked HTTPX responses with the real client and
council stages. They cover blank-completion retry recovery, exhaustion, failure
status, and deliberation fallback. They make no live provider calls or claims
about model quality. Retries do not increase the token budget or change reasoning
settings; a provider that repeatedly returns empty text may still fail and bill
for its attempts.

Ranking-validity regressions exercise real Stage 2 validation and capture the
chairman's request across all four synthesis modes with provider calls mocked.
They verify that declared-invalid orders stay diagnostic, qualitative rationale
is qualified, and invalid votes do not inflate deliberation position counts.
These checks establish prompt construction, not improved live-model answer quality.

## Frontend Regression Checks

Selected-conversation status checks and polling ignore responses from obsolete
navigation effects, so a slow previous conversation cannot replace the current
status, queue, or interactive prompt through those effects. Slow polling requests
do not overlap within an active effect.

Stop requests are tracked per conversation: stopping one conversation does not
disable another conversation's controls. Late Stop responses and local errors
are scoped to their conversation, while its saved transcript is still refreshed.
Repeated Stop clicks for the same pending request are ignored.

A delayed single-conversation deletion removes only its target from the latest
sidebar list and cache. It preserves another selected conversation, newly added
list entries, and a newer delete-confirmation dialog. If the deleted conversation
is selected when deletion finishes, selection is cleared. Delete failures retain
local conversation data and keep the existing global error handling. Clear-all
and other mutation flows are unchanged.

```bash
node --test frontend/tests/*.test.mjs
cd frontend && npm run lint && npm run build
```

The dependency-free tests execute the production polling/selection effects and
Stop/Send/Delete callbacks with controlled promises and store/API seams. They cover
navigation, cleanup, late responses, independent and duplicate Stop requests,
Send rollback/cache ownership, delayed deletion/list/dialog ownership, and
current-request controls, not rendered browser UI.

### Optional rendered send/navigation regression test

After installing frontend dependencies, run the real UI against synthetic,
intercepted API responses. Supply an installed Playwright Core module and a
sandbox-capable Chromium executable; this does not download a browser or change
production dependencies:

```bash
cd frontend
PLAYWRIGHT_MODULE=/absolute/path/to/playwright-core/index.mjs \
CHROMIUM_EXECUTABLE=/absolute/path/to/chrome-headless-shell \
node tests/send-navigation.browser.mjs
```

The script starts and stops a local Vite server on port 5197 and launches Chromium
with its sandbox enabled. At desktop and mobile viewport sizes it types and sends
a question in A, selects idle or active B through the sidebar, then releases A's
delayed started, queued, or failed response. It verifies B's selected conversation,
loading indicator, consultation status, queue, error, and send-button label remain
unchanged. These are API-mocked UI checks, not live-provider or backend integration
tests; no real API key or paid provider request is used.

### Optional rendered deletion/navigation regression test

With the same Playwright and sandbox-capable Chromium prerequisites above:

```bash
cd frontend
PLAYWRIGHT_MODULE=/absolute/path/to/playwright-core/index.mjs \
CHROMIUM_EXECUTABLE=/absolute/path/to/chrome-headless-shell \
node tests/delete-navigation.browser.mjs
```

This script owns local Vite port 5198 and verifies desktop/mobile delayed deletion:
selecting B while A is being deleted, creating C and opening a newer B confirmation,
deleting still-selected A, and a failed deletion retaining A. Requests are synthetic
and intercepted; no provider calls are made. Node tests additionally exercise the
real selection callback when starting with no selection and selecting A before its
deletion finishes. Neither suite verifies live network/provider integration.

### Optional rendered interactive-reply regression test

With the same Playwright and sandbox-capable Chromium prerequisites above:

```bash
cd frontend
PLAYWRIGHT_MODULE=/absolute/path/to/playwright-core/index.mjs \
CHROMIUM_EXECUTABLE=/absolute/path/to/chrome-headless-shell \
node tests/interactive-navigation.browser.mjs
```

Delayed Submit or Skip responses only dismiss the prompt they answered. A newer
conversation, session, or refinement round keeps its prompt and error state; an
authentication failure still reaches the global authentication handler. Polling
refreshes of the same logical prompt do not prevent normal dismissal. The tests
use intercepted synthetic API responses, not live model providers. Node callback
regressions additionally verify logical prompt identity and retryable failures.

### Optional rendered side-chat lifecycle regression test

With the same Playwright and sandbox-capable Chromium prerequisites above:

```bash
cd frontend
PLAYWRIGHT_MODULE=/absolute/path/to/playwright-core/index.mjs \
CHROMIUM_EXECUTABLE=/absolute/path/to/chrome-headless-shell \
node tests/side-chat-navigation.browser.mjs
```

Closing a side chat ends that modal's local UI session. An already submitted
request may still finish and save to its original thread, but cannot replace the
thread, draft, or error in another source’s newly opened side chat. Model selection
is unavailable while opening a thread, as it already is while sending a follow-up.
These lifecycle
checks use synthetic intercepted API responses on desktop/mobile; they do not
verify live provider behavior or cancel server-side work.

### Optional rendered new-consultation recovery regression test

With the same Playwright and sandbox-capable Chromium prerequisites above:

```bash
cd frontend
PLAYWRIGHT_MODULE=/absolute/path/to/playwright-core/index.mjs \
CHROMIUM_EXECUTABLE=/absolute/path/to/chrome-headless-shell \
node tests/creation-recovery.browser.mjs
```

When New Consultation removes an empty consultation, that successful deletion is
reflected immediately. If creating its replacement fails, retry can create a new
consultation without trying to delete the missing one again. A different
consultation selected while deletion is pending stays selected if creation fails.
Desktop/mobile browser checks cover retry, navigation, failed deletion, and normal
creation with synthetic API responses. Node callback tests additionally verify
that populated conversations are retained. These checks do not call live providers.

## Documentation

- [AGENTS.md](AGENTS.md) - Development guidelines and project standards.
- [.env.sample](.env.sample) - Configuration reference and templates.

### Synthesis navigation regression check

Synthesis controls belong to their conversation and assistant message. Switching
cached consultations keeps their loading, error, and version-tab state separate;
a pending result still updates its originating conversation. Returning to that
conversation before completion retains its busy controls.

The optional sandboxed browser check uses synthetic intercepted API responses,
including delayed success/failure and same-conversation control recovery. No model
provider is contacted. From `frontend/`, run:

```bash
PLAYWRIGHT_MODULE=/absolute/path/to/playwright-core/index.mjs \
CHROMIUM_EXECUTABLE=/absolute/path/to/chrome-headless-shell \
node tests/synthesis-navigation.browser.mjs
```

Use an installed sandbox-capable Chromium; do not disable its sandbox. This
checks desktop/mobile UI isolation, not live-provider or model-answer quality.

### Draft preservation during submission

The question and optional Negative Mode instructions remain in memory for their
conversation until the server accepts the submission (started or queued). A
failed submission leaves both fields available for editing and manual retry.
There is no automatic retry: a lost network response does not prove that the
backend rejected the original request, so check the conversation before resending.

While the initial submission is pending, another send for that conversation is
disabled, but its draft remains editable. A late success clears only the exact
submitted draft, never newer edits or another conversation's draft. Navigating
between consultations keeps their drafts separate. Drafts are not persisted to
browser storage and do not survive a page reload.

The optional rendered regression uses intercepted synthetic APIs and the same
sandbox-capable browser setup described above:

```bash
cd frontend
PLAYWRIGHT_MODULE=/absolute/path/to/playwright-core/index.mjs \
CHROMIUM_EXECUTABLE=/absolute/path/to/chrome-headless-shell \
node tests/draft-submission.browser.mjs
```

### Optional rendered IME submission regression test

The main and negative-prompt composers leave IME confirmation Enter events alone;
a later ordinary Enter still submits, and Shift+Enter inserts a newline.

```bash
cd frontend
CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell \
PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs \
node tests/composition-submit.browser.mjs
```

This desktop/mobile Chromium test dispatches composition-marked keyboard events
and intercepts all API requests. It verifies our event handler, not a native OS
IME or cross-browser input-method behavior. No real provider calls are made.
The guard follows [MDN's IME keydown guidance](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event#keydown_events_with_ime).

## License

MIT

### Optional rendered system-theme regression test

With no saved theme preference, the interface follows operating-system color-scheme
changes while open. The theme button's label and next click follow the same current
scheme; choosing a theme explicitly keeps that preference across system changes
and reloads. React subscribes to the browser media query so context and page styles
stay in sync ([React browser API subscription guidance](https://react.dev/reference/react/useSyncExternalStore)).

```bash
cd frontend
CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell \
PLAYWRIGHT_MODULE=/path/to/playwright-core/index.mjs \
node tests/system-theme.browser.mjs
```

This sandboxed desktop/mobile check uses emulated system color preferences and
synthetic API responses. It covers both transition directions, button labels and
one-click reversal, repeated system changes, and saved manual preferences. It does
not verify physical OS scheduling, other browser engines, or live model providers.

## Woodpecker CI

The [workflow](.woodpecker.yaml) runs on pushes to `development` and on manual runs after this
repository is enabled in Woodpecker. It selects a native Mac or Linux ARM64/Pi worker with the
tools below. Both use the **local backend**, not a Docker image: `image: bash` selects the
shell. Portable jobs take any matching available worker; strict Mac-first priority is not
configured.

[`.ci/check.sh`](.ci/check.sh) contains the project-specific build/test commands. Run the same
checks locally from the repository root with `bash .ci/check.sh`. Prerequisites: uv, the
repository-pinned Python, Node.js 22+, npm and Chromium system libraries. Browser regressions
use mocked APIs with the Chromium sandbox enabled; no provider key is needed.

Configure the server's GitHub integration and a reachable webhook endpoint once. The agents
connect outbound; no SSH access to the Mac is needed. Install the native `plugin-git` clone
helper and put all required tools on the worker's PATH. Use one workflow at a time per worker
because browser suites and simulators share local resources. Run only trusted code under a
dedicated worker account; the local backend is not a sandbox. Configure a finite repository
pipeline timeout.

Add a repository-scoped fine-grained **user PAT** as the Woodpecker secret
`github_comment_token`, with Contents: write permission for this repository (required to update
comments). It is injected only into the comment step. That step creates a pass/fail comment on
`CI_COMMIT_SHA`, with the pipeline link and a bounded failure excerpt; repeating the report for
the same pipeline/workflow updates its own comment. A reporting failure fails the step instead
of silently claiming that a comment was sent. Woodpecker's built-in commit statuses remain
separate from these comments.

Console output is retained by Woodpecker according to the server's retention policy. Files in
`.ci-output/` (including any raw Xcode log/result bundle) are temporary workspace output, not
automatically uploaded artifacts. Configure artifact storage before relying on those files after
worker cleanup. A Tailnet-only log link requires Tailnet access; the comment excerpt is stored
on GitHub and visible to repository readers. Known token values are masked, but logs must not
contain private data.

Test the reporter without network access: `python3 -B .ci/test_report.py`. No CI server, secret,
webhook or worker is provisioned merely by adding these files. Existing GitHub Actions workflows
are unchanged.
