---
name: section-writing
description: Instructions for the section_writer subagent and main agent on drafting deep, evidence-grounded report sections, persisting them via save_section_draft, and validating quality.
---

# Section Writing & Subagent Drafting Skill

## Objective
Produce deep, professional, evidence-backed annual report sections. Every section must be written strictly from retrieved facts, structured logically, saved persistently to disk, and audited through automated validation tools.

---

## 1. Writing Rules & Quality Standards

1. **Subagent Invocation**:
   - The coordinator delegates section drafting to the `section_writer` subagent:
     `task(subagent_type="section_writer", prompt="Draft section 03_operations_and_business_model for HDFC Bank covering branch network, digital transactions, and retail loan growth...")`
   - Alternatively, the main agent may draft sections directly.

2. **Grounded Drafting Process**:
   - **Step 1: Evidence Query**: Call `search_evidence(query=...)` with 2-4 targeted search terms relevant to the section.
   - **Step 2: Fact Ingestion**: Retrieve verified metrics using `get_canonical_facts()` and tables using `get_statement_table(...)`.
   - **Step 3: Narrative Construction**: Compose clear, comprehensive prose with structured subheadings, bullet points, and data tables.
   - **Step 4: Persistence**: Call `save_section_draft(slug="<slug>", content="<markdown>")` to write the file directly into `drafts/<slug>.md`.
   - **Step 5: Validation**: Call `check_section(markdown=..., section_name="<slug>")` and `check_entities(markdown=...)`. Fix any flagged issues immediately.

3. **Required Markdown Structure for Every Section**:
   ```markdown
   ## [Chapter Number] [Official Chapter Title]

   ### Executive Summary / Key Highlights
   [High-level summary of performance, strategic milestones, and major developments during the fiscal period]

   ### Detailed Review & Analysis
   [Comprehensive operational and strategic narrative supported by evidence-backed tables, metrics, and breakdowns]

   ### Performance & Operational Metrics
   | Metric / Indicator | Reporting Period | Prior Period | Unit / Currency | Source / Status |
   | :--- | :--- | :--- | :--- | :--- |
   | Net Revenue | 1,250.0 | 1,020.0 | USD Millions | Sourced Filing |

   ### Strategic Outlook & Priorities
   [Forward-looking initiatives, strategic focus, and regulatory considerations]

   *Sources: [Filing / Document Name], Section [X], Pages [Y-Z] / Management Interview*
   ```

4. **Zero-Hallucination Policy**:
   - If a specific metric, segment breakdown, or policy disclosure was not reported in the evidence or provided by management:
     Write plainly: `"Not disclosed in the sources reviewed / Not provided by management"`.
   - Never fabricate executive quotes, client testimonials, patent counts, or undisclosed financial line items.

5. **Entity Isolation Guard**:
   - Never leak competitor names, peer numbers, or unrelated entities into the target company's report as facts.
   - If industry benchmarks are discussed, explicitly identify them as external context:
     `"Industry Context: According to peer benchmarks, sector average NIM was 3.4%..."`

---

## 2. Sector-Specific Drafting Directives

### For Banks & Financial Institutions
- Emphasize deposit mix (CASA ratio), retail vs corporate advance growth, Net Interest Margin (NIM), gross/net NPA trajectories, provision coverage, and capital adequacy (CRAR/CET1).
- Never use manufacturing or retail concepts like inventory turnover, factories, bills of materials, or supply chain bottlenecks.

### For Technology & Software (SaaS)
- Emphasize Annual Recurring Revenue (ARR), Net Revenue Retention (NRR), customer churn, cloud platform infrastructure, developer talent, cybersecurity safeguards, and R&D investments.

### For Manufacturing & Industrials
- Emphasize plant capacities (installed vs utilized %), supply chain resilience, safety performance (LTIFR), lean manufacturing, and energy efficiency programs.

### For Startups & Maiden Reports
- Emphasize company genesis, founding vision, product development milestones, early customer traction, team formation, and funding runway. Mark all prior-period figures as `N/A (First Year of Operations)`.
