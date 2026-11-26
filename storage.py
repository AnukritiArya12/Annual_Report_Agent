"""Mandatory Database-Backed Storage & Retrieval Engine.

Strictly couples:
1. ChromaDB: Vector embeddings and semantic similarity retrieval.
2. SQLite: Structured document chunks, metadata, source references, page numbers,
   evidence logging, and deterministic relation tracking.

No BM25, FAISS, Pinecone, Qdrant or other alternative retrieval engines are used.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings

from config import settings


@dataclass
class StorageChunk:
    chunk_id: str             # Stable unique ID: "chk_<doc_id>_<idx>"
    doc_id: str
    doc_title: str
    url: str
    page: Optional[int]
    text: str
    is_table: bool = False
    fiscal_year: str = ""
    metadata: Optional[Dict[str, Any]] = None


class DatabaseStorageEngine:
    """Coupled ChromaDB + SQLite persistence engine for annual report intelligence."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        os.makedirs(run_dir, exist_ok=True)
        self.db_path = os.path.join(run_dir, settings.SQLITE_DB_NAME)
        self.chroma_dir = os.path.join(run_dir, "chroma_db")
        os.makedirs(self.chroma_dir, exist_ok=True)
        self._lock = threading.RLock()

        self._init_sqlite()
        self._init_chroma()

    # ------------------------------------------------------------------ initialization
    def _init_sqlite(self) -> None:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            # Document registry
            conn.execute("""CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT,
                url TEXT,
                local_path TEXT,
                kind TEXT,
                fiscal_year TEXT,
                matches_period INTEGER,
                pages INTEGER,
                urls_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            # Persistent chunks linked by stable ID
            conn.execute("""CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT,
                doc_title TEXT,
                url TEXT,
                page INTEGER,
                text TEXT,
                is_table INTEGER,
                fiscal_year TEXT,
                metadata_json TEXT,
                FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(page);")

            # SQLite FTS5 Full-Text Search virtual table over chunks
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                doc_id UNINDEXED,
                doc_title,
                text,
                content='chunks',
                content_rowid='rowid'
            );""")
            # FTS triggers to keep chunks and chunks_fts in sync
            conn.execute("""CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, chunk_id, doc_id, doc_title, text)
                VALUES (new.rowid, new.chunk_id, new.doc_id, new.doc_title, new.text);
            END;""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, doc_id, doc_title, text)
                VALUES('delete', old.rowid, old.chunk_id, old.doc_id, old.doc_title, old.text);
            END;""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, doc_id, doc_title, text)
                VALUES('delete', old.rowid, old.chunk_id, old.doc_id, old.doc_title, old.text);
                INSERT INTO chunks_fts(rowid, chunk_id, doc_id, doc_title, text)
                VALUES (new.rowid, new.chunk_id, new.doc_id, new.doc_title, new.text);
            END;""")

            # Evidence logs (auditable claims used by sections)
            conn.execute("""CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY,
                task_id TEXT,
                chunk_id TEXT,
                text TEXT,
                source_title TEXT,
                source_url TEXT,
                page INTEGER,
                origin TEXT,
                score REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence(task_id);")

    def _init_chroma(self) -> None:
        self.chroma_client = chromadb.PersistentClient(
            path=self.chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False, is_persistent=True)
        )
        self.collection = self.chroma_client.get_or_create_collection(
            name="annual_report_corpus",
            metadata={"description": "Company evidence chunks for annual report synthesis"}
        )

    # ------------------------------------------------------------------ chunking & ingestion
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

    def _window(self, words: List[str]) -> List[str]:
        step = max(1, settings.CHUNK_WORDS - settings.CHUNK_OVERLAP_WORDS)
        out = []
        for start in range(0, len(words), step):
            piece = words[start:start + settings.CHUNK_WORDS]
            if len(piece) >= 8:
                out.append(" ".join(piece))
            if start + settings.CHUNK_WORDS >= len(words):
                break
        return out

    def _chunk_page(self, text: str) -> List[Tuple[str, bool]]:
        """Return [(piece, is_table)]."""
        if not self._is_table(text):
            return [(w, False) for w in self._window(text.split())]
        out: List[Tuple[str, bool]] = []
        prose: List[str] = []
        for block in re.split(r"\n\s*\n", text):
            if self._is_table(block):
                if prose:
                    out += [(w, False) for w in self._window(" ".join(prose).split())]
                    prose = []
                out += [(t, True) for t in self._table_chunks(block)]
            else:
                prose.append(block)
        if prose:
            out += [(w, False) for w in self._window(" ".join(prose).split())]
        return out

    def add_document(self, doc_id: str, title: str, url: str, pages: Sequence[Tuple[int, str]],
                     local_path: str = "", kind: str = "document", fiscal_year: str = "",
                     matches_period: Optional[bool] = None, urls: Sequence[str] = ()) -> int:
        """Process document into chunks, persistently storing in SQLite and indexing in ChromaDB."""
        with self._lock:
            # 1. Register Document in SQLite
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (doc_id, title, url, local_path, kind, fiscal_year,
                     1 if matches_period is True else (0 if matches_period is False else None),
                     len(pages), json.dumps(list(urls)))
                )

            # 2. Extract Chunks with table metadata enrichment
            chunks: List[StorageChunk] = []
            idx = 0
            for page_no, text in pages:
                pieces = self._chunk_page(text or "")
                for piece, is_tbl in pieces:
                    cid = f"chk_{doc_id}_{idx:05d}"
                    idx += 1
                    meta: Dict[str, Any] = {}
                    if is_tbl:
                        tbl_lines = [l.strip() for l in piece.splitlines() if l.strip().startswith("|")]
                        if tbl_lines:
                            headers = [c.strip() for c in tbl_lines[0].strip("|").split("|") if c.strip()]
                            meta["headers"] = headers
                        cap_lines = [l.strip() for l in piece.splitlines() if l.strip() and not l.strip().startswith("|")]
                        if cap_lines:
                            meta["caption"] = " ".join(cap_lines)[:200]
                    chunks.append(StorageChunk(
                        chunk_id=cid, doc_id=doc_id, doc_title=title, url=url,
                        page=page_no, text=piece, is_table=is_tbl, fiscal_year=fiscal_year,
                        metadata=meta
                    ))

            if not chunks:
                return 0

            # 3. Store in SQLite
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?)",
                    [(c.chunk_id, c.doc_id, c.doc_title, c.url, c.page, c.text,
                      1 if c.is_table else 0, c.fiscal_year, json.dumps(c.metadata or {}))
                     for c in chunks]
                )

            # 4. Embed and Index in ChromaDB
            ids = [c.chunk_id for c in chunks]
            documents = [c.text for c in chunks]
            metadatas = [{
                "doc_id": c.doc_id,
                "doc_title": c.doc_title[:100],
                "page": c.page or 0,
                "is_table": 1 if c.is_table else 0,
                "fiscal_year": c.fiscal_year or ""
            } for c in chunks]

            # Ingest in batches of 100 for memory and API stability
            batch_size = 100
            for i in range(0, len(chunks), batch_size):
                b_ids = ids[i:i + batch_size]
                b_docs = documents[i:i + batch_size]
                b_meta = metadatas[i:i + batch_size]
                self.collection.upsert(
                    ids=b_ids,
                    documents=b_docs,
                    metadatas=b_meta
                )

            return len(chunks)

    # ------------------------------------------------------------------ search
    def _is_financial_or_numeric_query(self, query: str) -> bool:
        """Check if query contains digits or common financial metric terms."""
        if re.search(r"\d", query):
            return True
        financial_terms = {
            "revenue", "profit", "ebitda", "ebit", "loss", "turnover", "margin", "income",
            "expense", "assets", "liabilities", "equity", "debt", "cash", "dividend",
            "eps", "share", "balance", "capital", "expenditure", "capex", "opex",
            "borrowings", "crore", "lakh", "million", "billion", "inr", "usd", "eur", "gbp"
        }
        words = set(re.findall(r"\b[A-Za-z]+\b", query.lower()))
        return bool(words & financial_terms)

    def _fts_keyword_search(self, query: str, limit: int = 25,
                            doc_ids: Optional[Sequence[str]] = None,
                            table_only: bool = False) -> List[str]:
        """Perform SQLite FTS5 BM25 search over chunks."""
        words = re.findall(r"\b[A-Za-z0-9_]+\b", query)
        if not words:
            return []
        match_expr = " OR ".join(f'"{w}"' for w in words if len(w) > 1)
        if not match_expr:
            match_expr = f'"{words[0]}"'

        sql = [
            "SELECT c.chunk_id FROM chunks_fts f",
            "JOIN chunks c ON f.chunk_id = c.chunk_id",
            "WHERE chunks_fts MATCH ?"
        ]
        params: List[Any] = [match_expr]

        if doc_ids:
            placeholders = ",".join(["?"] * len(doc_ids))
            sql.append(f"AND c.doc_id IN ({placeholders})")
            params.extend(doc_ids)

        if table_only:
            sql.append("AND c.is_table = 1")

        sql.append("ORDER BY bm25(chunks_fts) LIMIT ?")
        params.append(limit)

        query_str = " ".join(sql)
        try:
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                cur = conn.cursor()
                cur.execute(query_str, params)
                return [row[0] for row in cur.fetchall()]
        except Exception:
            return []

    def _vector_search(self, query: str, limit: int = 25,
                       doc_ids: Optional[Sequence[str]] = None,
                       table_only: bool = False) -> List[str]:
        """Perform Chroma vector search over chunks."""
        total_items = self.collection.count()
        if total_items == 0:
            return []

        conditions = []
        if doc_ids:
            if len(doc_ids) == 1:
                conditions.append({"doc_id": doc_ids[0]})
            else:
                conditions.append({"doc_id": {"$in": list(doc_ids)}})
        if table_only:
            conditions.append({"is_table": 1})

        where_clause = None
        if len(conditions) == 1:
            where_clause = conditions[0]
        elif len(conditions) > 1:
            where_clause = {"$and": conditions}

        n_results = min(limit, total_items)
        try:
            res = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_clause
            )
            matched_ids = res.get("ids", [[]])[0]
            return matched_ids
        except Exception:
            try:
                res = self.collection.query(query_texts=[query], n_results=n_results)
                return res.get("ids", [[]])[0]
            except Exception:
                return []

    def search(self, query: str, k: int = 8, doc_ids: Optional[Sequence[str]] = None,
               table_only: bool = False) -> List[Tuple[StorageChunk, float]]:
        """Hybrid search combining SQLite FTS5 (BM25) and ChromaDB vector search via RRF."""
        with self._lock:
            # Fallback to pure vector search if HYBRID_SEARCH is disabled
            if not getattr(settings, "HYBRID_SEARCH", True):
                v_ids = self._vector_search(query, limit=k, doc_ids=doc_ids, table_only=table_only)
                if not v_ids:
                    return []
                placeholders = ",".join(["?"] * len(v_ids))
                with sqlite3.connect(self.db_path, timeout=30) as conn:
                    cur = conn.cursor()
                    cur.execute(f"SELECT chunk_id, doc_id, doc_title, url, page, text, is_table, fiscal_year, metadata_json FROM chunks WHERE chunk_id IN ({placeholders})", v_ids)
                    rows = {r[0]: r for r in cur.fetchall()}
                out: List[Tuple[StorageChunk, float]] = []
                for idx, cid in enumerate(v_ids):
                    if cid in rows:
                        r = rows[cid]
                        meta = json.loads(r[8]) if r[8] else {}
                        ch = StorageChunk(
                            chunk_id=r[0], doc_id=r[1], doc_title=r[2], url=r[3],
                            page=r[4], text=r[5], is_table=bool(r[6]), fiscal_year=r[7],
                            metadata=meta
                        )
                        out.append((ch, round(1.0 / (idx + 1), 4)))
                return out

            # 1. Gather candidates from Vector Search and SQLite FTS5 Keyword Search
            vector_limit = getattr(settings, "VECTOR_TOP_N", 25)
            keyword_limit = getattr(settings, "KEYWORD_TOP_N", 25)
            v_ids = self._vector_search(query, limit=vector_limit, doc_ids=doc_ids, table_only=table_only)
            k_ids = self._fts_keyword_search(query, limit=keyword_limit, doc_ids=doc_ids, table_only=table_only)

            all_cand_ids = list(dict.fromkeys(v_ids + k_ids))
            if not all_cand_ids:
                return []

            # 2. Fetch authoritative chunk records from SQLite
            placeholders = ",".join(["?"] * len(all_cand_ids))
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT chunk_id, doc_id, doc_title, url, page, text, is_table, fiscal_year, metadata_json FROM chunks WHERE chunk_id IN ({placeholders})", all_cand_ids)
                rows = {r[0]: r for r in cur.fetchall()}

            # 3. Compute Reciprocal Rank Fusion (RRF) scores
            rrf_k = getattr(settings, "RRF_K", 60)
            v_ranks = {cid: rank + 1 for rank, cid in enumerate(v_ids)}
            k_ranks = {cid: rank + 1 for rank, cid in enumerate(k_ids)}

            # Financial terms set for table boosting
            financial_terms = {
                "revenue", "profit", "ebitda", "ebit", "loss", "turnover", "margin", "income",
                "expense", "assets", "liabilities", "equity", "debt", "cash", "dividend",
                "eps", "share", "balance", "capital", "expenditure", "capex", "opex",
                "borrowings", "crore", "lakh", "million", "billion", "inr", "usd", "eur", "gbp"
            }
            query_words = set(re.findall(r"\b[A-Za-z]+\b", query.lower()))
            query_fin_matches = query_words & financial_terms
            query_has_num = bool(re.search(r"\d", query))

            scored_candidates: List[Tuple[StorageChunk, float]] = []
            for cid in all_cand_ids:
                if cid not in rows:
                    continue
                r = rows[cid]
                meta = json.loads(r[8]) if r[8] else {}
                ch = StorageChunk(
                    chunk_id=r[0], doc_id=r[1], doc_title=r[2], url=r[3],
                    page=r[4], text=r[5], is_table=bool(r[6]), fiscal_year=r[7],
                    metadata=meta
                )

                score = 0.0
                if cid in v_ranks:
                    score += 1.0 / (rrf_k + v_ranks[cid])
                if cid in k_ranks:
                    score += 1.0 / (rrf_k + k_ranks[cid])

                # Boost table chunks when query has financial terms/numbers and table matches them
                if ch.is_table:
                    ch_content = (ch.text + " " + json.dumps(meta)).lower()
                    if query_fin_matches and any(term in ch_content for term in query_fin_matches):
                        score *= 1.3
                    elif query_has_num and (not query_fin_matches):
                        score *= 1.15

                scored_candidates.append((ch, score))

            # 4. Optional Cross-Encoder Reranking
            if getattr(settings, "RERANK", False):
                try:
                    from sentence_transformers import CrossEncoder
                    if not hasattr(self, "_cross_encoder"):
                        self._cross_encoder = CrossEncoder(settings.RERANK_MODEL)
                    pairs = [[query, ch.text] for ch, _ in scored_candidates]
                    rerank_scores = self._cross_encoder.predict(pairs)
                    reranked = []
                    for (ch, _), r_score in zip(scored_candidates, rerank_scores):
                        reranked.append((ch, float(r_score)))
                    scored_candidates = reranked
                except Exception:
                    pass

            # Sort descending by score and return top k
            scored_candidates.sort(key=lambda x: x[1], reverse=True)
            return [(ch, round(s, 5)) for ch, s in scored_candidates[:k]]

    # ------------------------------------------------------------------ evidence persistence
    def save_evidence(self, items: Sequence[Any]) -> None:
        if not items:
            return
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO evidence (evidence_id, task_id, text, source_title, source_url, page, origin, score) VALUES (?,?,?,?,?,?,?,?)",
                [(getattr(i, "evidence_id", ""), getattr(i, "task_id", ""), getattr(i, "text", ""),
                  getattr(i, "source_title", ""), getattr(i, "source_url", ""), getattr(i, "page", None),
                  getattr(i, "origin", "knowledge_base"), getattr(i, "score", 0.0))
                 for i in items]
            )

    def total_chunks(self) -> int:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM chunks")
            return cur.fetchone()[0]
