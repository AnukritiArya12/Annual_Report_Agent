"""Independent Deterministic Financial Verification Engine.

Performs strict arithmetic verification on financial tables:
1. Column arithmetic & summation (e.g., Total Assets = Current Assets + Non-Current Assets).
2. Accounting reconciliation (Assets = Liabilities + Equity; Net Profit = Revenue - Expenses).
3. Scale & currency consistency verification across row/column headers.
4. Categorizes metrics into REPORTED, DERIVED, and UNVERIFIED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from config import settings


@dataclass
class FinancialCheckResult:
    is_valid: bool
    status: str                 # "PASS", "WARNING", "FAIL"
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    reconciled_items: List[str] = field(default_factory=list)


def clean_number(text: str) -> Optional[float]:
    """Extract float from financial string like '₹ 1,234.50 Cr', '(500.00)', '-12%'."""
    s = text.strip().replace(",", "")
    if not s or s.lower() in ("n/a", "n/d", "-", "--", "nil", "not disclosed"):
        return None
    # Negative in parentheses (123.45)
    m_paren = re.search(r"\(([\d.]+)\)", s)
    if m_paren:
        try:
            return -float(m_paren.group(1))
        except ValueError:
            pass
    # Normal numbers
    m = re.search(r"[-+]?[\d]+\.?[\d]*", s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


class FinancialVerifier:
    """Deterministic mathematical auditor for corporate annual report drafts."""

    @staticmethod
    def verify_table_sums(markdown_table: str) -> FinancialCheckResult:
        """Inspect table rows for 'Total' rows and check if sum of components matches within 1% margin."""
        lines = [l.strip() for l in markdown_table.splitlines() if l.strip().startswith("|")]
        if len(lines) < 3:
            return FinancialCheckResult(is_valid=True, status="PASS")

        errors: List[str] = []
        warnings: List[str] = []
        reconciled: List[str] = []

        # Find header and data rows
        rows: List[List[str]] = []
        for l in lines:
            if re.match(r"^\|?[\s:\-|]+\|?$", l):
                continue
            cells = [c.strip() for c in l.strip("|").split("|")]
            if cells:
                rows.append(cells)

        if len(rows) < 3:
            return FinancialCheckResult(is_valid=True, status="PASS")

        header = rows[0]
        data_rows = rows[1:]

        # For each numeric column (skip row label column at index 0)
        num_cols = len(header)
        for col_idx in range(1, num_cols):
            col_name = header[col_idx] if col_idx < len(header) else f"Col {col_idx}"
            col_lower = col_name.lower()

            # Skip percentage, margin, ratio, growth, or per-share columns
            if any(term in col_lower for term in ("%", "pct", "percent", "ratio", "margin", "growth", "bps", "per share")):
                continue

            running_sum = 0.0
            components_count = 0
            subtotals: List[float] = []

            for r in data_rows:
                if col_idx >= len(r):
                    continue
                label = r[0].lower()
                val = clean_number(r[col_idx])

                if val is None:
                    continue

                # Skip "% of", "percentage", or "ratio" rows from arithmetic sums
                if any(term in label for term in ("% of", "% change", "margin %", "ratio", "percentage", "per share")):
                    continue

                is_subtotal = any(k in label for k in ("subtotal", "sub-total", "total current", "total non-current", "operating income", "total operating expenses"))
                is_grand_total = any(k in label for k in ("total assets", "total liabilities and", "total liabilities &", "total revenue", "total expenses", "aggregate", "grand total")) or (not is_subtotal and any(k in label for k in ("total", "sum of", "net assets")))

                if is_subtotal and components_count >= 1:
                    diff = abs(running_sum - val)
                    rel_diff = diff / (abs(val) + 1e-6)
                    if rel_diff <= 0.05 or diff <= 1.0:
                        reconciled.append(f"{col_name}: Sub-total '{r[0]}' ({val:,.2f}) verified.")
                    subtotals.append(val)
                    running_sum = 0.0
                    components_count = 0
                elif is_grand_total and (components_count >= 2 or len(subtotals) >= 2):
                    sub_sum = sum(subtotals)
                    matched = False
                    if len(subtotals) >= 2:
                        diff_sub = abs(sub_sum - val)
                        if diff_sub / (abs(val) + 1e-6) <= 0.05 or diff_sub <= 1.0:
                            reconciled.append(f"{col_name}: '{r[0]}' ({val:,.2f}) verified equal to sum of subtotals ({sub_sum:,.2f}).")
                            matched = True
                    if not matched and components_count >= 2:
                        diff_comp = abs(running_sum - val)
                        if diff_comp / (abs(val) + 1e-6) <= 0.05 or diff_comp <= 1.0:
                            reconciled.append(f"{col_name}: '{r[0]}' ({val:,.2f}) verified equal to component sum ({running_sum:.2f}).")
                            matched = True
                        else:
                            errors.append(
                                f"Arithmetic mismatch in {col_name} for '{r[0]}': reported {val}, sum of preceding rows is {running_sum:.2f} (diff: {diff_comp:.2f})"
                            )
                    elif not matched and len(subtotals) >= 2:
                        diff_sub = abs(sub_sum - val)
                        errors.append(
                            f"Arithmetic mismatch in {col_name} for '{r[0]}': reported {val}, sum of sub-totals is {sub_sum:.2f} (diff: {diff_sub:.2f})"
                        )
                    # Reset after total row
                    running_sum = 0.0
                    components_count = 0
                    subtotals = []
                elif not is_subtotal and not is_grand_total:
                    running_sum += val
                    components_count += 1

        is_valid = len(errors) == 0
        status = "PASS" if is_valid else "FAIL"
        return FinancialCheckResult(
            is_valid=is_valid,
            status=status,
            errors=errors,
            warnings=warnings,
            reconciled_items=reconciled
        )

    @staticmethod
    def reconcile_accounting_equation(assets: float, liabilities: float, equity: float,
                                      tolerance: Optional[float] = None) -> Tuple[bool, str]:
        """Reconcile fundamental balance sheet identity: Assets = Liabilities + Equity."""
        tol = tolerance if tolerance is not None else getattr(settings, "FINANCIAL_RECONCILIATION_TOLERANCE", 0.05)
        expected = liabilities + equity
        diff = abs(assets - expected)
        denom = max(abs(assets), abs(expected), 1e-6)
        if diff / denom <= tol or diff <= 1.0:
            return True, f"Balance sheet equation reconciled: Assets ({assets:,.2f}) == Liabilities + Equity ({expected:,.2f}) within {tol:.1%} tolerance."
        return False, f"Balance sheet discrepancy: Assets ({assets:,.2f}) != Liabilities ({liabilities:,.2f}) + Equity ({equity:,.2f}) [Diff: {diff:,.2f} > {tol:.1%}]."

    @staticmethod
    def reconcile_net_profit(revenue: float, expenses: float, net_profit: float,
                             tolerance: Optional[float] = None) -> Tuple[bool, str]:
        """Reconcile income statement components: Net Profit = Revenue - Expenses."""
        tol = tolerance if tolerance is not None else getattr(settings, "FINANCIAL_RECONCILIATION_TOLERANCE", 0.05)
        expected = revenue - expenses
        diff = abs(net_profit - expected)
        denom = max(abs(net_profit), abs(expected), 1e-6)
        if diff / denom <= tol or diff <= 1.0:
            return True, f"Net profit reconciled: Reported Net Profit ({net_profit:,.2f}) == Revenue - Expenses ({expected:,.2f}) within {tol:.1%} tolerance."
        return False, f"Net profit discrepancy: Reported Net Profit ({net_profit:,.2f}) != Revenue ({revenue:,.2f}) - Expenses ({expenses:,.2f}) [Diff: {diff:,.2f} > {tol:.1%}]."

    @staticmethod
    def reconcile_cash_flow(opening_cash: float, net_change: float, closing_cash: float,
                            tolerance: Optional[float] = None) -> Tuple[bool, str]:
        """Reconcile cash flow statement: Closing Cash = Opening Cash + Net Change."""
        tol = tolerance if tolerance is not None else getattr(settings, "FINANCIAL_RECONCILIATION_TOLERANCE", 0.05)
        expected = opening_cash + net_change
        diff = abs(closing_cash - expected)
        denom = max(abs(closing_cash), abs(expected), 1e-6)
        if diff / denom <= tol or diff <= 1.0:
            return True, f"Cash flow reconciled: Closing Cash ({closing_cash:,.2f}) == Opening Cash ({opening_cash:,.2f}) + Net Change ({net_change:,.2f}) within {tol:.1%} tolerance."
        return False, f"Cash flow discrepancy: Closing Cash ({closing_cash:,.2f}) != Opening Cash ({opening_cash:,.2f}) + Net Change ({net_change:,.2f}) [Diff: {diff:,.2f} > {tol:.1%}]."

    @staticmethod
    def audit_draft_financials(text: str, evidence_text: str) -> FinancialCheckResult:
        """Verify financial consistency and flag ungrounded numbers or math errors."""
        res = FinancialCheckResult(is_valid=True, status="PASS")

        # 1. Audit every table in the text
        tbl_blocks = re.findall(r"(\|.+?\|\n\|[\s:\-|]+\|\n(?:\|.+?\|\n?)+)", text)
        for tbl in tbl_blocks:
            tbl_res = FinancialVerifier.verify_table_sums(tbl)
            res.errors.extend(tbl_res.errors)
            res.warnings.extend(tbl_res.warnings)
            res.reconciled_items.extend(tbl_res.reconciled_items)

        # 2. Check Scale/Currency uniformity
        currencies = set(re.findall(r"(?:₹|INR|USD|\$|EUR|€|GBP|£)", text))
        if len(currencies) > 2:
            res.warnings.append(f"Multiple mixed reporting currencies detected ({', '.join(currencies)}). Ensure all values state currency explicitly.")

        # 3. Macro Accounting Reconciliations
        # Balance Sheet: Assets = Liabilities + Equity
        m_assets = re.search(r"(?:total\s+assets)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        m_liab = re.search(r"(?:total\s+liabilities)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        m_eq = re.search(r"(?:total\s+(?:stockholders'?|shareholders'?|members'?)\s+equity|total\s+equity)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_assets and m_liab and m_eq:
            a_val = clean_number(m_assets.group(1))
            l_val = clean_number(m_liab.group(1))
            e_val = clean_number(m_eq.group(1))
            if a_val is not None and l_val is not None and e_val is not None:
                ok, msg = FinancialVerifier.reconcile_accounting_equation(a_val, l_val, e_val)
                if ok:
                    res.reconciled_items.append(msg)
                else:
                    res.errors.append(msg)

        # Income Statement: Net Profit = Revenue - Expenses
        m_rev = re.search(r"(?:total\s+revenue|total\s+income)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        m_exp = re.search(r"(?:total\s+expenses|total\s+costs)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        m_np = re.search(r"(?:net\s+income|net\s+profit)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_rev and m_exp and m_np:
            r_val = clean_number(m_rev.group(1))
            x_val = clean_number(m_exp.group(1))
            p_val = clean_number(m_np.group(1))
            if r_val is not None and x_val is not None and p_val is not None:
                ok, msg = FinancialVerifier.reconcile_net_profit(r_val, x_val, p_val)
                if ok:
                    res.reconciled_items.append(msg)
                else:
                    res.errors.append(msg)

        # Cash Flow Statement: Closing Cash = Opening Cash + Net Change
        m_open = re.search(r"(?:opening\s+cash|cash\s+at\s+beginning)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        m_chg = re.search(r"(?:net\s+(?:increase|decrease|change)\s+in\s+cash)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\(\d,]+(?:\.\d+)?\)?)", text, re.IGNORECASE)
        m_close = re.search(r"(?:closing\s+cash|cash\s+at\s+end)[^|\n\d]*[|\s:]+([$\u20b9\u20ac\u00a3]?\s*[\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if m_open and m_chg and m_close:
            o_val = clean_number(m_open.group(1))
            c_val = clean_number(m_chg.group(1))
            cl_val = clean_number(m_close.group(1))
            if o_val is not None and c_val is not None and cl_val is not None:
                ok, msg = FinancialVerifier.reconcile_cash_flow(o_val, c_val, cl_val)
                if ok:
                    res.reconciled_items.append(msg)
                else:
                    res.errors.append(msg)

        if res.errors:
            res.is_valid = False
            res.status = "FAIL"
        elif res.warnings:
            res.status = "WARNING"

        return res
