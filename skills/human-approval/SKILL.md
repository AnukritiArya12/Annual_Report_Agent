---
name: human-approval
description: Guidelines for Human-in-the-Loop (HITL) checkpoints, management interviews, and remote domain approval without stalling autonomous execution.
---

# Human-in-the-Loop (HITL) & Management Interview Skill

## Objective
Maintain seamless autonomous execution while engaging the user or management strictly when human judgment, missing proprietary data, or domain security decisions are genuinely required.

---

## 1. When to Engage Human Input

1. **Remote Web Domain Ingestion**:
   - Before downloading or scraping remote external URLs, call `request_human_decision` for domain approval (e.g., `decision_key="domain_sec.gov"`).
   - If the user has enabled `auto_approve=True`, skip asking and proceed immediately.
   - Local files supplied by the user never require approval.

2. **Cold-Start Management Interview**:
   - For companies without public filings, call `request_management_input(question, respondent_role, report_topics)` to interview management representatives on:
     - Corporate profile and founding genesis
     - Commercial operations and product lines
     - Financial performance, capital raised, and cash balance
     - Board governance and leadership
     - Key risks and mitigating actions

3. **Material Ambiguity or Identity Conflict**:
   - If a company name matches multiple conflicting entities across different jurisdictions, ask the user to clarify the intended entity.

---

## 2. What NOT to Ask the User

- **Do NOT ask for routine confirmations** (e.g. "Should I proceed to draft Chapter 3?", "Should I calculate the CASA ratio?"). The agent must work autonomously.
- **Do NOT ask for arbitrary formatting approvals** (e.g. "Do you like this font?"). Follow professional corporate standards.
- **Do NOT re-prompt for decisions already made** in previous sessions or checkpoints.

---

## 3. HITL State Persistence

- All human decisions and management responses are recorded in the session checkpoint via LangGraph's `SqliteSaver`.
- When resuming an interrupted run, previous responses are reloaded automatically, preventing duplicate interview questions.
