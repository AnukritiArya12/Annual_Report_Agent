"""Orchestrator for the generic annual-report agent.

    python main.py "Company Name" --fy "FY 2024-25" --country India
    python main.py "Company Name" --fy 2024 --country USA --pdf path/to/report.pdf --pages 250

Flow: ingest the company's real report(s) -> research profile -> plan the structure dynamically ->
write every section from retrieved evidence (parallel, with validation) -> assemble MD / DOCX / PDF.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from agent import GenericTaskExecutionAgent, RunContext
from assembler import ReportAssembler
from config import active_model_label, get_llm, settings
from knowledge import KnowledgeBase, ReferenceIngestor
from planner import DynamicPlanningEngine
from schemas import (ExecutionState, ReportInput, TaskDefinition, TaskOutput, TaskStatus, ValidationResult)
from tools import ResearchTools
from utils import slug, word_count
from validator import DynamicValidator


class MainOrchestrator:
    def __init__(self, run_dir: str, resume: bool = False):
        self.run_dir = run_dir
        self.resume = resume
        os.makedirs(run_dir, exist_ok=True)
        self.tools = ResearchTools(run_dir=run_dir)
        self.kb_path = os.path.join(run_dir, "kb.json")
        self.print_lock = threading.Lock()

    # --------------------------------------------------------------------- run
    def run(self, spec: ReportInput) -> ExecutionState:
        t0 = time.time()
        print("\n" + "=" * 72)
        print(f"  ANNUAL REPORT AGENT: {spec.company_name.upper()}  |  {spec.financial_year}  |  {spec.reporting_scope}")
        print(f"  Model: {active_model_label()}")
        print("=" * 72)

        self._check_llm()

        # ---- 1. knowledge base (the company's real report(s))
        print("\n>>> STEP 1: LOADING REFERENCE DOCUMENTS")
        planner_resume = self.resume and os.path.exists(os.path.join(self.run_dir, "blueprint.json")) \
            and os.path.exists(self.kb_path)
        if planner_resume:
            kb = KnowledgeBase.load(self.kb_path)
            print(f"  Resuming: knowledge base with {len(kb.chunks)} chunks loaded.")
            docs, reference_toc = [], ""
        else:
            shutil.rmtree(os.path.join(self.run_dir, "drafts"), ignore_errors=True)
            kb = KnowledgeBase()
            ingestor = ReferenceIngestor(self.tools, kb, self.run_dir)
            docs = ingestor.ingest(spec)
            kb.save(self.kb_path)
            reference_toc = ingestor.contents.get(docs[0].doc_id, "") if docs else ""
            if not docs:
                print("  WARNING: no reference report could be loaded. Content will rest on web snippets only and "
                      "will be thin. Supply the report with --pdf <file-or-url> for a full-length result.")

        # ---- 2. plan
        print("\n>>> STEP 2: DYNAMIC PLANNING")
        planner = DynamicPlanningEngine(tools=self.tools, kb=kb)
        blueprint = planner.load_blueprint(self.run_dir) if planner_resume else None
        if blueprint is None:
            profile = planner.research_company_profile(spec, docs)
            blueprint = planner.generate_blueprint(spec, profile, docs, reference_toc)
            planner.save_blueprint(blueprint, self.run_dir)
        else:
            print(f"  Resuming with saved blueprint: {len(blueprint.tasks)} tasks.")
        stages = blueprint.compute_execution_stages()

        # ---- 3. write + validate
        print(f"\n>>> STEP 3: WRITING {len(blueprint.tasks)} SECTIONS "
              f"({len(stages)} stages, {settings.MAX_PARALLEL_WORKERS} parallel workers)")
        self.ctx = RunContext(blueprint=blueprint, kb=kb, tools=self.tools,
                              draft_dir=os.path.join(self.run_dir, "drafts"))
        self.validator = DynamicValidator()
        state = ExecutionState(report_id=blueprint.report_id, blueprint=blueprint)
        done = 0
        total = len(blueprint.tasks)
        for si, stage in enumerate(stages, 1):
            tasks = [blueprint.get_task(t) for t in stage]
            print(f"\n--- Stage {si}/{len(stages)}: {len(tasks)} section(s) ---")
            with ThreadPoolExecutor(max_workers=max(1, settings.MAX_PARALLEL_WORKERS)) as ex:
                futs = {ex.submit(self._process, t): t for t in tasks}
                for fut in as_completed(futs):
                    t = futs[fut]
                    try:
                        out, val = fut.result()
                    except Exception as e:      # a bug in one task must not kill a 300-page run
                        out = TaskOutput(task_id=t.task_id, chapter_id=t.chapter_id, status=TaskStatus.FAILED,
                                         markdown_content=f"*(This section could not be generated: {str(e)[:160]})*",
                                         error_message=str(e))
                        val = ValidationResult(target_id=t.task_id, is_valid=False, issues_found=[str(e)])
                    state.task_outputs[t.task_id] = out
                    state.validation_results[t.task_id] = val
                    done += 1
                    print(f"  [Progress {done}/{total}] {' > '.join(t.path)}")

        # ---- 4. assemble
        print("\n>>> STEP 4: ASSEMBLY & EXPORT")
        assembler = ReportAssembler(tools=self.tools)
        rid = blueprint.report_id
        md_file = os.path.join(self.run_dir, f"{rid}.md")
        docx_file = os.path.join(self.run_dir, f"{rid}.docx")
        pdf_file = os.path.join(self.run_dir, f"{rid}.pdf")
        title = f"{blueprint.company_profile.company_name} - Annual Report {spec.financial_year}"
        assembler.assemble_markdown(blueprint, state.task_outputs, md_file)
        for name, fn, path in (("DOCX", assembler.export_docx, docx_file), ("PDF", assembler.export_pdf, pdf_file)):
            try:
                fn(md_file, path, title)
            except Exception as e:
                print(f"  [Assembler] {name} export failed ({e}); the Markdown file is complete.")
        state.final_report_path = pdf_file if os.path.exists(pdf_file) else md_file

        # ---- summary
        words = sum(word_count(o.markdown_content) for o in state.task_outputs.values())
        failed = [blueprint.get_task(i).title for i, o in state.task_outputs.items() if o.status == TaskStatus.FAILED]
        flagged = [blueprint.get_task(i).title for i, v in state.validation_results.items()
                   if not v.is_valid and state.task_outputs[i].status != TaskStatus.FAILED]
        summary = {
            "company": blueprint.company_profile.company_name, "financial_year": spec.financial_year,
            "target_pages": blueprint.target_pages, "chapters": len(blueprint.chapters), "tasks": total,
            "words_written": words, "approx_pages": round(words / settings.WORDS_PER_PAGE),
            "failed_sections": failed, "sections_with_open_validation_notes": flagged,
            "reference_documents": [d.title for d in blueprint.reference_documents],
            "elapsed_seconds": round(time.time() - t0, 1),
        }
        with open(os.path.join(self.run_dir, "run_report.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print("\n" + "=" * 72)
        print(f"  DONE in {summary['elapsed_seconds']}s | {blueprint.company_profile.company_name} {spec.financial_year}")
        print(f"  Chapters: {len(blueprint.chapters)} | Sections: {total} | ~{words:,} words (~{summary['approx_pages']} pages; plan was {blueprint.target_pages})")
        print(f"  Failed sections: {len(failed)} | Sections with open review notes: {len(flagged)}")
        for label, p in (("PDF", pdf_file), ("Word", docx_file), ("Markdown", md_file)):
            print(f"  {label:9}: {p}")
        if failed:
            print("  Re-run the same command with --resume to retry only the failed sections.")
        print("=" * 72 + "\n")
        return state

    # ----------------------------------------------------------------- helpers
    def _check_llm(self) -> None:
        print("\n[System] Checking LLM connection...")
        try:
            r = get_llm().invoke([{"role": "user", "content": "Reply with exactly: LLM_OK"}])
            if not r.content.strip():
                raise RuntimeError("empty reply")
            print("[System] LLM connected.")
        except Exception as e:
            print(f"[System] LLM is not usable: {e}\nFix the provider settings in .env (see .env.example) and retry. "
                  "The agent will not generate placeholder text without a working model.")
            sys.exit(1)

    def _process(self, task: TaskDefinition) -> Tuple[TaskOutput, ValidationResult]:
        agent = GenericTaskExecutionAgent(self.ctx)
        output = agent.execute_task(task)
        if agent.from_cache and output.status == TaskStatus.COMPLETED:
            return output, ValidationResult(target_id=task.task_id, is_valid=True)
        validation = self.validator.validate_task_output(task, output, self.ctx.snapshot_texts())
        retries = 0
        while not validation.is_valid and retries < settings.MAX_VALIDATION_RETRIES:
            retries += 1
            print(f"  [Revision {retries}/{settings.MAX_VALIDATION_RETRIES}] '{task.title}'")
            new = agent.execute_task(task, feedback=validation.revision_instructions)
            if new.status == TaskStatus.COMPLETED or output.status != TaskStatus.COMPLETED:
                output = new
            validation = self.validator.validate_task_output(task, output, self.ctx.snapshot_texts())
        return output, validation


def parse_arguments():
    p = argparse.ArgumentParser(description="Generic AI annual-report generator (any company, any country).")
    p.add_argument("company", help="Company name, e.g. 'Infosys'")
    p.add_argument("--fy", default="Latest financial year", help="Financial year, e.g. 'FY 2024-25' or '2024'")
    p.add_argument("--country", default="", help="Country (optional; discovered if omitted)")
    p.add_argument("--industry", default=None, help="Industry (optional; discovered if omitted)")
    p.add_argument("--scope", default="Consolidated", help="Reporting scope")
    p.add_argument("--pages", type=int, default=None, help="Target length in pages (default: matches the company's real report)")
    p.add_argument("--pdf", action="append", default=[], metavar="FILE_OR_URL",
                   help="Reference annual report (PDF path or URL). Repeatable. Strongly recommended.")
    p.add_argument("--extra", default=None, help="Extra instructions for the planner")
    p.add_argument("--workers", type=int, default=None, help="Parallel writing workers")
    p.add_argument("--resume", action="store_true", help="Reuse the saved plan, knowledge base and finished sections")
    a = p.parse_args()
    if a.workers:
        settings.MAX_PARALLEL_WORKERS = a.workers
    return ReportInput(company_name=a.company, financial_year=a.fy, country=a.country, industry=a.industry,
                       reporting_scope=a.scope, extra_instructions=a.extra, target_pages=a.pages,
                       source_documents=a.pdf), a.resume


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    spec, resume = parse_arguments()
    run_dir = os.path.abspath(os.path.join("runs", f"{slug(spec.company_name)}_{slug(spec.financial_year)}"))
    MainOrchestrator(run_dir=run_dir, resume=resume).run(spec)