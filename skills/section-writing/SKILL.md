---
name: section-writing
description: Write rigorous, evidence-grounded report chapters with zero cross-section repetition and full traceability.
---

# Section Writing Skill

## NAME
section-writing

## OBJECTIVE
Draft each report chapter and section strictly from retrieved evidence, citing source documents and page numbers, stating every fact once, eliminating boilerplate, and persisting every drafted section to disk.

## DECISION RULES
1. **Subagent Delegation for Clean Context**: For extensive or multi-chapter reports, the coordinator delegates individual chapter drafting to the specialized `section_writer` subagent using `task(subagent_type="section_writer", prompt=...)`. This ensures each chapter is authored in a dedicated, clean context window with zero memory bloat.
2. **Search Before Writing**: For every section, run `search_evidence` with targeted queries to retrieve relevant chunks, page references, and source document IDs.
3. **Cite Facts Strictly**: State figures with their exact reported period, currency, unit, and source reference (e.g. `[10-K, p.42]`).
4. **No Cross-Section Repetition**: Never repeat paragraphs or identical statements across sections. If an earlier section already established a metric or narrative, summarize it in a subordinate clause (e.g., *"As discussed in the Operational Highlights..."*) rather than re-quoting entire passages.
5. **Persist to Filesystem**: Write each section as a separate Markdown file in `drafts/` on disk (e.g., `drafts/01_strategic_review.md`). Do not retain full draft contents in conversation memory.
6. **Acknowledge Data Absences**: If evidence lacks information on a customary disclosure, state: `"Not disclosed in the sources reviewed"` and continue. Never invent plausible narratives.

## CONSTRAINTS
- Never fabricate numbers, dates, executive quotes, or customer testimonials.
- Shingle overlap with previously written sections must remain strictly below the configured threshold.
- Blank table cells are forbidden; use `N/A`, `Nil`, or `Not Disclosed`.

## RECOVERY
- If a section fails `check_section` due to high repetition or ungrounded claims, review the specific issues returned, rewrite the offending paragraphs with tighter phrasing, and re-validate.
