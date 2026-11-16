"""Knowledge base + reference-document ingestion.

Why this exists: a 200-500 page annual report cannot be written from web-search snippets. The
agent therefore loads the company's REAL report(s) (downloaded automatically, or supplied by the
user with --pdf), splits them into page-tagged chunks, and every section is written from the
chunks retrieved for that section. The same documents also provide the real table of contents,
so the report structure follows what THIS company actually publishes.

Retrieval is BM25 (pure Python, no embedding model to download), which suits reports full of
names, numbers and defined terms.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence, Tuple

from config import settings
from schemas import ReportInput, SourceDocument
from tools import ResearchTools
from utils import slug, tokenize

_STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were will with".split())


@dataclass
class Chunk:
    chunk_id: int
    doc_id: str
    doc_title: str
    url: str
    page: Optional[int]
    text: str


class KnowledgeBase:
    """Page-tagged chunks with BM25 search."""

    def __init__(self):
        self.chunks: List[Chunk] = []
        self.docs: Dict[str, dict] = {}
        self._index: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        self._lengths: List[int] = []

    # ------------------------------------------------------------------ build
    def add_document(self, doc_id: str, title: str, url: str, pages: Sequence[Tuple[int, str]]) -> int:
        n_before = len(self.chunks)
        for page_no, text in pages:
            words = (text or "").split()
            if len(words) < 8:
                continue
            step = max(1, settings.CHUNK_WORDS - settings.CHUNK_OVERLAP_WORDS)
            for start in range(0, len(words), step):
                piece = words[start:start + settings.CHUNK_WORDS]
                if len(piece) < 8:
                    break
                self._add_chunk(Chunk(len(self.chunks), doc_id, title, url, page_no, " ".join(piece)))
                if start + settings.CHUNK_WORDS >= len(words):
                    break
        self.docs[doc_id] = {"title": title, "url": url, "chunks": len(self.chunks) - n_before}
        return len(self.chunks) - n_before

    def _add_chunk(self, ch: Chunk) -> None:
        self.chunks.append(ch)
        toks = tokenize(ch.text)
        self._lengths.append(len(toks))
        for term, tf in Counter(toks).items():
            self._index[term].append((ch.chunk_id, tf))

    # ----------------------------------------------------------------- search
    def search(self, query: str, k: int = 8, doc_ids: Optional[Sequence[str]] = None) -> List[Tuple[Chunk, float]]:
        if not self.chunks:
            return []
        terms = [t for t in dict.fromkeys(tokenize(query)) if t not in _STOP and len(t) > 1]
        n = len(self.chunks)
        avg = (sum(self._lengths) / n) or 1.0
        k1, b = 1.5, 0.75
        scores: Dict[int, float] = defaultdict(float)
        for t in terms:
            postings = self._index.get(t)
            if not postings:
                continue
            idf = math.log(1 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for cid, tf in postings:
                denom = tf + k1 * (1 - b + b * self._lengths[cid] / avg)
                scores[cid] += idf * (tf * (k1 + 1)) / denom
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        out: List[Tuple[Chunk, float]] = []
        for cid, sc in ranked:
            ch = self.chunks[cid]
            if doc_ids and ch.doc_id not in doc_ids:
                continue
            out.append((ch, sc))
            if len(out) >= k:
                break
        return out

    # ------------------------------------------------------------ persistence
    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"docs": self.docs, "chunks": [asdict(c) for c in self.chunks]}, f)

    @classmethod
    def load(cls, path: str) -> "KnowledgeBase":
        kb = cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        kb.docs = data.get("docs", {})
        for c in data.get("chunks", []):
            kb._add_chunk(Chunk(**c))
        return kb


# ------------------------------------------------------------------------------
# Document parsing
# ------------------------------------------------------------------------------
def parse_pdf(path: str) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str, int]], int]:
    """Return (pages, toc, page_count). pages = [(page_no, text)], toc = [(level, title, page)]."""
    try:
        import pymupdf as fitz            # type: ignore
    except ImportError:
        try:
            import fitz                   # type: ignore
        except ImportError as e:
            raise ImportError("PyMuPDF is required to read PDFs: pip install pymupdf") from e
    doc = fitz.open(path)
    pages = [(i + 1, page.get_text("text") or "") for i, page in enumerate(doc)]
    toc = [(int(l), str(t).strip(), int(p)) for l, t, p in (doc.get_toc() or [])]
    n = len(pages)
    doc.close()
    return pages, toc, n


def contents_text_from(toc: List[Tuple[int, str, int]], pages: List[Tuple[int, str]], max_chars: int = 7000) -> str:
    """The document's own table of contents, as text (bookmarks if present, else the 'Contents' page)."""
    if toc:
        lines = [f"{'  ' * (lvl - 1)}{title} (p.{pg})" for lvl, title, pg in toc if lvl <= 3]
        return "\n".join(lines)[:max_chars]
    found = []
    for page_no, text in pages[:15]:
        head = text[:400].lower()
        if "contents" in head or "table of content" in head or "index" in head[:120]:
            found.append(text)
    return "\n\n".join(found)[:max_chars]


def _html_to_pages(text: str, words_per_page: int = 500) -> List[Tuple[int, str]]:
    words = text.split()
    return [(i // words_per_page + 1, " ".join(words[i:i + words_per_page]))
            for i in range(0, len(words), words_per_page)]


def _period_tokens(fy: str) -> List[str]:
    """'FY 2025-26' -> ['2025', '2026', '25-26'] (used to check a document is for the requested period)."""
    years = re.findall(r"\d{4}", fy)
    short = re.findall(r"\d{4}\s*[-–/]\s*(\d{2})", fy)
    toks = list(years)
    for y in years:
        toks.append(str(int(y) + 1))
    toks += short
    return list(dict.fromkeys(toks))


def _detect_fiscal_year(first_pages_text: str) -> str:
    m = re.search(r"(?:annual report|fiscal year|financial year|year ended)[^\d]{0,40}(\d{4}(?:\s*[-–/]\s*\d{2,4})?)",
                  first_pages_text, flags=re.I)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    years = re.findall(r"\b(20[0-3]\d)\b", first_pages_text)
    return Counter(years).most_common(1)[0][0] if years else ""


# ------------------------------------------------------------------------------
# Discovery + ingestion
# ------------------------------------------------------------------------------
class ReferenceIngestor:
    """Finds/loads the company's real annual report(s) into the knowledge base."""

    def __init__(self, tools: ResearchTools, kb: KnowledgeBase, run_dir: str):
        self.tools = tools
        self.kb = kb
        self.dir = os.path.join(run_dir, "sources")
        self.contents: Dict[str, str] = {}     # doc_id -> its own table of contents text
        os.makedirs(self.dir, exist_ok=True)

    def ingest(self, spec: ReportInput) -> List[SourceDocument]:
        docs: List[SourceDocument] = []
        for src in spec.source_documents:
            d = self._ingest_one(src, spec, kind="user_supplied", must_mention_company=False)
            if d:
                docs.append(d)
            else:
                print(f"  [Ingest] Could not load supplied source: {src}")
        if not docs:
            docs = self._discover(spec)
        docs.sort(key=lambda d: (not bool(d.matches_requested_period), -d.pages))
        return docs

    # ---------------------------------------------------------------- discover
    def _discover(self, spec: ReportInput) -> List[SourceDocument]:
        co, fy = spec.company_name, spec.financial_year
        queries = [
            f"{co} annual report {fy} pdf",
            f"{co} integrated annual report pdf",
            f"{co} annual report {_period_tokens(fy)[0] if _period_tokens(fy) else ''} investor relations",
            f"{co} 10-K OR 20-F OR annual report filing",
        ]
        cands: Dict[str, Tuple[int, str]] = {}
        co_tok = [t for t in tokenize(co) if len(t) > 2]
        year_toks = _period_tokens(fy)
        for q in queries:
            for r in self.tools.web_search(q, max_results=8):
                url, title = r["href"], r.get("title", "")
                low = (url + " " + title).lower()
                score = 0
                score += 3 if url.lower().split("?")[0].endswith(".pdf") else 0
                score += 2 if "annual" in low else 0
                score += 1 if any(t in low for t in co_tok) else 0
                score += 2 if any(y in low for y in year_toks) else 0
                score += 1 if any(k in low for k in ("sec.gov", "investor", "bseindia", "nseindia", "/ir")) else 0
                if any(bad in low for bad in ("wikipedia", "youtube", "facebook", "linkedin", "glassdoor")):
                    score = 0
                if score >= 4:
                    cands[url] = (max(score, cands.get(url, (0, ""))[0]), title)
        ranked = sorted(cands.items(), key=lambda kv: -kv[1][0])[:8]
        docs: List[SourceDocument] = []
        for url, (_, title) in ranked:
            if len(docs) >= settings.MAX_REFERENCE_DOCS:
                break
            d = self._ingest_one(url, spec, kind="reference_report", must_mention_company=True, title_hint=title)
            if d:
                docs.append(d)
        return docs

    # ------------------------------------------------------------------ ingest
    def _ingest_one(self, src: str, spec: ReportInput, kind: str, must_mention_company: bool,
                    title_hint: str = "") -> Optional[SourceDocument]:
        try:
            is_url = src.lower().startswith(("http://", "https://"))
            local = src
            if is_url:
                if src.lower().split("?")[0].endswith(".pdf") or "pdf" in src.lower():
                    local = self.tools.download_pdf(src, self.dir) or ""
                else:
                    local = ""
            if local and os.path.exists(local) and local.lower().endswith(".pdf"):
                pages, toc, n_pages = parse_pdf(local)
                contents = contents_text_from(toc, pages)
            elif is_url:
                text = self.tools.scrape_url(src, max_chars=800_000)
                if len(text.split()) < 3000:
                    return None
                pages, toc, n_pages = _html_to_pages(text), [], 0
                contents = ""
                n_pages = len(pages)
            else:
                return None

            head = " ".join(t for _, t in pages[:6]).lower()
            if must_mention_company:
                toks = [t for t in tokenize(spec.company_name) if len(t) > 2]
                if toks and not any(t in head for t in toks):
                    return None
                if n_pages < 10:
                    return None

            fy_detected = _detect_fiscal_year(" ".join(t for _, t in pages[:3]))
            wanted = _period_tokens(spec.financial_year)
            matches = any(w in head for w in wanted) if wanted else None

            doc_id = "doc_" + slug(os.path.basename(local) if local else src, 30)
            title = title_hint or os.path.basename(local or src)
            if fy_detected:
                title = f"{title} [{fy_detected}]"
            added = self.kb.add_document(doc_id, title, src if is_url else "", pages)
            self.contents[doc_id] = contents
            print(f"  [Ingest] {title}: {n_pages} pages, {added} chunks"
                  f"{'' if matches else '  (period may differ from requested)'}")
            return SourceDocument(doc_id=doc_id, title=title, url=src if is_url else "", local_path=local,
                                  kind=kind, fiscal_year=fy_detected, matches_requested_period=matches,
                                  pages=n_pages)
        except Exception as e:
            print(f"  [Ingest] Skipped {src}: {e}")
            return None