"""Knowledge base + reference-document ingestion.

Why this exists: a 200-500 page annual report cannot be written from web-search snippets. The
agent therefore loads the company's REAL report(s) (downloaded automatically, or supplied by the
user with --pdf), splits them into page-tagged chunks, and every section is written from the
chunks retrieved for that section. The same documents also provide the real table of contents,
so the report structure follows what THIS company actually publishes.

The agent finds its sources by itself (the user never has to supply a file):
  1. SEC EDGAR   - the company's own 10-K / 20-F / 40-F plus structured XBRL financial facts
  2. web search  - official annual-report PDFs, also by crawling the company's investor-relations pages
  3. web corpus  - if no report can be found: a broad set of public pages (company site, filings summaries,
                   news, reference sites) fetched in full and indexed, instead of thin search snippets
A report passed with --pdf is optional and only overrides this.

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
from tools import ResearchTools, rows_to_markdown
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
            for piece in self._chunk_page(text or ""):
                self._add_chunk(Chunk(len(self.chunks), doc_id, title, url, page_no, piece))
        added = len(self.chunks) - n_before
        info = self.docs.setdefault(doc_id, {"title": title, "url": url, "chunks": 0})
        info["chunks"] += added
        return added

    # ---- chunking: prose in overlapping word windows; TABLES stay whole (rows and columns intact)
    @staticmethod
    def _window(words: List[str]) -> List[str]:
        step = max(1, settings.CHUNK_WORDS - settings.CHUNK_OVERLAP_WORDS)
        out = []
        for start in range(0, len(words), step):
            piece = words[start:start + settings.CHUNK_WORDS]
            if len(piece) >= 8:
                out.append(" ".join(piece))
            if start + settings.CHUNK_WORDS >= len(words):
                break
        return out

    @staticmethod
    def _is_table(block: str) -> bool:
        return any(l.lstrip().startswith("|") for l in block.splitlines())

    def _table_chunks(self, block: str) -> List[str]:
        lines = [l for l in block.splitlines() if l.strip()]
        cap = [l for l in lines if not l.lstrip().startswith("|")]
        pipe = [l for l in lines if l.lstrip().startswith("|")]
        head, rows = pipe[:2], pipe[2:]
        limit = settings.CHUNK_WORDS * 2
        mk = lambda rs: "\n".join(cap + head + rs)
        if len(" ".join(pipe).split()) <= limit:
            return [mk(rows)]
        chunks, cur, w = [], [], 0
        for r in rows:
            rw = len(r.split())
            if cur and w + rw > limit:
                chunks.append(mk(cur))
                cur, w = [], 0
            cur.append(r)
            w += rw
        if cur:
            chunks.append(mk(cur))
        return chunks

    def _chunk_page(self, text: str) -> List[str]:
        if not self._is_table(text):
            return self._window(text.split())
        out: List[str] = []
        prose: List[str] = []
        for block in re.split(r"\n\s*\n", text):
            if self._is_table(block):
                if prose:
                    out += self._window(" ".join(prose).split())
                    prose = []
                out += self._table_chunks(block)
            else:
                prose.append(block)
        if prose:
            out += self._window(" ".join(prose).split())
        return out

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
def _pdf_tables_markdown(page) -> str:
    """Real tables of a PDF page as Markdown (PyMuPDF find_tables). Appended to the page text."""
    try:
        found = page.find_tables()
        out = []
        for tb in getattr(found, "tables", []) or []:
            md = rows_to_markdown(tb.extract())
            if md:
                out.append(md)
        return ("\n\n" + "\n\n".join(out)) if out else ""
    except Exception:
        return ""


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
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        if settings.PDF_TABLES:
            text += _pdf_tables_markdown(page)
        pages.append((i + 1, text))
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
    """Pseudo-pages from structured text. Tables are never cut; long prose is split by words."""
    blocks: List[str] = []
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        words = block.split()
        if not any(l.lstrip().startswith("|") for l in block.splitlines()) and len(words) > words_per_page:
            blocks += [" ".join(words[i:i + words_per_page]) for i in range(0, len(words), words_per_page)]
        else:
            blocks.append(block)
    pages, cur, w = [], [], 0
    for b in blocks:
        bw = len(b.split())
        if cur and w + bw > words_per_page:
            pages.append("\n\n".join(cur))
            cur, w = [], 0
        cur.append(b)
        w += bw
    if cur:
        pages.append("\n\n".join(cur))
    return [(i + 1, p) for i, p in enumerate(pages)]


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
# Autonomous source discovery + ingestion
# ------------------------------------------------------------------------------
_CORP_WORDS = {"inc", "corp", "corporation", "ltd", "limited", "plc", "co", "company", "the", "holdings", "group", "sa", "ag", "nv"}
_BAD_DOMAINS = ("wikipedia", "youtube", "facebook", "linkedin", "glassdoor", "instagram", "twitter", "x.com",
                "pinterest", "reddit", "quora", "tiktok", "amazon.", "flipkart")
_NOT_ANNUAL = ("quarter", "q1", "q2", "q3", "q4", "presentation", "press-release", "pressrelease", "earnings",
               "transcript", "newsletter", "factsheet", "fact-sheet", "notice-of", "proxy", "esg-data", "ppt")


def _target_end_year(fy: str) -> Optional[int]:
    """'FY 2024-25' -> 2025, 'FY 2025' -> 2025, '2024-2025' -> 2025."""
    years = re.findall(r"\d{4}", fy)
    if not years:
        return None
    if re.search(r"\d{4}\s*[-\u2013/]\s*\d{2}\b", fy):
        return int(years[0]) + 1
    return int(years[-1])


def _contents_from_items(text: str) -> str:
    """Table-of-contents substitute for 10-K style filings: the 'Item N. Title' headings, in order."""
    seen, out = set(), []
    for m in re.finditer(r"Item\s+(\d{1,2}[A-C]?)\.?\s+([A-Z][A-Za-z ,&'\u2019\-/]{3,70})", text[:400000]):
        key = m.group(1).upper()
        if key not in seen:
            seen.add(key)
            out.append(f"Item {key}. {m.group(2).strip()}")
    return "\n".join(out)


class ReferenceIngestor:
    """Finds the company's real report(s) on its own and loads them into the knowledge base."""

    def __init__(self, tools: ResearchTools, kb: KnowledgeBase, run_dir: str):
        self.tools = tools
        self.kb = kb
        self.dir = os.path.join(run_dir, "sources")
        self.contents: Dict[str, str] = {}     # doc_id -> table-of-contents text of that document
        os.makedirs(self.dir, exist_ok=True)

    # ------------------------------------------------------------------- main
    def ingest(self, spec: ReportInput) -> List[SourceDocument]:
        self._check_search(spec)
        docs: List[SourceDocument] = []
        for src in spec.source_documents:                              # optional manual override
            d = self._ingest_one(src, spec, kind="user_supplied", url_ok=True)
            if d:
                docs.append(d)
        if not docs:
            print("  [Ingest] Strategy 1/3: SEC EDGAR filings")
            docs = self._edgar(spec)
        if not docs:
            print("  [Ingest] Strategy 2/3: official annual-report PDFs (search + investor-relations pages)")
            docs = self._discover_reports(spec)
        if not docs:
            print("  [Ingest] Strategy 3/3: broad public web corpus (no annual report could be downloaded)")
            docs = self._web_corpus(spec)
        docs.sort(key=lambda d: (not bool(d.matches_requested_period), -d.pages))
        return docs

    def _check_search(self, spec: ReportInput) -> None:
        if not self.tools.web_search(f"{spec.company_name} annual report", max_results=3):
            print("  [Ingest] WARNING: web search returned nothing "
                  f"({self.tools.last_search_error or 'no error reported'}). Check your internet connection and "
                  "run 'pip install -U ddgs'. Search engines also rate-limit: wait a few minutes and retry.")

    # ---------------------------------------------------------- 1. SEC EDGAR
    def _edgar(self, spec: ReportInput) -> List[SourceDocument]:
        try:
            return self._edgar_inner(spec)
        except Exception as e:
            print(f"  [Ingest]   EDGAR step failed: {e}")
            return []

    def _edgar_inner(self, spec: ReportInput) -> List[SourceDocument]:
        hdr = {"User-Agent": settings.SEC_USER_AGENT}
        data = self.tools.fetch_json("https://www.sec.gov/files/company_tickers.json", hdr)
        if not data:
            print(f"  [Ingest]   EDGAR not reachable ({self.tools.last_fetch_error})")
            return []
        toks = [t for t in tokenize(spec.company_name) if t not in _CORP_WORDS]
        if not toks:
            return []
        matches = [v for v in data.values() if all(t in tokenize(v.get("title", "")) for t in toks)]
        if not matches:
            print("  [Ingest]   not an SEC registrant (normal for non-US companies)")
            return []
        exact = [v for v in matches if [w for w in tokenize(v["title"]) if w not in _CORP_WORDS] == toks]
        best = (exact or sorted(matches, key=lambda v: len(v["title"])))[0]
        cik10 = str(best["cik_str"]).zfill(10)
        print(f"  [Ingest]   SEC match: {best['title']} ({best.get('ticker', '?')}, CIK {cik10})")

        sub = self.tools.fetch_json(f"https://data.sec.gov/submissions/CIK{cik10}.json", hdr)
        if not sub:
            return []
        rec = sub.get("filings", {}).get("recent", {})
        rows = list(zip(rec.get("form", []), rec.get("accessionNumber", []), rec.get("primaryDocument", []),
                        rec.get("reportDate", []), rec.get("filingDate", [])))
        annual = [r for r in rows if r[0] in ("10-K", "20-F", "40-F", "10-K405") and r[2]]
        if not annual:
            return []
        end_year = _target_end_year(spec.financial_year)
        pick = next((r for r in annual if end_year and str(r[3])[:4] == str(end_year)), None)
        matches_period = pick is not None
        pick = pick or annual[0]                                         # most recent annual filing
        form, acc, doc, rep, filed = pick
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{acc.replace('-', '')}/{doc}"
        print(f"  [Ingest]   using {form} for period ending {rep} (filed {filed})"
              f"{'' if matches_period else '  - requested period not found, using the latest annual filing'}")

        text = self.tools.fetch_html_structured(url, headers=hdr, max_chars=2_500_000, timeout=120)
        out: List[SourceDocument] = []
        if len(text.split()) > 3000:
            pages = _html_to_pages(text, 500)
            doc_id = f"doc_sec_{form.lower()}_{str(rep)[:4]}"
            title = f"{best['title']} Form {form} (period ending {rep})"
            added = self.kb.add_document(doc_id, title, url, pages)
            self.contents[doc_id] = _contents_from_items(text.replace("|", " "))
            print(f"  [Ingest]   {title}: ~{len(text.split()):,} words, {text.count(chr(10) + '| ---')} tables, {added} chunks")
            out.append(SourceDocument(doc_id=doc_id, title=title, url=url, kind="sec_filing",
                                      fiscal_year=str(rep)[:4], matches_requested_period=matches_period,
                                      pages=max(30, len(text.split()) // 600)))
        else:
            print(f"  [Ingest]   filing text too short or blocked ({self.tools.last_fetch_error})")
        x = self._edgar_facts(cik10, best["title"], hdr)
        if x:
            out.append(x)
        return out if any(o.kind == "sec_filing" for o in out) else []

    def _edgar_facts(self, cik10: str, name: str, hdr: dict) -> Optional[SourceDocument]:
        """Structured XBRL numbers (revenue, profit, assets...) - exact figures for the financial sections."""
        facts = self.tools.fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json", hdr)
        gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
        if not gaap:
            return None
        tags = [("Revenues", "Total revenue"), ("RevenueFromContractWithCustomerExcludingAssessedTax", "Net revenue"),
                ("CostOfRevenue", "Cost of revenue"), ("GrossProfit", "Gross profit"),
                ("ResearchAndDevelopmentExpense", "Research and development expense"),
                ("OperatingIncomeLoss", "Operating income"), ("NetIncomeLoss", "Net income"),
                ("EarningsPerShareDiluted", "Diluted earnings per share"), ("Assets", "Total assets"),
                ("Liabilities", "Total liabilities"), ("StockholdersEquity", "Total stockholders' equity"),
                ("CashAndCashEquivalentsAtCarryingValue", "Cash and cash equivalents"),
                ("NetCashProvidedByUsedInOperatingActivities", "Net cash from operating activities"),
                ("PaymentsToAcquirePropertyPlantAndEquipment", "Capital expenditure"),
                ("PaymentsOfDividends", "Dividends paid"), ("PaymentsForRepurchaseOfCommonStock", "Share repurchases")]
        lines = [f"SEC XBRL structured annual financial data for {name} (values as reported, 10-K annual periods)."]
        for tag, label in tags:
            node = gaap.get(tag)
            if not node:
                continue
            for unit, entries in node.get("units", {}).items():
                best: Dict[str, dict] = {}
                for e in entries:
                    if e.get("form") == "10-K" and e.get("fp") == "FY" and e.get("end"):
                        if e["end"] not in best or e.get("filed", "") > best[e["end"]].get("filed", ""):
                            best[e["end"]] = e
                for end in sorted(best, reverse=True)[:4]:
                    v = best[end]["val"]
                    shown = f"{v:,.2f}" if isinstance(v, float) and abs(v) < 1000 else f"{v:,.0f}"
                    lines.append(f"{label} ({unit}), fiscal year ended {end}: {shown}")
        if len(lines) < 4:
            return None
        doc_id = "doc_sec_xbrl"
        added = self.kb.add_document(doc_id, f"SEC XBRL financial data - {name}", "https://data.sec.gov",
                                     [(1, " ".join(lines[i:i + 12])) for i in range(0, len(lines), 12)])
        print(f"  [Ingest]   SEC XBRL financial facts: {len(lines) - 1} data points, {added} chunks")
        return SourceDocument(doc_id=doc_id, title=f"SEC XBRL financial data - {name}", url="https://data.sec.gov",
                              kind="sec_xbrl", matches_requested_period=None, pages=0)

    # ------------------------------------------- 2. annual-report PDFs (web)
    @staticmethod
    def _score(url: str, text: str, co_tok: List[str], year_toks: List[str]) -> int:
        low = (url + " " + text).lower()
        if any(b in low for b in _BAD_DOMAINS):
            return 0
        s = 0
        path = url.lower().split("?")[0]
        s += 3 if path.endswith(".pdf") else 0
        s += 2 if re.search(r"annual|integrated[\s_-]*report|10-?k|20-?f|form[\s_-]*10", low) else 0
        s += 2 if any(y in low for y in year_toks) else 0
        s += 1 if any(t in low for t in co_tok) else 0
        s -= 3 if any(b in low for b in _NOT_ANNUAL) else 0
        return s

    def _discover_reports(self, spec: ReportInput) -> List[SourceDocument]:
        co, fy = spec.company_name, spec.financial_year
        co_tok = [t for t in tokenize(co) if len(t) > 2 and t not in _CORP_WORDS]
        year_toks = _period_tokens(fy)
        cands: Dict[str, Tuple[int, str]] = {}
        ir_pages: List[str] = []

        def add(url: str, text: str, bonus: int = 0) -> None:
            sc = self._score(url, text, co_tok, year_toks) + bonus
            if sc > cands.get(url, (-9, ""))[0]:
                cands[url] = (sc, text[:80])

        queries = [f"{co} annual report {fy} pdf", f"{co} integrated annual report pdf",
                   f"{co} investor relations annual reports", f"{co} annual report {fy} download"]
        for q in queries:
            for r in self.tools.web_search(q, max_results=8):
                add(r["href"], r.get("title", "") + " " + r.get("body", ""))
                low = r["href"].lower()
                if not low.split("?")[0].endswith(".pdf") and any(t in low for t in co_tok) \
                        and any(k in low for k in ("invest", "annual", "report", "/ir", "financial")):
                    ir_pages.append(r["href"])
        for page in list(dict.fromkeys(ir_pages))[:4]:                  # crawl IR pages for download links
            links = self.tools.extract_links(page)
            print(f"  [Ingest]   crawled {page} -> {len(links)} links")
            for href, anchor in links:
                if href.lower().split("?")[0].endswith(".pdf") or "annual" in (href + anchor).lower():
                    add(href, anchor, bonus=1)

        annual_signal = re.compile(r"annual|integrated[\s_-]*report|10-?k|20-?f|form[\s_-]*10|report", re.I)
        ranked = [(sc, u, t) for u, (sc, t) in cands.items()
                  if sc >= 5 and u.lower().split("?")[0].endswith(".pdf") and annual_signal.search(u + " " + t)]
        ranked.sort(reverse=True)
        print(f"  [Ingest]   {len(ranked)} PDF candidates" + (f"; best: {ranked[0][1]}" if ranked else ""))
        docs: List[SourceDocument] = []
        for sc, url, text in ranked[:8]:
            if len(docs) >= settings.MAX_REFERENCE_DOCS:
                break
            d = self._ingest_one(url, spec, kind="reference_report", url_ok=any(t in url.lower() for t in co_tok),
                                 title_hint=text)
            if d:
                docs.append(d)
        return docs

    # ------------------------------------------------------ 3. web corpus
    def _web_corpus(self, spec: ReportInput) -> List[SourceDocument]:
        from concurrent.futures import ThreadPoolExecutor
        co = spec.company_name
        topics = ["company overview history headquarters", "business segments products services",
                  "board of directors committees corporate governance", "annual financial results revenue profit",
                  "balance sheet cash flow financial statements", "management discussion and analysis outlook",
                  "risk factors", "sustainability ESG report emissions", "employees workforce diversity",
                  "subsidiaries shareholding promoters", "statutory auditors", "research development innovation",
                  "dividend shareholders investor relations", "awards recognition achievements"]
        urls: Dict[str, str] = {}
        per_domain: Dict[str, int] = {}
        for topic in topics:
            for r in self.tools.web_search(f"{co} {topic} {spec.financial_year}", max_results=6):
                u = r["href"]
                dom = re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
                if u in urls or any(b in u.lower() for b in _BAD_DOMAINS) or u.lower().split("?")[0].endswith(".pdf"):
                    continue
                if per_domain.get(dom, 0) >= 4:
                    continue
                per_domain[dom] = per_domain.get(dom, 0) + 1
                urls[u] = r.get("title", "") or dom
            if len(urls) >= settings.WEB_CORPUS_PAGES * 2:
                break
        if not urls:
            print("  [Ingest]   web corpus: no pages found (search returned nothing).")
            return []
        print(f"  [Ingest]   fetching up to {settings.WEB_CORPUS_PAGES} of {len(urls)} candidate pages...")
        items = list(urls.items())[: settings.WEB_CORPUS_PAGES * 2]
        with ThreadPoolExecutor(max_workers=6) as ex:
            texts = list(ex.map(lambda kv: self.tools.fetch_html_structured(kv[0], max_chars=60000, timeout=30), items))
        used: List[str] = []
        for (u, title), text in zip(items, texts):
            if len(used) >= settings.WEB_CORPUS_PAGES:
                break
            if len(text.split()) < 150:
                continue
            self.kb.add_document("web_corpus", title[:90], u, _html_to_pages(text, 400))
            used.append(u)
        if not used:
            return []
        print(f"  [Ingest]   web corpus: {len(used)} pages indexed")
        return [SourceDocument(doc_id="web_corpus", title=f"Public web sources ({len(used)} pages)", url="",
                               kind="web_corpus", matches_requested_period=None, pages=0, urls=used)]

    # ------------------------------------------------------------ one document
    def _ingest_one(self, src: str, spec: ReportInput, kind: str, url_ok: bool,
                    title_hint: str = "") -> Optional[SourceDocument]:
        try:
            is_url = src.lower().startswith(("http://", "https://"))
            local = src
            if is_url:
                local = self.tools.download_pdf(src, self.dir) or ""
                if not local:
                    print(f"  [Ingest]   skip {src[:90]}: {self.tools.last_download_error}")
                    return None
            if not (os.path.exists(local) and local.lower().endswith(".pdf")):
                print(f"  [Ingest]   skip {src[:90]}: not a PDF file")
                return None
            pages, toc, n_pages = parse_pdf(local)
            head = " ".join(t for _, t in pages[:6]).lower()
            if len(head) < 300:
                print(f"  [Ingest]   skip {src[:90]}: scanned/image PDF without a text layer")
                return None
            if kind != "user_supplied":
                toks = [t for t in tokenize(spec.company_name) if len(t) > 2 and t not in _CORP_WORDS]
                if n_pages < 10:
                    print(f"  [Ingest]   skip {src[:90]}: only {n_pages} pages")
                    return None
                if toks and not url_ok and not any(t in head for t in toks):
                    print(f"  [Ingest]   skip {src[:90]}: does not mention the company")
                    return None
            fy_detected = _detect_fiscal_year(" ".join(t for _, t in pages[:3]))
            end_year = _target_end_year(spec.financial_year)
            wanted = _period_tokens(spec.financial_year)
            if end_year:       # "2025", "2024-25", "2024/25" and "FY25" all count as the requested period
                short = f"{end_year % 100:02d}"
                matches = bool(str(end_year) in head or re.search(rf"20\d{{2}}\s*[-\u2013/]\s*{short}\b", head)
                               or re.search(rf"fy\s?{short}\b", head))
            else:
                matches = any(w in head for w in wanted) if wanted else None
            doc_id = "doc_" + slug(os.path.basename(local), 30)
            title = (title_hint or os.path.basename(local)).strip()
            if fy_detected:
                title = f"{title} [{fy_detected}]"
            added = self.kb.add_document(doc_id, title, src if is_url else "", pages)
            self.contents[doc_id] = contents_text_from(toc, pages)
            print(f"  [Ingest]   OK {title}: {n_pages} pages, {added} chunks"
                  f"{'' if matches else '  (period may differ from requested)'}")
            return SourceDocument(doc_id=doc_id, title=title, url=src if is_url else "", local_path=local,
                                  kind=kind, fiscal_year=fy_detected, matches_requested_period=matches,
                                  pages=n_pages)
        except Exception as e:
            print(f"  [Ingest]   skip {src[:90]}: {e}")
            return None
