"""Configuration and LLM provider clients (Ollama, Gemini, OpenAI, Anthropic).

All clients expose the same method:  client.invoke([{"role": "...", "content": "..."}]) -> obj.content
They retry on rate limits / timeouts and RAISE on real failures. There is no silent fallback:
a failed call must never turn into invented report text.
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _resolve_provider() -> str:
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit:
        return "gemini" if explicit == "google" else explicit
    if (os.getenv("OPENAI_API_KEY") or "").strip():
        return "openai"
    if (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        return "anthropic"
    if (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip():
        return "gemini"
    return "ollama"


class Settings:
    BASE_DIR: Path = Path(__file__).resolve().parent
    RUNS_DIR: Path = BASE_DIR / "runs"

    # --- LLM provider --------------------------------------------------------
    LLM_PROVIDER: str = _resolve_provider()
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gpt-oss:20b").strip()
    OLLAMA_THINK: str = os.getenv("OLLAMA_THINK", "").strip().lower()   # gpt-oss: low | medium | high (low = much faster)
    OLLAMA_NUM_CTX: int = _int("OLLAMA_NUM_CTX", 16384)   # Ollama's default (2-4k) silently truncates prompts
    GEMINI_API_KEY: str = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o").strip()
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip()

    DEFAULT_TEMPERATURE: float = _float("DEFAULT_TEMPERATURE", 0.2)
    MAX_OUTPUT_TOKENS: int = _int("MAX_OUTPUT_TOKENS", 16384)
    REQUEST_TIMEOUT_SECONDS: int = _int("REQUEST_TIMEOUT_SECONDS", 240)
    LLM_RETRIES: int = _int("LLM_RETRIES", 4)
    # minimum seconds between ANY two LLM requests (all threads share it). Free tiers cap requests per minute.
    LLM_MIN_INTERVAL_SECONDS: float = _float("LLM_MIN_INTERVAL_SECONDS", 6.0 if LLM_PROVIDER == "gemini" else 0.0)

    # --- Report size (everything is derived from these, nothing is fixed) ----
    WORDS_PER_PAGE: int = _int("WORDS_PER_PAGE", 420)
    MIN_PAGES: int = _int("MIN_PAGES", 30)
    MAX_PAGES: int = _int("MAX_PAGES", 500)
    LEAF_MAX_WORDS: int = _int("LEAF_MAX_WORDS", 1200)   # a section bigger than this is split further
    MIN_LEAF_WORDS: int = _int("MIN_LEAF_WORDS", 250)
    MAX_EXPANSION_DEPTH: int = _int("MAX_EXPANSION_DEPTH", 1)

    # --- Knowledge base / research ------------------------------------------
    SEC_USER_AGENT: str = os.getenv("SEC_USER_AGENT", "AnnualReportAgent/1.0 research-use contact@example.com").strip()
    WEB_CORPUS_PAGES: int = _int("WEB_CORPUS_PAGES", 30)
    MAX_REFERENCE_DOCS: int = _int("MAX_REFERENCE_DOCS", 2)
    MAX_DOWNLOAD_MB: int = _int("MAX_DOWNLOAD_MB", 150)
    PDF_TABLES: bool = _bool("PDF_TABLES", True)       # extract real tables from PDFs (slower, far better numbers)
    CHUNK_WORDS: int = _int("CHUNK_WORDS", 300)
    CHUNK_OVERLAP_WORDS: int = _int("CHUNK_OVERLAP_WORDS", 50)
    RETRIEVAL_TOP_K: int = _int("RETRIEVAL_TOP_K", 10)
    MIN_KB_HITS_BEFORE_WEB: int = _int("MIN_KB_HITS_BEFORE_WEB", 4)
    MAX_SEARCH_RESULTS: int = _int("MAX_SEARCH_RESULTS", 4)
    MAX_EVIDENCE_CHARS: int = _int("MAX_EVIDENCE_CHARS", 16000)

    # --- Execution / quality -------------------------------------------------
    MAX_PARALLEL_WORKERS: int = _int("MAX_PARALLEL_WORKERS", 3)
    MAX_VALIDATION_RETRIES: int = _int("MAX_VALIDATION_RETRIES", 1)
    EXPAND_PASSES: int = _int("EXPAND_PASSES", 1)
    VALIDATE_WITH_LLM: bool = _bool("VALIDATE_WITH_LLM", True)
    DUPLICATE_CONTAINMENT_LIMIT: float = _float("DUPLICATE_CONTAINMENT_LIMIT", 0.35)

    SQLITE_DB_NAME: str = os.getenv("SQLITE_DB_NAME", "evidence.db")


settings = Settings()


# ------------------------------------------------------------------------------
# LLM clients
# ------------------------------------------------------------------------------
class LLMResponse:
    def __init__(self, content: str):
        self.content = content


class QuotaExhausted(RuntimeError):
    """The provider's quota / rate limit is used up. Retrying now is pointless: the run stops cleanly and can be
    continued later with --resume."""


_throttle_lock = threading.Lock()
_last_call = [0.0]


def _throttle() -> None:
    gap = settings.LLM_MIN_INTERVAL_SECONDS
    if gap <= 0:
        return
    with _throttle_lock:                       # holding the lock while sleeping spaces calls out across threads
        wait = _last_call[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def _split_messages(messages: List[Any]) -> Tuple[str, List[Dict[str, str]]]:
    system_parts: List[str] = []
    turns: List[Dict[str, str]] = []
    for m in messages:
        if isinstance(m, dict):
            role, content = m.get("role", "user"), m.get("content", "")
        else:
            role = getattr(m, "role", getattr(m, "type", "user"))
            content = getattr(m, "content", str(m))
        if role == "human":
            role = "user"
        if role == "ai":
            role = "assistant"
        if role == "system":
            system_parts.append(str(content))
        else:
            turns.append({"role": role, "content": str(content)})
    return "\n\n".join(system_parts).strip(), turns


class _BaseClient:
    provider = "base"

    def __init__(self, model: str, temperature: Optional[float] = None, max_tokens: Optional[int] = None):
        self.model = model
        self.temperature = settings.DEFAULT_TEMPERATURE if temperature is None else temperature
        self.max_tokens = max_tokens or settings.MAX_OUTPUT_TOKENS

    def _post(self, url: str, headers: Optional[dict] = None, payload: Optional[dict] = None) -> dict:
        import requests
        last: Any = None
        timeouts = 0
        rate_limited = False
        max_attempts = max(8, settings.LLM_RETRIES)
        for attempt in range(max_attempts):
            _throttle()
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=settings.REQUEST_TIMEOUT_SECONDS)
                if r.status_code == 429:
                    body = r.text
                    if re.search(r"PerDay|per day|daily|insufficient_quota|free_tier_requests_per_day", body, re.I):
                        raise QuotaExhausted(f"{self.provider}: DAILY quota is used up. Wait until it resets "
                                             f"(for Gemini: midnight Pacific time) and continue with --resume. "
                                             f"Details: {body[:200]}")
                    rate_limited = True
                    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', body)
                    wait = float(m.group(1)) if m else 0.0
                    try:
                        wait = wait or float(r.headers.get("retry-after", 0))
                    except ValueError:
                        pass
                    last = RuntimeError(f"{self.provider} HTTP 429 (rate limit)")
                    wait_sec = min(90.0, max(wait, 20.0 + 5.0 * attempt))
                    with _throttle_lock:
                        _last_call[0] = time.time() + wait_sec
                    print(f"[LLM] rate limit hit; waiting {wait_sec:.0f}s (attempt {attempt + 1}/{max_attempts})", flush=True)
                    time.sleep(wait_sec)
                    continue
                rate_limited = False
                if r.status_code in (500, 502, 503, 504):
                    last = RuntimeError(f"{self.provider} HTTP {r.status_code}: {r.text[:300]}")
                elif not r.ok:
                    raise RuntimeError(f"{self.provider} API error [{r.status_code}]: {r.text[:600]}")
                else:
                    return r.json()
            except (requests.Timeout, requests.ConnectionError) as e:
                last = e
                if isinstance(e, requests.Timeout):
                    timeouts += 1
                    print(f"[LLM] {self.provider} timed out after {settings.REQUEST_TIMEOUT_SECONDS}s "
                          f"(attempt {attempt + 1}). The model is too slow for this request size.", flush=True)
                    if timeouts >= 3:          # do not burn hours retrying a request that cannot finish
                        break
            time.sleep(min(30.0, 2 ** attempt + random.random()))
        if rate_limited:
            raise QuotaExhausted(f"{self.provider}: rate limit still active after {max_attempts} attempts. "
                                 f"Wait a few minutes (or raise LLM_MIN_INTERVAL_SECONDS) and continue with --resume.")
        raise RuntimeError(f"{self.provider} request failed after retries: {last}")

    def invoke(self, messages: List[Any], max_tokens: Optional[int] = None) -> LLMResponse:
        raise NotImplementedError


class OllamaClient(_BaseClient):
    provider = "ollama"

    def __init__(self, model=None, temperature=None, max_tokens=None, base_url=None):
        super().__init__(model or settings.OLLAMA_MODEL, temperature, max_tokens)
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")

    def invoke(self, messages, max_tokens=None):
        system, turns = _split_messages(messages)
        msgs = ([{"role": "system", "content": system}] if system else []) + turns
        pred = max_tokens or self.max_tokens or 2048
        payload: Dict[str, Any] = {
            "model": self.model, "messages": msgs, "stream": False,
            "options": {"temperature": self.temperature,
                        "num_ctx": min(settings.OLLAMA_NUM_CTX, 8192),
                        "num_predict": pred},
        }
        if settings.OLLAMA_THINK in ("low", "medium", "high"):
            payload["think"] = settings.OLLAMA_THINK
        try:
            data = self._post(f"{self.base_url}/api/chat", payload=payload)
        except RuntimeError as e:
            if "after retries" in str(e):
                raise ConnectionError(f"Could not reach Ollama at {self.base_url}. "
                                      f"Is it running ('ollama serve')? Details: {e}")
            raise
        content = data.get("message", {}).get("content", "")
        if not content and "thinking" in data.get("message", {}):
            content = data["message"]["thinking"]
        return LLMResponse(content)


class GeminiClient(_BaseClient):
    provider = "gemini"
    _FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-3.8-flash"]

    def __init__(self, model=None, temperature=None, max_tokens=None, api_key=None):
        raw_model = (model or settings.GEMINI_MODEL).strip().lower()
        # Normalise common typos and deprecated IDs
        if "3.5" in raw_model or "lite" in raw_model:
            raw_model = "gemini-flash-lite-latest"
        elif raw_model in ("gemini-flash", "flash", "gemini-2.0-flash"):
            raw_model = "gemini-flash-latest"
        super().__init__(raw_model, temperature, max_tokens)
        self.api_key = api_key or settings.GEMINI_API_KEY
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is missing (set it in .env).")

    def invoke(self, messages, max_tokens=None):
        system, turns = _split_messages(messages)
        contents = [{"role": "model" if t["role"] == "assistant" else "user",
                     "parts": [{"text": t["content"]}]} for t in turns]
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": self.temperature,
                                 "maxOutputTokens": max(max_tokens or self.max_tokens, 2048)},
        }
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}

        models_to_try = [self.model] + [m for m in self._FALLBACK_MODELS if m != self.model]
        last_err = None
        for cand_model in models_to_try:
            try:
                data = self._post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{cand_model}:generateContent",
                    headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                    payload=payload,
                )
                cands = data.get("candidates") or []
                if not cands:
                    raise RuntimeError(f"Gemini returned no candidates: {str(data.get('promptFeedback', data))[:300]}")
                parts = cands[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                if not text.strip():
                    raise RuntimeError(f"Gemini returned empty text (finishReason={cands[0].get('finishReason')}).")
                self.model = cand_model  # stick to working model
                return LLMResponse(text)
            except QuotaExhausted:
                raise
            except Exception as e:
                err_str = str(e)
                if "404" in err_str or "503" in err_str or "not found" in err_str.lower():
                    print(f"[Gemini] Model '{cand_model}' failed ({err_str[:90]}), switching to fallback...", flush=True)
                    last_err = e
                    continue
                raise
        raise RuntimeError(f"All Gemini fallback models failed: {last_err}")


class OpenAIClient(_BaseClient):
    provider = "openai"

    def __init__(self, model=None, temperature=None, max_tokens=None, api_key=None):
        super().__init__(model or settings.OPENAI_MODEL, temperature, max_tokens)
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is missing (set it in .env).")

    def invoke(self, messages, max_tokens=None):
        system, turns = _split_messages(messages)
        msgs = ([{"role": "system", "content": system}] if system else []) + turns
        payload: Dict[str, Any] = {"model": self.model, "messages": msgs,
                                   "max_completion_tokens": max_tokens or self.max_tokens}
        if not self.model.lower().startswith(("o1", "o3", "o4", "gpt-5")):
            payload["temperature"] = self.temperature
        data = self._post("https://api.openai.com/v1/chat/completions",
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                          payload=payload)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenAI returned no choices: {str(data)[:300]}")
        return LLMResponse(choices[0]["message"].get("content") or "")


class AnthropicClient(_BaseClient):
    provider = "anthropic"

    def __init__(self, model=None, temperature=None, max_tokens=None, api_key=None):
        super().__init__(model or settings.ANTHROPIC_MODEL, temperature, max_tokens)
        self.api_key = api_key or settings.ANTHROPIC_API_KEY
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is missing (set it in .env).")

    def invoke(self, messages, max_tokens=None):
        system, turns = _split_messages(messages)
        payload: Dict[str, Any] = {"model": self.model, "max_tokens": max_tokens or self.max_tokens,
                                   "temperature": self.temperature, "messages": turns}
        if system:
            payload["system"] = system
        data = self._post("https://api.anthropic.com/v1/messages",
                          headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          payload=payload)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if not text.strip():
            raise RuntimeError(f"Anthropic returned empty text: {str(data)[:300]}")
        return LLMResponse(text)


def get_llm(model: Optional[str] = None, temperature: Optional[float] = None,
            max_tokens: Optional[int] = None):
    prov = settings.LLM_PROVIDER
    if prov in ("gemini", "google"):
        return GeminiClient(model, temperature, max_tokens)
    if prov == "ollama":
        return OllamaClient(model, temperature, max_tokens)
    if prov == "openai":
        return OpenAIClient(model, temperature, max_tokens)
    if prov == "anthropic":
        return AnthropicClient(model, temperature, max_tokens)
    raise ValueError(f"Unsupported LLM_PROVIDER '{prov}'. Use ollama, gemini, openai or anthropic.")


def active_model_label() -> str:
    prov = settings.LLM_PROVIDER
    model = {"ollama": settings.OLLAMA_MODEL, "gemini": settings.GEMINI_MODEL,
             "openai": settings.OPENAI_MODEL, "anthropic": settings.ANTHROPIC_MODEL}.get(prov, "?")
    return f"{model} ({prov})"