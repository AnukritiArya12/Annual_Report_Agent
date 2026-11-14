"""Configuration module for AI-Based Generic Annual Report Generation System.

Centralizes environment settings, directories, and LLM configuration.
Supports local Ollama models (gpt-oss:20b, qwen3.5:4b) as well as
Google Gemini (Free tier / Student access) with sub-second execution.
"""

import os
from pathlib import Path

# Safely load .env if python-dotenv is present
try:
    from dotenv import load_dotenv
    # Existing shell configuration wins, as dotenv's standard behavior intends.
    load_dotenv()
except ImportError:
    pass

# Windows treats environment variable names case-insensitively, while dotenv
# preserves their spelling. Normalize legacy model spelling only; provider
# selection is resolved explicitly below from the project .env.
for _canonical, _aliases in {
    "OLLAMA_MODEL": ("OLLAMA_MODEL", "ollama_model"),
}.items():
    if not os.getenv(_canonical):
        _alias_value = next((os.getenv(_name) for _name in _aliases[1:] if os.getenv(_name)), None)
        if _alias_value:
            os.environ[_canonical] = _alias_value

# Resolve provider deterministically: shell setting first; otherwise the last
# canonical LLM_PROVIDER entry in this project's .env. The legacy mixed-case
# name is used only if there is no canonical entry.
try:
    from dotenv import dotenv_values
    _env_path = Path(__file__).resolve().parent / ".env"
    _project_env = dotenv_values(_env_path)
    _canonical_providers = []
    _legacy_provider = None
    if _env_path.exists():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _match = __import__("re").match(r"^\s*([^#=\s]+)\s*=\s*(.*?)\s*$", _line)
            if _match:
                _name, _value = _match.groups()
                if _name == "LLM_PROVIDER" and _value:
                    _canonical_providers.append(_value)
                elif _name == "LLM_Provider" and _value:
                    _legacy_provider = _value
    _dotenv_provider = (_canonical_providers[-1] if _canonical_providers else _legacy_provider)
    if not os.getenv("LLM_PROVIDER") and _dotenv_provider:
        os.environ["LLM_PROVIDER"] = _dotenv_provider
    if not os.getenv("OLLAMA_MODEL") and _project_env.get("ollama_model"):
        os.environ["OLLAMA_MODEL"] = _project_env["ollama_model"]
except ImportError:
    pass


class Settings:
    """System settings loaded from environment or .env."""

    # Project Directories
    BASE_DIR: Path = Path(__file__).resolve().parent
    RUNS_DIR: Path = Path(__file__).resolve().parent / "runs"
    SKILLS_DIR: Path = Path(__file__).resolve().parent / "skills"

    # Knowledge Base / Storage
    SQLITE_DB_NAME: str = os.getenv("SQLITE_DB_NAME", "annual_reports.db")
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "chroma_store")

    # Prefer the OpenAI API when its key is configured; explicit provider wins.
    # Accept a few common spelling variants for compatibility with existing .env files.
    LLM_PROVIDER: str = (
        os.getenv("LLM_PROVIDER") or
        ("openai" if (os.getenv("OPENAI_API_KEY") or "").strip() else "ollama")
    ).lower().strip()
    
    # Ollama Settings
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen3.5:4b").strip()

    # Google Gemini Settings (Free Tier / Student Offer)
    GEMINI_API_KEY: str = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()

    # OpenAI Settings
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o").strip()

    # Workflow parameters - bounded limits for fast, reliable execution
    DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", "0.2"))
    MAX_VALIDATION_RETRIES: int = int(os.getenv("MAX_VALIDATION_RETRIES", "1"))
    REQUEST_TIMEOUT_SECONDS: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "45"))
    MAX_PARALLEL_WORKERS: int = int(os.getenv("MAX_PARALLEL_WORKERS", "2"))
    MAX_RESEARCH_STEPS: int = int(os.getenv("MAX_RESEARCH_STEPS", "2"))
    MAX_SEARCH_RESULTS: int = int(os.getenv("MAX_SEARCH_RESULTS", "3"))
    MAX_EVIDENCE_CHARS: int = int(os.getenv("MAX_EVIDENCE_CHARS", "10000"))


# Global singleton settings
settings = Settings()


class OllamaClient:
    """Native Ollama client using HTTP REST API."""
    def __init__(self, base_url: str = None, model: str = None, temperature: float = None):
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self.temperature = temperature if temperature is not None else settings.DEFAULT_TEMPERATURE

    def invoke(self, messages):
        """Invoke Ollama chat API."""
        payload_messages = []
        for m in messages:
            if isinstance(m, dict):
                payload_messages.append(m)
            elif hasattr(m, "role") and hasattr(m, "content"):
                payload_messages.append({"role": m.role, "content": m.content})
            elif hasattr(m, "type") and hasattr(m, "content"):
                role = "system" if m.type == "system" else ("user" if m.type in ("human", "user") else "assistant")
                payload_messages.append({"role": role, "content": m.content})
            else:
                payload_messages.append({"role": "user", "content": str(m)})

        import requests
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": payload_messages,
                    "stream": False,
                    "options": {"temperature": self.temperature}
                },
                timeout=min(120, getattr(settings, "REQUEST_TIMEOUT_SECONDS", 120))
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"Could not connect to Ollama at {self.base_url} ({e}). "
                "Ensure Ollama is running ('ollama serve' or start the Ollama desktop app)."
            )

        class LLMResponse:
            def __init__(self, text):
                self.content = text
        return LLMResponse(content)


class GeminiClient:
    """Native Google Gemini client using Google's official REST API.
    
    Zero compiled dependencies, ultra-fast, and compatible with free student API keys.
    """
    def __init__(self, api_key: str = None, model: str = None, temperature: float = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        if not self.api_key:
            raise ValueError(
                "Gemini API Key missing! Please set GEMINI_API_KEY=AIzaSy... in your .env file."
            )
        raw_model = (model or settings.GEMINI_MODEL or "gemini-2.5-flash").strip()
        if "3.5" in raw_model or "Lite" in raw_model:
            raw_model = "gemini-2.5-flash"
        self.model = raw_model
        self.temperature = temperature if temperature is not None else settings.DEFAULT_TEMPERATURE

    def invoke(self, messages):
        """Invoke Google Gemini generateContent API."""
        import requests
        
        system_text = ""
        user_parts = []

        for m in messages:
            role = m.get("role", "") if isinstance(m, dict) else getattr(m, "role", getattr(m, "type", ""))
            content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", str(m))
            
            if role in ("system",):
                system_text += content + "\n\n"
            else:
                user_parts.append({"text": content})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        
        payload = {
            "contents": [{"role": "user", "parts": user_parts}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": 65536
            }
        }
        if system_text.strip():
            payload["system_instruction"] = {
                "parts": [{"text": system_text.strip()}]
            }

        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, headers=headers, json=payload, timeout=300)
        
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API Error [{resp.status_code}]: {resp.text}")

        res_json = resp.json()
        candidates = res_json.get("candidates", [])
        if not candidates:
            return type("LLMResponse", (), {"content": ""})()

        parts = candidates[0].get("content", {}).get("parts", [])
        generated_text = "".join(p.get("text", "") for p in parts)

        class LLMResponse:
            def __init__(self, text):
                self.content = text
        return LLMResponse(generated_text)


class OpenAIClient:
    """Small OpenAI-compatible chat client using the existing requests dependency."""

    def __init__(self, api_key: str = None, model: str = None, temperature: float = None):
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            raise ValueError("OpenAI API key missing. Set OPENAI_API_KEY in the project .env file.")
        self.model = model or settings.OPENAI_MODEL
        self.temperature = temperature if temperature is not None else settings.DEFAULT_TEMPERATURE

    def invoke(self, messages):
        import requests

        payload_messages = []
        for message in messages:
            if isinstance(message, dict):
                role, content = message.get("role", "user"), message.get("content", "")
            else:
                role = getattr(message, "role", getattr(message, "type", "user"))
                if role == "human":
                    role = "user"
                content = getattr(message, "content", str(message))
            payload_messages.append({"role": role, "content": content})

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": payload_messages,
                "temperature": self.temperature,
            },
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
        )
        if not response.ok:
            try:
                detail = response.json().get("error", {}).get("message", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f"OpenAI API error [{response.status_code}]: {detail}")

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"OpenAI returned no choices: {data}")
        return type("LLMResponse", (), {"content": choices[0]["message"].get("content", "")})()


def get_llm(model: str = None, temperature: float = None):
    """Instantiate the Chat Model configured in .env.
    
    Supports:
    - 'ollama' (Local free models: gpt-oss:20b, qwen3.5:4b)
    - 'gemini' (Free Student / Developer Google AI Studio API)
    - 'openai' (OpenAI GPT-4o)
    """
    prov = settings.LLM_PROVIDER
    temp = temperature if temperature is not None else settings.DEFAULT_TEMPERATURE

    if prov in ("gemini", "google"):
        return GeminiClient(
            api_key=settings.GEMINI_API_KEY,
            model=model or settings.GEMINI_MODEL,
            temperature=temp
        )
    elif prov == "ollama":
        return OllamaClient(
            base_url=settings.OLLAMA_BASE_URL,
            model=model or settings.OLLAMA_MODEL,
            temperature=temp
        )
    elif prov == "openai":
        if not settings.OPENAI_API_KEY:
            raise ValueError("LLM_PROVIDER=openai requires OPENAI_API_KEY in .env or the environment.")
        return OpenAIClient(
            model=model or settings.OPENAI_MODEL,
            api_key=settings.OPENAI_API_KEY,
            temperature=temp,
        )
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER '{prov}'. Use 'openai', 'gemini', or 'ollama'.")
