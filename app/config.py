"""Central configuration for Enterprise Fabric AI.

All settings are read from environment variables or the `.env` file in the
project root. Nothing secret (API keys) and no model IDs are hard-coded here;
model IDs have no defaults, so a missing `.env` fails loudly instead of
silently using the wrong model.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the folder that contains `app/`.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed application settings, loaded from `.env`."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM (Claude via the anthropic SDK) ------------------------------
    # Either an OpenRouter gateway (base URL + bearer token, used by this project) or
    # the direct Anthropic API (api key). At least one credential must be set.
    llm_base_url: str | None = None  # e.g. https://openrouter.ai/api
    llm_auth_token: str | None = None  # sent as "Authorization: Bearer ..."
    anthropic_api_key: str | None = None  # direct Anthropic API instead
    model_light: str  # query understanding, routing tie-breaks, compression
    model_synth: str  # synthesis
    model_verify: str  # verification; must differ from model_synth
    # No temperature: the API removed sampling parameters for these models.
    # Effort is the control that replaces it (low | medium | high | xhigh | max).
    effort_light: str = "low"
    effort_synth: str = "medium"
    effort_verify: str = "medium"

    # --- Local models (sentence-transformers, CPU) -----------------------
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-base"

    # --- Run profile (CLAUDE.md section 2) --------------------------------------
    # lite: SQLite, embedded Qdrant, rank_bm25, in-process cache and event bus (laptop,
    #       tests, clean clone). full: the specified servers in deploy/compose.yaml.
    profile: Literal["lite", "full"] = "lite"
    # Server addresses for the full profile (no defaults: credentials stay in .env).
    database_url: str | None = None       # postgresql+psycopg://user:password@host:5432/db
    qdrant_url: str | None = None         # http://localhost:6333
    opensearch_url: str | None = None     # http://localhost:9200
    opensearch_index: str = "chunks"
    redis_url: str | None = None          # redis://localhost:6379/0
    kafka_bootstrap: str | None = None    # localhost:9092

    # --- Paths -------------------------------------------------------------
    data_dir: Path = PROJECT_ROOT / "data"
    corpus_dir: Path = PROJECT_ROOT / "data" / "corpus"
    manifest_path: Path = PROJECT_ROOT / "data" / "manifest.csv"
    # The committed synthetic documents only; used when the ICAI PDFs are absent (clean clone).
    synthetic_manifest_path: Path = PROJECT_ROOT / "data" / "manifest.synthetic.csv"
    users_path: Path = PROJECT_ROOT / "data" / "users.csv"
    engagements_path: Path = PROJECT_ROOT / "data" / "engagements.csv"
    qdrant_path: Path = PROJECT_ROOT / "data" / "qdrant"
    sqlite_path: Path = PROJECT_ROOT / "data" / "fabric.db"
    eval_results_dir: Path = PROJECT_ROOT / "eval" / "results"
    # End-of-chapter "Test Your Knowledge" text set aside for building the eval set.
    # ICAI material, so it is git-ignored like the PDFs.
    tyk_dir: Path = PROJECT_ROOT / "data" / "tyk_candidates"

    # --- Indexing -----------------------------------------------------------
    qdrant_collection: str = "chunks"
    embed_batch_size: int = 32
    watcher_debounce_seconds: float = 1.5
    api_watch_corpus: bool = True  # the API process also runs the folder watcher
    # The API process also runs the ingestion worker (M12a). Right for lite (in-process
    # event bus). In the full profile the worker usually runs as its own process
    # (python -m app.ingest.worker), so set API_RUN_WORKER=false there.
    api_run_worker: bool = True
    # Push connectors (the webhook) store received content here for the worker.
    landing_dir: Path = PROJECT_ROOT / "data" / "landing"

    # --- Chunking -----------------------------------------------------------
    chunk_max_tokens: int = 400
    chunk_overlap_tokens: int = 50

    # --- Retrieval ----------------------------------------------------------
    rrf_k: int = 60
    retrieval_top_k: int = 20  # candidates per sub-query, per retriever
    rerank_top_n: int = 5  # kept after cross-encoder re-ranking
    min_good_chunks: int = 3  # "N" in CLAUDE.md section 7.3
    good_chunk_threshold: float = 0.3  # re-ranker score counted as "good"
    superseded_weight: float = 0.5  # multiplier for superseded documents
    recency_weight: float = 0.2  # strength of the time_sensitive recency boost
    recency_half_life_days: float = 365.0  # freshness halves every year

    # --- Verification -------------------------------------------------------
    confidence_threshold: float = 0.6

    # --- Reproducibility ----------------------------------------------------
    random_seed: int = 42

    # --- Sign-in (author's request, 2026-11-14) -----------------------------
    # Initial password for the synthetic demo users (data/users.csv). Set it in .env;
    # it is applied only to demo users that have no password yet, so a password reset
    # is never overwritten. Without it, demo users cannot sign in.
    demo_password: str | None = None
    session_hours: float = 8.0  # a sign-in token is valid this long
    reset_link_minutes: float = 15.0  # a password-reset link is valid this long
    # Open self-registration is OFF by default (audit finding). When switched on, only
    # addresses at the firm's (synthetic) domain may register.
    allow_self_registration: bool = False
    self_registration_domain: str = "example-associates.test"
    # Sign-in protection, per client IP (CLAUDE.md section 10): calls per minute to the
    # /auth/* routes, and a lockout after repeated failed sign-ins.
    auth_rate_per_minute: float = 5.0
    auth_lockout_failures: int = 10
    auth_lockout_minutes: float = 15.0

    @model_validator(mode="after")
    def _check_llm_settings(self) -> "Settings":
        """Verifier differs from synthesiser; a credential exists; efforts are valid."""
        if self.model_verify == self.model_synth:
            raise ValueError("MODEL_VERIFY must differ from MODEL_SYNTH")
        if not (self.llm_auth_token or self.anthropic_api_key):
            raise ValueError("set LLM_AUTH_TOKEN (OpenRouter) or ANTHROPIC_API_KEY")
        allowed = {"low", "medium", "high", "xhigh", "max"}
        for name in ("effort_light", "effort_synth", "effort_verify"):
            if getattr(self, name) not in allowed:
                raise ValueError(f"{name.upper()} must be one of {sorted(allowed)}")
        return self

    @property
    def sqlite_url(self) -> str:
        """SQLAlchemy URL for the SQLite database."""
        return f"sqlite:///{self.sqlite_path.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings object (read `.env` once per process)."""
    return Settings()
