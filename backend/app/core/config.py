"""Single source of truth for all non-secret configuration.

Contract:
- Every default lives here. `.env` carries ONLY secrets and host-local
  deploy keys (ports, DB connection, Ollama endpoint, model paths,
  Langfuse keys) — see `.env.example` for the minimal shape.
- `extra = "ignore"` is required: docker-compose injects HOST_* and
  BACKEND_* vars via `env_file` that have no Settings field; `forbid`
  would crash container boot.
- `bge_m3_model_path` / `bge_reranker_v2_m3` accept the legacy
  `BGE_MODEL_DIR` / `RERANKER_MODEL_DIR` aliases via AliasChoices.
- Module-level `settings` singleton for call sites.
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
    ollama_default_model: str = "ornith-1.5:9b"
    ollama_timeout_ms: int = 300000  # httpx trust_env=False (proxy trap).

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
    rag_pdf_loader: Literal["docling", "opendataloader"] = "opendataloader"

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
    port: int = 8005
    cors_origins: str = "*"  # Q5 locked: plain str, split on ","; NOT list[str]
    log_level: str = "INFO"
    env: Literal["development", "production"] = "development"

    # Upload
    upload_dir: str = "./backend/uploads"
    max_upload_size_mb: int = 50
    allowed_extensions: list[str] = [
        ".pdf", ".docx", ".txt", ".md",
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
        ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".r",
        ".m", ".sh", ".ps1", ".sql", ".html", ".css", ".scss", ".less",
        ".json", ".xml", ".yaml", ".yml", ".toml", ".ini",
    ]
    #: Code files bypass vector ingest: stored on disk + marked ready, read
    #: as text on demand for the coding agent (never embedded).
    code_extensions: list[str] = [
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
        ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".r",
        ".m", ".sh", ".ps1", ".sql", ".html", ".css", ".scss", ".less",
        ".json", ".xml", ".yaml", ".yml", ".toml", ".ini",
    ]

    # Model defaults
    default_temperature: float = 0.1
    default_max_tokens: int = 2048
    default_timeout_ms: int = 120000
    # Per-task output budgets (both ride the same Ollama window — they cap
    # generated tokens only, they do not extend ollama_context_window).
    # Coding generation gets headroom over the shared default, but stays
    # bounded: 32768 never finished inside the 300s agent wall-clock on the
    # 9b host model (trace: "review the python code" timed out at exactly
    # 300s). 4096 fits ~160s at ~25 tok/s with prompt-processing headroom.
    # Review/explain asks reuse the shared default (see _coding_budget_for).
    coding_max_tokens: int = 4096
    chat_max_tokens: int = 1024

    # Control-plane call deadlines. The L1 router and the L3 ReAct planner emit
    # tiny JSON (intent; thought/executor/is_final) but were inheriting the full
    # ollama_timeout_ms generation budget. Under Ollama saturation that made a
    # ~20-token classification block for the full budget: trace5f98fe9c burned
    # 2 x 300s = the entire 600s run timeout (RUN_TIMEOUT_S) and completed zero
    # work. Failing these open fast keeps real generation budget for the run.
    router_timeout_ms: int = 20000
    planner_timeout_ms: int = 60000

    # Orchestration
    default_max_plan_steps: int = 10
    # Force L3 ReAct: when True, every request skips L1 Router + L2 builders
    # entirely and runs the ReAct loop. Env: FORCE_REACT (false by default).
    force_react: bool = False

    # Code sandbox (ADR-049): ephemeral local Docker containers running the
    # opencode CLI against host Ollama. Image is built once by the operator
    # (`docker build -t rip-sandbox sandbox/`); the backend needs the docker
    # socket mount. All live admin keys (read per execution, no restart).
    sandbox_image: str = "rip-sandbox:latest"
    sandbox_timeout_ms: int = 240000
    sandbox_cpus: float = 2.0
    sandbox_memory: str = "2g"

    # Chat memory (context-window-based, Q28 locked — port of athena memory.py)
    # estimator: len(text)//4 (no tiktoken); budget = int(ollama_context_window * summary_threshold_pct)
    ollama_context_window: int = 32768  # qwen2.5:14b native window; override per model
    memory_window_size: int = 10  # advisory verbatim window (WINDOW_SIZE); real constraint is budget
    summary_threshold_pct: float = 0.7
    summary_max_tokens: int = 512  # _SUMMARY_MAX_TOKENS; summary LLM call cap
    # Whole-file RAG shortcut: when every chunk of the scoped file(s) fits in
    # this share of the context window, rag.query returns them all and skips
    # the LLM sub-query planner + embed/vector/FTS/RRF/rerank entirely.
    # Per-SHARD share, not per-run: builders fan out up to 5 file-scoped
    # shards (compare_multi/summarize/quiz) and plan_graph concatenates every
    # shard into ONE reduce prompt, so 0.15 keeps the worst case (5 shards)
    # inside the window alongside memory + system prompt + answer.
    rag_whole_file_pct: float = 0.15
    # build_memory_context(provider, stored_conversation_summary, messages[{role,content}] oldest-first,
    #   window_size, folded_count) -> (MemoryContext, new_summary, new_count);
    # fold only newly-aged-out turns (folded_count dedup → notebooks.summary_message_count);
    # persist new summary → notebooks.conversation_summary (NOT the SSE summary event);
    # summarize via ollama_default_model (never gemini_model_*); trim newest-first to budget.

    # Observability (optional)
    langfuse_enabled: bool = True
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
