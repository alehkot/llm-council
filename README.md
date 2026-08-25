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
- **Conversation history**: Persistent JSON storage with full deliberation records.
- **Markdown Export**: Export full conversation context and reasoning to Markdown.
- **Focus View**: Dedicated modal for deep reading of the synthesized answer.
- **Agent Skills**: Upload a skill as a `.zip` and have its instructions
  steer the council's answers and the final synthesis.

### Advanced Features (Optional)

- **Negative Mode**: Models identify gaps, risks, and blind spots in responses.
- **Deliberation**: Models revise their answers after seeing peer feedback.
- **Interactive Mode**: Inline prompts for user guidance during processing.
- **Prompt Refinement**: Iteratively refine your initial query for better results.

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

### Skills

Uploaded skills live in `data/skills/`. Unlike the feature flags above,
`SKILLS_ENABLED` defaults to `true` — skills cost nothing until one is
uploaded *and* selected for a consultation.

| Variable                             | Description                | Default |
|--------------------------------------|----------------------------|---------|
| `SKILLS_ENABLED`                     | Master switch              | true    |
| `SKILLS_MAX_SELECTED`                | Skills per consultation    | 5       |
| `SKILLS_MAX_REFERENCES`              | Reference documents per run | 4       |
| `SKILLS_MAX_REFERENCE_SECTIONS`       | Reference sections inlined per run | 12 |
| `SKILLS_CONTEXT_MAX_CHARS`           | Stage 1 skill budget       | 48000   |
| `SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS` | Stage 3 skill budget       | 24000   |
| `SKILLS_REFERENCE_SELECTION_ENABLED` | Model-picked references    | true    |
| `SKILLS_AUTO_SELECT_ENABLED`         | Auto-pick skills when none ticked | false |
| `SKILLS_AUTO_SELECT_MODEL`           | Model for skill auto-selection | reference model |
| `SKILLS_UPLOAD_MAX_BYTES`            | Max upload size            | 5 MiB   |

See `.env.sample` for the remaining ingest limits.

## Agent Skills

An **Agent Skill** is a `.zip` containing a `SKILL.md` with YAML frontmatter
(`name` and `description`) plus optional `references/*.md`. This is the same
format Claude Code and Codex use, so existing skills work here unchanged.

Upload one from the **Skills** button in the console header. The modal's
**Default skills** list is a global setting: default skills apply to every
chat. In the composer, unchecking a default skill turns it off for that one
chat only (per-chat override), with a "Reset to default" button to clear the
overrides. Instructions are injected into **Stage 1** (every council model's
system prompt) and **Stage 3** (the chairman's synthesis prompt).

Skill selection is operator-driven by default: tick skills in the composer,
and only those are used. With `SKILLS_AUTO_SELECT_ENABLED=true`, sending a
question with **no** skills ticked instead asks a cheap model to pick up to
`SKILLS_MAX_SELECTED` enabled skills whose description matches the question;
explicit ticks always win over auto-selection. The pick is recorded in the
message metadata, so resume and export replay the same skill set.

Because council models have no tools, they cannot open a skill's
`references/*.md` on demand. Instead a cheap model is asked once per
consultation which reference **sections** the question actually needs, and
only those are inlined. Each reference file is indexed at ingest into
self-contained heading-delimited sections, so the selector can inline one
section of a large document without paying for the rest; selecting every
section of a document equals the whole file. Set
`SKILLS_REFERENCE_SELECTION_ENABLED=false` to use a deterministic
keyword-overlap picker instead and skip the extra model call.

Only `SKILL.md` and `references/*.md` ever reach a prompt. Anything else in
the bundle — `scripts/`, `assets/`, `evals/` — is stored for fidelity but
never executed and never injected. `evals/` in particular is an answer key,
and three independent layers keep it out of every prompt.

### Caveats

- **Stage 2 ranks skill-shaped answers without seeing the skill's rules.**
  Peer review is deliberately not skill-aware, so a model that correctly
  followed a terse house-style skill can be ranked down *for being terse*.
- **Verbose skills raise the odds of a truncated synthesis.** A larger prompt
  plus instructions encouraging long output makes Stage 3 more likely to hit
  `OPENROUTER_MAX_TOKENS` (default 10000), which fails the run rather than
  truncating silently. Lower `SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS` or raise
  `OPENROUTER_MAX_TOKENS` if you hit it.
- **Skill text is untrusted input that reaches every council model.** Only
  upload bundles you trust.

## Project Structure

```text
llm-council/
├── backend/                 # Python FastAPI backend
│   ├── main.py             # FastAPI app entry point & routes
│   ├── config.py           # Configuration management
│   ├── council.py          # Deliberation & Synthesis logic
│   ├── langgraph_flow.py   # LangGraph workflow orchestration
│   ├── consultation_manager.py # Async task orchestration
│   ├── skills.py           # Agent Skill ingest, storage & prompt rendering
│   └── storage.py          # JSON conversation persistence
├── frontend/               # React + Vite + Tailwind
│   ├── src/
│   │   ├── App.jsx         # Main orchestration
│   │   ├── store/          # Zustand global state
│   │   ├── api.js          # Backend API client
│   │   └── components/     # UI components (Stage1-3, etc.)
│   └── package.json
├── data/                   # Conversation storage
│   ├── conversations/      # JSON files per conversation
│   └── skills/             # Uploaded Agent Skill bundles
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

### Skill Library

| Endpoint                    | Method | Description                  |
|-----------------------------|--------|------------------------------|
| `/api/skills`               | GET    | List installed skills        |
| `/api/skills`               | POST   | Upload a `.zip` (raw body)   |
| `/api/skills/{id}`          | GET    | Skill detail                 |
| `/api/skills/{id}/markdown` | GET    | Raw `SKILL.md`               |
| `/api/skills/{id}`          | PATCH  | Enable/disable               |
| `/api/skills/{id}`          | DELETE | Remove a skill               |

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
