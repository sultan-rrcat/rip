"""Interim config for Phase 1.1 (hybrid).

Keeps legacy module constants (used by rag/file_processor) working after
the move to ``app/core/``, and exposes a minimal ``Settings`` (Pydantic)
so ``backend/tests/conftest.py`` can import it and probe Ollama.

Phase 1.3 REPLACES this file wholesale with the TARGET block in
MERGE_PLAN.md §Config (port 8000, OLLAMA_*, no LLM_URL/NEO4J_*).
"""

import os

from pydantic_settings import BaseSettings

# After the move this file lives at backend/app/core/config.py, so the
# backend dir is two levels up (app/core -> app -> backend).
BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# Primary names (documented in SETUP.md, set by docker-compose); the *_DIR
# aliases match .env/.env.example, which also use them for volume mounts.
BGE_M3_MODEL_PATH = os.getenv(
    "BGE_M3_MODEL_PATH",
    os.getenv("BGE_MODEL_DIR", os.path.join(BASE_DIR, "models", "bge-m3")),
)
BGE_RERANKER_V2_M3 = os.getenv(
    "BGE_RERANKER_V2_M3",
    os.getenv(
        "RERANKER_MODEL_DIR",
        os.path.join(BASE_DIR, "models", "reranker", "bge_reranker_v2_m3"),
    ),
)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))

# Legacy (forbidden in TARGET config, kept only until Phase 1.3 rewrite).
LLM_URL = os.getenv("LLM_URL")


class Settings(BaseSettings):
    """Minimal Phase 1.1 settings; full TARGET replaces in Phase 1.3."""

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "rip"
    db_user: str = "rip"
    db_password: str = "rippass"

    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "qwen2.5:14b"
    ollama_timeout_ms: int = 120000

    port: int = 8000
    cors_origins: str = "*"
    log_level: str = "INFO"

    upload_dir: str = "./backend/uploads"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
