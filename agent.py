"""ONE Autonomous Deep Agent for Annual Report Generation.

Builds a single main DeepAgent using LangChain `deepagents.create_deep_agent`.
The agent autonomously decides what to research, what chapters to design, how deep to go,
and when to ask a human. Output length scales truthfully with available evidence.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.middleware.subagents import SubAgent
from tools import ReportSession, create_report_tools
from tools.checkpointer import SqliteCheckpointSaver


SYSTEM_PROMPT = """You are ONE autonomous corporate reporting deep agent capable of producing a complete, rigorous, evidence-grounded annual report for ANY company in ANY jurisdiction.

GOAL:
Produce an authentic, multi-format (Markdown, DOCX, PDF) annual report for the requested company and reporting period. Output length must follow the company and the evidence found (a small startup or unlisted entity will be concise; an established listed enterprise may span 100 to 300+ pages). Never pad with boilerplate. Never invent figures. Never duplicate paragraphs across sections.

NON-NEGOTIABLE OPERATING RULES:
1. PLAN FIRST: Begin by formulating your todo list and report architecture plan. Save your plan to 'plan.json' or 'todo.json' on disk using the filesystem tools.
2. DISCOVER & CONFIRM: Resolve the company's identity and governing jurisdiction using 'resolve_company_identity'. Identify official filing sources with 'find_source_documents'. For unfamiliar sectors or new startups (e.g., banking, manufacturing, software, pharma), call 'research_industry_peers' to adopt standard peer chapters, sector KPIs, and risk disclosures. Before ingesting any web domain, request human confirmation using 'request_human_decision'.
3. CALIBRATE CAPACITY: Before finalizing your outline, call 'get_evidence_capacity'. Scale report depth and chapter count directly to authentic evidence words in storage.
4. CODE OWNS FACTS: Always build the canonical facts ledger ('build_facts_ledger') and pull numbers from 'get_canonical_facts'. Use pre-built statement tables from 'get_statement_table'. Never write financial metrics from memory.
5. GROUNDED WRITING: Write each section strictly from retrieved evidence chunks ('search_evidence'). State each fact once. Save every drafted section as an individual Markdown file in the 'drafts/' directory on disk.
6. VERIFY & PROTECT: Audit every draft with 'verify_financials', 'check_entities', and 'check_section'. Call 'check_regulatory_coverage' to ensure statutory and industry requirements are fulfilled. Ensure no data or names from other companies or competitors leak in. Fix all issues before marking a section ready.
7. ASSEMBLE: Generate visualization charts with 'make_charts' (only if >= 2 real data points exist) and compile final reports with 'assemble_report'.
8. DELEGATION & SUBAGENTS: Use the built-in 'task' tool to delegate focused work to your specialized subagents:
   - 'research_analyst': For corporate identity resolution, regulatory filing discovery, and peer industry benchmarking.
   - 'section_writer': For drafting individual chapters to disk ('drafts/<slug>.md') in a clean, isolated context.
   - 'compliance_auditor': For independent verification of numeric facts, coverage audits, and competitor leakage checks.
"""


def build_annual_report_agent(
    session: ReportSession,
    model: Optional[str | BaseChatModel] = None,
    interrupt_on_human_decisions: bool = True,
    checkpointer: Optional[Any] = None,
):
    """Build and return the compiled DeepAgent graph for the given report session."""
    # 1. Resolve model
    resolved_model = model
    if resolved_model is None:
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            resolved_model = os.getenv("LLM_MODEL") or os.getenv("GEMINI_MODEL") or "google_genai:gemini-3.8-flash"
        elif os.getenv("OPENAI_API_KEY"):
            resolved_model = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "openai:gpt-4o"
        elif os.getenv("ANTHROPIC_API_KEY"):
            resolved_model = os.getenv("LLM_MODEL") or os.getenv("ANTHROPIC_MODEL") or "anthropic:claude-3-7-sonnet"
        else:
            resolved_model = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL") or "ollama:llama3.1"

    # Normalize whitespace, case, and provider prefixes
    if isinstance(resolved_model, str):
        resolved_model = re.sub(r"\s+", "", resolved_model).lower()
        if resolved_model.startswith("google-genai:"):
            resolved_model = "google_genai:" + resolved_model[len("google-genai:"):]
        elif resolved_model.startswith("gemini-"):
            resolved_model = "google_genai:" + resolved_model

        # Canonicalize to available Google Gemini API model names
        if resolved_model.startswith("google_genai:"):
            model_id = resolved_model.split(":", 1)[1]
            if model_id in ("gemini-2.0-flash", "gemini-2.5-pro", "gemini-3.5-flash-lite", "gemini-flash-lite-latest"):
                model_id = "gemini-3.8-flash"
            resolved_model = f"google_genai:{model_id}"

    # Synchronize Gemini / Google API keys across environment variables
    if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
    elif os.getenv("GOOGLE_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]

    # 1b. Instantiate chat model with automatic retry for transient 503 / 429 server spikes
    if isinstance(resolved_model, str):
        try:
            from langchain.chat_models import init_chat_model
            resolved_model = init_chat_model(resolved_model, max_retries=8)
        except Exception:
            pass

    # 2. Composite Backend: skills loaded from repository root, workspace bound to run_dir
    repo_root = os.path.abspath(os.path.dirname(__file__))
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=session.run_dir),
        routes={"/skills": FilesystemBackend(root_dir=os.path.join(repo_root, "skills"))},
    )

    # 3. Create deterministic tools
    all_tools = create_report_tools(session)
    tool_map = {t.name: t for t in all_tools}

    # 4. Declarative specialized subagents for isolated context execution
    research_tools = [tool_map[n] for n in [
        "resolve_company_identity", "find_source_documents", "research_industry_peers", "search_evidence", "get_evidence_capacity"
    ] if n in tool_map]

    writer_tools = [tool_map[n] for n in [
        "search_evidence", "get_canonical_facts", "get_statement_table"
    ] if n in tool_map]

    auditor_tools = [tool_map[n] for n in [
        "check_regulatory_coverage", "check_entities", "check_section", "verify_financials", "get_canonical_facts"
    ] if n in tool_map]

    subagents = [
        SubAgent(
            name="research_analyst",
            description="Autonomous research specialist. Researches corporate identity, peer industry benchmarks, regulatory filing sources, and queries evidence capacity.",
            system_prompt=(
                "You are an expert corporate research analyst. Your job is to resolve corporate identity, "
                "discover official regulatory filings, benchmark sector peers, and search evidence. "
                "Never fabricate facts. Report all findings clearly and objectively."
            ),
            tools=research_tools,
        ),
        SubAgent(
            name="section_writer",
            description="Dedicated chapter drafting specialist. Writes a detailed, rigorous report section to disk in 'drafts/' based strictly on authentic evidence chunks.",
            system_prompt=(
                "You are an elite corporate annual report author. Draft comprehensive, professional sections "
                "grounded strictly in the provided evidence chunks. "
                "Rule 1: Every number must match cited chunk digits. "
                "Rule 2: Never pad with generic boilerplate or repeat paragraphs. "
                "Rule 3: Save your draft to 'drafts/<section_slug>.md'."
            ),
            tools=writer_tools,
        ),
        SubAgent(
            name="compliance_auditor",
            description="Independent regulatory and financial auditor. Verifies numeric facts against canonical ledger, audits regulatory coverage, and ensures zero competitor data leakage.",
            system_prompt=(
                "You are a meticulous compliance and financial auditor. Audit drafted sections for: "
                "1. Numeric discrepancies against canonical facts ledger ('verify_financials'). "
                "2. Regulatory coverage of mandatory statutory and peer chapters ('check_regulatory_coverage'). "
                "3. Text duplication, blank cells ('check_section') and foreign entity leakage ('check_entities'). "
                "Provide a clear, actionable audit report."
            ),
            tools=auditor_tools,
        ),
    ]

    # 5. Skills directory source path for SkillsMiddleware
    skill_paths = ["/skills/"]

    # 6. Checkpointer for persistence and resume
    if checkpointer is None:
        db_path = os.path.join(session.run_dir, "checkpoints.sqlite")
        checkpointer = SqliteCheckpointSaver(db_path)

    # 7. Interrupt configuration for Human-in-the-Loop
    interrupt_cfg = None
    if interrupt_on_human_decisions and not session.auto_approve:
        interrupt_cfg = {"request_human_decision": True}

    # 8. Compile DeepAgent with full subagent delegation support
    agent = create_deep_agent(
        model=resolved_model,
        tools=all_tools,
        subagents=subagents,
        system_prompt=SYSTEM_PROMPT,
        skills=skill_paths,
        backend=backend,
        checkpointer=checkpointer,
        interrupt_on=interrupt_cfg,
        name="AnnualReportDeepAgent",
    )
    return agent
