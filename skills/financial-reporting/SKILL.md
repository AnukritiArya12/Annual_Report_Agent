---
name: financial-reporting
description: Standards, workflows, accounting equations, and ledger verification for corporate and banking financial statements, ensuring zero numerical hallucinations and strict unit/currency accuracy.
---

# Financial Reporting & Statement Verification Skill

## Objective
Extract, verify, and present financial performance with 100% arithmetic integrity and rigorous source attribution. Prevent hallucinated numbers, mismatched currencies, and incorrect statement structures across both commercial corporations and regulated financial institutions (banks).

---

## 1. Core Workflow

1. **Build Canonical Ledger**:
   - Immediately after document ingestion, call `build_facts_ledger(force=True)`.
   - Review `canonical_facts`, `missing_core_keys`, and `conflicts`.
2. **Access Verified Data**:
   - Call `get_canonical_facts()` to retrieve exact verified figures, reporting periods, and evidence chunk citations.
   - Call `get_statement_table("income_statement" | "balance_sheet" | "cash_flow")` to retrieve pre-built statutory statement tables extracted directly from filings.
3. **Currency & Unit Consistency**:
   - Always state the currency (e.g. INR, USD, EUR, GBP) and magnitude scale (e.g. in Millions, in Billions, in Crores, in Thousands) prominently in every table header and text discussion.
   - Never mix scales (e.g., combining Thousands with Millions without conversion).
4. **Statement Arithmetic Verification**:
   - Run `verify_financials(markdown_text)` on every drafted section that includes financial tables.
   - Ensure the fundamental accounting equations hold.

---

## 2. Sector-Specific Financial Frameworks

### A. Commercial & Corporate Enterprises (Tech, Manufacturing, Retail, Energy)
* **Income Statement Arc**:
  - Revenue / Net Sales
  - Less: Cost of Goods Sold (COGS) / Operating Costs
  - **= Gross Profit**
  - Less: SG&A, R&D, Depreciation & Amortization
  - **= Operating Profit (EBIT / EBITDA)**
  - Less: Finance Costs / Interest Expense
  - Add: Other Income / Non-Operating Gain
  - **= Profit Before Tax (PBT)**
  - Less: Provision for Income Tax
  - **= Profit After Tax (PAT) / Net Income**
* **Balance Sheet Equation**:
  $$\text{Total Assets} = \text{Total Liabilities} + \text{Shareholders' Equity}$$
* **Cash Flow Statement**:
  - Operating Cash Flow (CFO)
  - Investing Cash Flow (CFI - CapEx, acquisitions)
  - Financing Cash Flow (CFF - debt, equity, dividends)
  - Net Change in Cash & Cash Equivalents

---

### B. Banking & Regulated Financial Institutions
**DO NOT use manufacturing or retail concepts (COGS, inventory, plant capacity, raw materials) for a bank.**

* **Profit & Loss Statement (Form B / Banking Standard)**:
  - **Interest Earned**: Interest/discount on advances/bills, income on investments, interest on balances with RBI/Central Bank.
  - **Interest Expended**: Interest paid on deposits (savings, term, current), interest on borrowings.
  - **= Net Interest Income (NII)**
  - **Net Interest Margin (NIM)**: $\text{NIM} = \frac{\text{Net Interest Income}}{\text{Average Earning Assets}}$
  - **Other / Non-Interest Income**: Commission, exchange, brokerage, profit on sale of investments, fees.
  - **Total Operating Income (NII + Non-Interest Income)**
  - Less: Operating Expenses (Employee costs, technology, branch overheads).
  - **= Pre-Provision Operating Profit (PPOP)**
  - Less: Provisions and Contingencies (NPA provisioning, standard asset provisions, tax).
  - **= Net Profit (PAT)**

* **Balance Sheet (Form A / Banking Standard)**:
  - **Capital & Liabilities**:
    1. Capital (Equity Share Capital)
    2. Reserves & Surplus (Statutory Reserves, Capital Reserves)
    3. Deposits (Demand Deposits, Savings Bank Deposits, Term Deposits) -> Calculate **CASA Ratio**: $\frac{\text{Current} + \text{Savings}}{\text{Total Deposits}}$
    4. Borrowings (Refinancing, interbank borrowings, debt securities)
    5. Other Liabilities & Provisions
  - **Assets**:
    1. Cash and Balances with Central Bank / Reserve Bank
    2. Balances with Banks and Money at Call & Short Notice
    3. Investments (Government Securities, Bonds, Equity)
    4. Advances (Loans, CC/OD, term loans, bills purchased) -> Net of NPAs
    5. Fixed Assets (Premises, IT infrastructure)
    6. Other Assets

* **Core Banking Prudential Metrics**:
  - **Asset Quality**:
    - Gross Non-Performing Assets (GNPA) & GNPA Ratio %
    - Net Non-Performing Assets (NNPA) & NNPA Ratio %
    - Provision Coverage Ratio (PCR) %
    - Slippage Ratio %
  - **Capital Adequacy (Basel III)**:
    - Capital to Risk-Weighted Assets Ratio (CRAR) %
    - Common Equity Tier 1 (CET1) Ratio %
    - Tier 1 Capital Ratio % and Tier 2 Capital Ratio %
  - **Liquidity & ALM**:
    - Liquidity Coverage Ratio (LCR) %
    - Net Stable Funding Ratio (NSFR) %

---

## 3. Strict Verification & Anti-Hallucination Rules

1. **No Estimated Prior-Year Figures**:
   - If writing an inaugural report (`is_first_year=True`), write `N/A (First Year of Operations)` for previous year columns.
   - Never generate fictitious prior-year numbers to fill a multi-year comparative column.
2. **Missing Statement Items**:
   - If a specific financial line item is missing from source files, do not fabricate a plausible estimate. State:
     `"Not disclosed in the sources reviewed / Not provided by management"`.
3. **Derived Calculations**:
   - When calculating financial ratios (e.g. Return on Equity, Debt-to-Equity, Net Margin, Current Ratio), derive them strictly from verified ledger figures.
   - Always state the formula and input numbers explicitly:
     `"Return on Equity = Net Profit ($12.4M) / Average Equity ($82.0M) = 15.1% [Derived]"`.
4. **Digit Verification**:
   - When adding a new fact via `record_verified_fact(key, value, source_chunk_id)`, the tool performs automated digit checks against the source chunk text. Never attempt to bypass this verification.
