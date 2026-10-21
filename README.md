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
- **Transparent reasoning**: View raw evaluations, debug traces, and validate ranking
  extraction.
- **Graceful degradation**: Continues with successful responses if some models
  fail.
- **Polling-based sync**: Robust state synchronization between frontend and backend.
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

## Documentation

- [AGENTS.md](AGENTS.md) - Development guidelines and project standards.
- [.env.sample](.env.sample) - Configuration reference and templates.

## License

MIT
