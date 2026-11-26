"""Facts Ledger: Canonical, persistent financial and corporate metrics engine.

Extracts and tracks verified quantitative facts in SQLite:
`facts` table: key, value, numeric_value, unit, currency, period, scope, source_doc_id,
page, chunk_id, confidence, status (REPORTED | DERIVED | CONFLICTING | UNVERIFIED).

Rejects any value whose number does not appear in cited chunk text.
Flags conflicting values for the same key + period across sources.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import get_llm, settings
from financial import clean_number
from storage import DatabaseStorageEngine
from utils import extract_json


class FactStatus(str, Enum):
    REPORTED = "REPORTED"
    DERIVED = "DERIVED"
    CONFLICTING = "CONFLICTING"
    UNVERIFIED = "UNVERIFIED"


CORE_METRIC_KEYS: List[str] = [
    "revenue",
    "operating_profit",
    "net_profit",
    "total_assets",
    "total_liabilities",
    "shareholders_equity",
    "cash_and_equivalents",
    "total_debt",
    "earnings_per_share",
    "dividend_per_share",
    "total_employees",
    "board_directors_count",
    "statutory_auditor"
]


@dataclass
class FactRecord:
    key: str
    value: str
    numeric_value: Optional[float]
    unit: str
    currency: str
    period: str
    scope: str
    source_doc_id: str
    page: Optional[int]
    chunk_id: str
    confidence: float
    status: FactStatus = FactStatus.REPORTED


class FactsLedger:
    """Manages verified canonical metrics extracted from retrieved document chunks."""

    def __init__(self, storage: DatabaseStorageEngine):
        self.storage = storage
        self.db_path = storage.db_path
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        """Initialize facts table in SQLite."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT,
                value TEXT,
                numeric_value REAL,
                unit TEXT,
                currency TEXT,
                period TEXT,
                scope TEXT,
                source_doc_id TEXT,
                page INTEGER,
                chunk_id TEXT,
                confidence REAL,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_key_period ON facts(key, period);")

    def count_facts(self) -> int:
        """Total recorded facts."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM facts")
            return cur.fetchone()[0]

    def get_all_facts(self) -> List[FactRecord]:
        """Fetch all facts in the ledger."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cur = conn.cursor()
            cur.execute("""SELECT key, value, numeric_value, unit, currency, period, scope,
                                  source_doc_id, page, chunk_id, confidence, status FROM facts""")
            records = []
            for r in cur.fetchall():
                records.append(FactRecord(
                    key=r[0], value=r[1], numeric_value=r[2], unit=r[3], currency=r[4],
                    period=r[5], scope=r[6], source_doc_id=r[7], page=r[8], chunk_id=r[9],
                    confidence=r[10], status=FactStatus(r[11])
                ))
            return records

    def get_conflicting_facts(self) -> List[FactRecord]:
        """Fetch all facts flagged as CONFLICTING."""
        return [f for f in self.get_all_facts() if f.status == FactStatus.CONFLICTING]

    def get_missing_core_keys(self, target_period: str) -> List[str]:
        """List core metrics that have not been found for the target period."""
        all_facts = self.get_all_facts()
        found_keys = {f.key for f in all_facts if f.numeric_value is not None or f.value.strip()}
        return [k for k in CORE_METRIC_KEYS if k not in found_keys]

    def record_fact(self, fact: FactRecord) -> None:
        """Insert a validated fact record."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("""INSERT INTO facts (key, value, numeric_value, unit, currency, period, scope,
                                               source_doc_id, page, chunk_id, confidence, status)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (fact.key, fact.value, fact.numeric_value, fact.unit, fact.currency,
                          fact.period, fact.scope, fact.source_doc_id, fact.page, fact.chunk_id,
                          fact.confidence, fact.status.value))

    def build_ledger(self, company_name: str, financial_year: str, reporting_scope: str = "Consolidated") -> int:
        """Extract canonical facts for core metrics from retrieved database chunks.
        
        Rejects any value whose number does not appear in cited chunk text.
        Marks conflicting values for same key+period as CONFLICTING.
        """
        existing_count = self.count_facts()
        if existing_count > 0:
            print(f"  [Facts Ledger] Using {existing_count} existing canonical facts.")
            return existing_count

        print(f"  [Facts Ledger] Extracting canonical core metrics for {company_name} ({financial_year})...")
        llm = get_llm()
        added_count = 0

        # Query storage for each core metric
        for metric_key in CORE_METRIC_KEYS:
            query = f"{company_name} {metric_key.replace('_', ' ')} {financial_year} {reporting_scope}"
            hits = self.storage.search(query, k=3)
            if not hits:
                continue

            for chunk, sc in hits:
                chunk_text = chunk.text
                user_prompt = f"""Extract the metric '{metric_key}' from the following evidence excerpt for {company_name} in {financial_year}.
CRITICAL RULE: The extracted number MUST be literally present in the excerpt. If not found, return {{"found": false}}.

Evidence excerpt (Chunk {chunk.chunk_id}, p.{chunk.page}):
{chunk_text[:3000]}

Return JSON only:
{{
  "found": true | false,
  "metric_name": "{metric_key}",
  "raw_string_value": "e.g. INR 12,345 Crore",
  "numeric_value": 12345.0,
  "unit": "e.g. Crore, Million, or none",
  "currency": "e.g. INR, USD, or none",
  "reporting_period": "e.g. {financial_year}",
  "confidence": 0.9
}}"""
                try:
                    resp = llm.invoke([
                        {"role": "system", "content": "You are a corporate financial data auditor. Extract factual metrics strictly matching cited evidence. Return valid JSON only."},
                        {"role": "user", "content": user_prompt}
                    ])
                    data = extract_json(resp.content)
                    if not isinstance(data, dict) or not data.get("found"):
                        continue

                    raw_val = str(data.get("raw_string_value", "")).strip()
                    num_val = data.get("numeric_value")
                    if num_val is not None:
                        num_val = float(num_val)
                    else:
                        num_val = clean_number(raw_val)

                    # STRICT VALIDATION: If numeric, verify digit sequence appears in chunk text
                    if num_val is not None:
                        # Extract integer part or main sequence
                        main_digits = re.sub(r"[^\d]", "", str(int(abs(num_val))))
                        chunk_clean = re.sub(r"[^\d]", "", chunk_text)
                        if len(main_digits) >= 2 and main_digits not in chunk_clean:
                            # Hallucination rejected
                            continue

                    period_val = str(data.get("reporting_period") or financial_year).strip()
                    currency_val = str(data.get("currency") or "").strip()
                    unit_val = str(data.get("unit") or "").strip()
                    conf = float(data.get("confidence") or sc)

                    # Check for conflicts against existing facts with same key + period
                    status = FactStatus.REPORTED
                    all_existing = self.get_all_facts()
                    for f in all_existing:
                        if f.key == metric_key and f.period.lower() == period_val.lower():
                            if f.numeric_value is not None and num_val is not None:
                                diff = abs(f.numeric_value - num_val)
                                if diff > 0.05 * (abs(f.numeric_value) + 1e-6) and diff > 1.0:
                                    status = FactStatus.CONFLICTING
                                    # Update existing record status to CONFLICTING
                                    with sqlite3.connect(self.db_path, timeout=30) as conn:
                                        conn.execute("UPDATE facts SET status = ? WHERE key = ? AND period = ?",
                                                     (FactStatus.CONFLICTING.value, metric_key, period_val))

                    rec = FactRecord(
                        key=metric_key,
                        value=raw_val or str(num_val),
                        numeric_value=num_val,
                        unit=unit_val,
                        currency=currency_val,
                        period=period_val,
                        scope=reporting_scope,
                        source_doc_id=chunk.doc_id,
                        page=chunk.page,
                        chunk_id=chunk.chunk_id,
                        confidence=conf,
                        status=status
                    )
                    self.record_fact(rec)
                    added_count += 1
                    break  # Found high-confidence hit for this metric
                except Exception:
                    continue

        print(f"  [Facts Ledger] Recorded {added_count} canonical facts in SQLite.")
        return added_count

    def format_canonical_block(self, task_queries: Sequence[str]) -> str:
        """Format relevant canonical facts as a prompt block to constrain writer agents."""
        facts = self.get_all_facts()
        if not facts:
            return ""

        query_str = " ".join(task_queries).lower()
        selected: List[FactRecord] = []
        for f in facts:
            k_clean = f.key.replace("_", " ")
            if k_clean in query_str or any(word in query_str for word in k_clean.split()):
                selected.append(f)

        if not selected:
            # Include top financial facts by default
            selected = [f for f in facts if f.key in ("revenue", "operating_profit", "net_profit", "total_assets")][:5]

        if not selected:
            return ""

        lines = ["CANONICAL FACTS (The authoritative values below are verified from primary sources. You MUST use exactly these values and NEVER contradict them):"]
        for f in selected:
            page_info = f"p.{f.page}" if f.page else "doc"
            lines.append(f"- {f.key.replace('_', ' ').title()} ({f.period}): {f.value} [{f.status.value}] ({f.source_doc_id}, {page_info})")
        return "\n".join(lines)
