---
name: cold-start-company
description: Protocol for building an authentic annual report for newly formed companies, private startups, or entities with zero prior public filings using locally supplied files and interactive management interviews.
---

# Cold-Start & Private Company Reporting Skill

## Objective
Enable the Autonomous Annual Report DeepAgent to produce a complete, credible, evidence-grounded annual report for a newly formed company, early-stage venture, private firm, or any entity lacking historical public filings—with zero internet dependence and zero hallucinated figures.

---

## 1. Operating Principles

1. **Air-Gapped & Local First**:
   - The agent operates primarily on documents supplied by the user (business plans, pitch decks, draft financials, board resolutions, trial balances).
   - If local files exist in the session input directory, ingest them immediately with `ingest_documents([local_paths])`. Local files require no external approval.

2. **No Invented Facts**:
   - Never fabricate revenue, customer counts, headcount, funding amounts, or board members.
   - If information is missing from documents and not supplied during interview, state clearly in the section text:
     `"Not disclosed in the sources reviewed / Not provided by management"`.

3. **Inaugural Year Conventions (`is_first_year=True`)**:
   - For a company's maiden annual report, prior-year comparative columns in financial tables must be explicitly labeled:
     `N/A (First Year of Operations)`.
   - Never calculate or display prior-year growth rates (YoY %) when there is no preceding statutory financial period.

4. **Source Attribution & Management Disclosures**:
   - Every metric or disclosure supplied directly by company leadership must be tagged as:
     `[Management-Provided: Company Submission]`.

---

## 2. Interactive Management Interview Protocol

When a target company has incomplete documentation, execute the following 5-part intake using `request_management_input(question, respondent_role, report_topics)`:

### Part 1: Corporate Profile & Genesis
- **Question**: "Please confirm the official legal entity name, date and place of incorporation, founding leadership, registered office, and the core mission/foundational vision of the company."
- **Target Section**: `01_corporate_profile.md`

### Part 2: Business Operations & Product Lines
- **Question**: "What are your primary commercial products, services, operating segments, target markets, and key operational milestones achieved during this reporting period?"
- **Target Section**: `03_operations_and_business_model.md`

### Part 3: Financial Performance & Capitalization
- **Question**: "What are your total revenue/receipts, principal operating expenses, net profit or loss, ending cash balance, and any equity/debt funding raised during the period? Please provide currency and exact figures."
- **Target Section**: `07_mda_financial_review.md` & `08_financial_statements.md`

### Part 4: Governance & Key Personnel
- **Question**: "Please state the names, designations, and backgrounds of the Board of Directors, executive officers, key advisors, and any statutory auditors appointed."
- **Target Section**: `04_corporate_governance.md`

### Part 5: Principal Risks & Outlook
- **Question**: "What are the key commercial, operational, market, regulatory, or liquidity risks the company faces, and what mitigation strategies are currently in place?"
- **Target Section**: `05_risk_management.md` & `10_outlook.md`

---

## 3. Tool Execution Sequence for Cold-Start

```
1. User provides initial profile or files -> ingest_documents / create_cold_start_dossier
2. Check for missing material pillars:
   - Profile / Operations / Financials / Governance / Risks
3. For each missing pillar:
   - Call request_management_input(question="...", respondent_role="Management", report_topics=[...])
4. Record responses into facts ledger:
   - Call build_facts_ledger(force=True)
   - Call record_verified_fact(key="...", value="...", source_chunk_id="management_input")
5. Plan and draft tailored chapters:
   - Delegate drafting to section_writer subagent
   - Save via save_section_draft(slug="...", content="...")
```

---

## 4. Chapter Adaptation for Early-Stage & Private Companies

Private and startup annual reports should replace rigid public-company bureaucracy with relevant corporate disclosures:

| Traditional Public Chapter | Cold-Start / Startup Adaptation |
| :--- | :--- |
| `01_corporate_profile` | Company Genesis, Vision, Legal Structure, and Foundational Milestones |
| `02_strategic_review` | Business Model, Market Opportunity, Product-Market Fit & Scale Strategy |
| `03_operations_and_business_model` | Product Architecture, Customer Segments, Delivery Channels & Operations |
| `04_corporate_governance` | Founding Leadership, Board Structure, Advisory Council & Shareholding |
| `05_risk_management` | Early-Stage Execution Risks, Runway/Liquidity Risk & Mitigations |
| `06_sustainability_and_impact` | Environmental Impact, Team Culture, Diversity & Community Initiatives |
| `07_mda_financial_review` | Capital Utilization, Revenue Inflow, Burn Rate & Unit Economics |
| `08_financial_statements` | Statement of Operations, Financial Position & Cash Flows (Audited or Management-Certified) |
| `09_notes_to_accounts` | Accounting Policies, Share Capital Structure, and Contingent Liabilities |
| `10_outlook` | Strategic Milestones, Technology Roadmap, and Next Period Growth Targets |
