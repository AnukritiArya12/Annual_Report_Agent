"""Web research, file download and evidence persistence (generic: any company, any country)."""

from __future__ import annotations

import os
import re
import sqlite3
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


class ResearchTools:
    def __init__(self, run_dir: Optional[str] = None):
        self.run_dir = run_dir or str(settings.RUNS_DIR / "default")
        os.makedirs(self.run_dir, exist_ok=True)
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
        return results

    @staticmethod
    def _raw_search(q: str, max_results: int) -> List[Dict[str, str]]:
        if DDGS is None:
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
            except Exception:
                continue
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

    def download_pdf(self, url: str, dest_dir: str) -> Optional[str]:
        """Download a PDF (size-capped). Returns the local path or None if it is not a real PDF."""
        os.makedirs(dest_dir, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", url.split("?")[0])[-80:].strip("_") or "document"
        path = os.path.join(dest_dir, name if name.lower().endswith("pdf") else name + ".pdf")
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            return path
        limit = settings.MAX_DOWNLOAD_MB * 1024 * 1024
        try:
            with requests.get(url, headers={"User-Agent": _UA}, timeout=60, stream=True) as r:
                r.raise_for_status()
                size, first = 0, True
                with open(path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if first:
                            if not chunk.startswith(b"%PDF"):
                                raise ValueError("not a PDF")
                            first = False
                        size += len(chunk)
                        if size > limit:
                            raise ValueError("file too large")
                        f.write(chunk)
            return path
        except Exception:
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