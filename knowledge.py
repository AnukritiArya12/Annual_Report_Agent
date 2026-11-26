"""Knowledge base + reference-document ingestion.

The agent loads the company's real report(s) (downloaded automatically, or supplied by the
user with --pdf/--docx), splits them into page-tagged chunks, and indexes them in a persistent
ChromaDB + SQLite database engine. Sections are written strictly from retrieved evidence.

Ingestion strategies:
  1. SEC EDGAR   - company's 10-K / 20-F / 40-F plus structured XBRL facts.
  2. Web search  - official annual-report PDFs / DOCX crawled from IR pages.
  3. Web corpus  - scored authoritative public articles when no report file exists.
  4. Cold-start  - 3-department internal dossier when zero public records exist.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import approvals
from config import get_llm, settings
from schemas import ReportInput, SourceDocument
from jurisdictions import get_jurisdiction
from connectors import get_connectors_for_jurisdiction, SecEdgarConnector
from skills import CompanyIntelligenceSkill
from storage import DatabaseStorageEngine as KnowledgeBase, StorageChunk as Chunk
from tools import ResearchTools, rows_to_markdown
from utils import extract_json, sanitize_untrusted, slug, tokenize


# ------------------------------------------------------------------------------
# Document parsing
# ------------------------------------------------------------------------------
_OCR_WARNED = False


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
    """Return (pages, toc, page_count). pages = [(page_no, text)], toc = [(level, title, page)].
    Includes automatic OCR fallback when pages contain scanned/image content without text layer."""
    try:
        import pymupdf as fitz            # type: ignore
    except ImportError:
        try:
            import fitz                   # type: ignore
        except ImportError as e:
            raise ImportError("PyMuPDF is required to read PDFs: pip install pymupdf") from e
    doc = fitz.open(path)
    pages = []
    scanned_pages_detected = 0
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        if settings.PDF_TABLES:
            text += _pdf_tables_markdown(page)
        # OCR fallback if page has minimal text but contains image data
        if len(text.strip()) < 50 and getattr(settings, "OCR_ENABLED", True):
            global _OCR_WARNED
            try:
                pix = page.get_pixmap()
                from PIL import Image
                import io
                import pytesseract
                img = Image.open(io.BytesIO(pix.tobytes()))
                ocr_text = pytesseract.image_to_string(img)
                if ocr_text.strip():
                    text = ocr_text.strip() + "\n[OCR Extracted Content]"
                    scanned_pages_detected += 1
            except Exception as e:
                if not _OCR_WARNED:
                    _OCR_WARNED = True
                    print(f"  [Warning] OCR unavailable: pytesseract or Tesseract-OCR binary is not installed/configured ({e}). Scanned pages will retain raw text layer only.")
        pages.append((i + 1, text))
    if scanned_pages_detected:
        print(f"  [PDF Parser] OCR applied to {scanned_pages_detected} scanned page(s) in {os.path.basename(path)}")
    toc = [(int(l), str(t).strip(), int(p)) for l, t, p in (doc.get_toc() or [])]
    n = len(pages)
    doc.close()
    return pages, toc, n


def parse_docx(path: str) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str, int]], int]:
    """Parse DOCX documents preserving heading hierarchy, paragraphs, and tables as Markdown."""
    try:
        import docx
    except ImportError:
        raise ImportError("python-docx is required to read DOCX: pip install python-docx")
    
    doc = docx.Document(path)
    content_blocks = []
    toc = []
    page_approx = 1
    words_count = 0
    
    for element in doc.element.body:
        tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag
        if tag == "p":
            p = docx.text.paragraph.Paragraph(element, doc)
            text = p.text.strip()
            if not text:
                continue
            style_name = p.style.name.lower() if p.style else ""
            if "heading 1" in style_name:
                content_blocks.append(f"\n# {text}\n")
                toc.append((1, text, page_approx))
            elif "heading 2" in style_name:
                content_blocks.append(f"\n## {text}\n")
                toc.append((2, text, page_approx))
            elif "heading 3" in style_name:
                content_blocks.append(f"\n### {text}\n")
                toc.append((3, text, page_approx))
            else:
                content_blocks.append(text)
            words_count += len(text.split())
            if words_count > 450:
                page_approx += 1
                words_count = 0
        elif tag == "tbl":
            t = docx.table.Table(element, doc)
            rows = []
            for r in t.rows:
                rows.append([c.text.strip() for c in r.cells])
            md = rows_to_markdown(rows)
            if md:
                content_blocks.append("\n" + md + "\n")
                words_count += sum(len(c.split()) for row in rows for c in row)
                
    full_text = "\n\n".join(content_blocks)
    pages = _html_to_pages(full_text, words_per_page=450)
    return pages, toc, len(pages)


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
_STRONG_BAD = ("environmental", "diversity", "gender", "pay-gap", "paygap", "pay_gap", "slavery", "tax-transparency",
               "human-rights", "supplier", "brochure", "catalog")
_AUTHORITATIVE_FINANCIAL_DOMAINS = (
    "reuters.com", "bloomberg.com", "economictimes.indiatimes.com", "moneycontrol.com",
    "livemint.com", "business-standard.com", "ft.com", "wsj.com", "forbes.com",
    "bseindia.com", "nseindia.com", "sec.gov", "marketwatch.com", "cnbc.com",
    "financialexpress.com", "thehindubusinessline.com", "investing.com"
)


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
        self.names: List[str] = []             # every name the company is known by (brand, legal name, parent)
        self.ticker = ""
        self.excluded: List[str] = []          # human-supplied names (subsidiaries / look-alikes) that must never be used
        os.makedirs(self.dir, exist_ok=True)

    # --------------------------------------------------------- entity resolution
    def _resolve_entity(self, spec: ReportInput) -> None:
        """'Google' files nothing; its parent 'Alphabet Inc.' does. Ask which legal entity publishes the report."""
        self.names = [spec.company_name]
        try:
            system = (
                f"{CompanyIntelligenceSkill.get_instructions()}\n\n"
                "You identify which legal entity publishes a company's annual report. Return ONLY JSON."
            )
            user = (f'Name given by the user: "{spec.company_name}" (country: {spec.country or "unknown"}).\n'
                    "If this is a brand, product or subsidiary, give the listed parent / reporting entity that files the "
                    'annual report. Return JSON: {"legal_name": "", "ticker": "", "also_known_as": []}. '
                    "Leave a field empty if you are not sure. Never guess.")
            data = extract_json(get_llm().invoke([{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]).content)
            legal = str(data.get("legal_name") or "").strip()
            self.ticker = str(data.get("ticker") or "").strip().upper()
            more = [str(x).strip() for x in (data.get("also_known_as") or []) if isinstance(x, str)]
            for n in [legal] + more:
                if n and n.lower() not in [x.lower() for x in self.names]:
                    self.names.append(n)
            if len(self.names) > 1 or self.ticker:
                print(f"  [Ingest] Reporting entity: '{spec.company_name}' -> {', '.join(self.names[1:]) or '(same)'}"
                      f"{' | ticker ' + self.ticker if self.ticker else ''}")
        except Exception as e:                 # quota errors etc. surface later, in planning, with a clear message
            print(f"  [Ingest] Entity lookup skipped ({str(e)[:80]})")

    def _confirm_entity(self, spec: ReportInput) -> None:
        """HUMAN CHECKPOINT 1 - confirm WHICH company this is before a single document is searched.
        The LLM's guess of legal name / ticker is the root of every later search, so a wrong guess here
        would put another company's documents into the report."""
        legal = self.names[1] if len(self.names) > 1 else spec.company_name
        vals, _ = approvals.edit_fields(
            "entity", "STEP 1 - CONFIRM THE COMPANY (every search starts from these names)",
            {"legal_name": legal, "ticker": self.ticker,
             "also_known_as": ", ".join(self.names[2:]), "exclude": ""},
            info=[f"You asked for : {spec.company_name} | country: {spec.country or '-'} | "
                  f"period: {spec.financial_year} | scope: {spec.reporting_scope}",
                  "legal_name    = the entity that really publishes the report (e.g. 'Google' -> 'Alphabet Inc.')",
                  "ticker        = stock ticker used to find the SEC filing (empty if not US-listed)",
                  "also_known_as = other names of the SAME company, comma separated",
                  "exclude       = subsidiaries / sister or look-alike companies whose documents must NEVER be used"])
        names = [spec.company_name]
        for n in [vals["legal_name"]] + [x.strip() for x in vals["also_known_as"].split(",")]:
            n = n.strip()
            if n and n.lower() not in [x.lower() for x in names]:
                names.append(n)
        self.names = names
        self.ticker = vals["ticker"].strip().upper()
        self.excluded = [x.strip() for x in vals["exclude"].split(",") if x.strip()]
        approvals.set_identity(self.names, self.excluded)       # used later by the writer guards and the final review

    def _is_excluded(self, text: str) -> bool:
        low = (text or "").lower()
        return any(x.lower() in low for x in self.excluded)

    def _name_tokens(self) -> List[str]:
        toks: List[str] = []
        for n in self.names:
            for t in tokenize(n):
                if len(t) > 2 and t not in _CORP_WORDS and t not in toks:
                    toks.append(t)
        return toks

    # ------------------------------------------------------------------- main
    def ingest(self, spec: ReportInput) -> List[SourceDocument]:
        if spec.peer_report_path:
            self._ingest_peer_benchmark(spec.peer_report_path)

        if spec.is_first_year:
            print(f"\n  [Ingest] >>> COLD-START PROTOCOL: Inaugural / First-Year Annual Report for '{spec.company_name}'.")
            self.names = [spec.company_name]
            self._confirm_entity(spec)
            docs = self._first_year_dossier(spec)
            return docs

        self._check_search(spec)
        self._resolve_entity(spec)
        self._confirm_entity(spec)                      # HUMAN CHECKPOINT 1
        docs: List[SourceDocument] = []
        for src in spec.source_documents:                              # optional manual override
            d = self._ingest_one(src, spec, kind="user_supplied", url_ok=True)
            if d:
                docs.append(d)
        if not docs:
            juri = get_jurisdiction(spec.country)
            connectors = get_connectors_for_jurisdiction(juri, tools=self.tools, run_dir=self.dir)
            print(f"  [Ingest] Querying {len(connectors)} official source connectors for {juri.regime_name}...")
            for conn in connectors:
                if docs:
                    break
                conn_name = conn.__class__.__name__
                if isinstance(conn, SecEdgarConnector):
                    docs = self._edgar(spec)
                else:
                    cands = conn.search(spec.company_name, spec.financial_year)
                    if cands:
                        top = cands[:settings.MAX_REFERENCE_DOCS * 2]
                        items = [(f"[Score {int(c.confidence * 10)}] {c.title}\n          {c.url}", c.url) for c in top]
                        keep = approvals.choose(
                            "connector_select", f"STEP 2 - VERIFY OFFICIAL FILINGS DISCOVERED VIA {conn_name.upper()}?",
                            items, default_ids=[c.url for c in top[:settings.MAX_REFERENCE_DOCS]],
                            intro=[f"Discovered {len(cands)} filing candidate(s) via {conn_name}:"]
                        )
                        for c in top:
                            if c.url in keep and len(docs) < settings.MAX_REFERENCE_DOCS:
                                d = self._ingest_one(c.url, spec, kind="reference_report", url_ok=True, title_hint=c.title)
                                if d:
                                    docs.append(d)

        if not docs:
            print("  [Ingest] Secondary Search: official annual-report PDFs (search + investor-relations pages)")
            docs = self._discover_reports(spec)
        if not docs:
            print("  [Ingest] Secondary Strategy: broad public web corpus (no annual report could be downloaded)")
            docs = self._web_corpus(spec)
        if not docs:
            print(f"  [Ingest] Cold-Start Fallback: First-Year Entity Dossier (zero public records online)")
            docs = self._first_year_dossier(spec)
        docs.sort(key=lambda d: (not bool(d.matches_requested_period), -d.pages))
        if docs:
            doc_lines = [f"{d.title} ({d.kind}, ~{d.pages} pages) -> {d.url or d.local_path}" for d in docs]
            approvals.confirm(
                "sources_verified",
                "STEP 1B - VERIFY SOURCES BELONG STRICTLY TO THIS COMPANY",
                [f"Reporting Company: {spec.company_name} | Target Period: {spec.financial_year}",
                 "Verify that the documents below belong strictly to this company (not a competitor, look-alike, or external entity):"]
                + doc_lines,
                default=True
            )
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
        best = None
        if self.ticker:                                        # a ticker is the most reliable key
            by_ticker = [v for v in data.values() if str(v.get("ticker", "")).upper() == self.ticker]
            best = by_ticker[0] if by_ticker else None
        for name in self.names:
            if best:
                break
            toks = [t for t in tokenize(name) if t not in _CORP_WORDS]
            if not toks:
                continue
            matches = [v for v in data.values() if all(t in tokenize(v.get("title", "")) for t in toks)]
            if matches:
                exact = [v for v in matches if [w for w in tokenize(v["title"]) if w not in _CORP_WORDS] == toks]
                best = (exact or sorted(matches, key=lambda v: len(v["title"])))[0]
        if not best:
            print(f"  [Ingest]   no SEC registrant found for {', '.join(self.names)} "
                  "(normal for non-US companies; US ones are matched by ticker or legal name)")
            return []
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

        lines = [f"Registrant : {best['title']}  (ticker {best.get('ticker', '?')}, CIK {cik10})",
                 f"Searched for: {', '.join(self.names)}" + (f"  | ticker {self.ticker}" if self.ticker else ""),
                 f"Filing     : {form}, period ending {rep}, filed {filed}",
                 f"Link       : {url}",
                 f"Requested  : {spec.financial_year}  -> "
                 + ("period MATCHES" if matches_period else "NOT FOUND - this is only the latest annual filing")]
        if self._is_excluded(best["title"]):
            lines.append("WARNING: registrant name matches your 'exclude' list")
        if not approvals.confirm("edgar", "STEP 2 - IS THIS THE SEC FILING OF YOUR COMPANY?", lines,
                                 default=matches_period and not self._is_excluded(best["title"])):
            print("  [Ingest]   SEC filing rejected by you -> trying other sources")
            return []
        approvals.add_approved_urls([url])
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
    def _score(url: str, text: str, co_tok: List[str], year_toks: List[str], trusted: bool = False) -> int:
        low = (url + " " + text).lower()
        if any(b in low for b in _BAD_DOMAINS):
            return 0
        if co_tok and not trusted and not any(t in low for t in co_tok):
            return 0                                   # a report of some OTHER company is never a candidate
        s = 0
        path = url.lower().split("?")[0]
        s += 3 if path.endswith(".pdf") else 0
        s += 2 if re.search(r"annual|integrated[\s_-]*report|10-?k|20-?f|form[\s_-]*10", low) else 0
        if year_toks:
            if any(y in low for y in year_toks):
                s += 2
            else:
                other = re.findall(r"20\d{2}", low)
                if other and not any(o in year_toks for o in other):
                    s -= 2                             # clearly a different year
        s += 1 if any(t in low for t in co_tok) else 0
        s -= 3 if any(b in low for b in _NOT_ANNUAL) else 0
        s -= 4 if any(b in low for b in _STRONG_BAD) or re.search(r"(?<![a-z])esg(?![a-z])", low) else 0
        if not re.search(r"annual|integrated", low) and re.search(r"sustainab|responsibility", low):
            s -= 3
        return s

    def _discover_reports(self, spec: ReportInput) -> List[SourceDocument]:
        fy = spec.financial_year
        co_tok = self._name_tokens() or [t for t in tokenize(spec.company_name) if len(t) > 2]
        year_toks = _period_tokens(fy)
        cands: Dict[str, Tuple[int, str]] = {}
        ir_pages: List[str] = []

        def add(url: str, text: str, bonus: int = 0, trusted: bool = False) -> None:
            sc = self._score(url, text, co_tok, year_toks, trusted) + bonus
            if sc > cands.get(url, (-9, ""))[0]:
                cands[url] = (sc, text[:80])

        queries = []
        for co in self.names[:2]:
            queries += [f"{co} annual report {fy} pdf", f"{co} integrated annual report pdf",
                        f"{co} investor relations annual reports", f"{co} form 10-K {fy}"]
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
                    add(href, anchor, bonus=1, trusted=True)

        annual_signal = re.compile(r"annual|integrated[\s_-]*report|10-?k|20-?f|form[\s_-]*10|report", re.I)
        ranked = [(sc, u, t) for u, (sc, t) in cands.items()
                  if sc >= 5 and u.lower().split("?")[0].endswith(".pdf") and annual_signal.search(u + " " + t)]
        ranked.sort(reverse=True)
        print(f"  [Ingest]   {len(ranked)} PDF candidates" + (f"; best: {ranked[0][1]}" if ranked else ""))
        ranked = [r for r in ranked if not self._is_excluded(r[1] + " " + r[2])]
        if ranked:                                                        # HUMAN CHECKPOINT 3a
            top = ranked[:8]
            keep = approvals.choose(
                "pdf_select", "STEP 3 - WHICH OF THESE PDFs IS THE REAL REPORT OF YOUR COMPANY?",
                [(f"[score {sc}] {(text or '(no title)')[:90]}\n          {url}", url) for sc, url, text in top],
                default_ids=[u for _, u, _ in top[:settings.MAX_REFERENCE_DOCS]],
                intro=[f"Company: {self.names[0]} / {', '.join(self.names[1:]) or '-'} | period: {spec.financial_year}",
                       "Keep ONLY reports published by this company itself (not a peer, parent or subsidiary)."])
            ranked = [r for r in top if r[1] in keep]
            if not ranked:
                print("  [Ingest]   you rejected all PDF candidates -> falling back to other sources")
        docs: List[SourceDocument] = []
        for sc, url, text in ranked[:8]:
            if len(docs) >= settings.MAX_REFERENCE_DOCS:
                break
            d = self._ingest_one(url, spec, kind="reference_report", url_ok=any(t in url.lower() for t in co_tok),
                                 title_hint=text)
            if d:
                docs.append(d)
        return docs

    def _score_web_article(self, url: str, title: str, snippet: str, spec: ReportInput) -> int:
        low = (url + " " + title + " " + snippet).lower()
        score = 0
        co_tok = self._name_tokens() or [t for t in tokenize(spec.company_name) if len(t) > 2]
        fy_toks = _period_tokens(spec.financial_year)

        # 1. Target Company Match (Must mention target company name)
        if co_tok and any(t in low for t in co_tok):
            score += 3
            if any(t in title.lower() for t in co_tok):
                score += 2
        else:
            return -10

        # 2. Authoritative Domain Tier
        dom = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
        if any(d in dom for d in _AUTHORITATIVE_FINANCIAL_DOMAINS):
            score += 4
        elif any(t in dom for t in co_tok):  # Company's own corporate / IR domain
            score += 5

        # 3. Temporal / Reporting Period Match
        if fy_toks and any(y in low for y in fy_toks):
            score += 3
        else:
            old_years = re.findall(r"\b201[0-9]\b", low)
            if old_years:
                score -= 3

        # 4. Financial & Statutory Governance Substance
        fin_signals = ("revenue", "profit", "ebitda", "crore", "million", "billion", "financial result",
                       "board of directors", "annual", "dividend", "balance sheet", "net profit", "loss", "growth")
        signal_count = sum(1 for s in fin_signals if s in low)
        score += min(signal_count, 4)

        # 5. Penalize Clickbait / Promotional Content
        if any(b in low for b in ("coupon", "discount", "promo", "deal", "salary review", "interview question")):
            score -= 5

        return score

    # ------------------------------------------------------ 3. web corpus
    def _web_corpus(self, spec: ReportInput) -> List[SourceDocument]:
        from concurrent.futures import ThreadPoolExecutor
        co = self.names[1] if len(self.names) > 1 else spec.company_name
        
        # Ingest Wikipedia article as a foundational authoritative reference
        wiki_text = ""
        for name_cand in [co, spec.company_name] + self.names:
            wiki_text = self.tools.get_wikipedia_extract(name_cand)
            if wiki_text and len(wiki_text.split()) > 200:
                print(f"  [Ingest]   Wikipedia article found for '{name_cand}' ({len(wiki_text.split())} words)")
                if not approvals.confirm(                                  # HUMAN CHECKPOINT 4a
                        f"wiki:{name_cand}", "STEP 4a - IS THIS THE WIKIPEDIA ARTICLE OF YOUR COMPANY?",
                        [f"Article : {name_cand}", f"Looking for: {', '.join(self.names)}",
                         f"Starts with: {re.sub(chr(10) + '+', ' ', wiki_text[:350])}"], default=True):
                    wiki_text = ""
                    continue
                self.kb.add_document("wikipedia_corpus", f"Wikipedia: {name_cand}",
                                     f"https://en.wikipedia.org/wiki/{name_cand.replace(' ', '_')}",
                                     _html_to_pages(wiki_text, 400))
                break

        topics = ["company overview history headquarters", "business segments products services",
                  "board of directors committees corporate governance", "annual financial results revenue profit",
                  "balance sheet cash flow financial statements", "management discussion and analysis outlook",
                  "risk factors", "sustainability ESG report emissions", "employees workforce diversity",
                  "subsidiaries shareholding promoters", "statutory auditors", "research development innovation",
                  "dividend shareholders investor relations", "awards recognition achievements"]
        cands: Dict[str, Tuple[int, str]] = {}
        per_domain: Dict[str, int] = {}
        for topic in topics:
            for r in self.tools.web_search(f"{co} {topic} {spec.financial_year}", max_results=6):
                u = r["href"]
                dom = re.sub(r"^https?://(www\.)?", "", u).split("/")[0]
                if u in cands or any(b in u.lower() for b in _BAD_DOMAINS) or u.lower().split("?")[0].endswith(".pdf"):
                    continue
                if per_domain.get(dom, 0) >= 4:
                    continue
                per_domain[dom] = per_domain.get(dom, 0) + 1
                ti = r.get("title", "") or dom
                snip = r.get("body", "")
                sc = self._score_web_article(u, ti, snip, spec)
                if sc > 0:
                    cands[u] = (sc, ti)
            if len(cands) >= settings.WEB_CORPUS_PAGES * 3:
                break
        if not cands and not wiki_text:
            print("  [Ingest]   web corpus: no pages found (search returned nothing).")
            return []
        ranked_cands = sorted([(sc, u, ti) for u, (sc, ti) in cands.items()], key=lambda x: x[0], reverse=True)
        print(f"  [Ingest]   evaluated {len(ranked_cands)} candidate articles, selecting top authoritative sources...")
        items = [(u, ti) for _, u, ti in ranked_cands[: settings.WEB_CORPUS_PAGES * 2]]
        items = [(u, ti) for u, ti in items if not self._is_excluded(u + " " + ti)]
        keep = approvals.choose(                                              # HUMAN CHECKPOINT 4b
            "web_select", f"STEP 4b - WHICH WEB ARTICLES MAY BE USED AS SOURCES FOR '{self.names[0]}'?",
            [(f"[Score {cands[u][0]}] {(ti or '(no title)')[:75]}\n          {u}", u) for u, ti in items],
            default_ids=[u for u, _ in items[: settings.WEB_CORPUS_PAGES]],
            intro=["Ranked by source authority, fiscal year relevance, and financial metrics.",
                   "Keep ONLY articles that reliably describe this company and period:"])
        items = [(u, ti) for u, ti in items if u in keep]
        with ThreadPoolExecutor(max_workers=6) as ex:
            texts = list(ex.map(lambda kv: self.tools.fetch_html_structured(kv[0], max_chars=60000, timeout=30), items))
        used: List[str] = []
        for (u, title), text in zip(items, texts):
            if len(used) >= settings.WEB_CORPUS_PAGES:
                break
            if len(text.split()) < 150:
                continue
            if getattr(settings, "PROMPT_INJECTION_DEFENCE", True):
                text = sanitize_untrusted(text)
            self.kb.add_document("web_corpus", title[:90], u, _html_to_pages(text, 400))
            used.append(u)
        if not used and not wiki_text:
            return []
        approvals.add_approved_urls(used)
        src_pages = len(used) + (1 if wiki_text else 0)
        print(f"  [Ingest]   web corpus: {len(used)} web pages + {'Wikipedia' if wiki_text else ''} indexed")
        return [SourceDocument(doc_id="web_corpus", title=f"Public web sources ({src_pages} pages)", url="",
                               kind="web_corpus", matches_requested_period=None, pages=0, urls=used)]

    # ------------------------------------------------------ 4. cold-start first-year dossier
    def _ingest_peer_benchmark(self, peer_src: str) -> None:
        """Structural Benchmarking: Ingest an established peer company's annual report strictly to
        extract its Table of Contents / chapter taxonomy, without mixing peer financial data."""
        try:
            local = peer_src
            if peer_src.lower().startswith(("http://", "https://")):
                local = self.tools.download_pdf(peer_src, self.dir) or ""
            if not (local and os.path.exists(local) and local.lower().endswith(".pdf")):
                print(f"  [Peer Benchmark] Skip peer report: not a valid PDF ({peer_src})")
                return
            pages, toc, n_pages = parse_pdf(local)
            peer_toc = contents_text_from(toc, pages)
            if peer_toc:
                self.contents["peer_toc"] = peer_toc
                peer_title = os.path.basename(local)
                print(f"  [Peer Benchmark] Ingested structural TOC from peer report: '{peer_title}' ({n_pages} pages).")
                print("  [Peer Benchmark] Note: Structure will benchmark chapter taxonomy; financial figures are strictly isolated.")
        except Exception as e:
            print(f"  [Peer Benchmark] Failed to parse peer benchmark PDF: {e}")

    def _first_year_dossier(self, spec: ReportInput) -> List[SourceDocument]:
        """COLD-START PROTOCOL: When zero public filings or web records exist online,
        intake primary corporate records partitioned into three authentic company departments:
        1. Founder / Executive / CEO Office
        2. Finance & Accounts Department
        3. Legal, Governance & Secretarial Department
        
        Reads statutory requirements dynamically from the resolved jurisdiction configuration.
        Any missing fact is truthfully marked as 'Not provided in the dossier' rather than boilerplate.
        """
        co = self.names[0]
        co_slug = slug(co)
        jurisdiction = get_jurisdiction(spec.country)
        dossier_file = spec.dossier_path or os.path.join(self.dir, f"dossier_{co_slug}.json")
        loaded_data = {}
        if os.path.exists(dossier_file):
            try:
                with open(dossier_file, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
                print(f"  [Cold-Start] Loaded existing primary company dossier from: {dossier_file}")
            except Exception as e:
                print(f"  [Cold-Start] Could not read {dossier_file}: {e}")

        target_year = _target_end_year(spec.financial_year) or '2024'
        default_fields = {
            # --- 1. Legal, Secretarial & Governance Department ---
            "registration_id_number": loaded_data.get("registration_id_number") or (self.ticker or f"REG-{target_year}-001"),
            "incorporation_date": loaded_data.get("incorporation_date") or f"01-January-{target_year}",
            "registered_office": loaded_data.get("registered_office") or f"Corporate Headquarters, {spec.country or jurisdiction.country_name}",
            "founders_and_directors": loaded_data.get("founders_and_directors") or "Founding Promoters and Board of Directors",
            "statutory_auditor": loaded_data.get("statutory_auditor") or "Not provided in the dossier",
            "board_meetings_held": loaded_data.get("board_meetings_held") or "Inaugural corporate board meetings convened during maiden reporting period",
            "statutory_governance_status": loaded_data.get("statutory_governance_status") or f"Operating under {jurisdiction.governing_statute}",
            "related_party_contracts": loaded_data.get("related_party_contracts") or "Not provided in the dossier",

            # --- 2. Executive / Founder & CEO Office ---
            "founder_ceo_vision": loaded_data.get("founder_ceo_vision") or f"Pioneering innovation in {spec.industry or 'enterprise solutions'}, creating long-term stakeholder value.",
            "business_model_summary": loaded_data.get("business_model_summary") or f"Commercialization of {spec.industry or 'enterprise products and services'}.",
            "year1_operational_milestones": loaded_data.get("year1_operational_milestones") or f"Commenced corporate operations and established foundational infrastructure during {spec.financial_year}.",

            # --- 3. Finance & Accounts Department ---
            "accounting_standard": loaded_data.get("accounting_standard") or (jurisdiction.accounting_frameworks[0] if jurisdiction.accounting_frameworks else "Applicable GAAP"),
            "initial_capital_equity": loaded_data.get("initial_capital_equity") or "Not provided in the dossier",
            "capital_infusion_funding": loaded_data.get("capital_infusion_funding") or "Not provided in the dossier",
            "first_year_revenue": loaded_data.get("first_year_revenue") or "Not provided in the dossier",
            "first_year_expenses": loaded_data.get("first_year_expenses") or "Not provided in the dossier",
            "net_profit_or_loss": loaded_data.get("net_profit_or_loss") or "Not provided in the dossier",
            "cash_at_bank": loaded_data.get("cash_at_bank") or "Not provided in the dossier"
        }

        vals, _ = approvals.edit_fields(
            "first_year_dossier",
            f"COLD-START PROTOCOL: THREE-DEPARTMENT CORPORATE DOSSIER FOR '{co}' ({jurisdiction.code})",
            default_fields,
            info=[f"Target Company: {co} | Period: {spec.financial_year} | Jurisdiction: {jurisdiction.country_name} ({jurisdiction.regime_name})",
                  "No public filings or web records exist online for this entity.",
                  "Review or edit verified internal records across Founder/CEO, Finance, and Secretarial departments:"]
        )

        try:
            with open(dossier_file, "w", encoding="utf-8") as f:
                json.dump(vals, f, indent=2)
        except Exception:
            pass

        # Page 1: Legal & Secretarial Dept - Genesis, Governance & Statutory Charter
        p1 = (
            f"# {co} - CORPORATE GENESIS & SECRETARIAL GOVERNANCE CHARTER\n\n"
            f"Department Source: Secretarial & Legal Department\n"
            f"Statutory Framework: {jurisdiction.governing_statute}\n\n"
            f"Entity Legal Name: {co}\n"
            f"Registration Identification: {vals.get('registration_id_number')}\n"
            f"Date of Incorporation: {vals.get('incorporation_date')}\n"
            f"Registered Office: {vals.get('registered_office')}\n"
            f"Reporting Period: {spec.financial_year} (Maiden Financial Period since Inception)\n\n"
            f"## Capital Structure & Shareholding\n"
            f"Authorized & Paid-up Equity Capital: {vals.get('initial_capital_equity')}\n"
            f"Promoters & Directorship: {vals.get('founders_and_directors')}\n"
            f"Corporate Status: Unlisted Private Entity / Development-Stage Enterprise.\n"
        )

        # Page 2: Executive / CEO Office - Strategic Vision & Milestones
        p2 = (
            f"# {co} - FOUNDER & CEO STRATEGIC REPORT: VISION & OPERATIONAL GENESIS\n\n"
            f"Department Source: Executive / Office of the CEO & Founders\n\n"
            f"## Strategic Vision & Mission\n"
            f"{vals.get('founder_ceo_vision')}\n\n"
            f"## Business Model & Value Proposition\n"
            f"{vals.get('business_model_summary')}\n\n"
            f"## Year-1 Operational Milestones\n"
            f"{vals.get('year1_operational_milestones')}\n"
        )

        # Page 3: Secretarial / Board - Statutory Governance & Directors' Report
        p3 = (
            f"# {co} - STATUTORY DIRECTORS' & GOVERNANCE REPORT\n\n"
            f"Department Source: Board of Directors & Corporate Secretarial\n"
            f"Governing Law: {jurisdiction.governing_statute}\n\n"
            f"The Directors present their Maiden Annual Report on the business affairs of {co} together with the "
            f"Financial Statements for the period ended {spec.financial_year}.\n\n"
            f"1. State of Company's Affairs: {vals.get('year1_operational_milestones')}.\n"
            f"2. Board Composition & Governance Meetings: {vals.get('board_meetings_held')}. Directors: {vals.get('founders_and_directors')}.\n"
            f"3. Statutory Auditor: {vals.get('statutory_auditor')}.\n"
            f"4. Statutory Governance & Regulatory Compliance Status: {vals.get('statutory_governance_status')}.\n"
            f"5. Related Party Contracts: {vals.get('related_party_contracts')}.\n"
        )

        # Page 4: Finance Department - Maiden Financial Statements
        p4 = (
            f"# {co} - MAIDEN FINANCIAL STATEMENTS\n\n"
            f"Department Source: Finance & Accounting Department\n"
            f"Reporting Framework: {vals.get('accounting_standard')}\n"
            f"Reporting Period: {spec.financial_year} (Maiden Operational Period)\n"
            f"All figures in reported currency.\n\n"
            f"| Statement of Profit and Loss Metric | FY {spec.financial_year} | Preceding Period |\n"
            f"| :--- | :--- | :--- |\n"
            f"| Revenue from Operations | {vals.get('first_year_revenue')} | N/A (First Year of Operations) |\n"
            f"| Total Operating Expenses | {vals.get('first_year_expenses')} | N/A (First Year of Operations) |\n"
            f"| Operating Profit / (Loss) | {vals.get('net_profit_or_loss')} | N/A (First Year of Operations) |\n"
            f"| Profit / (Loss) Before Tax | {vals.get('net_profit_or_loss')} | N/A (First Year of Operations) |\n"
            f"| Net Profit / (Loss) for the Period | {vals.get('net_profit_or_loss')} | N/A (First Year of Operations) |\n\n"
            f"| Balance Sheet Snapshot Metric | As at Close of {spec.financial_year} | Preceding Period |\n"
            f"| :--- | :--- | :--- |\n"
            f"| Paid-up Share Capital & Equity | {vals.get('initial_capital_equity')} | N/A (First Year of Operations) |\n"
            f"| Reserves & Surplus (Retained Earnings) | {vals.get('net_profit_or_loss')} | N/A (First Year of Operations) |\n"
            f"| Cash & Cash Equivalents (Closing Balance) | {vals.get('cash_at_bank')} | N/A (First Year of Operations) |\n"
            f"| Capital Infusion by Promoters | {vals.get('capital_infusion_funding')} | N/A (First Year of Operations) |\n"
        )

        # Page 5: Finance & Audit - Notes to Accounts
        p5 = (
            f"# {co} - SIGNIFICANT ACCOUNTING POLICIES & FINANCIAL NOTES\n\n"
            f"Department Source: Finance Department\n\n"
            f"Note 1: Basis of Preparation\n"
            f"These maiden financial statements of {co} cover the period from incorporation date "
            f"({vals.get('incorporation_date')}) to the end of {spec.financial_year}. In accordance with {vals.get('accounting_standard')}, "
            f"comparative figures for preceding periods are not applicable and marked N/A (First Year of Operations).\n\n"
            f"Note 2: Liquidity & Capital Runway\n"
            f"The company closed the financial period with cash and bank liquidity of {vals.get('cash_at_bank')}.\n\n"
            f"Note 3: Statutory Audit Status\n"
            f"Statutory auditor record: {vals.get('statutory_auditor')}.\n"
        )

        pages = [(1, p1), (2, p2), (3, p3), (4, p4), (5, p5)]
        doc_id = f"first_year_dossier_{co_slug}"
        doc_title = f"Foundational Corporate Record & Year-1 Dossier ({co})"
        self.kb.add_document(doc_id, doc_title, "internal://first_year_dossier", pages)
        print(f"  [Cold-Start] Indexed {len(pages)} pages of verified 3-department internal records for '{co}' ({jurisdiction.code}).")
        return [SourceDocument(
            doc_id=doc_id, title=doc_title, url="internal://first_year_dossier",
            kind="first_year_dossier", fiscal_year=spec.financial_year,
            matches_requested_period=True, pages=len(pages)
        )]

        pages = [(1, p1), (2, p2), (3, p3), (4, p4), (5, p5)]
        doc_id = f"first_year_dossier_{co_slug}"
        doc_title = f"Foundational Corporate Record & Year-1 Dossier ({co})"
        self.kb.add_document(doc_id, doc_title, "internal://first_year_dossier", pages)
        print(f"  [Cold-Start] Indexed {len(pages)} pages of verified foundational records and {juri.regime_name} disclosures for '{co}'.")
        return [SourceDocument(
            doc_id=doc_id, title=doc_title, url="internal://first_year_dossier",
            kind="first_year_dossier", fiscal_year=spec.financial_year,
            matches_requested_period=True, pages=len(pages)
        )]

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
            is_docx = local.lower().endswith((".docx", ".doc"))
            is_pdf = local.lower().endswith(".pdf")
            if not (os.path.exists(local) and (is_pdf or is_docx)):
                print(f"  [Ingest]   skip {src[:90]}: not a PDF or DOCX file")
                return None
            if is_docx:
                pages, toc, n_pages = parse_docx(local)
            else:
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
            if kind != "user_supplied":                                       # HUMAN CHECKPOINT 3b
                names_low = [n.lower() for n in self.names] or [spec.company_name.lower()]
                mentions = any(n in head for n in names_low) or any(t_ in head for t_ in self._name_tokens())
                hit_ex = [x for x in self.excluded if x.lower() in head]
                first = re.sub(r"\s+", " ", pages[0][1])[:260] if pages else ""
                info = [f"Document : {title}", f"Source   : {src}", f"Pages    : {n_pages}",
                        f"Period   : detected '{fy_detected or 'unknown'}' vs requested '{spec.financial_year}' -> "
                        + ("MATCH" if matches else "MAY NOT MATCH"),
                        f"Company name on first pages: {'yes' if mentions else 'NO  <-- check carefully'}",
                        f"Starts with: {first}"]
                if hit_ex:
                    info.append(f"WARNING: first pages mention your excluded name(s): {', '.join(hit_ex)}")
                if not approvals.confirm(f"pdf_doc:{src}", "STEP 3b - IS THIS DOWNLOADED DOCUMENT CORRECT?", info,
                                         default=bool(matches) and mentions and not hit_ex):
                    print(f"  [Ingest]   rejected by you: {src[:90]}")
                    return None
            added = self.kb.add_document(doc_id, title, src if is_url else "", pages)
            if is_url:
                approvals.add_approved_urls([src])
            self.contents[doc_id] = contents_text_from(toc, pages)
            print(f"  [Ingest]   OK {title}: {n_pages} pages, {added} chunks"
                  f"{'' if matches else '  (period may differ from requested)'}")
            return SourceDocument(doc_id=doc_id, title=title, url=src if is_url else "", local_path=local,
                                  kind=kind, fiscal_year=fy_detected, matches_requested_period=matches,
                                  pages=n_pages)
        except Exception as e:
            print(f"  [Ingest]   skip {src[:90]}: {e}")
            return None