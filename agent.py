"""ONE Autonomous Deep Agent for Annual Report Generation.

Builds a single main DeepAgent using LangChain `deepagents.create_deep_agent`.
The agent autonomously decides what to research, what chapters to design, how deep to go,
and when to ask a human. Output length scales truthfully with available evidence.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from langchain_core.language_models.chat_models import BaseChatModel

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
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
8. DELEGATION: You have the built-in 'task' tool to call the general-purpose subagent when you wish to execute focused research or draft specific sub-tasks while keeping your main context window clean.
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
            resolved_model = os.getenv("LLM_MODEL", "google-genai:gemini-2.5-pro")
        elif os.getenv("OPENAI_API_KEY"):
            resolved_model = os.getenv("LLM_MODEL", "openai:gpt-4o")
        elif os.getenv("ANTHROPIC_API_KEY"):
            resolved_model = os.getenv("LLM_MODEL", "anthropic:claude-3-7-sonnet")
        else:
            resolved_model = "ollama:llama3.1"

    # 2. Composite Backend: skills loaded from repository root, workspace bound to run_dir
    repo_root = os.path.abspath(os.path.dirname(__file__))
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=session.run_dir),
        routes={"/skills": FilesystemBackend(root_dir=os.path.join(repo_root, "skills"))},
    )

    # 3. Create deterministic tools
    tools = create_report_tools(session)

    # 4. Skills directory source path for SkillsMiddleware
    skill_paths = ["/skills/"]

    # 5. Checkpointer for persistence and resume
    if checkpointer is None:
        db_path = os.path.join(session.run_dir, "checkpoints.sqlite")
        checkpointer = SqliteCheckpointSaver(db_path)

    # 6. Interrupt configuration for Human-in-the-Loop
    interrupt_cfg = None
    if interrupt_on_human_decisions and not session.auto_approve:
        interrupt_cfg = {"request_human_decision": True}

    # 7. Compile DeepAgent
    agent = create_deep_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        skills=skill_paths,
        backend=backend,
        checkpointer=checkpointer,
        interrupt_on=interrupt_cfg,
        name="AnnualReportDeepAgent",
    )
    return agent
