"""
Annual Report Deep Agent — Streamlit Web UI

Runs the autonomous annual-report agent inside a Streamlit app.
Supports:
  • Existing or new companies (no prior records needed)
  • Local file upload (PDF, DOCX, TXT, Markdown)
  • Human-in-the-loop prompts for missing information
  • Live streaming of agent thoughts/tool calls
  • Downloadable DOCX + PDF final reports
  • Resume from checkpoint
"""

from __future__ import annotations

import os
import sys
import json
import queue
import shutil
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Optional

# --- load .env before anything else ----------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

import streamlit as st

# ─── Page config (must be first Streamlit call) ─────────────────────────────
st.set_page_config(
    page_title="Annual Report Deep Agent",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Styling ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Main header */
.main-header {
    background: linear-gradient(135deg, #1A365D 0%, #2D5A8E 100%);
    color: white;
    padding: 1.5rem 2rem;
    border-radius: 12px;
    margin-bottom: 1.5rem;
    box-shadow: 0 4px 16px rgba(26,54,93,0.18);
}
.main-header h1 { margin:0; font-size:1.9rem; font-weight:700; }
.main-header p  { margin:0.4rem 0 0; opacity:0.85; font-size:0.95rem; }

/* Status boxes */
.status-info    { background:#E8F4FD; border-left:4px solid #2196F3; padding:.7rem 1rem; border-radius:6px; margin:.5rem 0; }
.status-success { background:#E8F5E9; border-left:4px solid #4CAF50; padding:.7rem 1rem; border-radius:6px; margin:.5rem 0; }
.status-warning { background:#FFF8E1; border-left:4px solid #FFC107; padding:.7rem 1rem; border-radius:6px; margin:.5rem 0; }
.status-error   { background:#FFEBEE; border-left:4px solid #F44336; padding:.7rem 1rem; border-radius:6px; margin:.5rem 0; }

/* Log area */
.log-container {
    background:#0D1117; color:#C9D1D9; font-family:monospace;
    font-size:0.82rem; padding:1rem; border-radius:8px;
    max-height:420px; overflow-y:auto;
    border:1px solid #30363D;
}
/* Tool call pill */
.tool-pill {
    display:inline-block; background:#1F2937; color:#9CA3AF;
    border:1px solid #374151; border-radius:14px;
    padding:2px 10px; font-size:0.78rem; margin:2px 0;
}
/* Section tags */
.mgmt-label {
    background:#FFF3CD; color:#856404; border:1px solid #FFECB5;
    border-radius:4px; padding:1px 6px; font-size:0.78rem;
    font-weight:600; margin-left:4px;
}
</style>
""", unsafe_allow_html=True)

# ─── Header ───────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
  <h1>📋 Annual Report Deep Agent</h1>
  <p>Evidence-based annual reports for any company — existing or new — from local documents or user-provided information.</p>
</div>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════
# Session state bootstrap
# ═══════════════════════════════════════════════════════════════════════════
def _init_state():
    defaults = {
        "agent_running": False,
        "agent_done": False,
        "agent_error": None,
        "log_lines": [],
        "event_queue": queue.Queue(),
        "hitl_question": None,         # pending human input request
        "hitl_answer_event": threading.Event(),
        "hitl_answer": None,
        "run_dir": None,
        "company": "",
        "fy": "FY 2024",
        "country": "",
        "scope": "consolidated",
        "auto_approve": False,
        "resume_run": False,
        "output_files": {},
        "agent_thread": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════
def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "run"

def _post(kind: str, msg: str):
    """Post an event to the UI queue."""
    st.session_state.event_queue.put({"kind": kind, "msg": msg})

def _add_log(line: str):
    st.session_state.log_lines.append(line)
    if len(st.session_state.log_lines) > 800:
        st.session_state.log_lines = st.session_state.log_lines[-600:]

# ═══════════════════════════════════════════════════════════════════════════
# Agent runner (background thread)
# ═══════════════════════════════════════════════════════════════════════════
def _run_agent(
    company: str,
    fy: str,
    country: str,
    scope: str,
    api_key: str,
    model_name: str,
    run_dir: str,
    uploaded_paths: list[str],
    peer_path: Optional[str],
    auto_approve: bool,
    resume: bool,
    industry: str = "",
    is_first_year: bool = False,
):
    """Background thread: builds + runs the DeepAgent, posts events to queue."""
    try:
        # Inject API key into environment
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
            os.environ["GOOGLE_API_KEY"] = api_key

        # Import after env is set
        sys.path.insert(0, str(Path(__file__).parent))
        from config import settings as cfg, _llm  # noqa: F401 — touch _llm reset
        # Reset cached LLM so new key/model is used
        import config as _cfg_mod
        _cfg_mod._llm_cache = {}
        if api_key:
            _cfg_mod.settings.GEMINI_API_KEY = api_key
        if model_name:
            _cfg_mod.settings.GEMINI_MODEL = model_name

        from tools import ReportSession
        from tools.discovery_tools import build_discovery_tools
        from agent import build_annual_report_agent
        from utils import slug

        _post("info", f"🚀 Initialising session for **{company}** — {fy}")
        os.makedirs(run_dir, exist_ok=True)

        session = ReportSession(
            run_dir=run_dir,
            company_name=company,
            financial_year=fy,
            country=country,
            reporting_scope=scope,
            industry=industry,
            is_first_year=is_first_year,
            auto_approve=auto_approve,
        )

        # Ingest uploaded documents
        if uploaded_paths:
            disc_tools = {t.name: t for t in build_discovery_tools(session)}
            for p in uploaded_paths:
                _post("tool", f"📄 Ingesting: `{os.path.basename(p)}`")
                try:
                    result = disc_tools["ingest_documents"].invoke({"urls_or_paths": [p]})
                    res_obj = json.loads(result)
                    ing = res_obj.get("ingested", [])
                    rej = res_obj.get("rejected", [])
                    if ing:
                        _post("success", f"✅ Ingested `{os.path.basename(p)}` — {res_obj.get('total_chunks_in_storage', '?')} chunks")
                    if rej:
                        for r in rej:
                            _post("warning", f"⚠️ Skipped `{os.path.basename(p)}`: {r.get('reason', '')}")
                except Exception as exc:
                    _post("warning", f"⚠️ Could not ingest `{os.path.basename(p)}`: {exc}")

        # Peer report outline
        peer_outline = ""
        if peer_path and os.path.exists(peer_path):
            _post("info", f"📐 Reading peer-report structure from `{os.path.basename(peer_path)}`")
            try:
                from knowledge import parse_pdf, parse_docx
                ext = os.path.splitext(peer_path)[1].lower()
                if ext == ".pdf":
                    pages, toc, _ = parse_pdf(peer_path)
                    peer_outline = "\n".join(
                        "#" * max(1, min(l + 1, 6)) + " " + t for l, t, _ in toc
                    ) or "\n".join(text[:300] for _, text in pages[:2])
                elif ext in (".docx", ".doc"):
                    pages, toc, _ = parse_docx(peer_path)
                    peer_outline = "\n".join(
                        "#" * max(1, min(l + 1, 6)) + " " + t for l, t, _ in toc
                    ) or "\n".join(text[:300] for _, text in pages[:2])
                elif ext in (".txt", ".md"):
                    with open(peer_path, encoding="utf-8", errors="replace") as f:
                        peer_outline = "\n".join(
                            ln for ln in f if ln.lstrip().startswith("#")
                        )[:8000]
            except Exception as exc:
                _post("warning", f"⚠️ Could not parse peer report: {exc}")
            session.peer_report_outline = peer_outline[:8000]

        # Build agent
        _post("info", "🔧 Building DeepAgent graph…")
        agent = build_annual_report_agent(
            session=session,
            model=model_name or None,
            interrupt_on_human_decisions=not auto_approve,
        )
        co_slug = slug(company)
        fy_slug = slug(fy)
        thread_id = f"{co_slug}_{fy_slug}"
        config = {"configurable": {"thread_id": thread_id}}

        initial_prompt = (
            f"Target Company: {company}\n"
            f"Financial Year: {fy}\n"
            f"Reporting Scope: {scope}\n"
            f"Country/Jurisdiction: {country or 'Infer via resolve_company_identity'}\n\n"
            f"Optional structure-only reference outline (headings only):\n"
            f"{session.peer_report_outline or 'not supplied'}\n\n"
            "Create a complete annual report for this company and period. "
            "Use the standard corporate report arc — company overview, strategy, operations, "
            "governance, risks, financial performance, and applicable disclosures. "
            "Adapt chapter names, order, and depth to the company's industry and reporting framework; "
            "do not force irrelevant sections. "
            "Research and use evidence from any available sources: local files, user-provided "
            "company information, or public sources. "
            "If the company has no prior annual report or public filing, help build its first "
            "report from available documents and information, clearly marking all unknowns and "
            "asking the user (via request_management_input) for missing material inputs. "
            "Label all management-provided answers as MANAGEMENT-PROVIDED and "
            "identify missing information rather than inventing facts. "
            "Cover the full relevant annual-report structure with depth appropriate to company "
            "scale and available material. Keep the final document at or below 500 pages. "
            "Use DeepAgents tools, relevant skills, and focused subagents as appropriate. "
            "Validate material claims and assemble the final Markdown, DOCX, and PDF outputs."
        )

        inputs = None if resume else {"messages": [{"role": "user", "content": initial_prompt}]}
        nudges = 0
        MAX_NUDGES = 3
        seen_msg_ids: set = set()

        _post("info", "▶️ Starting agent execution loop…")

        while True:
            interrupted = False
            try:
                for event in agent.stream(inputs, config=config, stream_mode="values"):
                    msgs = event.get("messages", [])
                    if not msgs:
                        continue
                    last = msgs[-1]
                    mid = getattr(last, "id", None) or str(id(last))
                    if mid in seen_msg_ids:
                        continue
                    seen_msg_ids.add(mid)

                    # Tool calls
                    tcs = getattr(last, "tool_calls", None) or []
                    for tc in tcs:
                        tc_name = tc.get("name", "tool")
                        _post("tool", f"🔧 Calling `{tc_name}`…")

                    # Tool results
                    if getattr(last, "type", "") == "tool":
                        t_name = getattr(last, "name", "tool")
                        _post("tool_done", f"✅ `{t_name}` done")

                    # AI text
                    if getattr(last, "type", "") == "ai":
                        txt = getattr(last, "content", "")
                        if txt and isinstance(txt, str) and not tcs:
                            _post("agent", txt[:600] + ("…" if len(txt) > 600 else ""))

            except Exception as stream_exc:
                _post("error", f"❌ Stream error: {stream_exc}")
                raise

            # Check for interrupt
            state = agent.get_state(config)
            if state and getattr(state, "tasks", None):
                for task in state.tasks:
                    for intr in (getattr(task, "interrupts", None) or []):
                        interrupted = True
                        val = getattr(intr, "value", intr)

                        # Management input interrupt
                        if isinstance(val, dict) and val.get("kind") == "management_input":
                            question = val.get("question", "")
                            role = val.get("respondent_role", "company representative")
                            topics = val.get("report_topics", [])
                            why = val.get("why_needed", "")
                            # Post to UI
                            _post("hitl_management", json.dumps({
                                "question": question,
                                "role": role,
                                "topics": topics,
                                "why": why,
                            }))
                            # Wait for UI answer
                            st.session_state.hitl_answer_event.wait(timeout=600)
                            st.session_state.hitl_answer_event.clear()
                            answer = st.session_state.hitl_answer or "No information provided."
                            _post("info", f"💬 Answer recorded: {answer[:120]}…" if len(answer) > 120 else f"💬 Answer: {answer}")
                            from langgraph.types import Command
                            inputs = Command(resume=answer)
                            break

                        # Human approval interrupt
                        _post("hitl_approval", json.dumps(val if isinstance(val, dict) else {"message": str(val)}))
                        if auto_approve:
                            user_choice = "approve"
                            _post("info", "🤖 Auto-approved.")
                        else:
                            st.session_state.hitl_answer_event.wait(timeout=600)
                            st.session_state.hitl_answer_event.clear()
                            user_choice = st.session_state.hitl_answer or "approve"

                        n_actions = 1
                        if isinstance(val, dict) and "action_requests" in val:
                            n_actions = max(len(val.get("action_requests", [])), 1)
                        decision_type = "approve" if user_choice in ("approve", "y", "yes", "") else "reject"
                        from langgraph.types import Command
                        inputs = Command(resume={"decisions": [{"type": decision_type}] * n_actions})
                        break
                    if interrupted:
                        break

            if not interrupted:
                # Check if final report was assembled
                base = f"{_slug(company)}_{_slug(fy)}"
                found_files = {}
                for ext in ("md", "docx", "pdf"):
                    p = os.path.join(run_dir, f"{base}.{ext}")
                    if os.path.exists(p):
                        found_files[ext] = p
                if found_files:
                    _post("done", json.dumps(found_files))
                    break

                nudges += 1
                if nudges > MAX_NUDGES:
                    _post("warning", f"⚠️ Agent stopped without assembling report after {MAX_NUDGES} nudges. Use Resume to continue.")
                    break
                _post("info", f"🔄 Nudging agent to continue (attempt {nudges}/{MAX_NUDGES})…")
                inputs = {"messages": [{"role": "user", "content": (
                    f"Continue the annual report for {company}, {fy}. "
                    "Complete remaining research, drafting, and review steps, then assemble the final report."
                )}]}

    except Exception as exc:
        tb = traceback.format_exc()
        _post("error", f"❌ Fatal error: {exc}\n\n```\n{tb[:2000]}\n```")


# ═══════════════════════════════════════════════════════════════════════════
# Sidebar — Configuration
# ═══════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚙️ Configuration")

    # API Key
    st.markdown("### 🔑 Gemini API Key")
    env_key = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    api_key_input = st.text_input(
        "API Key",
        value=env_key,
        type="password",
        placeholder="AIza… (or set GEMINI_API_KEY in .env)",
        help="Your Google Gemini API key. Leave blank to use the GEMINI_API_KEY from .env",
    )
    effective_key = api_key_input.strip() or env_key

    # Model
    st.markdown("### 🤖 Model")
    env_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    model_options = [
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.7-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash-lite",
    ]
    idx = model_options.index(env_model) if env_model in model_options else 0
    model_choice = st.selectbox(
        "Gemini Model",
        options=model_options,
        index=idx,
        help="Select the Gemini model. gemini-3.8-flash is the primary recommended model.",
    )

    st.markdown("---")
    st.markdown("### ℹ️ About")
    st.markdown("""
**Annual Report Deep Agent** creates evidence-grounded reports from:
- 📄 Uploaded PDFs, DOCX, or text files
- 💬 Information you provide interactively
- 🌐 Web sources (after your approval)

No internet needed for local-only mode.
""")

    if not effective_key:
        st.warning("⚠️ No API key found. Enter your Gemini API key above.")

# ═══════════════════════════════════════════════════════════════════════════
# Main area
# ═══════════════════════════════════════════════════════════════════════════
tab_new, tab_docs, tab_run, tab_results = st.tabs([
    "1️⃣  Company Details",
    "2️⃣  Upload Documents",
    "3️⃣  Run Agent",
    "4️⃣  Results & Download",
])

# ─── Tab 1: Company Details ───────────────────────────────────────────────
with tab_new:
    st.markdown("### Company & Report Parameters")
    st.markdown("""
    <div class="status-info">
    ℹ️ Works for <strong>any company</strong> — large or small, new or established, with or without prior records.
    If no documents exist, the agent will ask you for the information it needs.
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        company_name = st.text_input(
            "🏢 Company Name *",
            placeholder="e.g. Acme Technologies Ltd",
            help="Full legal name of the company. For a new/private company, any name works.",
        )
        country = st.text_input(
            "🌍 Country / Jurisdiction",
            placeholder="e.g. India, United States, UK (optional — auto-inferred)",
            help="Leave blank to auto-detect. Affects mandatory disclosure sections.",
        )
        industry = st.text_input(
            "🏭 Industry / Sector (optional)",
            placeholder="e.g. Information Technology, Banking, Healthcare, Manufacturing",
            help="Guides sector-specific chapters, metrics, and risk factors.",
        )
    with col2:
        fy = st.text_input(
            "📅 Financial Year *",
            value="FY 2024",
            placeholder="e.g. FY 2024, FY2023-24, Year ended March 2024",
            help="Specify the reporting period.",
        )
        scope = st.selectbox(
            "📊 Reporting Scope",
            ["consolidated", "standalone", "group", "individual"],
            help="Consolidated = group including subsidiaries; Standalone = parent entity only",
        )
        is_first_year = st.checkbox(
            "🌱 Inaugural / First-Time Annual Report",
            value=False,
            help="Check if this is the company's first annual report (avoids requiring prior-year comparative figures).",
        )

    st.markdown("### Advanced Options")
    col3, col4 = st.columns(2)
    with col3:
        auto_approve = st.checkbox(
            "🤖 Auto-approve web source requests",
            value=False,
            help="Automatically approve any domain the agent wants to access. Disable for manual review.",
        )
        resume_run = st.checkbox(
            "⏩ Resume from previous checkpoint",
            value=False,
            help="Continue a previously interrupted run for the same company and FY.",
        )
    with col4:
        custom_run_dir = st.text_input(
            "📁 Custom output folder (optional)",
            placeholder=r"e.g. C:\Reports\Acme_FY2024",
            help="Leave blank to use default: runs/<company>_<fy>/",
        )

    # Always persist form values to session state
    st.session_state.company = company_name
    st.session_state.fy = fy
    st.session_state.country = country
    st.session_state.industry = industry
    st.session_state.is_first_year = is_first_year
    st.session_state.scope = scope
    st.session_state.auto_approve = auto_approve
    st.session_state.resume_run = resume_run

    if company_name and fy:
        default_run_dir = os.path.join(
            os.path.dirname(__file__), "runs",
            f"{_slug(company_name)}_{_slug(fy)}"
        )
        st.session_state.run_dir = custom_run_dir.strip() or default_run_dir
        st.markdown(f"""
        <div class="status-success">
        ✅ <strong>Output folder:</strong> <code>{st.session_state.run_dir}</code>
        </div>
        """, unsafe_allow_html=True)

# ─── Tab 2: Upload Documents ──────────────────────────────────────────────
with tab_docs:
    st.markdown("### Upload Company Documents")
    st.markdown("""
    <div class="status-info">
    ℹ️ Upload any available documents. The agent reads them to build evidence for the report.
    <strong>Not required</strong> — if you have no documents, the agent will ask you for the information instead.
    </div>
    """, unsafe_allow_html=True)

    uploaded_files = st.file_uploader(
        "Company documents (PDF, DOCX, TXT, Markdown)",
        type=["pdf", "docx", "doc", "txt", "md"],
        accept_multiple_files=True,
        help="Annual reports, financial statements, board minutes, dossiers, any company record",
    )

    peer_file = st.file_uploader(
        "🔗 Peer / Reference annual report (structure only, optional)",
        type=["pdf", "docx", "txt", "md"],
        help="Upload a report from a comparable company. Used for section structure only — no facts are copied.",
    )

    if uploaded_files:
        st.success(f"✅ {len(uploaded_files)} file(s) queued for ingestion")
        for f in uploaded_files:
            st.markdown(f"- 📄 `{f.name}` — {f.size:,} bytes")

    if peer_file:
        st.info(f"📐 Peer reference: `{peer_file.name}` (structure guidance only)")

# ─── Tab 3: Run Agent ─────────────────────────────────────────────────────
with tab_run:
    st.markdown("### Run the Annual Report Agent")

    if not st.session_state.company or not st.session_state.fy:
        st.warning("⚠️ Please fill in Company Name and Financial Year in the **Company Details** tab first.")
    elif not effective_key:
        st.error("❌ Gemini API key is required. Add it in the sidebar or in `.env`.")
    else:
        col_btn1, col_btn2, col_btn3 = st.columns([2, 1, 1])
        with col_btn1:
            start_btn = st.button(
                "🚀 Generate Annual Report",
                disabled=st.session_state.agent_running,
                type="primary",
                use_container_width=True,
            )
        with col_btn2:
            stop_lbl = "⏹ Running…" if st.session_state.agent_running else "⏹ Stop"
            stop_btn = st.button(stop_lbl, disabled=not st.session_state.agent_running, use_container_width=True)
        with col_btn3:
            clear_btn = st.button("🗑 Clear Log", use_container_width=True)

        if clear_btn:
            st.session_state.log_lines = []
            st.rerun()

        if stop_btn and st.session_state.agent_running:
            # Mark done so the loop exits gracefully
            st.session_state.agent_running = False
            _add_log("⏹ Run stopped by user.")
            st.rerun()

        if start_btn and not st.session_state.agent_running:
            # Save uploaded files to temp dir for the agent
            tmp_dir = tempfile.mkdtemp(prefix="annual_report_uploads_")
            saved_paths = []
            for uf in (uploaded_files or []):
                dest = os.path.join(tmp_dir, uf.name)
                with open(dest, "wb") as fh:
                    fh.write(uf.getbuffer())
                saved_paths.append(dest)

            peer_path = None
            if peer_file:
                peer_dest = os.path.join(tmp_dir, peer_file.name)
                with open(peer_dest, "wb") as fh:
                    fh.write(peer_file.getbuffer())
                peer_path = peer_dest

            # Reset state
            st.session_state.agent_running = True
            st.session_state.agent_done = False
            st.session_state.agent_error = None
            st.session_state.log_lines = []
            st.session_state.output_files = {}
            st.session_state.hitl_question = None
            st.session_state.hitl_answer = None
            st.session_state.hitl_answer_event = threading.Event()
            # Clear queue
            while not st.session_state.event_queue.empty():
                try:
                    st.session_state.event_queue.get_nowait()
                except queue.Empty:
                    break

            run_dir = st.session_state.run_dir
            os.makedirs(run_dir, exist_ok=True)

            t = threading.Thread(
                target=_run_agent,
                kwargs=dict(
                    company=st.session_state.company,
                    fy=st.session_state.fy,
                    country=st.session_state.get("country", ""),
                    scope=st.session_state.get("scope", "consolidated"),
                    api_key=effective_key,
                    model_name=model_choice,
                    run_dir=run_dir,
                    uploaded_paths=saved_paths,
                    peer_path=peer_path,
                    auto_approve=st.session_state.get("auto_approve", False),
                    resume=st.session_state.get("resume_run", False),
                    industry=st.session_state.get("industry", ""),
                    is_first_year=st.session_state.get("is_first_year", False),
                ),
                daemon=True,
            )
            t.start()
            st.session_state.agent_thread = t
            st.rerun()

        # ─── Live event processing ──────────────────────────────────────
        if st.session_state.agent_running:
            # Drain the event queue into log
            try:
                while True:
                    evt = st.session_state.event_queue.get_nowait()
                    kind, msg = evt["kind"], evt["msg"]

                    if kind == "done":
                        st.session_state.agent_running = False
                        st.session_state.agent_done = True
                        try:
                            st.session_state.output_files = json.loads(msg)
                        except Exception:
                            pass
                        _add_log("🎉 Report generation complete!")

                    elif kind == "error":
                        st.session_state.agent_running = False
                        st.session_state.agent_error = msg
                        _add_log(f"❌ ERROR: {msg}")

                    elif kind == "hitl_management":
                        st.session_state.hitl_question = {"type": "management", "data": json.loads(msg)}
                        _add_log(f"❓ Agent needs information: {json.loads(msg).get('question', '')[:100]}")

                    elif kind == "hitl_approval":
                        st.session_state.hitl_question = {"type": "approval", "data": json.loads(msg)}
                        _add_log("❓ Agent needs approval")

                    else:
                        icon = {"info": "ℹ", "success": "✅", "warning": "⚠", "tool": "🔧",
                                "tool_done": "✅", "agent": "🤖"}.get(kind, "·")
                        _add_log(f"{icon} {msg}")

            except queue.Empty:
                pass

        # ─── HITL panel ─────────────────────────────────────────────────
        hitl = st.session_state.get("hitl_question")
        if hitl and st.session_state.agent_running:
            if hitl["type"] == "management":
                d = hitl["data"]
                st.markdown("---")
                st.markdown("""
                <div class="status-warning">
                <strong>❓ The agent needs information from you</strong>
                </div>
                """, unsafe_allow_html=True)
                st.markdown(f"**Respondent:** {d.get('role', 'Company representative')}")
                st.markdown(f"**Question:** {d.get('question', '')}")
                if d.get("topics"):
                    st.markdown(f"**Report topics:** {', '.join(d['topics'])}")
                if d.get("why"):
                    st.markdown(f"**Why needed:** {d['why']}")
                st.markdown(
                    "<span class='mgmt-label'>MANAGEMENT-PROVIDED</span> "
                    "Your answer will be labelled as management-provided information.",
                    unsafe_allow_html=True
                )
                ans = st.text_area(
                    "Your answer",
                    placeholder="Enter the information, or type 'unknown' if not available, or 'skip' to skip.",
                    key="hitl_text_input",
                )
                col_ans1, col_ans2 = st.columns([2, 1])
                with col_ans1:
                    if st.button("📨 Submit Answer", type="primary"):
                        st.session_state.hitl_answer = ans or "No information provided."
                        st.session_state.hitl_question = None
                        st.session_state.hitl_answer_event.set()
                        st.rerun()
                with col_ans2:
                    if st.button("⏭ Skip"):
                        st.session_state.hitl_answer = "No information was provided for this question."
                        st.session_state.hitl_question = None
                        st.session_state.hitl_answer_event.set()
                        st.rerun()

            elif hitl["type"] == "approval":
                d = hitl["data"]
                st.markdown("---")
                st.markdown("""
                <div class="status-warning">
                <strong>🔒 The agent needs your approval</strong>
                </div>
                """, unsafe_allow_html=True)
                if isinstance(d, dict):
                    for action in d.get("action_requests", [d]):
                        if isinstance(action, dict):
                            args = action.get("args", {})
                            st.markdown(f"**Action:** `{action.get('name', 'unknown')}`")
                            for key in ("question", "approve_domain"):
                                if args.get(key):
                                    st.markdown(f"**{key.replace('_', ' ').title()}:** {args[key]}")
                col_ap1, col_ap2 = st.columns(2)
                with col_ap1:
                    if st.button("✅ Approve", type="primary"):
                        st.session_state.hitl_answer = "approve"
                        st.session_state.hitl_question = None
                        st.session_state.hitl_answer_event.set()
                        st.rerun()
                with col_ap2:
                    if st.button("❌ Reject"):
                        st.session_state.hitl_answer = "reject"
                        st.session_state.hitl_question = None
                        st.session_state.hitl_answer_event.set()
                        st.rerun()

        # ─── Live log display ────────────────────────────────────────────
        st.markdown("### 📋 Agent Log")
        if st.session_state.agent_running:
            st.markdown('<div class="status-info">⚡ Agent is running…</div>', unsafe_allow_html=True)
            # Auto-refresh every 2 seconds while running
            st.markdown("""
            <meta http-equiv="refresh" content="2">
            """, unsafe_allow_html=True)

        if st.session_state.agent_done:
            st.markdown('<div class="status-success">🎉 Report generation complete! Check the Results tab.</div>', unsafe_allow_html=True)

        if st.session_state.agent_error:
            st.error(f"❌ Error: {st.session_state.agent_error[:500]}")

        log = st.session_state.log_lines
        if log:
            log_text = "\n".join(str(l) for l in log[-150:])
            st.code(log_text, language=None)
        else:
            st.markdown("*No log yet. Click **Generate Annual Report** to start.*")

# ─── Tab 4: Results ───────────────────────────────────────────────────────
with tab_results:
    st.markdown("### 📥 Download Report")
    run_dir = st.session_state.get("run_dir", "")

    if st.session_state.agent_done or (run_dir and os.path.isdir(run_dir)):
        # Scan for output files
        output_files = dict(st.session_state.output_files)
        if run_dir and os.path.isdir(run_dir):
            co = _slug(st.session_state.company)
            fy_s = _slug(st.session_state.fy)
            base = f"{co}_{fy_s}"
            for ext in ("pdf", "docx", "md"):
                p = os.path.join(run_dir, f"{base}.{ext}")
                if os.path.exists(p) and ext not in output_files:
                    output_files[ext] = p

        if output_files:
            st.markdown(f"""
            <div class="status-success">
            🎉 Report generated for <strong>{st.session_state.company}</strong> — {st.session_state.fy}
            </div>
            """, unsafe_allow_html=True)

            col_d1, col_d2, col_d3 = st.columns(3)
            for col, (ext, label, mime) in zip(
                [col_d1, col_d2, col_d3],
                [
                    ("pdf", "📕 Download PDF", "application/pdf"),
                    ("docx", "📘 Download DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                    ("md", "📄 Download Markdown", "text/markdown"),
                ],
            ):
                p = output_files.get(ext)
                with col:
                    if p and os.path.exists(p):
                        size_kb = os.path.getsize(p) // 1024
                        with open(p, "rb") as fh:
                            st.download_button(
                                label=f"{label} ({size_kb} KB)",
                                data=fh.read(),
                                file_name=os.path.basename(p),
                                mime=mime,
                                use_container_width=True,
                            )
                    else:
                        st.button(f"{label} (not generated)", disabled=True, use_container_width=True)

            # Show Markdown preview
            md_path = output_files.get("md")
            if md_path and os.path.exists(md_path):
                st.markdown("---")
                st.markdown("### 👁 Report Preview (Markdown)")
                with open(md_path, encoding="utf-8", errors="replace") as f:
                    md_content = f.read()
                # Show first 10,000 chars in preview
                preview = md_content[:10000]
                st.markdown(preview + ("\n\n*…(truncated — download full report above)*" if len(md_content) > 10000 else ""))

            # Section files
            drafts_dir = os.path.join(run_dir, "drafts")
            if os.path.isdir(drafts_dir):
                draft_files = sorted(
                    [f for f in os.listdir(drafts_dir) if f.endswith(".md")]
                )
                if draft_files:
                    st.markdown("---")
                    st.markdown(f"### 📂 Draft Sections ({len(draft_files)} files)")
                    for df in draft_files:
                        fp = os.path.join(drafts_dir, df)
                        with open(fp, encoding="utf-8", errors="replace") as fh:
                            content = fh.read()
                        wc = len(content.split())
                        with st.expander(f"📄 {df} — {wc:,} words"):
                            st.markdown(content[:5000] + ("…" if len(content) > 5000 else ""))

        else:
            st.info("ℹ️ No completed report yet. Run the agent in the **Run Agent** tab.")

    else:
        st.markdown("""
        <div class="status-info">
        ℹ️ No report generated yet. Configure the company details and run the agent first.
        </div>
        """, unsafe_allow_html=True)

# ─── Auto-refresh while running ──────────────────────────────────────────────
if st.session_state.agent_running:
    # Drain events again after all tabs rendered, then rerun
    import time
    time.sleep(0.5)
    st.rerun()
