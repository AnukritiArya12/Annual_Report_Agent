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

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

import approvals
from agent import GenericTaskExecutionAgent, RunContext
from assembler import ReportAssembler
from config import QuotaExhausted, active_model_label, get_llm, settings
from knowledge import KnowledgeBase, ReferenceIngestor
from ledger import FactsLedger
from storage import DatabaseStorageEngine
from planner import DynamicPlanningEngine
from schemas import (ExecutionState, ReportInput, TaskDefinition, TaskOutput, TaskStatus, ValidationResult, ValidationStatus)
from tools import ResearchTools
from utils import SPARSE_MARK, foreign_entities, slug, word_count
from validator import DynamicValidator


class MainOrchestrator:
    def __init__(self, run_dir: str, resume: bool = False, plan_only: bool = False):
        self.run_dir = run_dir
        self.resume = resume
        self.plan_only = plan_only
        os.makedirs(run_dir, exist_ok=True)
        approvals.configure(run_dir, auto=getattr(settings, "AUTO_APPROVE", False), reset=not resume)
        self.tools = ResearchTools(run_dir=run_dir)
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
        storage_engine = DatabaseStorageEngine(run_dir=self.run_dir)
        planner_resume = self.resume and os.path.exists(os.path.join(self.run_dir, "blueprint.json")) \
            and storage_engine.total_chunks() > 0
        if planner_resume:
            kb = storage_engine
            print(f"  Resuming: database with {kb.total_chunks()} persistent chunks loaded.")
            docs, reference_toc = [], ""
        else:
            shutil.rmtree(os.path.join(self.run_dir, "drafts"), ignore_errors=True)
            kb = storage_engine
            ingestor = ReferenceIngestor(self.tools, kb, self.run_dir)
            docs = ingestor.ingest(spec)
            reference_toc = ingestor.contents.get("peer_toc") or (ingestor.contents.get(docs[0].doc_id, "") if docs else "")
            if not docs:
                print("  WARNING: the agent found no source documents at all (see the [Ingest] lines above for why). "
                      "Sections will rest on per-section web search only and will be thin.")
            else:
                print(f"  Sources loaded: {', '.join(d.title for d in docs)}")

        # ---- 1b. facts ledger (canonical metrics extraction)
        print("\n>>> STEP 1b: CANONICAL FACTS LEDGER")
        ledger = FactsLedger(storage=kb)
        if not (self.resume and ledger.count_facts() > 0):
            ledger.build_ledger(spec.company_name, spec.financial_year, spec.reporting_scope)
        else:
            print(f"  Resuming with existing facts ledger: {ledger.count_facts()} facts recorded.")

        # ---- 2. plan
        print("\n>>> STEP 2: DYNAMIC PLANNING")
        planner = DynamicPlanningEngine(tools=self.tools, kb=kb)
        blueprint = planner.load_blueprint(self.run_dir) if planner_resume else None
        if blueprint is None:
            try:
                profile = planner.research_company_profile(spec, docs)
                blueprint = planner.generate_blueprint(spec, profile, docs, reference_toc)
            except QuotaExhausted as e:
                print(f"\n[Quota] Planning stopped: {e}\nNothing was lost except the plan; run the same command again later. "
                      "Tip: add --low-quota to use far fewer LLM calls.")
                sys.exit(1)
            planner.save_blueprint(blueprint, self.run_dir)
        else:
            print(f"  Resuming with saved blueprint: {len(blueprint.tasks)} tasks.")
        stages = blueprint.compute_execution_stages()
        self._write_outline(blueprint)
        if self.plan_only:
            print("\n--plan-only: stopping after planning. Outline saved to outline.txt and blueprint.json.")
            print("Run again WITHOUT --plan-only (add --resume to reuse this plan) to write the report.")
            return ExecutionState(report_id=blueprint.report_id, blueprint=blueprint)

        # ---- HUMAN CHECKPOINT 6: approve the outline before the (expensive) writing starts
        if not approvals.confirm("plan", "STEP 2b - APPROVE THE REPORT OUTLINE BEFORE WRITING",
                                 [f"{len(blueprint.chapters)} chapters, {len(blueprint.tasks)} sections (full list printed above, "
                                  "also in outline.txt)",
                                  "Say 'n' to stop; then edit blueprint.json if you want and run again with --resume."],
                                 default=True, remember_no=False):
            print("\nStopped before writing. Edit blueprint.json if needed, then run again with --resume.")
            return ExecutionState(report_id=blueprint.report_id, blueprint=blueprint)

        # ---- HUMAN CHECKPOINT: Conflicting & Missing Core Facts Review
        conflicts = ledger.get_conflicting_facts()
        missing = ledger.get_missing_core_keys(spec.financial_year)
        if conflicts or missing:
            details = []
            if conflicts:
                details.append(f"CONFLICTING FACTS ({len(conflicts)} items detected across sources):")
                for c in conflicts[:10]:
                    details.append(f"  - {c.key} ({c.period}): '{c.value}' from {c.source_doc_id}")
            if missing:
                details.append(f"MISSING CORE METRICS for {spec.financial_year}: {', '.join(missing)}")
            details.append("Writing will proceed using available canonical facts. Do you approve?")
            if not approvals.confirm("facts_review", "CHECKPOINT: REVIEW CANONICAL FACTS & REPORTING GAPS",
                                     details, default=True):
                print("\nStopped to allow reviewing facts or providing supplementary documents. Run again with --resume.")
                return ExecutionState(report_id=blueprint.report_id, blueprint=blueprint)

        # ---- 3. write + validate
        calls = len(blueprint.tasks) * (1 + (1 if settings.VALIDATE_WITH_LLM else 0))
        print(f"\n[Budget] Writing needs about {calls} LLM calls (more if sections are revised or expanded). "
              f"If your provider has a small daily quota, restart with --low-quota.")
        print(f"\n>>> STEP 3: WRITING {len(blueprint.tasks)} SECTIONS "
              f"({len(stages)} stages, {settings.MAX_PARALLEL_WORKERS} parallel workers)")
        self.ctx = RunContext(blueprint=blueprint, kb=kb, tools=self.tools,
                              draft_dir=os.path.join(self.run_dir, "drafts"), ledger=ledger)
        self.validator = DynamicValidator()
        self.validator.set_ledger(ledger)
        _names, _excluded = approvals.identity()
        self.validator.set_company(_names or [blueprint.company_profile.company_name], _excluded)
        state = ExecutionState(report_id=blueprint.report_id, blueprint=blueprint)
        done = 0
        total = len(blueprint.tasks)
        quota_msg = None
        for si, stage in enumerate(stages, 1):
            if quota_msg:
                break
            tasks = [blueprint.get_task(t) for t in stage]
            print(f"\n--- Stage {si}/{len(stages)}: {len(tasks)} section(s) ---")
            with ThreadPoolExecutor(max_workers=max(1, settings.MAX_PARALLEL_WORKERS)) as ex:
                futs = {ex.submit(self._process, t): t for t in tasks}
                for fut in as_completed(futs):
                    if fut.cancelled():
                        continue
                    t = futs[fut]
                    try:
                        out, val = fut.result()
                    except QuotaExhausted as e:
                        if not quota_msg:
                            quota_msg = str(e)
                            print(f"\n[Quota] {quota_msg}\n[Quota] Stopping cleanly; finished sections are saved.", flush=True)
                            for f in futs:
                                f.cancel()
                        continue
                    except Exception as e:      # a bug in one task must not kill a 300-page run
                        out = TaskOutput(task_id=t.task_id, chapter_id=t.chapter_id, status=TaskStatus.FAILED,
                                         markdown_content=f"*(This section could not be generated: {str(e)[:160]})*",
                                         error_message=str(e))
                        val = ValidationResult(target_id=t.task_id, is_valid=False, issues_found=[str(e)])
                    state.task_outputs[t.task_id] = out
                    state.validation_results[t.task_id] = val
                    done += 1
                    print(f"  [Progress {done}/{total}] {' > '.join(t.path)}")

        # ---- HUMAN CHECKPOINT: other-company content in the written sections
        self._entity_review(blueprint, state)

        # ---- HUMAN CHECKPOINT: Risk-Based Financial Discrepancy & Regulatory Uncertainty Review
        self._risk_based_review(blueprint, state)

        # ---- summary stats
        words = sum(word_count(o.markdown_content) for o in state.task_outputs.values())
        failed = [blueprint.get_task(i).title for i, o in state.task_outputs.items() if o.status == TaskStatus.FAILED]
        flagged = [blueprint.get_task(i).title for i, v in state.validation_results.items()
                   if not v.is_valid and state.task_outputs[i].status != TaskStatus.FAILED]
        sparse_secs = [blueprint.get_task(i).title for i, o in state.task_outputs.items() if o.sparse]

        # ---- HUMAN CHECKPOINT 3: approve final report before export
        if not approvals.confirm("final_review", "STEP 4 - APPROVE COMPLETED REPORT FOR PUBLICATION & EXPORT",
                                 [f"Company: {blueprint.company_profile.company_name} | {spec.financial_year}",
                                  f"Generated: {len(blueprint.chapters)} chapters, {total} sections (~{words:,} words, ~{round(words / settings.WORDS_PER_PAGE)} pages)",
                                  f"Failed sections: {len(failed)} | Open review notes: {len(flagged)} | Undisclosed gaps: {len(sparse_secs)}",
                                  "Confirm to assemble and generate PDF & DOCX reports."],
                                 default=True, remember_no=False):
            print("\nExport paused by user. Finished sections and blueprint are preserved; run again with --resume to finalize.")
            return state

        # ---- 4. assemble
        print("\n>>> STEP 4: ASSEMBLY & EXPORT")
        assembler = ReportAssembler(tools=self.tools)
        assembler.remove_repetition(blueprint, state.task_outputs)
        rid = blueprint.report_id
        md_file = os.path.join(self.run_dir, f"{rid}.md")
        docx_file = os.path.join(self.run_dir, f"{rid}.docx")
        pdf_file = os.path.join(self.run_dir, f"{rid}.pdf")
        title = f"{blueprint.company_profile.company_name} - Annual Report {spec.financial_year}"
        assembler.assemble_markdown(blueprint, state.task_outputs, md_file, ledger=ledger)
        for name, fn, path in (("DOCX", assembler.export_docx, docx_file), ("PDF", assembler.export_pdf, pdf_file)):
            try:
                fn(md_file, path, title)
            except Exception as e:
                print(f"  [Assembler] {name} export failed ({e}); the Markdown file is complete.")
        state.final_report_path = pdf_file if os.path.exists(pdf_file) else md_file
        summary = {
            "company": blueprint.company_profile.company_name, "financial_year": spec.financial_year,
            "target_pages": blueprint.target_pages, "chapters": len(blueprint.chapters), "tasks": total,
            "words_written": words, "approx_pages": round(words / settings.WORDS_PER_PAGE),
            "failed_sections": failed, "sections_without_source_material": sparse_secs, "sections_with_open_validation_notes": flagged,
            "reference_documents": [d.title for d in blueprint.reference_documents],
            "elapsed_seconds": round(time.time() - t0, 1),
        }
        with open(os.path.join(self.run_dir, "run_report.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print("\n" + "=" * 72)
        print(f"  DONE in {summary['elapsed_seconds']}s | {blueprint.company_profile.company_name} {spec.financial_year}")
        print(f"  Chapters: {len(blueprint.chapters)} | Sections: {total} | ~{words:,} words (~{summary['approx_pages']} pages; plan was {blueprint.target_pages})")
        print(f"  Failed sections: {len(failed)} | Open review notes: {len(flagged)} | No source material: {len(sparse_secs)}")
        if sparse_secs:
            print("  Some sections had no source material (listed in the report's Basis of Preparation).")
        for label, p in (("PDF", pdf_file), ("Word", docx_file), ("Markdown", md_file)):
            print(f"  {label:9}: {p}")
        unwritten = total - len(state.task_outputs)
        if quota_msg:
            print(f"\n  STOPPED EARLY: provider quota used up. {len(state.task_outputs)} of {total} sections are done "
                  f"({unwritten} not started). The report above is PARTIAL.")
            print("  Continue later with the SAME command plus --resume (finished sections are kept).")
        elif failed:
            print("  Re-run the same command with --resume to retry only the failed sections.")
        print("=" * 72 + "\n")
        return state

    def _risk_based_review(self, blueprint, state: ExecutionState) -> None:
        """Risk-based human review for:
        1. Arithmetic & financial verification failures.
        2. Regulatory framework uncertainty.
        3. Missing critical disclosures or conflicting source metrics."""
        tasks = {t.task_id: t for t in blueprint.tasks}
        
        # 1. Identify Financial Verification Discrepancies
        math_issues = []
        for tid, val in state.validation_results.items():
            if val.status == ValidationStatus.FAIL:
                math_errs = [i for i in val.issues_found if "Arithmetic mismatch" in i or "ungrounded" in i]
                if math_errs:
                    math_issues.append((tasks[tid].title, math_errs))

        if math_issues and not approvals.is_auto():
            lines = ["The following sections contain financial figure mismatches or arithmetic discrepancies:"]
            for title, errs in math_issues[:5]:
                lines.append(f"  * {title}: {errs[0]}")
            lines.append("You may approve proceeding with cautionary notes, or abort to revise.")
            approvals.confirm(
                "risk_financial_discrepancy",
                "RISK CHECKPOINT - ARITHMETIC / FINANCIAL VERIFICATION ALERT",
                lines,
                default=True
            )

        # 2. Regulatory & Critical Disclosure Uncertainty Review
        profile = blueprint.company_profile
        if not profile.applicable_regulations or profile.industry == "Unknown":
            if not approvals.is_auto():
                approvals.confirm(
                    "risk_regulatory_uncertainty",
                    "RISK CHECKPOINT - REGULATORY / STATUTORY UNCERTAINTY",
                    [
                        f"Company: {profile.company_name} | Country: {profile.country or 'Unknown'}",
                        f"Industry: {profile.industry} | Framework: {profile.reporting_framework or 'Not Disclosed'}",
                        "Applicable statutory corporate laws could not be unambiguously resolved from online public sources.",
                        "Confirm to proceed using general global corporate disclosure standards, or abort to provide statutory details."
                    ],
                    default=True
                )

        # 3. Missing Critical Disclosures (Sparse sections)
        sparse_critical = [
            tasks[tid].title for tid, out in state.task_outputs.items()
            if out.sparse and tasks[tid].kind in ("financial_statement", "statutory")
        ]
        if sparse_critical and not approvals.is_auto():
            approvals.confirm(
                "risk_missing_critical",
                "RISK CHECKPOINT - MISSING CRITICAL STATUTORY / FINANCIAL DISCLOSURES",
                [
                    f"The following critical sections could not find official data in public sources ({len(sparse_critical)}):",
                    *[f"  - {t}" for t in sparse_critical[:6]],
                    "These sections will be published with explicit 'Not Disclosed in Sources Reviewed' disclaimers.",
                    "Approve proceeding to publication, or provide relevant internal records via --pdf?"
                ],
                default=True
            )

    # ----------------------------------------------------------------- helpers
    def _write_outline(self, bp) -> None:
        lines = [f"{bp.company_profile.company_name} - {bp.input_spec.financial_year} | target ~{bp.target_pages} pages | "
                 f"{len(bp.chapters)} chapters, {len(bp.tasks)} sections", ""]
        tasks = {t.task_id: t for t in bp.tasks}
        for ch in sorted(bp.chapters, key=lambda c: c.sequence_order):
            words = sum(tasks[i].target_words for i in ch.task_ids)
            lines.append(f"{ch.sequence_order}. {ch.title}   [~{words // settings.WORDS_PER_PAGE or 1} pages, {len(ch.task_ids)} sections]")
            for i in ch.task_ids:
                t = tasks[i]
                if len(t.path) > 1:
                    lines.append(f"{'    ' * (len(t.path) - 1)}- {' > '.join(t.path[1:])}  ({t.kind}, ~{t.target_words} words)")
        text = "\n".join(lines)
        with open(os.path.join(self.run_dir, "outline.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        print("\n" + text + "\n")

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

    # ------------------------------------------------------------------ human review of other-company content
    def _entity_review(self, blueprint, state: ExecutionState) -> None:
        """After ALL sections are written (never inside the parallel workers), show the human every organisation
        that appears in the text but is not this company and not in the evidence. The human decides per NAME
        (allowed / forbidden - asked once, remembered) and per SECTION (regenerate / withdraw)."""
        co = blueprint.company_profile.company_name
        names, excluded = approvals.identity()
        names = names or [co]
        tasks = {t.task_id: t for t in blueprint.tasks}

        def flags_of(tid: str) -> list:
            out = state.task_outputs.get(tid)
            if not out or out.status != TaskStatus.COMPLETED or out.sparse:
                return []
            fl = self.validator.entity_flags.get(tid)
            if fl is None:        # section came from the draft cache: only the human's own lists can be applied
                fl = foreign_entities(out.markdown_content, names, excluded, "", approvals.entity_statuses(),
                                      check_evidence=False)
            st = approvals.entity_statuses()
            return [f for f in fl if st.get(f[1]) != "allowed"]

        flagged = {tid: flags_of(tid) for tid in tasks}
        flagged = {tid: fl for tid, fl in flagged.items() if fl}
        print(f"\n>>> REVIEW: other-company content check ({len(flagged)} of {len(tasks)} sections flagged)")
        if not flagged:
            return
        for tid, fl in flagged.items():
            print(f"  - {' > '.join(tasks[tid].path)}: " + "; ".join(f[0] for f in fl[:4]))
        if approvals.is_auto():
            print("  [auto-approve] review skipped. Re-run without --auto-approve (and --resume) to review these.")
            return

        # ---- (a) one decision per distinct name
        by_key: Dict[str, dict] = {}
        for tid, fl in flagged.items():
            for disp, key, why in fl:
                e = by_key.setdefault(key, {"name": disp, "why": why, "tids": []})
                e["tids"].append(tid)
        for key, e in by_key.items():
            if approvals.entity_statuses().get(key):
                continue
            lines = [f"Why flagged: {e['why']}", f"Appears in {len(e['tids'])} section(s):"]
            for tid in e["tids"][:3]:
                text = state.task_outputs[tid].markdown_content
                pos = text.lower().find(e["name"].lower().split()[0])
                snip = text[max(0, pos - 70): pos + len(e["name"]) + 70].replace("\n", " ") if pos >= 0 else ""
                lines.append(f"  - {' > '.join(tasks[tid].path)}: ...{snip}...")
            choice = approvals.select_one(
                f"entity:{key}", f"IS '{e['name']}' ALLOWED IN THIS REPORT OF {co.upper()}?", lines,
                [("a", "allowed - a legitimate mention (auditor, regulator, customer, competitor, parent, own subsidiary)"),
                 ("f", "forbidden - it must NOT appear in this report")], default="f")
            approvals.set_entity_status(key, "allowed" if choice == "a" else "forbidden", e["name"])

        # ---- (b) regenerate the sections that contain forbidden names
        def bad_names(tid: str) -> List[str]:
            st = approvals.entity_statuses()
            return [f[0] for f in flags_of(tid) if st.get(f[1]) == "forbidden"]

        todo = [tid for tid in tasks if bad_names(tid)]
        if not todo:
            print("  [Review] no section contains a forbidden name.")
            return
        keep = approvals.choose(
            "entity_regen:" + ",".join(sorted(todo)), "REGENERATE THE SECTIONS THAT MENTION FORBIDDEN NAMES",
            [(f"{' > '.join(tasks[tid].path)}   [{', '.join(bad_names(tid)[:3])}]", tid) for tid in todo],
            default_ids=todo, intro=["Selected sections are rewritten once with the instruction to remove those names."])
        for tid in keep:
            task = tasks[tid]
            fb = (f"Remove EVERY mention of: {', '.join(n.rstrip('.') for n in bad_names(tid))}. They are not part of {co}. Write only about "
                  f"{co} using only evidence about it; if the evidence is missing say 'Not disclosed in the sources reviewed'.")
            try:
                out = GenericTaskExecutionAgent(self.ctx).execute_task(task, feedback=fb)
                val = self.validator.validate_task_output(task, out, self.ctx.snapshot_texts())
            except QuotaExhausted as e:
                print(f"  [Quota] {e}\n  [Review] stopped; remaining sections keep their current text.")
                break
            state.task_outputs[tid], state.validation_results[tid] = out, val

        # ---- (c) what still contains a forbidden name: withdraw the text (human confirms)
        left = [tid for tid in tasks if bad_names(tid)]
        if left and approvals.confirm(
                "entity_withdraw:" + ",".join(sorted(left)), "WITHDRAW THE TEXT OF SECTIONS THAT STILL MENTION FORBIDDEN NAMES",
                [f"{' > '.join(tasks[tid].path)}   [{', '.join(bad_names(tid)[:3])}]" for tid in left]
                + ["They will be replaced by a short 'not available' notice (the draft file is overwritten)."],
                default=True, remember_no=False):
            for tid in left:
                notice = (f"{SPARSE_MARK} The draft of this section mentioned organisations that are not part of this "
                          f"report and was withdrawn after human review.*")
                out = state.task_outputs[tid]
                out.markdown_content, out.sparse, out.sources, out.word_count = notice, True, [], word_count(notice)
                with open(os.path.join(self.ctx.draft_dir, f"{tid}.md"), "w", encoding="utf-8") as f:
                    f.write(notice)
                self.ctx.record(tid, notice)
                self.validator.entity_flags[tid] = []

    def _process(self, task: TaskDefinition) -> Tuple[TaskOutput, ValidationResult]:
        agent = GenericTaskExecutionAgent(self.ctx)
        output = agent.execute_task(task)
        if agent.from_cache and output.status == TaskStatus.COMPLETED:
            return output, ValidationResult(target_id=task.task_id, is_valid=True)
        validation = self.validator.validate_task_output(task, output, self.ctx.snapshot_texts())
        retries = 0
        while (not validation.is_valid and output.status == TaskStatus.COMPLETED
               and retries < settings.MAX_VALIDATION_RETRIES):
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
                   help="OPTIONAL override: use this report instead of the agent's own source discovery. Repeatable.")
    p.add_argument("--extra", default=None, help="Extra instructions for the planner")
    p.add_argument("--workers", type=int, default=None, help="Parallel writing workers")
    p.add_argument("--low-quota", action="store_true",
                   help="Minimise LLM calls (free tiers): 1 worker, no LLM review, no revisions, larger sections")
    p.add_argument("--auto-approve", action="store_true",
                   help="Skip all human approval questions (accept the agent's own choices). Default: ask the human.")
    p.add_argument("--plan-only", action="store_true", help="Only design the structure, print the outline, then stop")
    p.add_argument("--resume", action="store_true", help="Reuse the saved plan, knowledge base and finished sections")
    p.add_argument("--first-year", action="store_true",
                   help="Generate a Maiden / First-Year Annual Report (for newly incorporated entities or entities without prior reports)")
    p.add_argument("--dossier", default=None, metavar="PATH",
                   help="Path to local company intake dossier JSON (incorporation date, capital, founders, initial financials)")
    p.add_argument("--peer-report", default=None, metavar="PATH_OR_URL",
                   help="Optional peer company annual report (PDF or URL) to benchmark industry structure and topics")
    a = p.parse_args()
    if a.low_quota:
        settings.MAX_PARALLEL_WORKERS = 1
        settings.VALIDATE_WITH_LLM = False
        settings.MAX_VALIDATION_RETRIES = 0
        settings.EXPAND_PASSES = 0
        settings.LEAF_MAX_WORDS = 1200
        settings.MAX_EXPANSION_DEPTH = 1
        settings.LLM_RETRIES = 3
        print("[Low-quota mode] 1 worker, no LLM review/revisions/expansion, sections up to 1200 words.")
    if a.workers:
        settings.MAX_PARALLEL_WORKERS = a.workers
    settings.AUTO_APPROVE = a.auto_approve
    return ReportInput(company_name=a.company, financial_year=a.fy, country=a.country, industry=a.industry,
                       reporting_scope=a.scope, extra_instructions=a.extra, target_pages=a.pages,
                       source_documents=a.pdf, is_first_year=a.first_year, dossier_path=a.dossier,
                       peer_report_path=a.peer_report), a.resume, a.plan_only


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    spec, resume, plan_only = parse_arguments()
    run_dir = os.path.abspath(os.path.join("runs", f"{slug(spec.company_name)}_{slug(spec.financial_year)}"))
    try:
        MainOrchestrator(run_dir=run_dir, resume=resume, plan_only=plan_only).run(spec)
    except approvals.ApprovalAborted as e:
        print(f"\n[Approval] Run stopped: {e}. Your earlier answers are saved; "
              "run again with --resume to continue where you left off.")
        sys.exit(1)