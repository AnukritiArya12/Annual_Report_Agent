---
name: cold-start-company
description: Handle inaugural startups, private entities, or companies with minimal or zero public web presence.
---

# Cold Start Company Skill

## NAME
cold-start-company

## OBJECTIVE
Construct a complete, authentic corporate annual report for an entity that lacks published historical annual reports or public filings, using founder dossiers, internal management accounts, or charter records without fabricating history or comparative figures.

## DECISION RULES
1. **Identify First-Year / Private Entity Status**: When `find_source_documents` returns zero results or the user specifies inaugural reporting, activate the cold-start protocol.
2. **Collect Primary Corporate Facts & Peer Blueprint**: Call `create_cold_start_dossier` with known fields (e.g., registration ID, incorporation date, business model, initial share capital, revenues, expenses, cash balances). Call `research_industry_peers` with the company's industry or business model (e.g., banking, technology, manufacturing, healthcare, retail) to discover how established peer companies structure their chapters and what sector-specific disclosures are expected.
3. **Respect Jurisdiction Inaugural Rules**: Check `first_year_rules` retrieved from `resolve_company_identity`. If the governing statute specifies that comparative prior-period figures are not required for first-year entities, label prior periods as `"N/A (First Year of Operations)"`.
4. **Mark Gaps Truthfully**: If any core metric (e.g., auditor name, debt, cash flow) is unprovided, explicitly mark it as `"Not disclosed in the sources reviewed"` rather than guessing or interpolating.

## CONSTRAINTS
- Never fabricate prior-year comparative columns or multi-year trends when prior operations did not exist.
- Do not invent quotes, executive officers, or governance committees not recorded in the dossier.
- Scale report length down to match the authentic capacity of the dossier (typically 3–8 focused chapters).

## RECOVERY
- If internal figures conflict or key operational metrics are missing, call `request_human_decision` to obtain clarification or prompt the user for supplemental inputs before writing.
