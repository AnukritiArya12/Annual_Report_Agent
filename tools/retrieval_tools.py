"""Hybrid retrieval and evidence capacity tools."""

from __future__ import annotations

import json
import sqlite3
from typing import List
from langchain_core.tools import BaseTool, tool

from config import settings
from tools.session import ReportSession
from utils import chunk_foreign_reason, sanitize_untrusted


def build_retrieval_tools(session: ReportSession) -> List[BaseTool]:
    """Create hybrid search and evidence capacity tools bound to `session`."""

    @tool
    def search_evidence(query: str, k: int = 6) -> str:
        """Hybrid search (SQLite FTS5 keyword + ChromaDB vector search with RRF and table boost).

        Returns verified evidence chunks with chunk_id, page, source document, and text.
        GUARDRAIL: Automatically drops chunks belonging to excluded/forbidden entities or unapproved domains.
        """
        raw_hits = session.storage.search(query, k=max(1, k * 2))
        results = []
        for chunk, score in raw_hits:
            # Guardrail: drop foreign / forbidden entity chunks
            reason = chunk_foreign_reason(
                chunk.text,
                session.approved_names,
                session.excluded_names,
                session.entity_statuses,
                strict_org=False,
            )
            if reason:
                continue
            results.append({
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "page": chunk.page,
                "is_table": bool(chunk.is_table),
                "score": round(float(score), 4),
                "text": sanitize_untrusted(chunk.text)[:1600],
            })
            if len(results) >= k:
                break

        return json.dumps({
            "query": query,
            "hits": len(results),
            "results": results,
        }, indent=2)

    @tool
    def get_evidence_capacity() -> str:
        """Measure total ingested evidence words and calculate authentic supported page range.

        Use this before committing to a report outline to ensure output length scales
        truthfully with available evidence.
        """
        total_words = 0
        total_docs = 0
        try:
            with sqlite3.connect(session.storage.db_path, timeout=30) as conn:
                cur = conn.cursor()
                cur.execute("SELECT text FROM chunks")
                for (txt,) in cur.fetchall():
                    total_words += len((txt or "").split())
                cur.execute("SELECT COUNT(DISTINCT doc_id) FROM chunks")
                total_docs = cur.fetchone()[0] or 0
        except Exception:
            pass

        ratio = getattr(settings, "EVIDENCE_WORDS_RATIO", 2.5)
        words_per_page = getattr(settings, "MIN_EVIDENCE_WORDS_PER_PAGE", 150)
        max_pages = max(1, int(total_words / (ratio * words_per_page))) if total_words > 0 else 0

        return json.dumps({
            "total_documents": total_docs,
            "total_chunks": session.storage.total_chunks(),
            "total_evidence_words": total_words,
            "supported_pages_estimate": max_pages,
        }, indent=2)

    return [search_evidence, get_evidence_capacity]
