"""Configuration for the LLM Council."""

import os
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


def _parse_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_csv_env(name: str, default: List[str]) -> List[str]:
    parsed = _parse_csv(os.getenv(name))
    return parsed or list(default)


def _get_bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number (got {value!r})") from exc


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer (got {value!r})") from exc


# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError(
        "OPENROUTER_API_KEY environment variable not set. "
        "Please add it to your .env file."
    )

# API auth key for local requests
API_AUTH_KEY = os.getenv("API_AUTH_KEY")
if not API_AUTH_KEY:
    raise ValueError(
        "API_AUTH_KEY environment variable not set. "
        "Please add it to your .env file."
    )

# Basic brute-force protection for API auth key
API_KEY_BRUTEFORCE_MAX_ATTEMPTS = _get_int_env("API_KEY_BRUTEFORCE_MAX_ATTEMPTS", 8)
API_KEY_BRUTEFORCE_WINDOW_SECONDS = _get_int_env("API_KEY_BRUTEFORCE_WINDOW_SECONDS", 300)
API_KEY_BRUTEFORCE_LOCKOUT_SECONDS = _get_int_env("API_KEY_BRUTEFORCE_LOCKOUT_SECONDS", 300)
if API_KEY_BRUTEFORCE_MAX_ATTEMPTS < 0:
    raise ValueError("API_KEY_BRUTEFORCE_MAX_ATTEMPTS must be >= 0")
if API_KEY_BRUTEFORCE_MAX_ATTEMPTS > 0:
    if API_KEY_BRUTEFORCE_WINDOW_SECONDS <= 0:
        raise ValueError("API_KEY_BRUTEFORCE_WINDOW_SECONDS must be > 0")
    if API_KEY_BRUTEFORCE_LOCKOUT_SECONDS <= 0:
        raise ValueError("API_KEY_BRUTEFORCE_LOCKOUT_SECONDS must be > 0")

# Council members - list of OpenRouter model identifiers
DEFAULT_COUNCIL_MODELS = [
    "openai/gpt-5.5",
    "google/gemini-3.1-pro-preview",
    "anthropic/claude-opus-4.7",
    "x-ai/grok-4.3",
    "moonshotai/kimi-k2.6",
    "deepseek/deepseek-v4-pro",
]
COUNCIL_MODELS = _get_csv_env("COUNCIL_MODELS", DEFAULT_COUNCIL_MODELS)

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = os.getenv("CHAIRMAN_MODEL") or "google/gemini-3.1-pro-preview"

# Model for prompt refinement (defaults to chairman)
PROMPT_REFINEMENT_MODEL = os.getenv("PROMPT_REFINEMENT_MODEL") or CHAIRMAN_MODEL

# Model for conversation title generation
TITLE_MODEL = os.getenv("TITLE_MODEL") or "google/gemini-3.1-pro-preview"

# Feature flags (set to "true" in .env to enable)
NEGATIVE_MODE_ENABLED = _get_bool_env("NEGATIVE_MODE_ENABLED")
DELIBERATION_ENABLED = _get_bool_env("DELIBERATION_ENABLED")
INTERACTIVE_MODE_ENABLED = _get_bool_env("INTERACTIVE_MODE_ENABLED")
PROMPT_REFINEMENT_ENABLED = _get_bool_env("PROMPT_REFINEMENT_ENABLED")
PAIRWISE_RANKING_ENABLED = _get_bool_env("PAIRWISE_RANKING_ENABLED")

# OpenRouter API endpoint
OPENROUTER_API_URL = (
    os.getenv("OPENROUTER_API_URL")
    or "https://openrouter.ai/api/v1/chat/completions"
)

# OpenRouter response length limit
OPENROUTER_MAX_TOKENS = _get_int_env("OPENROUTER_MAX_TOKENS", 10000)
if OPENROUTER_MAX_TOKENS <= 0:
    raise ValueError("OPENROUTER_MAX_TOKENS must be > 0")

# OpenRouter retry configuration
OPENROUTER_MAX_RETRIES = _get_int_env("OPENROUTER_MAX_RETRIES", 2)
OPENROUTER_RETRY_BASE_SECONDS = _get_float_env("OPENROUTER_RETRY_BASE_SECONDS", 0.5)
OPENROUTER_RETRY_MAX_SECONDS = _get_float_env("OPENROUTER_RETRY_MAX_SECONDS", 8.0)
OPENROUTER_RETRY_JITTER = _get_float_env("OPENROUTER_RETRY_JITTER", 0.2)
if OPENROUTER_MAX_RETRIES < 0:
    raise ValueError("OPENROUTER_MAX_RETRIES must be >= 0")
if OPENROUTER_RETRY_BASE_SECONDS <= 0:
    raise ValueError("OPENROUTER_RETRY_BASE_SECONDS must be > 0")
if OPENROUTER_RETRY_MAX_SECONDS <= 0:
    raise ValueError("OPENROUTER_RETRY_MAX_SECONDS must be > 0")
if OPENROUTER_RETRY_MAX_SECONDS < OPENROUTER_RETRY_BASE_SECONDS:
    raise ValueError("OPENROUTER_RETRY_MAX_SECONDS must be >= OPENROUTER_RETRY_BASE_SECONDS")
if OPENROUTER_RETRY_JITTER < 0:
    raise ValueError("OPENROUTER_RETRY_JITTER must be >= 0")

# Data directory for conversation storage
DATA_DIR = os.getenv("DATA_DIR") or "data/conversations"

# CORS allowlist for the backend API
DEFAULT_CORS_ALLOW_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
]
CORS_ALLOW_ORIGINS = _get_csv_env("CORS_ALLOW_ORIGINS", DEFAULT_CORS_ALLOW_ORIGINS)

# Timeout for interactive queue operations (seconds)
# This is the max time to wait for a single user response to an interactive prompt
INTERACTIVE_TIMEOUT_SECONDS = _get_float_env("INTERACTIVE_TIMEOUT_SECONDS", 1200.0)

# Client-side streaming timeout (seconds)
# Resets on activity - only triggers if truly idle
STREAM_TIMEOUT_SECONDS = _get_float_env("STREAM_TIMEOUT_SECONDS", 1200.0)
if STREAM_TIMEOUT_SECONDS <= 0:
    raise ValueError("STREAM_TIMEOUT_SECONDS must be > 0")

# Prompt length limits (characters)
MAX_PROMPT_CHARS = _get_int_env("MAX_PROMPT_CHARS", 15000)
MAX_NEGATIVE_PROMPT_CHARS = _get_int_env("MAX_NEGATIVE_PROMPT_CHARS", 15000)
SYNTHESIS_CONTEXT_MAX_CHARS = _get_int_env("SYNTHESIS_CONTEXT_MAX_CHARS", 45000)
if MAX_PROMPT_CHARS <= 0:
    raise ValueError("MAX_PROMPT_CHARS must be > 0")
if MAX_NEGATIVE_PROMPT_CHARS <= 0:
    raise ValueError("MAX_NEGATIVE_PROMPT_CHARS must be > 0")
if SYNTHESIS_CONTEXT_MAX_CHARS <= 0:
    raise ValueError("SYNTHESIS_CONTEXT_MAX_CHARS must be > 0")

# -----------------------------------------------------------------------------
# Agent skills
# -----------------------------------------------------------------------------
# Uploadable Agent Skills (a SKILL.md bundle plus optional references/*.md)
# injected into the Stage 1 council prompts and the Stage 3 chairman prompt.
# Enabled by default - unlike the feature flags above, skills cost nothing until
# one is uploaded *and* selected for a consultation.
SKILLS_ENABLED = _get_bool_env("SKILLS_ENABLED", "true")

# Zip upload hardening limits (see backend/skills.py inspect_archive).
SKILLS_UPLOAD_MAX_BYTES = _get_int_env("SKILLS_UPLOAD_MAX_BYTES", 5_242_880)  # 5 MiB
SKILLS_MAX_ENTRIES = _get_int_env("SKILLS_MAX_ENTRIES", 500)
SKILLS_MAX_UNCOMPRESSED_BYTES = _get_int_env("SKILLS_MAX_UNCOMPRESSED_BYTES", 20_971_520)
SKILLS_MAX_COMPRESSION_RATIO = _get_int_env("SKILLS_MAX_COMPRESSION_RATIO", 100)

# How many skills a single consultation may select, and how many references the
# two-pass selector may inline across all of them.
SKILLS_MAX_SELECTED = _get_int_env("SKILLS_MAX_SELECTED", 5)
SKILLS_MAX_REFERENCES = _get_int_env("SKILLS_MAX_REFERENCES", 4)

# Character budgets for the rendered <skills> block, per stage.
SKILLS_CONTEXT_MAX_CHARS = _get_int_env("SKILLS_CONTEXT_MAX_CHARS", 48000)
SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS = _get_int_env("SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS", 24000)

# Two-pass reference selection: a cheap model picks which references/*.md the
# query actually needs. When disabled, a deterministic keyword-overlap fallback
# runs instead (no extra model call).
SKILLS_REFERENCE_SELECTION_ENABLED = _get_bool_env("SKILLS_REFERENCE_SELECTION_ENABLED", "true")
SKILLS_REFERENCE_SELECTION_MODEL = (
    os.getenv("SKILLS_REFERENCE_SELECTION_MODEL") or PROMPT_REFINEMENT_MODEL
)

# Max length of a skill's frontmatter description (not an env knob).
SKILL_DESCRIPTION_MAX_CHARS = 1024

if SKILLS_UPLOAD_MAX_BYTES <= 0:
    raise ValueError("SKILLS_UPLOAD_MAX_BYTES must be > 0")
if SKILLS_MAX_ENTRIES <= 0:
    raise ValueError("SKILLS_MAX_ENTRIES must be > 0")
if SKILLS_MAX_UNCOMPRESSED_BYTES <= 0:
    raise ValueError("SKILLS_MAX_UNCOMPRESSED_BYTES must be > 0")
if SKILLS_MAX_COMPRESSION_RATIO <= 0:
    raise ValueError("SKILLS_MAX_COMPRESSION_RATIO must be > 0")
if SKILLS_MAX_SELECTED <= 0:
    raise ValueError("SKILLS_MAX_SELECTED must be > 0")
if SKILLS_MAX_REFERENCES <= 0:
    raise ValueError("SKILLS_MAX_REFERENCES must be > 0")
if SKILLS_CONTEXT_MAX_CHARS <= 0:
    raise ValueError("SKILLS_CONTEXT_MAX_CHARS must be > 0")
if SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS <= 0:
    raise ValueError("SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS must be > 0")
if SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS > SKILLS_CONTEXT_MAX_CHARS:
    raise ValueError(
        "SKILLS_SYNTHESIS_CONTEXT_MAX_CHARS must be <= SKILLS_CONTEXT_MAX_CHARS"
    )

# Consultation thread pool size
CONSULTATION_POOL_SIZE = _get_int_env("CONSULTATION_POOL_SIZE", 4)
if CONSULTATION_POOL_SIZE < 1:
    raise ValueError("CONSULTATION_POOL_SIZE must be >= 1")
