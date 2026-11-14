"""Online Research and Knowledge Storage Tools.

Provides generic web search, page scraping, and evidence persistence tools
usable across any company and industry.
"""

import os
import re
import json
import sqlite3
from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from config import settings
from schemas import EvidenceItem


class ResearchTools:
    """Core online research and evidence capture utilities."""

    def __init__(self, run_dir: Optional[str] = None):
        self.run_dir = run_dir or str(settings.RUNS_DIR / "default")
        os.makedirs(self.run_dir, exist_ok=True)
        self.db_path = os.path.join(self.run_dir, settings.SQLITE_DB_NAME)
        self._init_sqlite_db()

    def _init_sqlite_db(self):
        """Initialize SQLite table for persisting verified evidence items."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    fact_or_data TEXT,
                    source_url TEXT,
                    source_title TEXT,
                    metric_name TEXT,
                    metric_value TEXT,
                    confidence REAL,
                    harvested_at TEXT
                )
            """)
            conn.commit()

    def web_search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Search the web using DuckDuckGo with automatic query simplification fallback.
        
        Args:
            query: The search query string.
            max_results: Maximum number of search results to return.
            
        Returns:
            List of dicts containing 'title', 'href', 'body'.
        """
        results = self._raw_search(query, max_results=max_results)
        
        # If no results and query is verbose (> 4 words), simplify and retry
        if not results:
            words = query.split()
            if len(words) > 4:
                # Keep first 2 words (typically company name) and key substantive terms
                stopwords = {"official", "company", "main", "statutory", "compliance", "model", "products", "services"}
                filtered = [w for w in words if w.lower() not in stopwords]
                simplified = " ".join(filtered[:4])
                if simplified and simplified != query:
                    # Retry with simplified query
                    results = self._raw_search(simplified, max_results=max_results)

        return results

    def _raw_search(self, q: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Internal search executor."""
        results = []
        try:
            with DDGS(timeout=5) as ddgs:
                ddgs_results = list(ddgs.text(q, max_results=max_results))
                for r in ddgs_results:
                    href = r.get("href", "") or r.get("link", "")
                    title = r.get("title", "")
                    body = r.get("body", "") or r.get("snippet", "")
                    if href and (title or body):
                        results.append({
                            "title": title,
                            "href": href,
                            "body": body
                        })
        except Exception as e:
            # Silently catch or handle
            pass
        return results

    def scrape_url(self, url: str, max_chars: int = 6000) -> str:
        """Fetch and extract clean text content from a web page.
        
        Args:
            url: Target URL to fetch.
            max_chars: Character limit to prevent oversized context.
            
        Returns:
            Extracted text content.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        try:
            req_timeout = min(8, getattr(settings, "REQUEST_TIMEOUT_SECONDS", 10))
            response = requests.get(url, headers=headers, timeout=req_timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Remove scripts, styles, navigation, footer elements
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                tag.decompose()
                
            text = soup.get_text(separator=" ", strip=True)
            clean_text = re.sub(r"\s+", " ", text).strip()
            return clean_text[:max_chars]
        except Exception as e:
            return ""

    def save_evidence(self, item: EvidenceItem):
        """Persist an atomic evidence item into the SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO evidence (
                    evidence_id, task_id, fact_or_data, source_url,
                    source_title, metric_name, metric_value, confidence, harvested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.evidence_id,
                item.task_id,
                item.fact_or_data,
                item.source_url,
                item.source_title,
                item.metric_name,
                item.metric_value,
                item.confidence,
                item.harvested_at.isoformat() if hasattr(item.harvested_at, "isoformat") else str(item.harvested_at)
            ))
            conn.commit()

    def get_evidence_for_task(self, task_id: str) -> List[EvidenceItem]:
        """Retrieve all stored evidence items for a given task ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM evidence WHERE task_id = ?", (task_id,))
            rows = cursor.fetchall()
            
            items = []
            for r in rows:
                items.append(EvidenceItem(
                    evidence_id=r["evidence_id"],
                    task_id=r["task_id"],
                    fact_or_data=r["fact_or_data"],
                    source_url=r["source_url"],
                    source_title=r["source_title"],
                    metric_name=r["metric_name"],
                    metric_value=r["metric_value"],
                    confidence=r["confidence"]
                ))
            return items
