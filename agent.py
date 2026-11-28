"""The ONE annual-report agent (a DeepAgent).

ONE main agent plans the whole job itself. It uses tools (tools/), loads skills (skills/) when it needs them and
delegates focused work to sub-agents through the built-in `task` tool. The main agent and every sub-agent use the
SAME Gemini model (config.get_llm) and therefore the same rate limiter / quota.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.middleware.subagents import SubAgent

from config import get_llm
from tools import ReportSession, create_report_tools
from tools.checkpointer import SqliteCheckpointSaver

# Kept concise and directive: re-sent on each graph step. Detailed guidelines live in skills/*/SKILL.md.
SYSTEM_PROMPT = """You are an autonomous corporate annual-report deep agent. Produce a professional, evidence-grounded
annual report for the company, period, scope, and jurisdiction in the session. You plan and execute the complete
work: structure, research, section drafting, audit checks, and final assembly. Nothing is hardcoded.

How you work:
1. GATHER EVIDENCE:
   - If documents are already indexed (check get_evidence_capacity), use search_evidence and build_facts_ledger.
   - For a public/existing company with remote sources, discover via find_source_documents / search_company_sources.
     Ask approval (request_human_decision) before remote ingestion.
   - For a new, private, or inaugural company without prior reports: use create_cold_start_dossier to index
     user-supplied information, and use request_management_input to ask missing details (profile, operations,
     finances, governance, risks). Label all answers as [Management-Provided].
2. PLAN THE REPORT ARC (Adapt to the company's industry and scale — NEVER force identical chapters):
   - Call research_industry_peers(sector) to get industry-specific chapter recommendations.
   - For a BANK / FINANCIAL INSTITUTION:
     * NEVER use generic manufacturing chapters (factories, raw materials, inventory, COGS).
     * Use banking-specific chapters & metrics:
       - 01_banking_franchise_overview: Network, branches, ATMs, customer base, digital banking & payments.
       - 02_strategic_review: Leadership message, macroeconomic & monetary policy environment.
       - 03_lending_and_deposits: Retail vs Corporate advances, CASA deposits, deposit franchise.
       - 04_credit_risk_and_asset_quality: Gross NPA, Net NPA, Provision Coverage Ratio (PCR), slippages, recoveries.
       - 05_capital_adequacy_basel_iii: Capital Adequacy Ratio (CAR/CRAR), Tier 1, CET1, leverage ratio.
       - 06_treasury_and_net_interest_margin: NII, Net Interest Margin (NIM), investment portfolio, forex.
       - 07_liquidity_and_alm: Asset-Liability Management (ALM), Liquidity Coverage Ratio (LCR), NSFR.
       - 08_corporate_governance_and_compliance: Board committees (Audit, Risk, ALCO), central bank/RBI compliance.
       - 09_financial_statements: Interest Earned vs Expended, Balance Sheet (Advances/Deposits), Cash Flow.
       - 10_notes_and_statutory_disclosures: Schedule of advances, provisions, regulatory capital disclosures.
   - For NON-FINANCIAL / GENERAL CORPORATE:
     * 01_corporate_profile, 02_strategic_review, 03_operations_and_business_model, 04_corporate_governance,
       05_risk_management, 06_sustainability_and_impact, 07_mda_financial_review, 08_financial_statements,
       09_notes_to_accounts, 10_statutory_disclosures.
3. DRAFT & SAVE SECTIONS:
   - Draft each section grounded in search_evidence / get_canonical_facts / get_statement_table.
   - Save every section using save_section_draft(slug="...", content="...").
   - Never invent numbers. If a metric or topic is unevidenced, write:
     "Not disclosed in the sources reviewed / Not provided by management".
   - Validate sections with check_section and check_entities before finalizing.
4. ASSEMBLE:
   - Call make_charts(include=True) if financial data exists.
   - Call assemble_report(section_files=[...]) to compile Markdown, DOCX, and PDF.
Continue until the final report is assembled.
"""


def build_annual_report_agent(
    session: ReportSession,
    model: Optional[str] = None,
    interrupt_on_human_decisions: bool = True,
    checkpointer: Optional[Any] = None,
):
    """Build the agent. `model` is optional, e.g. 'gemini-3.8-flash'; default is GEMINI_MODEL from .env."""
    # 1. THE model: the same object is shared by the main agent, sub-agents, validator and ledger
    llm = get_llm(model)

    # 2. Files: the agent works inside the run folder; skills are read from ./skills
    repo_root = os.path.abspath(os.path.dirname(__file__))
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=session.run_dir),
        routes={"/skills": FilesystemBackend(root_dir=os.path.join(repo_root, "skills"))},
    )

    # 3. Tools, and which sub-agent may use which tool
    all_tools = create_report_tools(session)
    by_name = {t.name: t for t in all_tools}

    def pick(*names):
        return [by_name[n] for n in names if n in by_name]

    subagents = [
        SubAgent(
            name="research_analyst",
            description="Resolves company identity, discovers primary sources, indexes user information, and searches evidence.",
            system_prompt="You are a research analyst. Gather evidence about the target company and period. "
                          "For companies without public filings, use create_cold_start_dossier and request_management_input. "
                          "Search indexed evidence with search_evidence. Never fabricate facts.",
            tools=pick("resolve_company_identity", "find_source_documents", "search_company_sources",
                       "research_industry_peers", "search_evidence", "get_evidence_capacity",
                       "create_cold_start_dossier", "request_management_input", "ingest_documents"),
        ),
        SubAgent(
            name="section_writer",
            description="Drafts ONE comprehensive report section from evidence and saves it via save_section_draft. "
                        "Give it the section title, slug, and topics to cover.",
            system_prompt="You are an expert annual-report writer. Write only the requested section, deeply grounded in "
                          "search_evidence results. Use exact figures from get_canonical_facts / get_statement_table; "
                          "never invent numbers. Clearly cite source and page. Label management answers as [Management-Provided]. "
                          "Save your completed section with save_section_draft(slug, content) and run check_section.",
            tools=pick("search_evidence", "get_canonical_facts", "get_statement_table", "check_section",
                       "check_entities", "save_section_draft"),
        ),
        SubAgent(
            name="compliance_auditor",
            description="Independent reviewer: numeric consistency, other-company leakage, duplication, applicable "
                        "disclosure coverage. Give it the draft text or file path.",
            system_prompt="You are a meticulous report reviewer. Use your tools to check numbers, entity attribution, "
                          "duplication and coverage. Treat coverage flags as candidates to assess, not mandates. "
                          "Return short, actionable findings.",
            tools=pick("check_regulatory_coverage", "check_entities", "check_section", "verify_financials",
                       "get_canonical_facts"),
        ),
    ]

    # 4. Resume support + human approval
    if checkpointer is None:
        checkpointer = SqliteCheckpointSaver(os.path.join(session.run_dir, "checkpoints.sqlite"))
    interrupt_cfg = ({"request_human_decision": True}
                     if (interrupt_on_human_decisions and not session.auto_approve) else None)

    # 5. The agent
    return create_deep_agent(
        model=llm,
        tools=all_tools,
        subagents=subagents,
        system_prompt=SYSTEM_PROMPT,
        skills=["/skills/"],
        backend=backend,
        checkpointer=checkpointer,
        interrupt_on=interrupt_cfg,
        name="AnnualReportAgent",
    )
