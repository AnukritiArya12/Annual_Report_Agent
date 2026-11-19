"""Web research, file download and evidence persistence (generic: any company, any country)."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from urllib.parse import urljoin
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:                                    # older package name
    try:
        from duckduckgo_search import DDGS             # type: ignore
    except ImportError:
        DDGS = None                                    # type: ignore

from config import settings
from schemas import EvidenceItem

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def rows_to_markdown(rows: List[List[str]]) -> str:
    """Rows of cell text -> Markdown table. Returns '' for layout-only tables (too small / no numbers)."""
    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows]
    cleaned: List[List[str]] = []
    for r in rows:
        lead_blank = bool(r) and r[0] == ""             # header rows leave the label column empty: keep it aligned
        merged: List[str] = []
        for c in r:
            if c == "":
                continue
            if merged and c in (")", "%", ")%", "%)", "pts", "bps"):
                merged[-1] += c                              # "(1,234" + ")" -> "(1,234)"
            elif merged and merged[-1] in ("$", "(", "($", "\u20ac", "\u00a3", "\u20b9", "US$"):
                merged[-1] += c                              # "$" + "1,234" -> "$1,234"
            else:
                merged.append(c)
        if merged:
            cleaned.append(([""] if lead_blank else []) + merged)
    if len(cleaned) < 2:
        return ""
    n = max(len(r) for r in cleaned)
    if n < 2 or sum(1 for r in cleaned for c in r if any(ch.isdigit() for ch in c)) < 3:
        return ""
    cleaned = [r + [""] * (n - len(r)) for r in cleaned]
    esc = lambda c: c.replace("|", "/")
    lines = ["| " + " | ".join(esc(c) for c in cleaned[0]) + " |", "| " + " | ".join(["---"] * n) + " |"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in cleaned[1:]]
    return "\n".join(lines)


class ResearchTools:
    def __init__(self, run_dir: Optional[str] = None):
        self.run_dir = run_dir or str(settings.RUNS_DIR / "default")
        os.makedirs(self.run_dir, exist_ok=True)
        self.last_search_error = ""
        self.last_download_error = ""
        self.last_fetch_error = ""
        self.db_path = os.path.join(self.run_dir, settings.SQLITE_DB_NAME)
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY, task_id TEXT, text TEXT, source_title TEXT,
                source_url TEXT, page INTEGER, origin TEXT, score REAL)""")

    # ------------------------------------------------------------------ search
    def web_search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        results = self._raw_search(query, max_results)
        if not results and len(query.split()) > 5:          # retry with a shorter query
            results = self._raw_search(" ".join(query.split()[:5]), max_results)
        if not results:                                     # resilient fallback: Wikipedia + Yahoo Finance
            wiki_res = self.search_wikipedia(query, max_results=max_results)
            if wiki_res:
                results.extend(wiki_res)
            if len(results) < max_results:
                yfin_res = self.search_yahoo_finance(query, max_results=max_results - len(results))
                results.extend(yfin_res)
        return results

    def _raw_search(self, q: str, max_results: int) -> List[Dict[str, str]]:
        if DDGS is None:
            self.last_search_error = "the 'ddgs' package is not installed (pip install ddgs)"
            return []
        for _ in range(2):
            try:
                with DDGS(timeout=10) as ddgs:
                    out = []
                    for r in ddgs.text(q, max_results=max_results):
                        href = r.get("href") or r.get("link") or ""
                        title, body = r.get("title", ""), r.get("body") or r.get("snippet") or ""
                        if href and (title or body):
                            out.append({"title": title, "href": href, "body": body})
                    if out:
                        return out
            except Exception as e:
                self.last_search_error = str(e)[:200]
                time.sleep(1.0)
        return []

    def search_wikipedia(self, q: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Search Wikipedia API for company overviews, financials, subsidiaries, and corporate data."""
        try:
            url = "https://en.wikipedia.org/w/api.php"
            params = {"action": "query", "list": "search", "srsearch": q, "format": "json", "utf8": 1}
            headers = {"User-Agent": "AnnualReportAgent/1.0 (academic/research; contact@example.com)"}
            r = requests.get(url, params=params, headers=headers, timeout=12)
            results = []
            if r.ok:
                for item in r.json().get("query", {}).get("search", [])[:max_results]:
                    title = item.get("title", "")
                    snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
                    t_slug = title.replace(" ", "_")
                    href = f"https://en.wikipedia.org/wiki/{t_slug}"
                    results.append({"title": title, "href": href, "body": snippet})
            return results
        except Exception as e:
            self.last_search_error = f"Wikipedia search: {e}"
            return []

    def get_wikipedia_extract(self, title: str) -> str:
        """Fetch full plain text extract of a Wikipedia article."""
        try:
            url = "https://en.wikipedia.org/w/api.php"
            params = {"action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "format": "json"}
            headers = {"User-Agent": "AnnualReportAgent/1.0 (academic/research; contact@example.com)"}
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.ok:
                pages = r.json().get("query", {}).get("pages", {})
                for pid, p in pages.items():
                    if "extract" in p:
                        return p["extract"]
            return ""
        except Exception:
            return ""

    def search_yahoo_finance(self, q: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Search Yahoo Finance API for ticker, legal name, sector, and investor relations news."""
        try:
            url = "https://query2.finance.yahoo.com/v1/finance/search"
            headers = {"User-Agent": _UA}
            r = requests.get(url, params={"q": q, "newsCount": max_results}, headers=headers, timeout=12)
            results = []
            if r.ok:
                data = r.json()
                for quote in data.get("quotes", [])[:max_results]:
                    sym = quote.get("symbol", "")
                    sname = quote.get("shortname") or quote.get("longname") or sym
                    exch = quote.get("exchange", "")
                    sec = quote.get("sector", "")
                    ind = quote.get("industry", "")
                    body = f"Symbol: {sym} | Exchange: {exch} | Sector: {sec} | Industry: {ind}"
                    href = f"https://finance.yahoo.com/quote/{sym}"
                    results.append({"title": f"{sname} ({sym})", "href": href, "body": body})
                for n in data.get("news", [])[:max_results]:
                    title = n.get("title", "")
                    link = n.get("link", "")
                    pub = n.get("publisher", "Yahoo Finance")
                    if link and title:
                        results.append({"title": f"{title} [{pub}]", "href": link, "body": title})
            return results[:max_results]
        except Exception as e:
            self.last_search_error = f"Yahoo Finance search: {e}"
            return []

    # --------------------------------------------------------------- fetching
    def scrape_url(self, url: str, max_chars: int = 6000) -> str:
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
            r.raise_for_status()
            if "pdf" in r.headers.get("content-type", "").lower():
                return ""
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                tag.decompose()
            text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()
            return text[:max_chars]
        except Exception:
            return ""

    def fetch_text(self, url: str, headers: Optional[dict] = None, max_chars: int = 1_500_000,
                   timeout: int = 60) -> str:
        """Fetch an HTML page and return its visible text (no size limit beyond max_chars)."""
        h = {"User-Agent": _UA}
        h.update(headers or {})
        try:
            r = requests.get(url, headers=h, timeout=timeout)
            r.raise_for_status()
            if "pdf" in r.headers.get("content-type", "").lower():
                return ""
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "noscript", "ix:header"]):
                tag.decompose()
            return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()[:max_chars]
        except Exception as e:
            self.last_fetch_error = f"{url}: {str(e)[:150]}"
            return ""

    def fetch_html_structured(self, url: str, headers: Optional[dict] = None, max_chars: int = 2_500_000,
                              timeout: int = 120) -> str:
        """Fetch an HTML page keeping its TABLES as Markdown tables (10-K filings are mostly tables).
        Each table is preceded by a 'Table context:' line with the text just before it (its title)."""
        h = {"User-Agent": _UA}
        h.update(headers or {})
        try:
            r = requests.get(url, headers=h, timeout=timeout)
            r.raise_for_status()
            if "pdf" in r.headers.get("content-type", "").lower():
                return ""
            try:
                soup = BeautifulSoup(r.text, "lxml")
            except Exception:
                soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "noscript", "ix:header"]):
                tag.decompose()
            for tbl in list(soup.find_all("table")):
                try:
                    rows = [[td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                            for tr in tbl.find_all("tr")]
                    md = rows_to_markdown(rows)
                    if md:
                        tbl.replace_with(soup.new_string(f"\n\n@@TABLE@@\n{md}\n@@/TABLE@@\n\n"))
                except Exception:
                    continue
            raw = soup.get_text(" ")
            parts = re.split(r"@@TABLE@@\n(.*?)\n@@/TABLE@@", raw, flags=re.S)
            out, prev = [], ""
            for i, seg in enumerate(parts):
                if i % 2 == 0:
                    txt = re.sub(r"\s+", " ", seg).strip()
                    if txt:
                        out.append(txt)
                        prev = txt
                else:
                    out.append(f"Table context: {' '.join(prev.split()[-30:])}\n{seg.strip()}")
            return "\n\n".join(out)[:max_chars]
        except Exception as e:
            self.last_fetch_error = f"{url}: {str(e)[:150]}"
            return ""

    def fetch_json(self, url: str, headers: Optional[dict] = None, timeout: int = 60) -> Optional[Any]:
        h = {"User-Agent": _UA, "Accept": "application/json"}
        h.update(headers or {})
        try:
            r = requests.get(url, headers=h, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            self.last_fetch_error = f"{url}: {str(e)[:150]}"
            return None

    def extract_links(self, url: str) -> List[tuple]:
        """All (absolute_url, anchor_text) links on a page - used to find report downloads on IR pages."""
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            self.last_fetch_error = f"{url}: {str(e)[:150]}"
            return []
        seen, out = set(), []
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"].strip())
            if not href.lower().startswith(("http://", "https://")) or href in seen:
                continue
            seen.add(href)
            out.append((href, (a.get_text(" ", strip=True) or a.get("title", "") or "")[:160]))
        return out

    def download_pdf(self, url: str, dest_dir: str) -> Optional[str]:
        """Download a PDF (size-capped). Returns the local path or None if it is not a real PDF."""
        os.makedirs(dest_dir, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", url.split("?")[0])[-80:].strip("_") or "document"
        name = re.sub(r"_?pdf$", "", name, flags=re.I) or "document"
        path = os.path.join(dest_dir, name + ".pdf")          # always a real .pdf name (it used to end in '_pdf')
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            return path
        limit = settings.MAX_DOWNLOAD_MB * 1024 * 1024
        try:
            ua = settings.SEC_USER_AGENT if "sec.gov" in url else _UA      # SEC blocks browser-like agents
            with requests.get(url, headers={"User-Agent": ua}, timeout=60, stream=True) as r:
                r.raise_for_status()
                size, first = 0, True
                with open(path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if first:
                            if b"%PDF" not in chunk[:1024]:
                                raise ValueError("the server did not return a PDF (blocked or a web page)")
                            first = False
                        size += len(chunk)
                        if size > limit:
                            raise ValueError("file too large")
                        f.write(chunk)
            return path
        except Exception as e:
            self.last_download_error = str(e)[:150]
            if os.path.exists(path):
                os.remove(path)
            return None

    # --------------------------------------------------------------- evidence
    def save_evidence(self, items: List[EvidenceItem]) -> None:
        if not items:
            return
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?,?,?,?)",
                [(i.evidence_id, i.task_id, i.text, i.source_title, i.source_url, i.page, i.origin, i.score)
                 for i in items])