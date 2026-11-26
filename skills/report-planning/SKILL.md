---
name: report-planning
description: Autonomously design chapter hierarchy, outline, and target page depth scaled truthfully to evidence capacity.
---

# Report Planning Skill

## NAME
report-planning

## OBJECTIVE
Formulate a tailored annual report blueprint and chapter outline reflecting the company's authentic operating segments, industry requirements, statutory mandates, and evidence capacity, with zero hardcoded chapter lists.

## DECISION RULES
1. **Calibrate Capacity Before Outlining**: Always invoke `get_evidence_capacity` to determine total ingested evidence words and the authentic supported page range.
2. **Scale Length to Evidence**:
   - Thin evidence (< 3,000 words): plan a concise, tight report (4–6 core chapters, ~5–15 pages).
   - Moderate evidence (10,000–50,000 words): plan a standard full report (7–12 chapters, ~30–75 pages).
   - Deep comprehensive filings (> 100,000 words): plan an exhaustive enterprise annual report (12–25 chapters, 100–300+ pages).
3. **Incorporate Statutory Mandates Dynamically**: Retrieve mandatory chapters from `resolve_company_identity` for the company's governing jurisdiction and industry (e.g., MD&A, Risk Factors, Governance Disclosures, Notes to Accounts).
4. **Tailor to Industry Reality**:
   - Financial Institutions: include Credit Risk, Capital Adequacy / Basel metrics, NPA / Loan Loss provisions, Net Interest Margin.
   - Technology / SaaS: include Annual Recurring Revenue (ARR), R&D Capitalization, Customer Retention, Cloud Infrastructure.
   - Manufacturing: include Production Capacity, Supply Chain, Raw Material Price Exposure, Health & Safety.
   - Pharmaceuticals: include Clinical Trial Pipeline, Regulatory Filings/Approvals, Patent Expiries.
5. **Formalize Plan**: Save the outline as `plan.json` or `todo.json` on disk, and call `request_human_decision` for human confirmation of the outline before writing.

## CONSTRAINTS
- Never pad or create empty chapters just to meet an arbitrary page target.
- Never hardcode chapter titles or stage numbers. The agent generates the plan dynamically.
- Each chapter must have clear focus areas, dependencies, and search queries.

## RECOVERY
- If human rejects the outline or requests modifications, revise chapter depth, re-balance word budgets, and re-submit the updated plan.
