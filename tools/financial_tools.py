"""Facts ledger, financial statements, and deterministic math verification tools."""

from __future__ import annotations

import json
import re
from typing import List, Optional
from langchain_core.tools import BaseTool, tool

from financial import FinancialVerifier, clean_number
from ledger import FactRecord, FactStatus
from statements import extract_structured_statements
from tools.session import ReportSession


def build_financial_tools(session: ReportSession) -> List[BaseTool]:
    """Create ledger, statement, and arithmetic verifier tools bound to `session`."""

    @tool
    def build_facts_ledger(force: bool = False) -> str:
        """Scan indexed evidence chunks and build the canonical SQLite facts ledger.

        Extracts multi-period metrics, rejects any number not literally present in cited chunks,
        and flags conflicting values across sources.
        """
        added = session.ledger.build_ledger(
            session.company_name,
            session.financial_year,
            session.reporting_scope,
            force=force,
        )
        conflicts = session.ledger.get_conflicting_facts()
        missing = session.ledger.get_missing_core_keys(session.financial_year)
        return json.dumps({
            "facts_added": added,
            "total_facts": session.ledger.count_facts(),
            "conflicts": [
                {"key": c.key, "value": c.value, "period": c.period, "source": c.source_doc_id}
                for c in conflicts
            ],
            "missing_core_keys": missing,
        }, indent=2)

    @tool
    def get_canonical_facts(keys: Optional[List[str]] = None) -> str:
        """Retrieve verified canonical facts from the ledger, optionally filtered by metric keys.

        Returns exact values, periods, source documents, page numbers, and status.
        """
        facts = session.ledger.get_all_facts()
        if keys:
            key_set = {k.strip().lower() for k in keys}
            facts = [f for f in facts if f.key.lower() in key_set]

        return json.dumps({
            "count": len(facts),
            "facts": [
                {
                    "key": f.key,
                    "value": f.value,
                    "numeric_value": f.numeric_value,
                    "unit": f.unit,
                    "currency": f.currency,
                    "period": f.period,
                    "source": f.source_doc_id,
                    "page": f.page,
                    "chunk_id": f.chunk_id,
                    "status": f.status.value if hasattr(f.status, "value") else str(f.status),
                }
                for f in facts
            ],
        }, indent=2)

    @tool
    def record_verified_fact(
        key: str,
        value: str,
        period: str,
        source_doc_id: str,
        chunk_id: str = "",
        page: int = 0,
        currency: str = "",
        unit: str = "",
    ) -> str:
        """Record a verified metric into the canonical ledger.

        GUARDRAIL: If numeric, verifies digits appear in the cited chunk (when chunk_id is provided).
        """
        num_val = clean_number(value)
        if num_val is not None and chunk_id:
            # Verify digit sequence exists in cited chunk
            hits = session.storage.search(value, k=5)
            matched_chunk = next((c for c, _ in hits if c.chunk_id == chunk_id), None)
            if matched_chunk is not None:
                main_digits = re.sub(r"[^\d]", "", str(int(abs(num_val))))
                chunk_clean = re.sub(r"[^\d]", "", matched_chunk.text)
                if len(main_digits) >= 2 and main_digits not in chunk_clean:
                    return json.dumps({
                        "status": "REJECTED",
                        "reason": f"Digits '{main_digits}' do not appear literally in cited chunk {chunk_id}.",
                    })

        rec = FactRecord(
            key=key,
            value=value,
            numeric_value=num_val,
            unit=unit,
            currency=currency,
            period=period,
            scope=session.reporting_scope,
            source_doc_id=source_doc_id,
            page=page,
            chunk_id=chunk_id,
            confidence=0.95,
            status=FactStatus.REPORTED,
        )
        session.ledger.record_fact(rec)
        return json.dumps({"status": "RECORDED", "key": key, "value": value, "period": period})

    @tool
    def get_statement_table(kind: str) -> str:
        """Retrieve deterministic Markdown summary table for 'balance_sheet', 'income_statement', or 'cash_flow'.

        Returns read-only tables built from filings or verified ledger facts.
        """
        if not session.structured_statements:
            session.structured_statements = extract_structured_statements(session.storage)
        tbl = session.structured_statements.get(kind.strip().lower())
        if not tbl:
            return json.dumps({
                "status": "NOT_AVAILABLE",
                "kind": kind,
                "available_kinds": list(session.structured_statements.keys()),
            })
        return json.dumps({
            "status": "AVAILABLE",
            "kind": kind,
            "markdown_table": tbl,
        }, indent=2)

    @tool
    def verify_financials(markdown: str) -> str:
        """Run deterministic accounting verification on a drafted Markdown section.

        Checks table row/column sums, Balance Sheet equation (Assets = Liabilities + Equity),
        Net Profit (Revenue - Expenses = Net Profit), and Cash Flow reconciliation.
        """
        res = FinancialVerifier.audit_draft_financials(markdown, evidence_text="")
        return json.dumps({
            "status": res.status,
            "is_valid": res.is_valid,
            "errors": res.errors,
            "warnings": res.warnings,
            "reconciled_items": res.reconciled_items,
        }, indent=2)

    return [
        build_facts_ledger,
        get_canonical_facts,
        record_verified_fact,
        get_statement_table,
        verify_financials,
    ]
