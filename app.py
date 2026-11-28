"""CLI entry point and interactive runner for the ONE Autonomous Annual Report DeepAgent."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

from agent import build_annual_report_agent
from config import settings
from tools import ReportSession
from utils import slug


def _page_progress(run_dir: str) -> str:
    """One-line size report of the drafts written so far, e.g. 'Drafted 12 sections, 18,400 words = about 53 pages'."""
    words, sections = 0, 0
    drafts = os.path.join(run_dir, "drafts")
    if os.path.isdir(drafts):
        for name in os.listdir(drafts):
            if name.endswith(".md"):
                sections += 1
                with open(os.path.join(drafts, name), encoding="utf-8", errors="replace") as f:
                    words += len(f.read().split())
    pages = round(words / settings.WORDS_PER_PAGE)
    return f"Drafted {sections} sections, {words:,} words = about {pages} pages (limit {settings.MAX_PAGES})"


def run_agent_cli(
    company: str,
    fy: str,
    country: str = "",
    scope: str = "consolidated",
    model: Optional[str] = None,
    run_dir: Optional[str] = None,
    auto_approve: bool = False,
    pdf_path: Optional[str] = None,
    dossier_path: Optional[str] = None,
    resume: bool = False,
    peer_report_path: Optional[str] = None,
    industry: str = "",
    is_first_year: bool = False,
    target_pages: Optional[int] = None,
) -> int:
    """Run the DeepAgent with persistent checkpointing and interactive HITL support."""
    co_slug = slug(company)
    fy_slug = slug(fy)
    active_run_dir = run_dir or os.path.join("runs", f"{co_slug}_{fy_slug}")
    os.makedirs(active_run_dir, exist_ok=True)

    active_prov = "gemini"
    active_model = model or settings.GEMINI_MODEL

    print("\n" + "=" * 72)
    print(f"  AUTONOMOUS ANNUAL REPORT DEEPAGENT: {company.upper()}")
    print(f"  Period: {fy}  |  Scope: {scope}  |  Country: {country or 'Inferred'}")
    if industry:
        print(f"  Industry: {industry}")
    if is_first_year:
        print("  Mode: Inaugural / First-Time Corporate Report")
    print(f"  Provider: {active_prov.upper()}  |  Active Model: {active_model}")
    print(f"  Workspace: {active_run_dir}")
    print("=" * 72 + "\n")

    # 1. Initialize session
    session = ReportSession(
        run_dir=active_run_dir,
        company_name=company,
        financial_year=fy,
        country=country,
        reporting_scope=scope,
        industry=industry,
        is_first_year=is_first_year,
        auto_approve=auto_approve,
    )

    # Validate resume manifest if continuing an existing run
    if resume:
        valid_resume, resume_msg = session.validate_resume_manifest()
        if not valid_resume:
            print(f"\n[!] Resume verification failed: {resume_msg}")
            return 1
        print(f"[*] Verified resume integrity: {resume_msg}")
    else:
        session.update_manifest("run_started", {
            "company": company,
            "financial_year": fy,
            "scope": scope,
            "country": country,
        })

    # 2. If user provided a local PDF or dossier, ingest immediately into storage
    if pdf_path and os.path.exists(pdf_path):
        print(f"[*] Ingesting provided primary document: {pdf_path}")
        from tools.discovery_tools import build_discovery_tools
        disc_tools = {t.name: t for t in build_discovery_tools(session)}
        disc_tools["ingest_documents"].invoke({"urls_or_paths": [pdf_path]})

    if dossier_path and os.path.exists(dossier_path):
        print(f"[*] Ingesting internal dossier file: {dossier_path}")
        from tools.discovery_tools import build_discovery_tools
        disc_tools = {t.name: t for t in build_discovery_tools(session)}
        disc_tools["ingest_documents"].invoke({"urls_or_paths": [dossier_path]})

    if peer_report_path and os.path.exists(peer_report_path):
        print(f"[*] Recording structure-only peer report reference: {peer_report_path}")
        session.peer_report_path = os.path.abspath(peer_report_path)
        try:
            from knowledge import parse_pdf, parse_docx
            ext = os.path.splitext(session.peer_report_path)[1].lower()
            if ext == ".pdf":
                pages, toc, _ = parse_pdf(session.peer_report_path)
                outline = "\n".join(("#" * max(1, min(level + 1, 6))) + " " + title for level, title, _ in toc)
                if not outline:
                    outline = "\n".join(text[:300] for _, text in pages[:2])
            elif ext in (".docx", ".doc"):
                pages, toc, _ = parse_docx(session.peer_report_path)
                outline = "\n".join(("#" * max(1, min(level + 1, 6))) + " " + title for level, title, _ in toc)
                if not outline:
                    outline = "\n".join(text[:300] for _, text in pages[:2])
            elif ext in (".txt", ".md"):
                with open(session.peer_report_path, "r", encoding="utf-8", errors="replace") as peer_file:
                    outline = "\n".join(line for line in peer_file if line.lstrip().startswith("#"))[:12000]
            else:
                outline = "Unsupported reference format. Use PDF, DOCX, TXT, or Markdown."
            session.peer_report_outline = outline[:12000]
        except Exception as exc:
            session.peer_report_outline = f"Could not extract structure: {str(exc)[:300]}"

    # 3. Build DeepAgent graph with SQLite checkpointer and interrupt_on
    agent = build_annual_report_agent(
        session=session,
        model=model,
        interrupt_on_human_decisions=not auto_approve,
    )

    thread_id = f"{co_slug}_{fy_slug}"
    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    initial_prompt = (
        f"Target Company: {company}\n"
        f"Financial Year: {fy}\n"
        f"Reporting Scope: {scope}\n"
        f"Country/Jurisdiction: {country or 'Infer via resolve_company_identity'}\n\n"
        f"Optional structure-only reference outline (headings only):\n{session.peer_report_outline or 'not supplied'}\n\n"
        "Create a complete annual report for this company and period, using the normal corporate report arc "
        "from company overview through strategy, operations, governance, risks, financial performance, and "
        "applicable disclosures. Adapt the chapter names, order, and depth to the company's industry and "
        "reporting framework; do not force irrelevant sections. Research and use evidence from any suitable "
        "available sources. The web is optional: use provided local files, user-provided company information, "
        "public sources, or a combination. If the company has no prior annual report or public filing, help "
        "build its first report from the information and documents available, clearly marking unknowns and "
        "asking the user for missing material inputs only when needed. If no sources are available, ask for "
        "company records and management answers and prepare a first-report draft with clearly marked "
        "unprovided facts. Cover the full relevant annual-report structure with depth appropriate to company "
        "scale and available material; do not default a substantial company to a short summary, and do not "
        "pad with invented or repeated material. Existing company reports are factual "
        "sources for their own periods; other companies' reports may inform structure and presentation only, "
        "never target-company facts. Keep the final document at or below 500 pages; use evidence capacity to "
        "choose a supported length and never pad to reach a page count. Use DeepAgents tools, relevant skills, "
        "and focused subagents as appropriate. Validate material claims and assemble the final Markdown, DOCX, "
        "and PDF outputs. Continue from the current checkpoint on resume."
    )

    inputs = None if resume else {"messages": [{"role": "user", "content": initial_prompt}]}

    # 4. Stream graph events and handle human-in-the-loop interrupts
    print("[*] Starting DeepAgent execution loop...")
    nudges = 0
    MAX_NUDGES = 3          # how many times we may say "continue" before stopping (each nudge costs LLM quota)
    while True:
        try:
            # Stream execution
            interrupted = False
            seen_msg_ids = set()
            for event in agent.stream(inputs, config=config, stream_mode="values"):
                # Print assistant messages, tool calls, and status
                msgs = event.get("messages", [])
                if msgs:
                    last_msg = msgs[-1]
                    msg_id = getattr(last_msg, "id", None) or str(id(last_msg))
                    if msg_id in seen_msg_ids:
                        continue
                    seen_msg_ids.add(msg_id)

                    # Print tool calls if LLM decided to invoke tools
                    tool_calls = getattr(last_msg, "tool_calls", None)
                    if tool_calls:
                        for tc in tool_calls:
                            tc_name = tc.get("name", "tool")
                            tc_args = tc.get("args", {})
                            print(f"[*] [Action] Calling tool '{tc_name}'...", flush=True)

                    # Print tool result confirmation
                    if hasattr(last_msg, "type") and last_msg.type == "tool":
                        t_name = getattr(last_msg, "name", "tool")
                        print(f"[✓] [Success] Tool '{t_name}' finished.", flush=True)
                        if t_name in ("write_file", "edit_file", "assemble_report"):
                            print(f"    [Size] {_page_progress(active_run_dir)}", flush=True)

                    # Print assistant thought / text content (AIMessage only)
                    msg_type = getattr(last_msg, "type", "")
                    if msg_type == "ai":
                        txt = getattr(last_msg, "content", "")
                        if txt and isinstance(txt, str) and not txt.startswith("{") and not tool_calls:
                            if len(txt) > 300:
                                print(f"[DeepAgent] {txt[:280]}...", flush=True)
                            else:
                                print(f"[DeepAgent] {txt}", flush=True)

            # Check if agent paused on an interrupt
            state = agent.get_state(config)
            if state and getattr(state, "tasks", None):
                for task in state.tasks:
                    if getattr(task, "interrupts", None):
                        interrupted = True
                        for intr in task.interrupts:
                            val = getattr(intr, "value", intr)
                            if isinstance(val, dict) and val.get("kind") == "management_input":
                                print("\n" + "#" * 60)
                                print(f"  COMPANY INPUT NEEDED — {val.get('respondent_role', 'company representative')}")
                                print(f"  Question: {val.get('question', '')}")
                                if val.get("report_topics"):
                                    print(f"  Report topics: {', '.join(val['report_topics'])}")
                                if val.get("why_needed"):
                                    print(f"  Why this is needed: {val['why_needed']}")
                                print("  Answer from company records or the relevant team. Mark estimates as estimates; enter 'unknown' if unavailable.")
                                print("#" * 60)
                                try:
                                    answer = input("Company answer (or 'skip' / 'abort'): ").strip()
                                except (EOFError, KeyboardInterrupt):
                                    answer = "abort"
                                if answer.lower() == "abort":
                                    print("[*] Run paused/aborted by user.")
                                    return 1
                                if answer.lower() == "skip":
                                    answer = "No information was provided for this question."
                                from langgraph.types import Command
                                inputs = Command(resume=answer)
                                interrupted = True
                                break

                            print("\n" + "#" * 60)
                            print("  HUMAN APPROVAL REQUIRED (INTERRUPT)")
                            print(f"  {val}")
                            if isinstance(val, dict):
                                for action in val.get("action_requests", []):
                                    if not isinstance(action, dict):
                                        continue
                                    action_name = action.get("name") or action.get("tool") or action.get("tool_name")
                                    action_args = action.get("args", {})
                                    if action_name:
                                        print(f"  Requested action: {action_name}")
                                    if isinstance(action_args, dict):
                                        for key in ("question", "approve_domain", "approve_names", "exclude_names", "reject_domain", "reject_source"):
                                            if action_args.get(key):
                                                print(f"  {key.replace('_', ' ').title()}: {action_args[key]}")
                            print("#" * 60)

                            if auto_approve:
                                user_choice = "approve"
                                print("  [--auto-approve] Auto-approving checkpoint.")
                            else:
                                prompt_text = "\nEnter 'y' to approve, 'n' to reject, 'm' to modify scope/entity, or 'abort': "
                                try:
                                    resp = input(prompt_text).strip().lower()
                                except (EOFError, KeyboardInterrupt):
                                    resp = "abort"

                                if resp == "abort":
                                    print("[*] Run aborted by user.")
                                    return 1
                                if resp in ("y", "yes", ""):
                                    user_choice = "approve"
                                elif resp in ("m", "modify"):
                                    mod_scope = input("Enter revised reporting scope (or press Enter to keep): ").strip()
                                    if mod_scope:
                                        session.reporting_scope = mod_scope
                                        print(f"  [Configuration Updated] Reporting scope set to '{mod_scope}'.")
                                    user_choice = "modified"
                                else:
                                    user_choice = "reject"
                                    try:
                                        rej_target = input("Enter domain or URL to permanently block (or press Enter to skip): ").strip()
                                    except (EOFError, KeyboardInterrupt):
                                        rej_target = ""
                                    if rej_target:
                                        if rej_target.startswith(("http://", "https://")):
                                            session.reject_source(rej_target)
                                        else:
                                            session.reject_domain(rej_target)
                                        print(f"  [Security Guard] Added '{rej_target}' to permanent rejection list.")

                            # Build the decisions payload expected by HumanInTheLoopMiddleware
                            n_actions = 1
                            if isinstance(val, dict) and "action_requests" in val:
                                n_actions = max(len(val.get("action_requests", [])), 1)

                            decision_type = "approve" if user_choice in ("approve", "modified") else "reject"
                            resume_payload = {"decisions": [{"type": decision_type}] * n_actions}

                            # Resume graph execution with the decision
                            from langgraph.types import Command
                            inputs = Command(resume=resume_payload)
                            break
                        if interrupted:
                            break

            if not interrupted:
                # Check if final report was assembled
                expected_docx = os.path.join(active_run_dir, f"{co_slug}_{fy_slug}.docx")
                expected_pdf = os.path.join(active_run_dir, f"{co_slug}_{fy_slug}.pdf")
                expected_md = os.path.join(active_run_dir, f"{co_slug}_{fy_slug}.md")
                has_final_report = os.path.exists(expected_docx) or os.path.exists(expected_pdf) or os.path.exists(expected_md)

                if not has_final_report:
                    nudges += 1
                    if nudges > MAX_NUDGES:
                        print("\n[!] Agent stopped without assembling a report after "
                              f"{MAX_NUDGES} reminders. Stopping to save quota. "
                              "Run again with --resume to continue.")
                        return 1
                    print("\n[*] [Autonomous Drive] Agent paused before assembling final report. Continuing pipeline...", flush=True)
                    inputs = {
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    f"Continue the annual report for {company}, {fy}, from the current checkpoint. "
                                    "Use the appropriate remaining research, drafting, and review steps for this "
                                    "company and assemble the final report when ready. Do not restart completed work."
                                ),
                            }
                        ]
                    }
                    continue

                # Graph reached end and report is assembled
                print("\n[✓] DeepAgent execution completed successfully.")
                print(f"[*] Final report generated in: {active_run_dir}")
                print(f"[*] {_page_progress(active_run_dir)}")
                break

        except Exception as e:
            print(f"\n[!] DeepAgent execution error: {e}")
            if any(k in str(e) for k in ("429", "RESOURCE_EXHAUSTED", "quota", "Quota")):
                print("[!] Looks like a quota / rate-limit problem. Wait (daily quota resets at midnight Pacific), "
                      "lower LLM_RPM in .env, then continue with --resume.")
            import traceback
            traceback.print_exc()
            return 1

    return 0


def main():
    parser = argparse.ArgumentParser(description="Autonomous Annual Report DeepAgent")
    parser.add_argument("company", type=str, nargs="?", default="", help="Company name (positional)")
    parser.add_argument("--company", type=str, default="", dest="company_opt", help="Company name (flag)")
    parser.add_argument("--fy", type=str, default="FY 2024", help="Financial Year (e.g. 'FY 2024')")
    parser.add_argument("--country", type=str, default="", help="Country or jurisdiction")
    parser.add_argument("--scope", type=str, default="consolidated", help="Reporting scope")
    parser.add_argument("--industry", type=str, default="", help="Industry / sector (optional)")
    parser.add_argument("--pages", type=int, default=None, help="Target pages guideline (optional)")
    parser.add_argument("--first-year", action="store_true", help="Inaugural / first year reporting company")
    parser.add_argument("--model", type=str, default=None, help="Gemini model name, e.g. gemini-3.8-flash (default: GEMINI_MODEL in .env)")
    parser.add_argument("--run-dir", type=str, default=None, help="Custom run directory")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all HITL checkpoints")
    parser.add_argument("--pdf", type=str, default=None, help="Path to local annual report PDF")
    parser.add_argument("--dossier", type=str, default=None, help="Path to internal corporate dossier")
    parser.add_argument("--peer-report", type=str, default=None, help="Optional other-company annual report used only as a structural reference")
    parser.add_argument("--resume", action="store_true", help="Resume from existing checkpoints")
    parser.add_argument("--api-key", type=str, default="", help="Gemini API Key override")

    args = parser.parse_args()
    target_company = (args.company_opt or args.company or "").strip()
    if not target_company:
        parser.error("Company name is required. Specify as 'python app.py \"Apple Inc\"' or 'python app.py --company \"Apple Inc\"'.")

    # Resolve API key from CLI flag or fallback text files to bypass .env editor locks
    cli_key = (args.api_key or "").strip()
    if not cli_key:
        for kfile in ("gemini_key.txt", "key.txt", ".env.local"):
            if os.path.exists(kfile):
                try:
                    with open(kfile, "r", encoding="utf-8") as f:
                        val = f.read().strip()
                        if val:
                            cli_key = val
                            break
                except Exception:
                    pass

    if cli_key:
        os.environ["GEMINI_API_KEY"] = cli_key
        os.environ["GOOGLE_API_KEY"] = cli_key
        from config import settings as _settings
        _settings.GEMINI_API_KEY = cli_key      # settings was read at import time, so update it too

    sys.exit(run_agent_cli(
        company=target_company,
        fy=args.fy,
        country=args.country,
        scope=args.scope,
        model=args.model,
        run_dir=args.run_dir,
        auto_approve=args.auto_approve,
        pdf_path=args.pdf,
        dossier_path=args.dossier,
        resume=args.resume,
        peer_report_path=args.peer_report,
        industry=args.industry,
        is_first_year=args.first_year,
        target_pages=args.pages,
    ))


if __name__ == "__main__":
    main()
