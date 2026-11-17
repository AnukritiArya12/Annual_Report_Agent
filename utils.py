"""Small shared helpers (text cleaning, JSON extraction, similarity)."""

import json
import re
from typing import Any, List, Set


def clean_llm_text(text: str) -> str:
    """Remove reasoning blocks and an outer code fence from an LLM reply."""
    text = text or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"^.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)  # unclosed opener
    text = text.strip()
    m = re.match(r"^```(?:markdown|md|json)?\s*\n(.*?)\n```$", text, flags=re.DOTALL)
    return (m.group(1) if m else text).strip()


def extract_json(text: str) -> Any:
    """Return the largest valid JSON object/array embedded in an LLM reply."""
    text = clean_llm_text(text)
    text = re.sub(r"```(?:json)?", "", text)
    decoder = json.JSONDecoder()
    best = None
    best_len = -1
    pos = 0
    for m in re.finditer(r"[\{\[]", text):
        if m.start() < pos:
            continue
        try:
            obj, end = decoder.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, (dict, list)) and end > best_len:
            best, best_len = obj, end
        pos = m.start() + end
    if best is None:
        raise ValueError("No valid JSON found in LLM output")
    return best


def slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:n] or "item"


def word_count(text: str) -> int:
    return len((text or "").split())


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def shingle_set(text: str, n: int = 5) -> Set[int]:
    """Hashed word n-grams, used to detect repeated passages across sections."""
    # ignore table rows and numbers-only noise
    body = "\n".join(l for l in (text or "").splitlines() if not l.strip().startswith("|"))
    toks = tokenize(body)
    if len(toks) < n:
        return set()
    return {hash(" ".join(toks[i:i + n])) for i in range(len(toks) - n + 1)}


def digest(text: str, max_chars: int = 400) -> str:
    """Short prose digest of a section (skips headings/tables) for the coverage ledger."""
    lines = [l.strip() for l in (text or "").splitlines()
             if l.strip() and not l.strip().startswith(("#", "|", "*Sources"))]
    out = " ".join(lines)
    out = re.sub(r"\s+", " ", out)
    return out[:max_chars] + ("..." if len(out) > max_chars else "")


# Sections whose evidence was missing are replaced by this one-line notice instead of tables full of "Not disclosed".
SPARSE_MARK = "*Not available in the public sources reviewed."
