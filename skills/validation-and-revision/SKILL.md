---
name: validation-and-revision
description: Audit drafted sections for hallucinations, entity leakage, repetition, and formatting before final compilation.
---

# Validation and Revision Skill

## NAME
validation-and-revision

## OBJECTIVE
Subject every drafted section to dual-pass deterministic validation (entity guard, repetition check, table formatting, and ledger consistency), interpreting tool feedback and executing corrective edits before marking a section ready for assembly.

## DECISION RULES
1. **Always Call `check_section`**: Once a draft is written to disk, call `check_section` passing the section content, section title, and evidence excerpts.
2. **Always Call `check_entities`**: Ensure no competitor names, unapproved subsidiaries, or stray corporate entities entered the narrative from pre-training memory.
3. **Handle Audit Statuses**:
   - `PASS`: Proceed to next chapter.
   - `WARNING`: Log the warning, verify no factual distortion, proceed.
   - `FAIL`: Examine the listed `issues`. Identify whether the failure is caused by (a) repetition, (b) ungrounded numbers, (c) blank table cells, or (d) ledger mismatch.
4. **Execute Targeted Edits**: Use `edit_file` to fix specific failing paragraphs or tables rather than rewriting the entire section blindly.
5. **Re-Validate**: Always call `check_section` again after revising until it returns `PASS`.

## CONSTRAINTS
- A section with unresolved `FAIL` status must never be included in the final report assembly.
- Do not bypass entity flags. If an external entity is genuinely relevant (e.g., an independent statutory auditor or major technology partner), request human approval to whitelist the entity via `request_human_decision`.

## RECOVERY
- If a section cannot achieve `PASS` due to conflicting evidence in source documents, call `request_human_decision` to resolve the conflict with human input.
