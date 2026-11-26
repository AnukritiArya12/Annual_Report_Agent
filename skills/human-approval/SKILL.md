---
name: human-approval
description: Identify risky or ambiguous decisions and solicit structured human authorization via interrupt checkpoints.
---

# Human Approval Skill

## NAME
human-approval

## OBJECTIVE
Safeguard the report generation process by identifying high-risk decision points and invoking structured human approval checkpoints, ensuring full alignment on identity, data sources, report scope, and final release.

## DECISION RULES
1. **Mandatory Checkpoints**: You MUST call `request_human_decision` for:
   - **Entity Confirmation**: Confirming the exact legal reporting entity, tickers/registration IDs, and jurisdiction.
   - **Source Domain Whitelisting**: Approving any external web domain before downloading or ingesting filings.
   - **Outline Approval**: Confirming the chapter structure, focus areas, and projected page length before drafting begins.
   - **Entity / Factual Conflict Resolution**: Adjudicating unverified third-party names or contradictory numbers across filings.
   - **Final Publication Sign-Off**: Confirming the assembled Markdown, DOCX, and PDF documents prior to completing the run.
2. **Formulate Clear Options**: When calling `request_human_decision`, supply clear, human-intelligible questions with concrete options (e.g., `["Approve outline and proceed", "Revise outline"]`).
3. **Persist State**: The tool automatically records human decisions in `approvals.json` so resuming a session will not re-prompt for previously approved choices.

## CONSTRAINTS
- Never simulate or fake human approval.
- An interrupted checkpoint pauses execution; when resumed, read the human's response from tool output and adhere strictly to it.
- If a human rejects a proposal or chooses an alternative option, follow the directive immediately.

## RECOVERY
- If human approval is denied for a source domain or candidate document, drop the candidate and search for alternative approved filings or switch to internal dossier mode.
