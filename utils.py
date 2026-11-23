"""Small shared helpers (text cleaning, JSON extraction, similarity)."""

import json
import re
from typing import Any, List, Set, Tuple


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


# =====================================================================================================
# Entity guard: find content that is about some OTHER organisation (generic: no company hard-coded)
# =====================================================================================================
_CORP_WORDS = {"inc", "incorporated", "corp", "corporation", "ltd", "limited", "plc", "llc", "llp", "lp", "gmbh",
               "ag", "sa", "nv", "pvt", "private", "co", "company", "holdings", "holding", "group", "bank",
               "the", "and", "of"}
_LEAD_STOP = {"the", "our", "its", "their", "this", "that", "these", "those", "in", "on", "at", "by", "for", "from",
              "with", "under", "during", "after", "before", "since", "as", "and", "also", "however", "moreover",
              "following", "while", "whereas", "both", "between", "among", "to", "we", "an", "a", "subsequent",
              "additionally", "furthermore", "overall", "meanwhile", "although", "if", "when", "where", "which"}
_ORG_RE = re.compile(
    r"\b((?:(?:[A-Z][A-Za-z0-9&'\-.]*|&)\s+){1,5}"
    r"(?:Inc|Incorporated|Corp|Corporation|Ltd|Limited|PLC|plc|LLC|LLP|GmbH|AG|SA|NV|Pvt|Co|Company|Holdings|Group|Bank)\b\.?)")


def norm_text(s: str) -> str:
    """lowercase, only letters/digits separated by single spaces, padded with spaces (for whole-word 'in' tests)."""
    return " " + re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip() + " "


def org_core(name: str) -> str:
    """'Google LLC' -> 'google', 'The Coca-Cola Company' -> 'coca cola' (legal suffixes and filler removed)."""
    toks = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    while toks and toks[0] in _LEAD_STOP:
        toks.pop(0)
    return " ".join(t for t in toks if t not in _CORP_WORDS)


def find_org_names(text: str) -> List[str]:
    """Organisation-like names in a text (a capitalised name ending in Inc/Ltd/Corp/Group/Bank/...)."""
    body = (text or "").split("*Sources:")[0]
    body = re.sub(r",\s+(?=(?:Inc|Corp|Ltd|LLC|LLP|PLC|plc|Limited|Corporation|Co)\b)", " ", body)   # 'Amazon.com, Inc.'
    out, seen = [], set()
    for m in _ORG_RE.finditer(body):
        words = m.group(1).split()
        while words and words[0].lower().strip(".") in _LEAD_STOP:
            words.pop(0)
        disp = " ".join(words).strip()
        core = org_core(disp)
        if not core or core in seen:
            continue
        seen.add(core)
        out.append(disp)
    return out


def _company_cores(names) -> List[str]:
    return [c for c in dict.fromkeys(org_core(n) for n in names if n) if c]


def mentions_company(text: str, names) -> bool:
    nt = norm_text(text)
    return any(f" {c} " in nt for c in _company_cores(names))


def _blocked(excluded, statuses) -> List[Tuple[str, str, str]]:
    """(core, display, why) for every name the human excluded or marked forbidden (unless later allowed)."""
    statuses = statuses or {}
    items = []
    for ex in excluded or []:
        k = org_core(ex) or norm_text(ex).strip()
        if k and statuses.get(k) != "allowed":
            items.append((k, ex, "excluded by you"))
    for k, st in statuses.items():
        if st == "forbidden":
            items.append((k, k, "marked forbidden by you"))
    return items


def foreign_entities(text: str, names, excluded=(), evidence_text: str = "", statuses=None,
                     check_evidence: bool = True) -> List[Tuple[str, str, str]]:
    """Organisations mentioned in a written section that should not be there.
    Returns [(display name, normalised key, reason)]. A name is OK when
      * the human marked it 'allowed', or
      * it is the company itself (any approved name), or
      * it also appears in the evidence the section was written from (auditor, peer, customer, own subsidiary ...).
    A name is flagged when it is excluded/forbidden by the human, or (check_evidence=True) is in none of the above,
    which means the model wrote it from its own memory.  check_evidence=False only applies the human's lists."""
    statuses = statuses or {}
    body = (text or "").split("*Sources:")[0]
    nt = norm_text(body)
    out: List[Tuple[str, str, str]] = []
    seen = set()
    for k, disp, why in _blocked(excluded, statuses):
        if f" {k} " in nt and k not in seen:
            seen.add(k)
            out.append((disp, k, why))
    if not check_evidence:
        return out
    ev = norm_text(evidence_text) if evidence_text else ""
    comp = set(_company_cores(names))
    for disp in find_org_names(body):
        k = org_core(disp)
        if k in seen or statuses.get(k) == "allowed" or k in comp:
            continue
        toks = k.split()
        if ev and any(f" {' '.join(toks[i:])} " in ev for i in range(0, min(2, len(toks) - 1) + 1)):
            continue
        seen.add(k)
        out.append((disp, k, "forbidden by you" if statuses.get(k) == "forbidden"
                    else "not found in this company's evidence (written from the model's own knowledge?)"))
    return out


def chunk_foreign_reason(text: str, names, excluded=(), statuses=None, strict_org: bool = False) -> str:
    """'' if an evidence chunk is fine to use; otherwise why it looks like it is about another organisation.
    A chunk that names the company is always kept. strict_org=True (web pages) also drops chunks that name some
    other organisation without naming the company at all."""
    if mentions_company(text, names):
        return ""
    nt = norm_text(text)
    for k, disp, why in _blocked(excluded, statuses):
        if f" {k} " in nt:
            return f"mentions '{disp}' ({why}) and not the company"
    if strict_org:
        comp = set(_company_cores(names))
        for disp in find_org_names(text):
            k = org_core(disp)
            if k and k not in comp and (statuses or {}).get(k) != "allowed":
                return f"is about another organisation ('{disp}')"
    return ""