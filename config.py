"""Central configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
MATCH_TIMEZONE = os.getenv("MATCH_TIMEZONE", "America/Chicago")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", ROOT / "data" / "championship.db"))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")
FOOTBALL_DATA_COMPETITION = os.getenv("FOOTBALL_DATA_COMPETITION", "WC")
MAX_MATCHES_PER_RUN = int(os.getenv("MAX_MATCHES_PER_RUN", "16"))
TAVILY_MAX_RESULTS = int(os.getenv("TAVILY_MAX_RESULTS", "5"))
RESEARCH_CONTENT_CHARS = int(os.getenv("RESEARCH_CONTENT_CHARS", "1800"))
ANALYST_MAX_TOKENS = int(os.getenv("ANALYST_MAX_TOKENS", "1000"))
MASTER_MAX_TOKENS = int(os.getenv("MASTER_MAX_TOKENS", "1500"))
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FOOTBALL_DATA_URL = "https://api.football-data.org/v4"
REQUEST_TIMEOUT_SECONDS = 90

MODELS = [
    {"id": "gpt-5.5", "model": "openai/gpt-5.5"},
    {"id": "claude-sonnet-4.6", "model": "anthropic/claude-sonnet-4.6"},
    {"id": "gemini-3.5-flash", "model": "google/gemini-3.5-flash"},
    {"id": "grok-4.3", "model": "x-ai/grok-4.3"},
    {"id": "deepseek-v3", "model": "deepseek/deepseek-chat"},
]
_enabled_model_ids = {item.strip() for item in os.getenv("ENABLED_MODEL_IDS", "").split(",") if item.strip()}
if _enabled_model_ids:
    MODELS = [model for model in MODELS if model["id"] in _enabled_model_ids]
MASTER_MODEL = os.getenv("MASTER_MODEL", "openai/gpt-5.5")
