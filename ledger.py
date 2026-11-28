"""Facts Ledger: Canonical, persistent financial and corporate metrics engine.

Extracts and tracks verified quantitative facts in SQLite:
`facts` table: key, value, numeric_value, unit, currency, period, scope, source_doc_id,
page, chunk_id, confidence, status (REPORTED | DERIVED | CONFLICTING | UNVERIFIED).

Rejects any value whose number does not appear in cited chunk text.
Flags conflicting values for the same key + period across sources.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from financial import clean_number
from storage import DatabaseStorageEngine


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

    def __init__(self, storage: Optional[DatabaseStorageEngine] = None, db_path: Optional[str] = None):
        if storage is not None:
            self.storage = storage
            self.db_path = storage.db_path
        elif db_path is not None:
            self.storage = None
            self.db_path = db_path
        else:
            raise ValueError("Must provide either storage or db_path to FactsLedger")
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
        tp_low = target_period.strip().lower()
        found_keys = {
            f.key for f in all_facts
            if (f.numeric_value is not None or (f.value and f.value.strip()))
            and (not tp_low or (f.period and (tp_low in f.period.lower() or f.period.lower() in tp_low)))
        }
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

    def build_ledger(self, company_name: str, financial_year: str, reporting_scope: str = "Consolidated",
                     force: bool = False) -> int:
        """Extract canonical facts for core metrics from retrieved database chunks.
        
        Rejects any value whose number does not appear in cited chunk text.
        Marks conflicting values for same key+period as CONFLICTING.
        """
        all_existing = self.get_all_facts()
        fy_low = financial_year.strip().lower()
        if not force and any(f.period and (fy_low in f.period.lower() or f.period.lower() in fy_low) for f in all_existing):
            print(f"  [Facts Ledger] Using existing canonical facts for {financial_year}.")
            return len(all_existing)

        if self.storage is None:
            print("  [Facts Ledger] No storage attached; cannot build ledger.")
            return 0
        print(f"  [Facts Ledger] Extracting canonical core metrics for {company_name} ({financial_year})...")
        added_count = 0

        patterns_map = {
            "revenue": [r"revenue", r"turnover", r"sales", r"total\s+income"],
            "operating_profit": [r"operating\s+profit", r"ebit", r"operating\s+income"],
            "net_profit": [r"net\s+profit", r"net\s+income", r"profit\s+for\s+the\s+(?:period|year)"],
            "total_assets": [r"total\s+assets"],
            "total_liabilities": [r"total\s+liabilities"],
            "shareholders_equity": [r"shareholders\s+equity", r"total\s+equity", r"equity\s+capital", r"initial\s+capital"],
            "cash_and_equivalents": [r"cash\s+and\s+equivalents", r"cash\s+and\s+bank", r"cash\s+at\s+bank", r"closing\s+cash"],
            "total_debt": [r"total\s+debt", r"borrowings"],
            "earnings_per_share": [r"earnings\s+per\s+share", r"eps"],
            "dividend_per_share": [r"dividend\s+per\s+share", r"dps"],
            "total_employees": [r"total\s+employees", r"employee\s+count", r"headcount"],
            "board_directors_count": [r"board\s+directors", r"board\s+members"],
            "statutory_auditor": [r"statutory\s+auditor", r"independent\s+auditor"],
        }

        # Query storage for each core metric
        for metric_key in CORE_METRIC_KEYS:
            query = f"{company_name} {metric_key.replace('_', ' ')} {financial_year} {reporting_scope}"
            hits = self.storage.search(query, k=3)
            if not hits:
                continue

            for chunk, sc in hits:
                chunk_text = chunk.text
                extracted_data = None

                # Deterministic regex extraction (no LLM: numbers must come from the document text)
                pats = patterns_map.get(metric_key, [metric_key.replace("_", r"\s+")])
                for p in pats:
                    m = re.search(rf"(?:[-*]\s*)?(?:\*\*)?\b({p})\b(?:\*\*)?\s*[:|=]\s*([^\n\r]+)", chunk_text, re.IGNORECASE)
                    if not m:
                        m = re.search(rf"\|\s*({p})\s*\|\s*([^|\n\r]+)", chunk_text, re.IGNORECASE)
                    if m:
                        val_str = m.group(2).split("|")[0].strip().strip("*").strip()
                        extracted_data = {
                            "found": True,
                            "metric_name": metric_key,
                            "raw_string_value": val_str,
                            "numeric_value": clean_number(val_str),
                            "unit": "",
                            "currency": "",
                            "reporting_period": financial_year,
                            "confidence": 0.95,
                        }
                        break

                if not extracted_data or not extracted_data.get("found"):
                    continue

                try:
                    data = extracted_data
                    raw_val = str(data.get("raw_string_value", "")).strip()
                    num_val = data.get("numeric_value")
                    if num_val is not None:
                        num_val = float(num_val)
                    else:
                        num_val = clean_number(raw_val)

                    # STRICT VALIDATION: If numeric, verify digit sequence appears in chunk text
                    if num_val is not None:
                        main_digits = re.sub(r"[^\d]", "", str(int(abs(num_val))))
                        chunk_clean = re.sub(r"[^\d]", "", chunk_text)
                        if len(main_digits) >= 2 and main_digits not in chunk_clean:
                            # Hallucination rejected
                            continue

                    period_val = str(data.get("reporting_period") or financial_year).strip()
                    currency_val = str(data.get("currency") or "").strip()
                    unit_val = str(data.get("unit") or "").strip()
                    conf = float(data.get("confidence") or sc)

                    # Check for duplicate from exact same chunk+period
                    if any(f.key == metric_key and f.scope.lower() == reporting_scope.lower()
                           and f.chunk_id == chunk.chunk_id for f in all_existing):
                        continue

                    # Check for conflicts against existing facts with same key + period
                    # Management answers can inform drafting but are not
                    # independently verified financial records.
                    status = (
                        FactStatus.UNVERIFIED
                        if chunk.url.startswith("internal://management-interview")
                        else FactStatus.REPORTED
                    )
                    for f in all_existing:
                        if (f.key == metric_key and f.period.lower() == period_val.lower()
                                and f.scope.lower() == reporting_scope.lower()):
                            if f.numeric_value is not None and num_val is not None:
                                diff = abs(f.numeric_value - num_val)
                                if diff > 0.05 * (abs(f.numeric_value) + 1e-6) and diff > 1.0:
                                    status = FactStatus.CONFLICTING
                                    f.status = FactStatus.CONFLICTING
                                    with sqlite3.connect(self.db_path, timeout=30) as conn:
                                        conn.execute("UPDATE facts SET status = ? WHERE key = ? AND period = ? AND lower(scope) = lower(?)",
                                                     (FactStatus.CONFLICTING.value, metric_key, period_val, reporting_scope))

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
                    all_existing.append(rec)
                    added_count += 1
                except Exception:
                    continue

        print(f"  [Facts Ledger] Recorded {added_count} canonical facts in SQLite.")
        return added_count