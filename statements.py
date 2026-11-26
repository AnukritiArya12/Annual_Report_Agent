"""Deterministic Financial Statement Construction Module.

Where structured data exists (SEC XBRL facts, verified FactsLedger, or parsed PDF tables),
constructs immutable Markdown tables in code for:
1. Balance Sheet / Statement of Financial Position
2. Income Statement / Statement of Profit and Loss
3. Statement of Cash Flows

The writing agent receives these pre-built tables as read-only context and injects them
directly into the final section markdown so LLMs never fabricate or alter financial figures.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from ledger import FactsLedger


class FinancialStatementBuilder:
    """Constructs deterministic, verified financial statement Markdown tables."""

    @staticmethod
    def build_balance_sheet(facts_dict: Dict[str, Any], period: str = "Current Year",
                            currency: str = "") -> Optional[str]:
        """Construct deterministic Balance Sheet table from verified quantitative metrics."""
        assets = facts_dict.get("total_assets")
        liab = facts_dict.get("total_liabilities")
        equity = facts_dict.get("shareholders_equity")

        # If none of the balance sheet figures exist, cannot build
        if not assets and not liab and not equity:
            return None

        curr_prefix = f"{currency} " if currency else ""
        lines = [
            f"| Statement of Financial Position (Balance Sheet) | Period: {period} | Verification Status |",
            "| :--- | :--- | :--- |"
        ]
        if assets:
            lines.append(f"| **Total Assets** | {curr_prefix}{assets} | Primary Reported Metric |")
        if liab:
            lines.append(f"| Total Liabilities | {curr_prefix}{liab} | Primary Reported Metric |")
        if equity:
            lines.append(f"| Total Stockholders' / Shareholders' Equity | {curr_prefix}{equity} | Primary Reported Metric |")
        if liab and equity and not facts_dict.get("total_liabilities_and_equity"):
            # If numeric components exist, calculate sum
            try:
                l_num = float(re.sub(r"[^\d.]", "", str(liab)))
                e_num = float(re.sub(r"[^\d.]", "", str(equity)))
                tot = l_num + e_num
                lines.append(f"| **Total Liabilities and Stockholders' Equity** | {curr_prefix}{tot:,.2f} | Arithmetically Reconciled |")
            except Exception:
                pass
        elif facts_dict.get("total_liabilities_and_equity"):
            lines.append(f"| **Total Liabilities and Stockholders' Equity** | {curr_prefix}{facts_dict['total_liabilities_and_equity']} | Primary Reported Metric |")

        return "\n".join(lines)

    @staticmethod
    def build_income_statement(facts_dict: Dict[str, Any], period: str = "Current Year",
                               currency: str = "") -> Optional[str]:
        """Construct deterministic Income Statement table from verified quantitative metrics."""
        rev = facts_dict.get("revenue")
        op_prof = facts_dict.get("operating_profit")
        net_prof = facts_dict.get("net_profit")
        eps = facts_dict.get("earnings_per_share")

        if not rev and not op_prof and not net_prof:
            return None

        curr_prefix = f"{currency} " if currency else ""
        lines = [
            f"| Statement of Profit and Loss (Income Statement) | Period: {period} | Verification Status |",
            "| :--- | :--- | :--- |"
        ]
        if rev:
            lines.append(f"| **Revenue from Operations / Total Revenue** | {curr_prefix}{rev} | Primary Reported Metric |")
        if op_prof:
            lines.append(f"| Operating Profit / (Loss) | {curr_prefix}{op_prof} | Primary Reported Metric |")
        if net_prof:
            lines.append(f"| **Net Profit / (Loss) for the Period** | {curr_prefix}{net_prof} | Primary Reported Metric |")
        if eps:
            lines.append(f"| Diluted Earnings Per Share (EPS) | {eps} | As Reported |")

        return "\n".join(lines)

    @staticmethod
    def build_cash_flow_statement(facts_dict: Dict[str, Any], period: str = "Current Year",
                                  currency: str = "") -> Optional[str]:
        """Construct deterministic Cash Flow statement table from verified quantitative metrics."""
        cash = facts_dict.get("cash_and_equivalents")
        debt = facts_dict.get("total_debt")
        dividend = facts_dict.get("dividend_per_share")

        if not cash and not debt and not dividend:
            return None

        curr_prefix = f"{currency} " if currency else ""
        lines = [
            f"| Statement of Cash Flows & Liquidity Position | Period: {period} | Verification Status |",
            "| :--- | :--- | :--- |"
        ]
        if cash:
            lines.append(f"| **Cash and Cash Equivalents (Closing)** | {curr_prefix}{cash} | Primary Reported Metric |")
        if debt:
            lines.append(f"| Total Borrowings / Outstanding Debt | {curr_prefix}{debt} | Primary Reported Metric |")
        if dividend:
            lines.append(f"| Dividend Declared / Paid Per Share | {dividend} | Corporate Action Record |")

        return "\n".join(lines)


def extract_structured_statements(storage_engine) -> Dict[str, str]:
    """Inspect database storage and FactsLedger for verified structured statement tables.
    
    Returns a mapping of statement kind to deterministic Markdown table:
    {"balance_sheet": "...", "income_statement": "...", "cash_flow": "..."}.
    """
    out: Dict[str, str] = {}
    if storage_engine is None:
        return out

    # 1. Check FactsLedger for canonical metrics
    ledger = FactsLedger(storage_engine)
    facts = ledger.get_all_facts()
    if facts:
        facts_map: Dict[str, str] = {}
        period = facts[0].period if facts else "Target FY"
        currency = facts[0].currency if facts else ""
        for f in facts:
            if f.value and f.value.strip():
                facts_map[f.key] = f.value

        bs_tbl = FinancialStatementBuilder.build_balance_sheet(facts_map, period=period, currency=currency)
        if bs_tbl:
            out["balance_sheet"] = bs_tbl

        is_tbl = FinancialStatementBuilder.build_income_statement(facts_map, period=period, currency=currency)
        if is_tbl:
            out["income_statement"] = is_tbl

        cf_tbl = FinancialStatementBuilder.build_cash_flow_statement(facts_map, period=period, currency=currency)
        if cf_tbl:
            out["cash_flow"] = cf_tbl

    # 2. Check for pre-existing verified Markdown tables in chunks (from PDF or SEC filings)
    try:
        with sqlite3.connect(storage_engine.db_path, timeout=30) as conn:
            cur = conn.cursor()
            cur.execute("SELECT chunk_id, doc_id, text FROM chunks WHERE is_table = 1")
            tbl_rows = cur.fetchall()
            for cid, doc_id, text in tbl_rows:
                t_lower = text.lower()
                if "balance sheet" in t_lower or "financial position" in t_lower:
                    if "balance_sheet" not in out or len(text) > len(out["balance_sheet"]):
                        # Prefer larger complete table from filings
                        out["balance_sheet"] = text.strip()
                elif "statement of profit and loss" in t_lower or "income statement" in t_lower or "statement of operations" in t_lower:
                    if "income_statement" not in out or len(text) > len(out["income_statement"]):
                        out["income_statement"] = text.strip()
                elif "cash flow" in t_lower or "cash flows" in t_lower:
                    if "cash_flow" not in out or len(text) > len(out["cash_flow"]):
                        out["cash_flow"] = text.strip()
    except Exception:
        pass

    return out


def get_statement_for_task(task_title: str, structured_statements: Dict[str, str]) -> Optional[str]:
    """Match task title to a corresponding pre-built deterministic financial statement table."""
    if not structured_statements:
        return None
    t_low = task_title.lower()
    if any(k in t_low for k in ("balance sheet", "financial position", "assets and liabilities")):
        return structured_statements.get("balance_sheet")
    if any(k in t_low for k in ("income statement", "profit and loss", "profit & loss", "financial performance", "statement of operations")):
        return structured_statements.get("income_statement")
    if any(k in t_low for k in ("cash flow", "cash flows", "liquidity and capital")):
        return structured_statements.get("cash_flow")
    return None
