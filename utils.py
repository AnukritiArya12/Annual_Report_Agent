"""Small shared helpers (text cleaning, JSON extraction, similarity)."""

import re
from typing import Any, List, Optional, Sequence, Set, Tuple


_INJECTION_LINE_PATTERNS = [
    re.compile(r"^\s*(?:system|user|assistant|human|ai)\s*:\s*", re.IGNORECASE),
    re.compile(r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts|rules|commands)", re.IGNORECASE),
    re.compile(r"(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be)\s+(?:a|an|the|my)?\s*", re.IGNORECASE),
    re.compile(r"<\s*\|(?:im_start|im_end|endoftext)\|\s*>", re.IGNORECASE),
    re.compile(r"\[\s*/?(?:inst|system|prompt)\s*\]", re.IGNORECASE),
    re.compile(r"<\s*/?(?:system|prompt|instruction)\s*>", re.IGNORECASE),
    re.compile(r"^\s*(?:override|bypass)\s+(?:all\s+)?(?:safety|system|guardrail)\s+(?:rules|instructions)", re.IGNORECASE),
    re.compile(r"^\s*(?:new|updated|secret)\s+(?:instructions|guidelines|directive)\s*:\s*", re.IGNORECASE),
]

_HIDDEN_HTML_PATTERNS = [
    re.compile(r"<[^>]+style\s*=\s*['\"][^'\"]*display\s*:\s*none[^'\"]*['\"][^>]*>.*?<\s*/\s*[^>]+>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<[^>]+style\s*=\s*['\"][^'\"]*visibility\s*:\s*hidden[^'\"]*['\"][^>]*>.*?<\s*/\s*[^>]+>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<[^>]+style\s*=\s*['\"][^'\"]*font-size\s*:\s*0(?:px)?['\"][^>]*>.*?<\s*/\s*[^>]+>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<!--.*?-->", re.DOTALL),
]


def sanitize_untrusted(text: str, log_removals: bool = True) -> str:
    """Neutralize and strip prompt injection attempts and hidden text from untrusted inputs.
    
    Removes instruction overrides, role markers, and hidden HTML payloads.
    Logs count of neutralized patterns.
    """
    if not text:
        return ""
    removed_count = 0
    cleaned = text

    # 1. Strip hidden HTML injection payloads
    for pat in _HIDDEN_HTML_PATTERNS:
        matches = len(pat.findall(cleaned))
        if matches:
            removed_count += matches
            cleaned = pat.sub("", cleaned)

    # 2. Filter instruction-like lines
    lines = cleaned.splitlines()
    safe_lines = []
    for line in lines:
        is_injection = False
        for pat in _INJECTION_LINE_PATTERNS:
            if pat.search(line):
                is_injection = True
                removed_count += 1
                break
        if not is_injection:
            safe_lines.append(line)
        else:
            safe_lines.append("[UNTRUSTED CONTENT REMOVED]")

    if removed_count > 0 and log_removals:
        print(f"  [Security] Neutralized {removed_count} prompt-injection / hidden pattern(s) in untrusted text.")

    return "\n".join(safe_lines)


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


def find_org_names(text: str, allow_list: Optional[Sequence[str]] = None) -> List[str]:
    """Organisation names in text (regex on corporate suffixes), skipping names in the allow-list."""
    body = (text or "").split("*Sources:")[0]
    body = re.sub(r",\s+(?=(?:Inc|Corp|Ltd|LLC|LLP|PLC|plc|Limited|Corporation|Co)\b)", " ", body)
    out: List[str] = []
    seen: Set[str] = set()

    allow_cores = set(_company_cores(allow_list or []))

    # Organisation names written with a corporate suffix (Inc, Ltd, Limited, Group, ...)
    for m in _ORG_RE.finditer(body):
        words = m.group(1).split()
        while words and words[0].lower().strip(".") in _LEAD_STOP:
            words.pop(0)
        disp = " ".join(words).strip()
        core = org_core(disp)
        if not core or core in seen or core in allow_cores:
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
                     check_evidence: bool = True, allow_list: Sequence[str] = ()) -> List[Tuple[str, str, str]]:
    """Organisations mentioned in a written section that should not be there.
    Returns [(display name, normalised key, reason)]. A name is OK when
      * the human marked it 'allowed', or
      * it is the company itself (any approved name) or in the allow-list (parent, subsidiaries, auditor, regulators), or
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
    comp = set(_company_cores(list(names) + list(allow_list or [])))
    for disp in find_org_names(body, allow_list=list(names) + list(allow_list or [])):
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
