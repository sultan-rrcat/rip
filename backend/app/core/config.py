"""Merged Pydantic Settings (MERGE_PLAN.md §Config TARGET, Phase 1.3).

Deltas vs the TARGET block (all forced by pre-1.4 environment, see log):
- `bge_m3_model_path` / `bge_reranker_v2_m3` accept the legacy
  `BGE_MODEL_DIR` / `RERANKER_MODEL_DIR` aliases via AliasChoices (Q5:
  local boot breaks without them; current .env only sets the aliases).
- `extra = "ignore"` until Phase 1.4 rewrites `.env`/`.env.example`
  (current `.env` still carries forbidden `LLM_URL`; pydantic-settings
  defaults to forbid, which would crash boot).
- Module-level `settings` singleton for call sites (TARGET defines the
  class only).
"""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings


def _resolve_repo_root() -> Path:
    """Repo root (`rip/`) anchored to this file, independent of CWD."""
    return Path(__file__).resolve().parents[3]


def _normalize_db_host(value: str) -> str:
    """Map IPv6-blackholed `localhost` to `127.0.0.1`; keep rest as-is."""
    text = (value or "").strip()
    if text.lower() == "localhost":
        return "127.0.0.1"
    return text


def _to_absolute_model_path(value: str) -> str:
    """Resolve relative BGE paths against the repo root; keep absolute as-is."""
    text = (value or "").strip()
    if not text:
        return text
    if text.startswith("/"):
        # POSIX absolute (compose `/app/...`); preserve verbatim even when
        # validated on Windows where Path() lacks a drive letter.
        return text
    candidate = Path(text)
    if candidate.is_absolute():
        return str(candidate)
    return str((_resolve_repo_root() / candidate).resolve())


class Settings(BaseSettings):
    # Database (target; current pre-merge is prototype_rip/trainee @10.10.30.65)
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "rip"
    db_user: str = "rip"
    db_password: str = "rippass"
    db_connect_timeout_s: int = 5

    # Ollama (replaces LLM_URL=http://10.10.30.77:21434)
    model_provider: str = "ollama"
    ollama_base_url: str = "http://host.docker.internal:11434"  # localhost outside docker
    ollama_default_model: str = "qwen2.5:14b"
    ollama_timeout_ms: int = 120000  # Q28: Athena used 180000; locked 120000. httpx trust_env=False (proxy trap).
    ollama_image_model: str = ""  # empty = image.generate fails honest (Athena ADR-024)

    # Embedding models (local paths)
    bge_m3_model_path: str = Field(
        default="./backend/models/bge-m3",
        validation_alias=AliasChoices("bge_m3_model_path", "bge_model_dir"),
    )
    bge_reranker_v2_m3: str = Field(
        default="./backend/models/reranker/bge_reranker_v2_m3",
        validation_alias=AliasChoices("bge_reranker_v2_m3", "reranker_model_dir"),
    )

    @field_validator("bge_m3_model_path", "bge_reranker_v2_m3", mode="before")
    @classmethod
    def _abs_model_path(cls, v: object) -> object:
        if isinstance(v, (str, Path)):
            return _to_absolute_model_path(str(v))
        return v

    # PDF ingestion: selected loader runs first, the other is fallback.
    # RAG_PDF_LOADER=docling | opendataloader (default docling).
    rag_pdf_loader: Literal["docling", "opendataloader"] = "docling"

    @field_validator("rag_pdf_loader", mode="before")
    @classmethod
    def _norm_pdf_loader(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("upload_dir", mode="before")
    @classmethod
    def _abs_upload_dir(cls, v: object) -> object:
        if isinstance(v, (str, Path)):
            return _to_absolute_model_path(str(v))
        return v

    @field_validator("db_host", mode="before")
    @classmethod
    def _norm_db_host(cls, v: object) -> object:
        if isinstance(v, str):
            return _normalize_db_host(v)
        return v

    # Server (keep 8000 to avoid nginx/frontend churn; was 8010 in draft)
    port: int = 8000
    cors_origins: str = "*"  # Q5 locked: plain str, split on ","; NOT list[str]
    log_level: str = "INFO"

    # Upload
    upload_dir: str = "./backend/uploads"

    # Model defaults
    default_temperature: float = 0.2
    default_max_tokens: int = 2048
    default_timeout_ms: int = 30000

    # Code sandbox
    sandbox_image: str = "python:3.11-slim"
    sandbox_memory: str = "256m"
    sandbox_pids_limit: int = 64
    sandbox_timeout_ms: int = 30000
    sandbox_output_max_bytes: int = 1048576

    # Orchestration
    default_max_plan_steps: int = 10

    # Chat memory (context-window-based, Q28 locked — port of athena memory.py)
    # estimator: len(text)//4 (no tiktoken); budget = int(ollama_context_window * summary_threshold_pct)
    ollama_context_window: int = 32768  # qwen2.5:14b native window; override per model
    memory_window_size: int = 10  # advisory verbatim window (WINDOW_SIZE); real constraint is budget
    summary_threshold_pct: float = 0.7
    summary_max_tokens: int = 512  # _SUMMARY_MAX_TOKENS; summary LLM call cap
    # build_memory_context(provider, stored_conversation_summary, messages[{role,content}] oldest-first,
    #   window_size, folded_count) -> (MemoryContext, new_summary, new_count);
    # fold only newly-aged-out turns (folded_count dedup → notebooks.summary_message_count);
    # persist new summary → notebooks.conversation_summary (NOT the SSE summary event);
    # summarize via ollama_default_model (never gemini_model_*); trim newest-first to budget.

    # Observability (optional)
    langfuse_enabled: bool = False
    langfuse_host: str = "http://localhost:3002"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # Request-scoped trace attributes (see observability/langfuse.py).
    langfuse_environment: str = "development"
    langfuse_release: str = "dev"

    # Auth (optional)
    api_keys_json: dict = {}

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Phase 1.3 only: tolerate legacy .env keys until 1.4 rewrite


settings = Settings()
