"""CLI entry point and interactive runner for the ONE Autonomous Annual Report DeepAgent."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
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

    print("\n" + "=" * 72)
    print(f"  AUTONOMOUS ANNUAL REPORT DEEPAGENT: {company.upper()}")
    print(f"  Period: {fy}  |  Scope: {scope}  |  Country: {country or 'Inferred'}")
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
        f"Generate a comprehensive, evidence-grounded annual report for {company} for {fy}. "
        f"Reporting scope is {scope}. Country/jurisdiction hint: '{country}'. "
        f"First, plan your tasks using your todo list and save to disk. "
        f"Calibrate depth against available evidence and seek human confirmation via request_human_decision "
        f"when required before drafting and final publication."
    )

    inputs = {"messages": [{"role": "user", "content": initial_prompt}]}

    # 4. Stream graph events and handle human-in-the-loop interrupts
    print("[*] Starting DeepAgent execution loop...")
    while True:
        try:
            # Stream execution
            interrupted = False
            for event in agent.stream(inputs, config=config, stream_mode="values"):
                # Print assistant messages or status
                msgs = event.get("messages", [])
                if msgs:
                    last_msg = msgs[-1]
                    # Print if text content
                    txt = getattr(last_msg, "content", "")
                    if txt and isinstance(txt, str) and not txt.startswith("{"):
                        # Truncate large outputs
                        if len(txt) > 300:
                            print(f"[DeepAgent] {txt[:280]}...")
                        else:
                            print(f"[DeepAgent] {txt}")

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
                # Graph reached end
                print("\n[✓] DeepAgent execution completed successfully.")
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

    args = parser.parse_args()
    target_company = (args.company_opt or args.company or "").strip()
    if not target_company:
        parser.error("Company name is required. Specify as 'python app.py \"Apple Inc\"' or 'python app.py --company \"Apple Inc\"'.")

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
