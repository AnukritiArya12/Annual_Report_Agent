---
name: report-planning
description: Dynamic planning and architecture selection for annual reports across diverse industries, company scales, and statutory jurisdictions, ensuring tailored chapters and proportionate depth.
---

# Report Planning & Chapter Architecture Skill

## Objective
Design a bespoke, comprehensive annual report structure tailored to the target company's industry, jurisdiction, scale, and available evidence. Avoid rigid, one-size-fits-all chapter templates.

---

## 1. Planning Execution Sequence

1. **Step 1: Frame the Entity & Jurisdiction**:
   - Determine corporate identity, country, and regulatory framework using `resolve_company_identity(name, country)`.
   - Recognize whether the company is an established corporate, a regulated bank, a private entity, or a first-year startup (`is_first_year=True`).
2. **Step 2: Assess Evidence Depth**:
   - Call `get_evidence_capacity()` to calculate verified data volume.
   - Determine realistic report depth based on evidence volume rather than arbitrary padding.
3. **Step 3: Industry Benchmark Query**:
   - Call `research_industry_peers(industry_or_sector)` to retrieve sector-tailored chapter blueprints, key regulatory indicators, and standard industry disclosures.
4. **Step 4: Establish Chapter Slugs**:
   - Select 8 to 12 tailored chapter slugs (e.g. `01_corporate_profile`, `02_strategic_review`, `03_...`).
   - Save the plan or company profile to `company_profile.md`.

---

## 2. Sector-Tailored Chapter Architectures

### A. Banking & Financial Institutions
```
01_corporate_profile                 -> Banking franchise, license, network, market position
02_chairman_and_ceo_statement        -> Strategic outlook, monetary environment, growth
03_macroeconomic_and_banking_context -> Interest rate environment, regulatory landscape, central bank directives
04_deposit_and_lending_operations   -> CASA deposits, retail & wholesale advances, digital banking
05_treasury_and_investment_portfolio -> SLR/Non-SLR investments, yields, forex, ALM
06_asset_quality_and_credit_risk     -> Gross/Net NPA, slippages, provisioning, recovery mechanisms
07_capital_adequacy_and_basel_iii    -> CRAR, Tier 1, CET1 ratios, risk-weighted assets (RWA)
08_corporate_governance_and_board    -> Board committees (Audit, Risk, Nomination), ethics, compliance
09_management_discussion_and_nii     -> Net Interest Income (NII), NIM, cost-to-income, operating profit
10_financial_statements_and_schedules-> Form A (Balance Sheet), Form B (P&L), Cash Flows, Notes
11_statutory_and_basel_disclosures   -> Basel III Pillar 3 disclosures, customer grievance redressal
```

### B. Technology & Digital Platforms (SaaS)
```
01_corporate_profile                 -> Mission, platform ecosystem, global reach, customer base
02_strategic_review                  -> Market opportunities, cloud expansion, AI integration
03_products_technology_and_r_and_d   -> Product roadmap, platform architecture, patent/IP portfolio
04_operations_and_customer_success   -> ARR/NRR performance, user acquisition, customer retention
05_human_capital_and_culture         -> Tech talent recruitment, engineering culture, diversity
06_cybersecurity_and_data_privacy    -> SOC2/ISO compliance, data protection, security infrastructure
07_risk_management                   -> Platform downtime, regulatory tech compliance, competition
08_corporate_governance              -> Leadership, board oversight, stock-based compensation
09_mda_financial_review              -> Revenue growth, gross margins, EBITDA, free cash flow
10_financial_statements_and_notes    -> Income Statement, Balance Sheet, Cash Flows, Notes
```

### C. Manufacturing, Automotive & Industrial
```
01_corporate_profile                 -> Industrial heritage, global manufacturing footprint, brands
02_management_review_and_strategy    -> Market demand, capacity expansion, automation
03_manufacturing_and_operations      -> Installed vs utilized capacity, lean manufacturing, supply chain
04_quality_health_and_plant_safety   -> LTIFR, occupational safety standards, ISO certifications
05_supply_chain_and_raw_materials    -> Procurement, raw material volatility, logistics resilience
06_esg_and_environmental_stewardship -> Emissions reduction, energy efficiency, circular economy
07_risk_management                   -> Commodity price risk, supply chain disruptions, operational risks
08_corporate_governance              -> Board structure, plant audits, statutory compliance
09_mda_financial_review              -> Revenue, COGS, EBITDA margins, working capital cycle, CapEx
10_financial_statements_and_notes    -> Audited financial statements, segment reporting, schedules
```

### D. Inaugural / Cold-Start Startup Companies
```
01_corporate_profile                 -> Genesis, founding team, corporate charter, mission
02_strategic_vision_and_opportunity  -> Target market problem, solution, commercial opportunity
03_products_services_and_operations  -> Product architecture, early deployments, key milestones
04_founding_governance_and_advisors  -> Board of directors, advisory council, equity structure
05_execution_and_liquidity_risks     -> Execution risks, burn rate management, runway, mitigations
06_team_culture_and_values           -> Early team, core values, operational foundations
07_financial_performance_review      -> Capital raised, operational burn, revenue traction [Mgmt-Provided]
08_financial_statements              -> Balance sheet, receipts and disbursements (Audited/Management)
09_notes_and_capitalization          -> Seed/equity capitalization, outstanding notes, commitments
10_milestones_and_growth_roadmap     -> 12-24 month product and market milestones
```

---

## 3. Subagent Delegation Guide for Planning

- Delegate domain background search to `research_analyst`:
  `task(subagent_type="research_analyst", prompt="Analyze industry peers for sector <industry> and summarize standard chapter arcs and KPIs.")`
- Delegate each drafted section to `section_writer`:
  `task(subagent_type="section_writer", prompt="Draft chapter <slug> adhering to skills/section-writing/SKILL.md...")`
- Delegate final cross-section review to `compliance_auditor`:
  `task(subagent_type="compliance_auditor", prompt="Review all drafted sections for numerical consistency and missing statutory disclosures.")`
