"""Foundational Skills for the Generic Autonomous Annual Report Agent.

Skills are the foundational operating system of DeepAgents. Each skill is self-contained
with a standardized architecture:
  1. NAME
  2. OBJECTIVE
  3. INVOCATION & APPLICABILITY
  4. DECISION RULES & OPERATIONAL WORKFLOW
  5. CONSTRAINTS & QUALITY STANDARDS
  6. RECOVERY STRATEGIES
"""

from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# SECTION KINDS & WRITING PATTERNS
# ---------------------------------------------------------------------------
SECTION_KINDS = [
    "cover", "message", "narrative", "summary", "data_table",
    "financial_statement", "notes_to_accounts", "statutory", "list", "appendix"
]


def _skill_pattern(purpose: str, structure: str, must: str, must_not: str, example: str) -> str:
    return (f"PURPOSE: {purpose}\nSTRUCTURE: {structure}\nMUST: {must}\n"
            f"MUST NOT: {must_not}\nEXAMPLE PATTERN: {example}")


STYLE_GUIDES: Dict[str, str] = {
    "cover": _skill_pattern(
        "Front matter that authoritatively identifies the annual report.",
        "Corporate identifiers: Registered company name, statutory identification code (Ticker/CIN/CIK/LEI), registered office, reporting period, and reporting scope.",
        "Copy names, dates and identifiers exactly as they appear in the official evidence.",
        "Add marketing slogans, fictitious dates or identifiers not found in the evidence.",
        "[Reporting Entity Legal Name] | Annual Report [Financial Year] | [Reporting Scope]"),

    "message": _skill_pattern(
        "A formal corporate address reflecting strategic perspectives and operating context.",
        "Opening macroeconomic context; operational review; business model resilience; strategic growth drivers; closing stakeholder acknowledgments.",
        "Ground every claim, figure, and initiative in verified corporate evidence. Maintain formal corporate voice.",
        "Invent personal quotes, forward-looking commitments not stated in the evidence, or ungrounded targets.",
        "In FY [Period], [Company] achieved [Metric] [Value] amidst [Operating Context]. Looking ahead, our strategic priority remains [Strategy from Evidence]."),

    "narrative": _skill_pattern(
        "Explanatory corporate analysis of operations, strategy, segment performance, or enterprise governance.",
        "Analytical overview of developments, operational metrics, causal drivers, and trajectory with #### thematic subheadings.",
        "Ground every statement in verified evidence. Always accompany figures with reporting period, unit, and currency. Where evidence is absent, state 'Not Disclosed in Sources Reviewed' once and proceed.",
        "Invent statistics or operational facts. Use generic boilerplate that could describe any company. Discuss unrelated corporate groups.",
        "[Segment/Initiative] recorded [metric] of [value] [currency/unit] in [period] compared to [prior value] in [prior period], driven by [grounded driver]."),

    "summary": _skill_pattern(
        "Executive synthesis condensing key operational and financial milestones.",
        "Concise thematic bullets or compact metric table summarizing preceding evidence.",
        "Reuse figures from the earlier verified sections with exact fidelity.",
        "Introduce new metrics or unsubstantiated claims not found in detailed sections.",
        "- [Operational Milestone]: [Metric Value] [Unit] ([Period])"),

    "data_table": _skill_pattern(
        "Structured multi-period or multi-segment performance metrics with zero empty cells.",
        "Markdown table (Metrics as rows, periods/segments as columns, unit and currency in headers) followed by concise explanatory commentary.",
        "Extract every cell from verified evidence. If a specific metric is not disclosed for a given period, write 'Not Disclosed' or 'n/d'. EVERY CELL MUST HAVE A VALUE OR AN EXPLICIT NOT DISCLOSED MARKER.",
        "Leave ANY cell blank. Fabricate, extrapolate, or estimate figures. Mix mismatched currencies or periods in the same column without notice.",
        "| Metric | FY [Current] | FY [Prior] |\n| :--- | :--- | :--- |\n| Revenue from Operations ([Currency] [Unit]) | [Value] | [Value] |\n| Operating Margin (%) | [Value]% | [Value]% |\n| Segment Capital Employed ([Currency]) | [Value] | Not Disclosed |"),

    "financial_statement": _skill_pattern(
        "Authoritative reproduction of primary financial statements.",
        "Official line items in reported sequence, matching exact columns, reporting framework, currency, and scale units.",
        "Preserve exact line item descriptions, signs (parentheses for negative), and totals. If only partial lines are retrieved, report them faithfully and explicitly state missing disclosures.",
        "Omit reported line items, invent estimates, reclassify line items, or leave columns blank.",
        "| Line Item ([Currency] in [Scale]) | As at [Date 1] | As at [Date 2] |\n| :--- | :--- | :--- |\n| Equity Share Capital | [Value] | [Value] |\n| Reserves and Surplus | [Value] | [Value] |\n| Total Non-Current Liabilities | [Value] | [Value] |"),

    "notes_to_accounts": _skill_pattern(
        "Detailed accounting policies, segmental splits, commitments, contingencies, and statutory reconciliations.",
        "Note heading, accounting policy summary, structured schedule table, and contingent liability disclosures.",
        "Preserve exact reported numbers, reconciliation rows, and statutory notes. Explicitly state 'Not Disclosed' for missing sub-schedules.",
        "Summarize away material statutory details or recalculate reported values.",
        "### Note [X]: Segment Reporting\nAccounting Policy: [Reported Policy]\n| Segment | Revenue | Profit before Tax | Assets Employed |\n| :--- | :--- | :--- | :--- |\n| [Segment A] | [Val] | [Val] | [Val] |\n| [Segment B] | [Val] | Not Disclosed | [Val] |"),

    "statutory": _skill_pattern(
        "Mandatory regulatory disclosures required by corporate law, listing rules, or national regulators.",
        "Statutory compliance matrix, governance declarations, committee compositions, or statutory auditor comments.",
        "State exact dates, member names, meeting attendances, thresholds, and statutory citations as reported. If an item is not reported, write '[Statutory Item]: Not Disclosed in Sources Reviewed'.",
        "Invent committee members, fabricate audit qualifications, or assert regulatory compliance without evidence.",
        "- Secretarial Audit Compliance: [Grounded finding]\n- Related Party Transactions (RPT) Policy: [Grounded finding or Not Disclosed in Sources Reviewed]"),

    "list": _skill_pattern(
        "Scannable catalog of items, subsidiaries, facilities, key management personnel, or risk registers.",
        "Bulleted or structured 2-column format with uniform item descriptors.",
        "Ground every entry in evidence. Keep descriptors concise, factual, and uniform.",
        "Add speculative items, competitors, or external entities not affiliated with the reporting entity.",
        "- [Entity/Facility Name]: [Location], [Operational Capacity / Shareholding %]"),

    "appendix": _skill_pattern(
        "Reference documentation, glossary of industry terms, basis of preparation, and verification disclaimers.",
        "Clean reference tables or glossary entries defining reporting scope, exchange rates, and methodology.",
        "State source document provenance, reporting currency standards, and disclosure limitation notices.",
        "Introduce narrative arguments or unsubstantiated claims.",
        "| Term / Metric | Statutory Definition | Applicable Framework |\n| :--- | :--- | :--- |\n| [Term] | [Definition from Evidence] | [Regulatory Standard] |"),
}


# ---------------------------------------------------------------------------
# SKILL 1: COMPANY INTELLIGENCE & SOURCE VERIFICATION SKILL
# ---------------------------------------------------------------------------
class CompanyIntelligenceSkill:
    """Skill 1: Resolves corporate identity, discovers regulatory jurisdiction, and verifies source authenticity."""

    NAME = "Company Intelligence & Source Verification Skill"
    OBJECTIVE = (
        "Establish the verified corporate identity of the reporting entity, identify its applicable statutory "
        "regime, and ensure that 100% of ingested reference documents belong strictly to the target company, "
        "filtering out all competitors, unlisted sister companies, and look-alike entities."
    )

    SYSTEM_PROMPT = f"""SKILL: {NAME}
OBJECTIVE: {OBJECTIVE}

INVOCATION & APPLICABILITY:
Loaded at the beginning of the reporting lifecycle before any document chunking, indexing, or structural planning.

DECISION RULES & OPERATIONAL WORKFLOW:
1. CORPORATE ENTITY RESOLUTION:
   - Differentiate commercial trade names/brands from legal filing entities (e.g., 'Google' -> 'Alphabet Inc.').
   - Determine exact identification codes (Ticker, CIK, CIN, LEI, ISIN) and primary stock exchange(s).
2. JURISDICTION & REGULATORY REGIME IDENTIFICATION:
   - Identify the primary country of incorporation, listing domicile, and applicable corporate law.
   - Map the mandatory disclosure regime (e.g. US SEC Form 10-K, UK Companies Act Strategic Report, India SEBI LODR / Companies Act / RBI, European CSRD/IFRS).
   - Identify the primary accounting framework (US GAAP, IFRS, Ind AS, local GAAP) and fiscal year convention.
3. SOURCE PROVENANCE VERIFICATION:
   - Examine candidate filings and reports retrieved from regulatory databases, company investor relations, or web search.
   - Verify that the title, registration identifier, and reporting entity match the target company.
   - EXCLUDE any document belonging to peer competitors, external industry analyses, or independent subsidiaries unless consolidated.
4. HUMAN SUPERVISION GATE:
   - Present the resolved legal entity, identifiers, primary jurisdiction, and verified source documents for human confirmation.
   - Ensure the user confirms that the sources belong strictly to the target company and the requested financial year before indexing.

RECOVERY STRATEGIES:
- If automated regulatory filing retrieval fails, transition to the company's official Investor Relations web portal.
- If corporate naming is ambiguous, pause and prompt the human supervisor with disambiguation choices."""

    @classmethod
    def get_instructions(cls) -> str:
        return cls.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# SKILL 2: DYNAMIC REPORT ARCHITECT SKILL (GENUINE FIRST-PRINCIPLES PLANNING)
# ---------------------------------------------------------------------------
class DynamicReportArchitectSkill:
    """Skill 2: Autonomously designs a 100% company-specific annual report structure from first principles.
    Strictly forbids hardcoded templates or predefined chapter lists."""

    NAME = "Dynamic Report Architect Skill (Zero Predefined Templates)"
    OBJECTIVE = (
        "Autonomously synthesize the complete, publication-grade structure of an annual report tailored specifically "
        "to the company's business model, real operating divisions, regulatory framework, and scale. "
        "Never impose any fixed chapter template, predefined industry chapter list, or rigid category outline."
    )

    SYSTEM_PROMPT = f"""SKILL: {NAME}
OBJECTIVE: {OBJECTIVE}

INVOCATION & APPLICABILITY:
Loaded after company intelligence is confirmed and reference source documents are loaded into the knowledge base.

DECISION RULES & OPERATIONAL WORKFLOW:
1. STRICT ZERO-TEMPLATE PRINCIPLE:
   - NEVER use a predefined chapter list or force a standard template.
   - Every company publishes an annual report uniquely structured according to its own business model, jurisdiction, and industry.
   - For example:
     * A US-listed registrant follows the SEC Form 10-K structure (Item 1 Business, 1A Risk Factors, 1C Cybersecurity, 7 MD&A, 8 Financial Statements).
     * An Indian commercial bank structures chapters around RBI banking norms, advances/deposits, asset quality, Basel III capital adequacy, and Form A/B financial statements.
     * A global manufacturing or automotive firm structures chapters around passenger/commercial vehicles, electric mobility, supply chain, and manufacturing plants.
     * A pure IT services firm structures chapters around digital technology offerings, client industry verticals, offshore delivery engines, and human capital attrition.
     * A pharmaceutical firm structures chapters around therapeutic divisions, drug formulations, R&D pipeline, clinical trials, and regulatory compliance (e.g. USFDA).
2. FIRST-PRINCIPLES STRUCTURAL SYNTHESIS:
   - IF A REAL REFERENCE TABLE OF CONTENTS IS DISCOVERED: Use the company's own published Table of Contents as the primary blueprint. Preserve its exact terminology, sequence, and hierarchy.
   - IF NO REFERENCE TOC IS DISCOVERED: Construct the chapters organically by synthesizing:
     (a) The company's real operating business segments and operating model.
     (b) The mandatory statutory reporting and governance framework of its home jurisdiction.
     (c) The primary audited financial statements and accounting schedules prescribed for this company type.
3. DYNAMIC PAGE BUDGETING:
   - Page count MUST NOT be an arbitrary fixed number.
   - Derive the target page count dynamically from the company's operational scale, complexity, and reference filings:
     * Mid-sized or focused companies: ~50-100 pages.
     * Large diversified multinationals or financial institutions: ~150-350+ pages.
   - Distribute page and word budgets across chapters proportionally according to their material significance.
4. CRITIQUE, PRUNING & REFINEMENT PROTOCOL:
   - Evaluate the synthesized structure with an autonomous critic pass:
     * PRUNE irrelevant chapters (e.g., no inventory/plant chapters for digital services; no banking capital adequacy for non-banks).
     * MERGE overlapping or redundant sections to eliminate duplication.
     * ENSURE statutory and financial statement completeness specific to the company's jurisdiction.
     * Split chapters recursively into atomic leaf tasks (300 to 1,200 words) with clear evidence queries and validation criteria.
5. HUMAN SUPERVISION GATE:
   - Present the dynamically generated blueprint (chapters, sections, budgets, and rationale) to the human supervisor for approval or modification BEFORE generating content."""

    @classmethod
    def get_instructions(cls) -> str:
        return cls.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# SKILL 3: EVIDENCE-GROUNDED WRITING & TABLE INTEGRITY SKILL
# ---------------------------------------------------------------------------
class EvidenceGroundedWritingSkill:
    """Skill 3: Governs factual synthesis, zero blank table cells, unit/period consistency, and isolation guards."""

    NAME = "Evidence-Grounded Writing & Table Integrity Skill"
    OBJECTIVE = (
        "Author publication-grade annual report sections grounded strictly in verified evidence, ensuring zero "
        "factual hallucination, complete tabular presentation with zero blank cells, and strict isolation from external entities."
    )

    SYSTEM_PROMPT = f"""SKILL: {NAME}
OBJECTIVE: {OBJECTIVE}

INVOCATION & APPLICABILITY:
Loaded by the task execution agent for every writing and drafting task across all chapters of the annual report.

DECISION RULES & OPERATIONAL WORKFLOW:
1. STRICT FACTUAL FIDELITY (ZERO FABRICATION):
   - Ground every financial metric, percentage, date, headcount, and operational claim strictly in the retrieved EVIDENCE chunks.
   - NEVER fabricate, extrapolate, or estimate numerical figures.
   - Where a specific detail or metric is not disclosed in the evidence, state 'Not Disclosed in Sources Reviewed' (or 'n/d') once and proceed.
   - Do NOT fill disclosure gaps with generic boilerplate or external assumptions.
2. ZERO BLANK TABLE CELLS PROTOCOL (MANDATORY REQUIREMENT):
   - Every Markdown table must be well-formed with aligned columns, clear headers, and '| :--- |' separators.
   - NEVER leave any cell empty or blank ('| |' or '|   |').
   - If a numerical value is present in the evidence, report the exact figure accompanied by unit and currency.
   - If a cell value is not present in the evidence for that specific period or segment, explicitly enter 'Not Disclosed' or 'n/d'.
   - Every column header MUST specify the reporting period (e.g., 'FY 2024-25', 'As at March 31, 2024') and unit/currency.
3. ENTITY ISOLATION GUARD:
   - The report is SOLELY about the target company and its consolidated group.
   - Never describe external competitors or third parties as if they were part of the reporting company.
   - External entities (statutory auditors, regulators, key technology partners, credit rating agencies) may be mentioned ONLY when explicitly named in the evidence in an external capacity.
4. METRIC & UNIT INTEGRITY:
   - Always state currency and scale (e.g. ₹ Crore, US$ Millions, € Millions) for every figure reported in narrative and tables.
5. RECOVERY & VALIDATION PROTOCOL:
   - If evidence retrieved for a section is thin, execute targeted live queries or clearly document the disclosure gap.
   - If a draft is rejected by validation (e.g. for empty table cells or ungrounded claims), revise the section immediately by eliminating the offending content and adding explicit disclosure notices."""

    @classmethod
    def get_instructions(cls) -> str:
        return cls.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# REVIEWER SKILL (Used by Validator)
# ---------------------------------------------------------------------------
REVIEW_GUIDANCE = (
    "VALIDATION & AUDIT RULES:\n"
    "1. ZERO BLANK CELLS: Reject any output containing Markdown tables with empty cells ('| |' or '|   |'). Every cell must contain valid data or 'Not Disclosed'.\n"
    "2. NUMERIC GROUNDING: Reject any specific financial figure, percentage, or date that contradicts or is missing from the evidence.\n"
    "3. ENTITY ISOLATION: Reject any text describing external companies, competitors, or third parties as part of the reporting company.\n"
    "4. DISCLOSURE COMPLIANCE: An honest 'Not Disclosed in Sources Reviewed' satisfies an ungrounded requirement and must NOT be failed.\n"
    "5. ACTIONABLE REVISION: Return precise, corrective instructions pointing out exact discrepancies for single-pass remediation."
)


def get_style_guidance(kind: str) -> str:
    k = (kind or "narrative").lower().strip()
    if k in STYLE_GUIDES:
        return STYLE_GUIDES[k]
    for name, text in STYLE_GUIDES.items():
        if name in k or k in name:
            return text
    return STYLE_GUIDES["narrative"]
