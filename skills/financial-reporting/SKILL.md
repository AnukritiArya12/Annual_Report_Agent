---
name: financial-reporting
description: Enforce canonical ledger adherence, deterministic statement embedding, and double-entry mathematical integrity.
---

# Financial Reporting Skill

## NAME
financial-reporting

## OBJECTIVE
Produce accurate financial sections, statements, and notes by pulling figures directly from the canonical facts ledger and deterministic statement tables, verifying that all financial statements obey fundamental accounting identities.

## DECISION RULES
1. **Initialize Canonical Ledger**: Before drafting financial sections, invoke `build_facts_ledger` to extract and verify core metrics in SQLite.
2. **Canonical Figures Are Immutable**: Retrieve verified metrics via `get_canonical_facts`. You must use these exact numbers and periods; never modify, round differently, or override them from model memory.
3. **Use Pre-Built Statement Tables**: When generating Balance Sheet, Income Statement, or Cash Flow sections, call `get_statement_table` and embed the returned table verbatim. Never attempt to type out a financial statement from scratch.
4. **Enforce Double-Entry Verification**: Call `verify_financials` on every drafted financial section. Verify:
   - Balance Sheet Equation: Assets = Liabilities + Equity.
   - Income Statement: Net Profit = Total Revenue - Total Expenses.
   - Cash Flow: Closing Cash = Opening Cash + Net Change in Cash.
   - Column and row summations in all Markdown tables match within margin.

## CONSTRAINTS
- Never invent financial metrics or derive figures without verifying them.
- In multi-currency environments, state the reporting currency and denomination (e.g., Millions, Billions, Crores) on every table and metric mention.
- Reconcile any discrepancies flagged by `verify_financials` before considering a section complete.

## RECOVERY
- If `verify_financials` reports arithmetic discrepancies or missing totals, inspect the underlying numbers against `get_canonical_facts`. If the source document itself contains an apparent error or rounding variance, add an explanatory footnote rather than modifying the reported figure.
