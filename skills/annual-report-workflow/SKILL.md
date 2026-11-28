---
name: annual-report-workflow
description: Master end-to-end execution workflow for the Autonomous Annual Report DeepAgent. Explains how the deep agent plans, delegates to subagents, adapts to any company (established, bank, tech, manufacturing, startup), handles missing records, and compiles audited reports.
---

# Master Annual Report DeepAgent Workflow

## Objective
Produce an authentic, comprehensive, evidence-grounded corporate annual report for ANY target company and fiscal period. The agent plans dynamically, adapts chapters to the company's industry and scale, interviews management for missing data, eliminates hallucinations, and exports Markdown, Word (.docx), and PDF (.pdf) outputs.

---

## 6-Phase Execution Lifecycle

### Phase 1: Entity & Jurisdiction Framing
1. **Extract Session Targets**:
   - Company name, fiscal year, reporting scope (`consolidated` vs `standalone`), jurisdiction, industry, and inaugural status.
2. **Resolve Identity & Statute**:
   - Call `resolve_company_identity(name, country)` to determine corporate structure, governing statute (e.g. US SEC Form 10-K, India Companies Act 2013, UK Companies House, EU CSRD/IFRS, or Global GENERIC).
3. **Handle First-Year / Inaugural Status**:
   - If the company is newly formed or in its maiden year (`is_first_year=True`), apply first-year statutory conventions: comparative prior-period columns are labeled `N/A (First Year of Operations)`, and governance focuses on founding charter, promoter equity, and initial milestones.

---

### Phase 2: Evidence Grounding & Cold-Start Protocol
1. **Local Documents First (Air-Gapped Mode)**:
   - If user provided local PDF, DOCX, or text files, call `ingest_documents([local_paths])` immediately. Local files require NO human approval.
2. **Public Statutory Filings (When Available)**:
   - Call `find_source_documents(company, fy)` to discover official filings (SEC EDGAR, BSE/NSE, Companies House, IR portals).
   - Before downloading remote web URLs, call `request_human_decision` for domain approval (skipped if `auto_approve=True`).
3. **Cold-Start Protocol (For Companies with Zero Prior Filings / Private Startups)**:
   - If no public documents exist, NEVER stop or fail. Execute the Cold-Start Intake:
     a. Call `create_cold_start_dossier(fields, source_label="management-provided")` with any available user information.
     b. Call `request_management_input(question, respondent_role, report_topics)` to interview company representatives on missing material topics:
        - Corporate Profile & Genesis (Founders, incorporation, legal structure)
        - Operating Model & Segments (Products, services, key clients, geography)
        - Financial Performance (Revenue, expenses, net profit/loss, cash balance, funding)
        - Governance & Leadership (Board of directors, advisors, key executives)
        - Principal Risks & Mitigations (Market, operational, liquidity risks)
     c. Label all resulting answers as `[Management-Provided: Company Submission]`.
4. **Build Canonical Facts Ledger**:
   - Call `build_facts_ledger(force=True)`. This indexes verified numeric metrics into SQLite with strict digit verification against source chunks.

---

### Phase 3: Sector-Specific Architecture Planning
1. **Assess Evidence Capacity**:
   - Call `get_evidence_capacity()` to calculate authentic evidence volume and estimate supported page depth. Never pad with fluff to reach an arbitrary page count.
2. **Retrieve Industry Benchmark**:
   - Call `research_industry_peers(industry_or_sector)` to obtain sector-specific chapter titles, KPIs, and risk factors.
3. **Tailor Chapters to the Industry (NEVER Force Generic Chapters)**:
   - **For Banks & Financial Institutions**:
     * Use Banking Franchise, CASA Deposits, Lending Book, Credit Risk & Asset Quality (GNPA, NNPA, Slippages, PCR), Basel III Capital Adequacy (CRAR, Tier 1, CET1), Treasury & NIM, ALM / Liquidity (LCR, NSFR).
     * DO NOT use manufacturing concepts (factories, raw materials, inventory, plant capacity, COGS).
   - **For Technology & SaaS Companies**:
     * Use Cloud Platforms, Annual Recurring Revenue (ARR), Net Revenue Retention (NRR), R&D & IP Portfolio, Developer Talent, Cybersecurity & Data Privacy.
   - **For Manufacturing & Industrial Companies**:
     * Use Manufacturing Footprint, Installed vs Utilized Capacity %, Supply Chain, Raw Materials, Lean Engineering, Plant Safety (LTIFR), CapEx Modernization.
   - **For Healthcare & Pharmaceuticals**:
     * Use Therapeutic Focus, Clinical Trials (Phase I/II/III), R&D Pipeline, Patent Cliffs, cGMP Quality & Regulatory Inspections.
   - **For Energy & Utilities**:
     * Use Generation Capacity (MW), Renewable Transition (Solar/Wind), Plant Load Factor (PLF), Transmission/Refining, Scope 1/2 Emissions.
   - **For Real Estate & Infrastructure**:
     * Use Project Portfolio, Construction Delivery (EPC), Order Backlog, Book-to-Bill Ratio, Sq. Ft. Under Development, Presales Value, Occupancy %.

---

### Phase 4: Section Drafting & Subagent Delegation
1. **Subagent Delegation (Via `task` Tool)**:
   - For focused research: delegate to `research_analyst`.
   - For drafted chapters: delegate to `section_writer` with section title, slug, topics, and evidence boundaries.
   - Alternatively, the main agent can draft chapters directly.
2. **Drafting Rules**:
   - Every section must start with `## Section Title` and end with `*Sources: ...*`.
   - Figures must be drawn strictly from `get_canonical_facts`, `get_statement_table`, or cited evidence chunks.
   - **Zero Hallucination Rule**: If a fact or metric is unevidenced, state:
     `"Not disclosed in the sources reviewed / Not provided by management"`.
   - **Persistence Rule**: Every section MUST be saved immediately using `save_section_draft(slug="...", content="...")` which writes to `drafts/<slug>.md`.

---

### Phase 5: Verification & Quality Auditing
1. **Section Validation**:
   - Run `check_section(markdown, section_name)` on every drafted section to catch:
     * High repetition (>15% shingle overlap) with earlier chapters.
     * Blank table cells.
     * Numbers that contradict the canonical facts ledger.
2. **Entity Isolation**:
   - Run `check_entities(markdown)` to prevent accidental leakage of competitor names or peer data into the target company's report.
3. **Financial Statement Integrity**:
   - For sections containing financial tables, run `verify_financials(markdown)` to audit table sums and accounting equations ($Assets = Liabilities + Equity$).
4. **Independent Review**:
   - Delegate full draft review to `compliance_auditor` subagent for risk screening.

---

### Phase 6: Multi-Modal Charts & Final Report Assembly
1. **Visual Trend Analytics**:
   - Call `make_charts(include=True)`. Generates real PNG trend charts from verified facts in the ledger (zero chart fabrication if fewer than 2 authentic data points exist).
2. **Document Assembly**:
   - Call `assemble_report(section_files=[...])`.
   - Automatically compiles:
     * Corporate Cover & Metadata
     * Verified Company Profile (`company_profile.md`)
     * Dynamic Table of Contents with accurate page numbers
     * All drafted sections from `drafts/*.md` in sequential order
     * Visual Analytics & Trend Charts
     * Canonical Facts Ledger Appendix
   - Generates three synchronized output formats:
     * **Markdown (`.md`)**
     * **Microsoft Word (`.docx`)**
     * **Executive PDF (`.pdf`)**
3. **Final Delivery**:
   - Confirm file existence, final word count, and page count. Present download links to the user.
