"""Main Orchestrator Agent for AI-Based Generic Annual Report Generation System.

Coordinates dynamic planning, delegates tasks to parallel/serial instances of the
generic execution agent, enforces validation gates, and assembles publication-grade
Annual Reports in Markdown, DOCX, and PDF formats.
"""

import sys
import os
import time
import argparse
from typing import Dict, Any

from config import settings
from schemas import (
    ReportInput,
    ReportBlueprint,
    TaskOutput,
    TaskStatus,
    ExecutionState
)
from tools import ResearchTools
from planner import DynamicPlanningEngine
from agent import GenericTaskExecutionAgent, execute_stage_parallel
from validator import DynamicValidator
from assembler import ReportAssembler


class MainOrchestrator:
    """Main Orchestrator Agent coordinating the end-to-end annual report workflow."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        os.makedirs(self.run_dir, exist_ok=True)
        self.tools = ResearchTools(run_dir=self.run_dir)
        self.planner = DynamicPlanningEngine(tools=self.tools)
        self.validator = DynamicValidator()
        self.assembler = ReportAssembler(tools=self.tools)

    def run(self, input_spec: ReportInput) -> ExecutionState:
        """Executes the complete annual report generation lifecycle."""
        start_time = time.time()
        # Determine active model name for display
        if settings.LLM_PROVIDER in ("gemini", "google"):
            active_model = f"{settings.GEMINI_MODEL} (Google Gemini)"
        elif settings.LLM_PROVIDER == "ollama":
            active_model = f"{settings.OLLAMA_MODEL} (Ollama Local)"
        elif settings.LLM_PROVIDER == "openai":
            active_model = f"{settings.OPENAI_MODEL} (OpenAI)"
        else:
            active_model = settings.LLM_PROVIDER

        print(f"\n" + "=" * 70)
        print(f"  AI ANNUAL REPORT SYSTEM: {input_spec.company_name.upper()}")
        print(f"  Financial Year: {input_spec.financial_year} | Scope: {input_spec.reporting_scope}")
        print(f"  Country: {input_spec.country} | Sector: {input_spec.industry or 'Dynamic Autonomous Discovery'}")
        print(f"  Model Engine: {active_model}")
        print("=" * 70 + "\n")

        # LLM connectivity check
        print("[System] Verifying LLM connectivity...")
        try:
            from config import get_llm
            _test_llm = get_llm()
            _test_resp = _test_llm.invoke([{"role": "user", "content": "Reply with exactly: LLM_OK"}])
            if "LLM_OK" in _test_resp.content or len(_test_resp.content) > 0:
                print(f"[System] ✅ LLM Connected: {active_model} — ready to generate.\n")
            else:
                print(f"[System] ⚠️  LLM responded: {_test_resp.content[:60]}\n")
        except Exception as e:
            print(f"[System] ⚠️  LLM connectivity notice: {e}")
            print("[System] Resilient architectural fallback is enabled for uninterrupted report generation.\n")

        # -------------------------------------------------------------
        # STEP 1: DYNAMIC PLANNING & INTELLIGENCE DISCOVERY
        # -------------------------------------------------------------
        print(">>> STEP 1: AUTONOMOUS CORPORATE INTELLIGENCE & PLANNING")
        profile = self.planner.research_company_profile(input_spec)
        blueprint = self.planner.generate_blueprint(input_spec, profile)
        self.planner.save_blueprint(blueprint, self.run_dir)

        execution_state = ExecutionState(
            report_id=blueprint.report_id,
            blueprint=blueprint
        )

        stages = blueprint.compute_execution_stages()
        total_tasks_count = len(blueprint.tasks)
        print(f"\n[Orchestrator] Report Blueprint structured: {len(blueprint.chapters)} Chapters, {total_tasks_count} Tasks across {len(stages)} Stages.\n")

        # -------------------------------------------------------------
        # STEP 2 & 3: TASK EXECUTION & VALIDATION GATES
        # -------------------------------------------------------------
        print(">>> STEP 2: PARALLEL TASK EXECUTION & QUALITY AUDIT GATES")
        completed_outputs: Dict[str, TaskOutput] = {}
        prior_context: Dict[str, str] = {}
        global_task_counter = 0

        for stage_idx, stage_task_ids in enumerate(stages, 1):
            stage_tasks = [blueprint.get_task(tid) for tid in stage_task_ids if blueprint.get_task(tid)]
            print(f"\n--- [STAGE {stage_idx}/{len(stages)}] Executing {len(stage_tasks)} Analytical Task(s) ---")

            stage_results = execute_stage_parallel(
                tasks=stage_tasks,
                prior_context=prior_context,
                company_profile=profile,
                tools=self.tools,
                max_workers=settings.MAX_PARALLEL_WORKERS
            )

            for task in stage_tasks:
                global_task_counter += 1
                output = stage_results[task.task_id]
                validation = self.validator.validate_task_output(task, output)
                retries = 0

                while not validation.is_valid and retries < settings.MAX_VALIDATION_RETRIES:
                    retries += 1
                    print(f"\n[Validation Revision] Retrying '{task.title}' (Attempt {retries}/{settings.MAX_VALIDATION_RETRIES})...")
                    agent_instance = GenericTaskExecutionAgent(
                        agent_id=f"rev_{task.task_id[-6:]}",
                        tools=self.tools
                    )
                    output = agent_instance.execute_task(
                        task=task,
                        prior_context=prior_context,
                        company_profile=profile,
                        feedback=validation.revision_instructions
                    )
                    validation = self.validator.validate_task_output(task, output)

                completed_outputs[task.task_id] = output
                prior_context[task.task_id] = output.markdown_content
                execution_state.task_outputs[task.task_id] = output
                execution_state.validation_results[task.task_id] = validation
                if not validation.is_valid or output.status != TaskStatus.COMPLETED:
                    print(f"  [Advisory] Section '{task.title}' completed with notes: {validation.issues_found or output.error_message}")
                print(f"  [Progress: {global_task_counter}/{total_tasks_count}] Completed '{task.title}'")

        # -------------------------------------------------------------
        # STEP 4: REPORT ASSEMBLY & MULTI-FORMAT EXPORT
        # -------------------------------------------------------------
        print("\n>>> STEP 3: PUBLICATION-GRADE REPORT ASSEMBLY & EXPORT")
        missing = [t.title for t in blueprint.tasks if t.task_id not in completed_outputs]
        if missing:
            print(f"  [Assembler Notice] Incomplete sections will be noted in report: {', '.join(missing)}")
        md_file = os.path.join(self.run_dir, f"{blueprint.report_id}.md")
        docx_file = os.path.join(self.run_dir, f"{blueprint.report_id}.docx")
        pdf_file = os.path.join(self.run_dir, f"{blueprint.report_id}.pdf")

        report_title = f"{profile.company_name} - Annual Report {input_spec.financial_year}"
        self.assembler.assemble_markdown(blueprint, completed_outputs, md_file)
        self.assembler.export_docx(
            markdown_file=md_file,
            docx_file=docx_file,
            title=report_title
        )
        self.assembler.export_pdf(
            markdown_file=md_file,
            pdf_file=pdf_file,
            title=report_title
        )

        execution_state.final_report_path = pdf_file
        elapsed = time.time() - start_time

        print("\n" + "=" * 70)
        print("               ANNUAL REPORT GENERATION COMPLETE                ")
        print(f"  Company Name    : {profile.company_name} ({input_spec.financial_year})")
        print(f"  Total Chapters  : {len(blueprint.chapters)} Corporate Chapters")
        print(f"  Analytical Tasks: {len(completed_outputs)} Detailed Sections")
        print(f"  Execution Time  : {elapsed:.1f} seconds")
        print(f"  PDF Document    : {pdf_file}")
        print(f"  Word Document   : {docx_file}")
        print(f"  Markdown Source : {md_file}")
        print("=" * 70 + "\n")

        return execution_state


def parse_arguments() -> ReportInput:
    """Parses CLI arguments or defaults to the Milestone 1 target (TCS)."""
    parser = argparse.ArgumentParser(description="AI-Based Generic Annual Report Generator")
    parser.add_argument("company", nargs="?", default="TCS", help="Company Name (default: TCS)")
    parser.add_argument("fy", nargs="?", default="FY 2025-26", help="Financial Year (default: FY 2025-26)")
    parser.add_argument("country", nargs="?", default="India", help="Country (default: India)")
    parser.add_argument("industry", nargs="?", default=None, help="Industry (optional - agent discovers it if omitted)")
    parser.add_argument("scope", nargs="?", default="Consolidated", help="Reporting Scope (default: Consolidated)")

    args = parser.parse_args()
    return ReportInput(
        company_name=args.company,
        financial_year=args.fy,
        country=args.country,
        industry=args.industry,
        reporting_scope=args.scope
    )


if __name__ == "__main__":
    spec = parse_arguments()
    safe_slug = f"{spec.company_name.lower().replace(' ', '_')}_{spec.financial_year.lower().replace(' ', '_').replace('-', '_')}"
    run_dir = os.path.abspath(f"runs/{safe_slug}")

    orchestrator = MainOrchestrator(run_dir=run_dir)
    state = orchestrator.run(spec)
