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
from tools import ReportSession
from utils import slug


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
) -> int:
    """Run the DeepAgent with persistent checkpointing and interactive HITL support."""
    co_slug = slug(company)
    fy_slug = slug(fy)
    active_run_dir = run_dir or os.path.join("runs", f"{co_slug}_{fy_slug}")
    os.makedirs(active_run_dir, exist_ok=True)

    active_prov = (os.getenv("LLM_PROVIDER") or "gemini").strip()
    active_model = model or os.getenv("LLM_MODEL") or (os.getenv("GEMINI_MODEL", "gemini-3.5-flash") if "gemini" in active_prov.lower() else os.getenv("OLLAMA_MODEL", "gpt-oss:20b"))

    print("\n" + "=" * 72)
    print(f"  AUTONOMOUS ANNUAL REPORT DEEPAGENT: {company.upper()}")
    print(f"  Period: {fy}  |  Scope: {scope}  |  Country: {country or 'Inferred'}")
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
        f"Execute the complete annual report pipeline autonomously from start to finish:\n"
        f"1. Save your task plan to 'plan.json' using 'write_file' (never use 'fs.write_file'). "
        f"CRITICAL: 'write_file' content MUST be a plain string — never pass a dict. "
        f"Convert to JSON string first: write_file(path='plan.json', content=json.dumps(your_plan_dict, indent=2)).\n"
        f"2. Immediately call 'resolve_company_identity' and 'find_source_documents'.\n"
        f"3. Request human confirmation via 'request_human_decision' before ingesting external domains.\n"
        f"4. Ingest sources ('ingest_documents'), build the facts ledger ('build_facts_ledger'), calibrate capacity ('get_evidence_capacity'), "
        f"draft each chapter into 'drafts/' (via 'section_writer' subagent or 'write_file'), verify with 'check_section', 'check_entities', "
        f"'verify_financials', and 'check_regulatory_coverage', generate charts ('make_charts'), and compile final outputs ('assemble_report').\n"
        f"Do NOT ask for company name, period, or country—they are already provided above. Begin calling tools immediately."
    )

    inputs = {"messages": [{"role": "user", "content": initial_prompt}]}

    # 4. Stream graph events and handle human-in-the-loop interrupts
    print("[*] Starting DeepAgent execution loop...")
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
                            print("\n" + "#" * 60)
                            print("  HUMAN APPROVAL REQUIRED (INTERRUPT)")
                            print(f"  {val}")
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

                            # Resume graph execution with the decision
                            from langgraph.types import Command
                            inputs = Command(resume=user_choice)
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
                    print("\n[*] [Autonomous Drive] Agent paused before assembling final report. Continuing pipeline...", flush=True)
                    inputs = {
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    f"Continue executing the next steps from your plan.json for {company} {fy}: "
                                    "1. Call 'find_source_documents' to locate regulatory filings and financial data. "
                                    "2. Ingest filings with 'ingest_documents', build facts with 'build_facts_ledger', and check capacity with 'get_evidence_capacity'. "
                                    "3. Draft required chapters to 'drafts/<chapter>.md', run verification tools ('check_section', 'check_regulatory_coverage'), "
                                    "and call 'assemble_report' to produce the final annual report."
                                ),
                            }
                        ]
                    }
                    continue

                # Graph reached end and report is assembled
                print("\n[✓] DeepAgent execution completed successfully.")
                print(f"[*] Final report generated in: {active_run_dir}")
                break

        except Exception as e:
            print(f"\n[!] DeepAgent execution error: {e}")
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
    parser.add_argument("--model", type=str, default=None, help="LLM model (provider:model)")
    parser.add_argument("--run-dir", type=str, default=None, help="Custom run directory")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all HITL checkpoints")
    parser.add_argument("--pdf", type=str, default=None, help="Path to local annual report PDF")
    parser.add_argument("--dossier", type=str, default=None, help="Path to internal corporate dossier")
    parser.add_argument("--resume", action="store_true", help="Resume from existing checkpoints")
    parser.add_argument("--provider", type=str, default=None, choices=["gemini", "ollama"], help="LLM provider (gemini or ollama)")
    parser.add_argument("--api-key", type=str, default="", help="Gemini API Key override")

    args = parser.parse_args()
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
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
    ))


if __name__ == "__main__":
    main()
