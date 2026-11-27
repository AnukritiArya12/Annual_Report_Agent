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
1. DO NOT ASK FOR INPUTS: The target company, financial year, reporting scope, and country/jurisdiction are ALREADY provided in the user prompt. NEVER ask the user in plain text for company name, ticker, period, or jurisdiction. Immediately begin calling tools and continue until 'assemble_report' completes.
2. EXACT TOOL NAMES: Always use exact tool names ('write_file', 'read_file', 'edit_file', 'ls', 'task', 'resolve_company_identity', 'find_source_documents', 'ingest_documents', 'build_facts_ledger', 'get_evidence_capacity', 'search_evidence', 'get_canonical_facts', 'get_statement_table', 'verify_financials', 'check_entities', 'check_section', 'check_regulatory_coverage', 'make_charts', 'assemble_report', 'request_human_decision'). NEVER prefix tool names with 'fs.' (use 'write_file', NOT 'fs.write_file').
2a. WRITE_FILE CONTENT MUST BE A STRING: When calling 'write_file', the 'content' argument MUST always be a plain Python string — never a dict, list, or any other object. If you want to save structured data (e.g. a plan dict), first serialize it: import json; content=json.dumps(data, indent=2). Pass the resulting string as 'content'. Example: write_file(path='plan.json', content=json.dumps({"steps": [...]}, indent=2)). Passing a dict directly will raise a validation error and halt the pipeline.
3. PLAN FIRST: Begin by formulating your todo list and report architecture plan. Save your plan to 'plan.json' on disk using 'write_file'. The content MUST be a JSON string (use json.dumps), NOT a dict object.
4. DISCOVER & CONFIRM: Resolve the company's identity and governing jurisdiction using 'resolve_company_identity'. Identify official filing sources with 'find_source_documents'. For unfamiliar sectors or new startups (e.g., banking, manufacturing, software, pharma), call 'research_industry_peers' to adopt standard peer chapters, sector KPIs, and risk disclosures. Before ingesting any web domain, request human confirmation using 'request_human_decision'.
5. CALIBRATE CAPACITY: Before finalizing your outline, call 'get_evidence_capacity'. Scale report depth and chapter count directly to authentic evidence words in storage.
6. CODE OWNS FACTS: Always build the canonical facts ledger ('build_facts_ledger') and pull numbers from 'get_canonical_facts'. Use pre-built statement tables from 'get_statement_table'. Never write financial metrics from memory.
7. GROUNDED WRITING: Write each section strictly from retrieved evidence chunks ('search_evidence'). State each fact once. Save every drafted section as an individual Markdown file in the 'drafts/' directory on disk using 'write_file'.
8. VERIFY & PROTECT: Audit every draft with 'verify_financials', 'check_entities', and 'check_section'. Call 'check_regulatory_coverage' to ensure statutory and industry requirements are fulfilled. Ensure no data or names from other companies or competitors leak in. Fix all issues before marking a section ready.
9. ASSEMBLE: Generate visualization charts with 'make_charts' (only if >= 2 real data points exist) and compile final reports with 'assemble_report'.
10. DELEGATION & SUBAGENTS: Use the built-in 'task' tool to delegate focused work to your specialized subagents:
   - 'research_analyst': For corporate identity resolution, regulatory filing discovery, and peer industry benchmarking.
   - 'section_writer': For drafting individual chapters to disk ('drafts/<slug>.md') in a clean, isolated context.
   - 'compliance_auditor': For independent verification of numeric facts, coverage audits, and competitor leakage checks.
11. FULL AUTONOMOUS EXECUTION: Never stop calling tools until 'assemble_report' has compiled the final report files. After resolving company identity, immediately call 'find_source_documents', then 'ingest_documents', 'build_facts_ledger', draft chapters to 'drafts/', verify them, and call 'assemble_report'.
"""


def build_annual_report_agent(
    session: ReportSession,
    model: Optional[str | BaseChatModel] = None,
    interrupt_on_human_decisions: bool = True,
    checkpointer: Optional[Any] = None,
):
    """Build and return the compiled DeepAgent graph for the given report session."""
    # 1. Resolve model (Ollama and Gemini only)
    resolved_model = model
    if resolved_model is None:
        raw_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        if "gemini" in raw_provider or "google" in raw_provider:
            provider = "gemini"
        elif "ollama" in raw_provider or "local" in raw_provider or "llama" in raw_provider:
            provider = "ollama"
        elif not raw_provider:
            provider = ""
        else:
            provider = raw_provider

        model_override = os.getenv("LLM_MODEL", "").strip()
        if model_override and ":" in model_override:
            resolved_model = model_override
        elif provider == "gemini":
            has_gemini_key = bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())
            if not has_gemini_key:
                raise ValueError("GEMINI_API_KEY is missing in .env. Please paste your API key in .env at: GEMINI_API_KEY=<your_key>")
            resolved_model = f"google_genai:{model_override or os.getenv('GEMINI_MODEL') or 'gemini-2.5-flash-lite'}"
        elif provider in ("ollama", ""):
            resolved_model = f"ollama:{model_override or os.getenv('OLLAMA_MODEL') or 'llama3.1'}"
        else:
            raise ValueError(f"Unsupported LLM_PROVIDER '{raw_provider}'. Only 'ollama' and 'gemini' are supported.")

    # Normalize whitespace, case, and provider prefixes
    if isinstance(resolved_model, str):
        resolved_model = re.sub(r"\s+", "", resolved_model).lower()
        if resolved_model in ("gemini", "google", "google_genai", "google-genai"):
            raise ValueError(
                "The model name is only a provider label. For Ollama, set OLLAMA_MODEL="
                "to an installed model (for example gpt-oss:20b) and remove LLM_MODEL, "
                "or pass --model ollama:<model-name>. For Gemini, use "
                "--model google_genai:<model-name>."
            )
        if resolved_model.startswith("google-genai:"):
            resolved_model = "google_genai:" + resolved_model[len("google-genai:"):]
        elif resolved_model.startswith("gemini-"):
            resolved_model = "google_genai:" + resolved_model
        elif resolved_model.startswith(("openai:", "anthropic:")):
            raise ValueError(f"Unsupported model provider in '{resolved_model}'. Only Ollama and Gemini are supported.")

        # Canonicalize deprecated or unavailable model aliases to official free-tier models
        if resolved_model.startswith("google_genai:"):
            model_id = resolved_model.split(":", 1)[1]
            # Remove any old/broken alias redirects — let .env GEMINI_MODEL drive the choice
            resolved_model = f"google_genai:{model_id}"

    # Synchronize Gemini / Google API keys across environment variables
    if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
    elif os.getenv("GOOGLE_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]

    # 1b. Instantiate chat model with provider-specific reliability settings
    if isinstance(resolved_model, str):
        if resolved_model.startswith("ollama:"):
            from langchain.chat_models import init_chat_model
            ollama_ctx = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
            ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            resolved_model = init_chat_model(
                resolved_model,
                num_ctx=ollama_ctx,
                base_url=ollama_url,
                temperature=0.2,
            )
        else:
            # Gemini: use resilient multi-model failover with api_version='v1beta'
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.outputs import ChatResult, ChatGeneration
            from typing import Sequence

            class ResilientGeminiChatModel(BaseChatModel):
                models: List[Any]

                @property
                def _llm_type(self) -> str:
                    return "resilient_gemini"

                def _generate(self, messages: List[Any], stop: Optional[List[str]] = None, **kwargs: Any) -> ChatResult:
                    last_exc = None
                    for m in self.models:
                        try:
                            if hasattr(m, "invoke"):
                                msg = m.invoke(messages, stop=stop, **kwargs)
                                return ChatResult(generations=[ChatGeneration(message=msg)])
                            return m._generate(messages, stop=stop, **kwargs)
                        except Exception as e:
                            err_msg = str(e)
                            if any(x in err_msg for x in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "404", "NOT_FOUND")):
                                print(f"[*] [Quota Guard] Model hit quota/temporary spike. Smoothly rolling over to next free-tier model...", flush=True)
                                last_exc = e
                                continue
                            raise
                    raise last_exc or RuntimeError("All Gemini candidate models failed.")

                def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ResilientGeminiChatModel":
                    bound = [m.bind_tools(tools, **kwargs) for m in self.models]
                    return ResilientGeminiChatModel(models=bound)

            _GEMINI_POOL = [
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-3.6-flash",
            ]
            primary_id = resolved_model.split(":", 1)[1] if ":" in resolved_model else resolved_model
            model_order = [primary_id] + [m for m in _GEMINI_POOL if m != primary_id]

            instances = []
            for mid in model_order:
                try:
                    c = ChatGoogleGenerativeAI(
                        model=mid,
                        api_version="v1beta",
                        temperature=0.2,
                        max_retries=1,
                    )
                    instances.append(c)
                except Exception:
                    pass

            resolved_model = ResilientGeminiChatModel(models=instances)

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
