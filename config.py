"""Settings (.env) and the ONE Gemini model used by the main agent and every sub-agent."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    BASE_DIR: Path = Path(__file__).resolve().parent
    RUNS_DIR: Path = BASE_DIR / "runs"

    # --- LLM: Gemini only ----------------------------------------------------
    GEMINI_API_KEY: str = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()

    DEFAULT_TEMPERATURE: float = _float("DEFAULT_TEMPERATURE", 0.2)
    MAX_OUTPUT_TOKENS: int = _int("MAX_OUTPUT_TOKENS", 16384)
    LLM_RETRIES: int = _int("LLM_RETRIES", 6)
    # Max requests per MINUTE sent to Gemini. Default 30.
    LLM_RPM: int = _int("LLM_RPM", 30)

    # --- Report size (everything is derived from these, nothing is fixed) ----
    WORDS_PER_PAGE: int = _int("WORDS_PER_PAGE", 350)
    MAX_PAGES: int = _int("MAX_PAGES", 500)

    # --- Knowledge base / research ------------------------------------------
    SEC_USER_AGENT: str = os.getenv("SEC_USER_AGENT", "AnnualReportAgent/1.0 research-use contact@example.com").strip()
    MAX_DOWNLOAD_MB: int = _int("MAX_DOWNLOAD_MB", 150)
    PDF_TABLES: bool = _bool("PDF_TABLES", True)       # extract real tables from PDFs (slower, far better numbers)
    CHUNK_WORDS: int = _int("CHUNK_WORDS", 300)
    CHUNK_OVERLAP_WORDS: int = _int("CHUNK_OVERLAP_WORDS", 50)

    SQLITE_DB_NAME: str = os.getenv("SQLITE_DB_NAME", "evidence.db")

    # --- Hybrid Retrieval Settings -------------------------------
    HYBRID_SEARCH: bool = _bool("HYBRID_SEARCH", True)
    RRF_K: int = _int("RRF_K", 60)
    KEYWORD_TOP_N: int = _int("KEYWORD_TOP_N", 25)
    VECTOR_TOP_N: int = _int("VECTOR_TOP_N", 25)
    OCR_ENABLED: bool = _bool("OCR_ENABLED", True)

    # --- Financial Reconciliation & Safety Settings --------------------------
    FINANCIAL_RECONCILIATION_TOLERANCE: float = _float("FINANCIAL_RECONCILIATION_TOLERANCE", 0.05)
    MAX_SECTION_OVERLAP: float = _float("MAX_SECTION_OVERLAP", 0.35)   # max repeated text allowed between sections
    PROMPT_INJECTION_DEFENCE: bool = _bool("PROMPT_INJECTION_DEFENCE", True)

    # --- Phase 3 Coverage, Charts & Evidence Budgeting Settings --------------
    EVIDENCE_WORDS_RATIO: float = _float("EVIDENCE_WORDS_RATIO", 2.5)
    MIN_EVIDENCE_WORDS_PER_PAGE: int = _int("MIN_EVIDENCE_WORDS_PER_PAGE", 150)
    COMPANIES_HOUSE_API_KEY: str = os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()
    CHARTS_ENABLED: bool = _bool("CHARTS_ENABLED", True)


settings = Settings()


# ------------------------------------------------------------------------------
# THE LLM (Gemini) used by the main agent and all sub-agents
# ------------------------------------------------------------------------------
_llm_cache: dict[str, Any] = {}


def get_llm(model: Optional[Any] = None):
    """Return a configured Gemini chat model cached by name, or return `model` if already a BaseChatModel instance.
    `model` may be 'gemini-3.8-flash', 'gemini-3.5-flash', etc.; default is GEMINI_MODEL from .env."""
    if model is not None and not isinstance(model, str):
        return model

    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is missing. Put it in .env:  GEMINI_API_KEY=your_key")
    name = (model or settings.GEMINI_MODEL).strip()
    for prefix in ("google_genai:", "google-genai:", "gemini:"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]

    if name in _llm_cache:
        return _llm_cache[name]

    from langchain_core.rate_limiters import InMemoryRateLimiter
    from langchain_google_genai import ChatGoogleGenerativeAI

    # Spaces requests out so rate limits are respected without unnecessary stalls
    rpm = max(1, settings.LLM_RPM)
    limiter = InMemoryRateLimiter(
        requests_per_second=rpm / 60.0,
        check_every_n_seconds=0.1,
        max_bucket_size=2,
    )
    llm_instance = ChatGoogleGenerativeAI(
        model=name,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=settings.DEFAULT_TEMPERATURE,
        max_output_tokens=settings.MAX_OUTPUT_TOKENS,
        max_retries=settings.LLM_RETRIES,      # built-in backoff on 429 / 503
        rate_limiter=limiter,
    )
    _llm_cache[name] = llm_instance
    return llm_instance
