---
name: validation-and-revision
description: Multi-tier auditing protocol covering section quality, entity leakage prevention, financial statement arithmetic, compliance auditing, and multi-format document assembly.
---

# Validation, Auditing & Report Assembly Skill

## Objective
Audit all drafted sections for factual accuracy, consistency, zero competitor leakage, and mathematical integrity before compiling the final multi-format corporate annual report (Markdown, DOCX, and PDF).

---

## 1. Multi-Tier Auditing Protocol

### Tier 1: Section-Level Quality Check
- Run `check_section(markdown=..., section_name="<slug>", evidence_excerpts=[...])` on every drafted chapter.
- **Verifies**:
  - Minimum substantive depth (detects empty or stub sections).
  - Repetition and redundancy (flags >15% shingle overlap with earlier drafted sections).
  - Table integrity (flags blank or malformed cells).
  - Numeric alignment with canonical facts ledger.

### Tier 2: Entity Isolation & Anti-Leakage Check
- Run `check_entities(markdown=...)` on the corporate profile and all drafted chapters.
- **Verifies**:
  - Prevents accidental copying of peer company names, competitor metrics, or unrelated entities into the target company's report.
  - Ensures peer benchmarks are strictly quarantined and labeled as external context.

### Tier 3: Financial Statement Arithmetic Audit
- Run `verify_financials(markdown=...)` on all sections containing financial tables.
- **Verifies**:
  - Addition and subtraction across rows and columns.
  - Balance Sheet accounting identity: $\text{Assets} = \text{Liabilities} + \text{Equity}$.
  - Income Statement flow: $\text{Revenue} - \text{Expenses} = \text{Net Profit}$.
  - For Banks: $\text{Interest Earned} - \text{Interest Expended} = \text{Net Interest Income (NII)}$.

### Tier 4: Regulatory & Statutory Coverage Review
- Call `check_regulatory_coverage(sector="<industry>")` to benchmark drafted topics against mandatory disclosure checklists (e.g. SEC 10-K, India Companies Act 2013, Basel III, ESG).
- Treat missing items as candidates: include them if evidence exists, or explicitly note them as `"Not disclosed in the sources reviewed / Not provided by management"`.

### Tier 5: Subagent Compliance Review
- Delegate full report audit to `compliance_auditor` subagent:
  `task(subagent_type="compliance_auditor", prompt="Audit drafted sections for numeric consistency, unsupported claims, and formatting...")`

---

## 2. Multi-Format Report Assembly

Once all sections pass validation:

1. **Generate Real Visual Charts**:
   - Call `make_charts(include=True)`.
   - Generates authentic trend charts (Revenue, Net Income, Asset Quality) saved as PNG files.
   - If fewer than 2 data points exist, chart fabrication is suppressed to maintain truthfulness.

2. **Assemble the Final Report**:
   - Call `assemble_report(section_files=[...])`.
   - If `section_files` is omitted, the tool automatically discovers and sequences all markdown drafts from `drafts/*.md`.
   - **Outputs Generated**:
     - `annual_report.md` (Clean, standardized Markdown)
     - `annual_report.docx` (Executive Word document with formal styling, tables, headers, and footers)
     - `annual_report.pdf` (Publication-grade PDF with table of contents, running headers, and page numbers)

3. **Page Limit Management**:
   - If `PAGE_LIMIT_EXCEEDED` is flagged:
     - Review sections for verbosity and redundancy.
     - Trim unnecessary filler, summarize verbose paragraphs, and reassemble.
