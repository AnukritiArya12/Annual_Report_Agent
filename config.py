"""Configuration and LLM provider clients (Ollama, Gemini, OpenAI, Anthropic).

All clients expose the same method:  client.invoke([{"role": "...", "content": "..."}]) -> obj.content
They retry on rate limits / timeouts and RAISE on real failures. There is no silent fallback:
a failed call must never turn into invented report text.
"""

from __future__ import annotations

import os
import random
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
    LLM_RETRIES: int = _int("LLM_RETRIES", 6)

    # --- Report size (everything is derived from these, nothing is fixed) ----
    WORDS_PER_PAGE: int = _int("WORDS_PER_PAGE", 420)
    MIN_PAGES: int = _int("MIN_PAGES", 30)
    MAX_PAGES: int = _int("MAX_PAGES", 500)
    LEAF_MAX_WORDS: int = _int("LEAF_MAX_WORDS", 700)   # a section bigger than this is split further
    MIN_LEAF_WORDS: int = _int("MIN_LEAF_WORDS", 120)
    MAX_EXPANSION_DEPTH: int = _int("MAX_EXPANSION_DEPTH", 3)

    # --- Knowledge base / research ------------------------------------------
    MAX_REFERENCE_DOCS: int = _int("MAX_REFERENCE_DOCS", 2)
    MAX_DOWNLOAD_MB: int = _int("MAX_DOWNLOAD_MB", 150)
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
        for attempt in range(max(1, settings.LLM_RETRIES)):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=settings.REQUEST_TIMEOUT_SECONDS)
                if r.status_code in (429, 500, 502, 503, 504):
                    last = RuntimeError(f"{self.provider} HTTP {r.status_code}: {r.text[:300]}")
                    try:
                        wait = float(r.headers.get("retry-after", 0))
                    except ValueError:
                        wait = 0.0
                    if wait:
                        time.sleep(min(90.0, wait))
                elif not r.ok:
                    raise RuntimeError(f"{self.provider} API error [{r.status_code}]: {r.text[:600]}")
                else:
                    return r.json()
            except (requests.Timeout, requests.ConnectionError) as e:
                last = e
            time.sleep(min(60.0, 2 ** attempt + random.random()))
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
        payload: Dict[str, Any] = {
            "model": self.model, "messages": msgs, "stream": False,
            "options": {"temperature": self.temperature,
                        "num_ctx": settings.OLLAMA_NUM_CTX,
                        # reasoning models (gpt-oss, qwen3) spend tokens on thinking: never go very low
                        "num_predict": max(max_tokens or self.max_tokens, 4096)},
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
        return LLMResponse(data.get("message", {}).get("content", ""))


class GeminiClient(_BaseClient):
    provider = "gemini"

    def __init__(self, model=None, temperature=None, max_tokens=None, api_key=None):
        # Gemini model ids are lowercase ("gemini-2.5-flash-lite"); normalise typos like "...-Lite"
        super().__init__((model or settings.GEMINI_MODEL).strip().lower(), temperature, max_tokens)
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
                                 # thinking tokens count against this limit, so never go very low
                                 "maxOutputTokens": max(max_tokens or self.max_tokens, 4096)},
        }
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        data = self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
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
        return LLMResponse(text)


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