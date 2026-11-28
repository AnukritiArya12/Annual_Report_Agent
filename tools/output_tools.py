"""Chart generation, report assembly, and human approval tools."""

from __future__ import annotations

import json
import os
import math
from typing import List, Optional
from langchain_core.tools import BaseTool, tool

from assembler import ReportAssembler
from charts import generate_all_report_charts
from tools.session import ReportSession
from utils import slug


def build_output_tools(session: ReportSession) -> List[BaseTool]:
    """Create chart generation, report assembly, and HITL decision tools bound to `session`."""

    @tool
    def save_section_draft(slug: str, content: str) -> str:
        """Save a drafted report section in Markdown to drafts/<slug>.md and register it.

        Args:
            slug: Short lowercase filename slug without .md (e.g. '01_corporate_profile', '02_strategic_review', '03_governance', '04_financial_performance')
            content: Complete section text in Markdown starting with ## Heading and ending with *Sources: ...*
        """
        import re
        clean_slug = re.sub(r"[^a-z0-9_]+", "_", (slug or "").lower()).strip("_")
        if not clean_slug:
            clean_slug = "section"
        drafts_dir = os.path.join(session.run_dir, "drafts")
        os.makedirs(drafts_dir, exist_ok=True)
        file_path = os.path.join(drafts_dir, f"{clean_slug}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        words = len(content.split())
        session.completed_sections[clean_slug] = content.strip()
        return json.dumps({
            "status": "SAVED",
            "slug": clean_slug,
            "path": file_path,
            "word_count": words,
        }, indent=2)

    @tool
    def make_charts(include: bool = True) -> str:
        """Generate PNG financial trend charts from verified facts in the ledger.

        Charts are created ONLY when 2 or more authentic data points exist. Zero fabrication.
        """
        charts = generate_all_report_charts(session.ledger, session.storage, session.run_dir) if include else []
        return json.dumps({
            "charts_generated": len(charts),
            "charts": [
                {
                    "chart_id": c.chart_id,
                    "title": c.title,
                    "caption": c.caption,
                    "image_path": c.image_path,
                }
                for c in charts
            ],
        }, indent=2)

    @tool
    def assemble_report(
        section_files: List[str],
        report_title: str = "",
        include_charts: bool = True,
        include_facts_appendix: bool = False,
        include_company_profile: bool = True,
    ) -> str:
        """Compile drafted Markdown section files on disk into final Markdown, DOCX, and PDF reports.

        Compiles selected sections into Markdown, DOCX, and PDF. Optional profile, charts, and facts appendix are controlled by arguments. The final report is capped at 500 estimated pages.
        """
        co_slug = slug(session.company_name)
        fy_slug = slug(session.financial_year)
        base = f"{co_slug}_{fy_slug}"
        title = report_title or f"{session.company_name} - Annual Report {session.financial_year}"

        md_path = os.path.join(session.run_dir, f"{base}.md")
        docx_path = os.path.join(session.run_dir, f"{base}.docx")
        pdf_path = os.path.join(session.run_dir, f"{base}.pdf")

        lines = [
            f"# {title}",
            f"**Reporting Scope:** {session.reporting_scope}  |  **Jurisdiction:** {session.country or 'Global'}",
        ]

        if include_company_profile:
            profile_path = os.path.join(session.run_dir, "company_profile.md")
            if not os.path.exists(profile_path):
                profile_path = os.path.join(session.run_dir, "drafts", "company_profile.md")
            if os.path.exists(profile_path):
                with open(profile_path, "r", encoding="utf-8", errors="replace") as f:
                    profile = f.read().strip()
                if profile:
                    lines.extend(["\n---\n", profile])

        lines.extend(["\n---\n", "## Table of Contents"])

        drafts_dir = os.path.join(session.run_dir, "drafts")
        sections_body = []
        target_files = list(section_files) if section_files else []

        # If section_files is empty, auto-discover all drafts
        if not target_files and os.path.isdir(drafts_dir):
            target_files = sorted([
                f for f in os.listdir(drafts_dir)
                if f.endswith(".md") and not f.startswith("outline")
            ])

        for fpath in target_files:
            resolved = fpath if os.path.isabs(fpath) else os.path.join(session.run_dir, fpath)
            if not os.path.exists(resolved):
                cand = os.path.join(drafts_dir, os.path.basename(fpath))
                if os.path.exists(cand):
                    resolved = cand
                else:
                    continue
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read().strip()
            if not txt:
                continue
            first_h = next((l.lstrip("#").strip() for l in txt.splitlines() if l.startswith("#")), os.path.basename(fpath))
            lines.append(f"- {first_h}")
            sections_body.append(txt)

        # Fallback if no files matched: grab any drafts in drafts directory
        if not sections_body and os.path.isdir(drafts_dir):
            for fname in sorted(os.listdir(drafts_dir)):
                if fname.endswith(".md") and not fname.startswith("outline"):
                    fp = os.path.join(drafts_dir, fname)
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        txt = f.read().strip()
                    if txt:
                        first_h = next((l.lstrip("#").strip() for l in txt.splitlines() if l.startswith("#")), fname)
                        lines.append(f"- {first_h}")
                        sections_body.append(txt)

        charts = generate_all_report_charts(session.ledger, session.storage, session.run_dir) if include_charts else []
        if charts:
            lines.append("- Visual Analytics & Charts")
        facts = session.ledger.get_all_facts() if include_facts_appendix else []
        if facts:
            lines.append("- Canonical Facts Ledger Appendix")
        lines.append("\n---\n")

        for sec in sections_body:
            lines.append(sec)
            lines.append("\n---\n")

        if charts:
            lines.append("## Visual Analytics & Charts\n")
            for ch in charts:
                image_name = os.path.basename(ch.image_path)
                lines.append(f"### {ch.title}\n")
                if os.path.exists(ch.image_path):
                    lines.append(f"![{ch.title}]({image_name})\n")
                lines.append(f"*{ch.caption}*\n")
                if getattr(ch, "source_line", ""):
                    lines.append(f"*{ch.source_line}*\n")

        if facts:
            lines.append("## Canonical Facts Ledger Appendix\n")
            lines.append("| Metric | Value | Period | Source | Status |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for f in facts:
                st = f.status.value if hasattr(f.status, "value") else str(f.status)
                lines.append(f"| {f.key} | {f.value} | {f.period} | {f.source_doc_id} | {st} |")
            lines.append("")

        # Early text-only estimate; actual PDF pages are checked after export.
        from config import settings
        report_text = "\n".join(lines + sections_body)
        words = len(report_text.split())
        estimated_pages = max(1, math.ceil(words / max(1, settings.WORDS_PER_PAGE)))
        max_pages = min(500, max(1, int(settings.MAX_PAGES)))

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        assembler = ReportAssembler()
        out_files = {"markdown": md_path}
        try:
            assembler.export_docx(md_path, docx_path, title)
            out_files["docx"] = docx_path
        except Exception as e:
            out_files["docx_error"] = str(e)
        try:
            assembler.export_pdf(md_path, pdf_path, title)
            from knowledge import parse_pdf
            _, _, actual_pages = parse_pdf(pdf_path)
            if actual_pages > max_pages:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
                out_files["pdf_error"] = (
                    f"Rendered report is {actual_pages} pages; maximum is {max_pages}. "
                    "Reduce nonessential repetition/detail while preserving material coverage, then assemble again."
                )
            else:
                out_files["pdf"] = pdf_path
                out_files["actual_pages"] = actual_pages
        except Exception as e:
            out_files["pdf_error"] = str(e)

        page_limit_exceeded = "actual_pages" not in out_files and "maximum is" in out_files.get("pdf_error", "")
        final_status = "PAGE_LIMIT_EXCEEDED" if page_limit_exceeded else "ASSEMBLED"
        session.update_manifest("report_assembled", {
            "status": final_status,
            "sections_included": len(sections_body),
            "estimated_pages": estimated_pages,
            "files": out_files,
        })

        return json.dumps({
            "status": final_status,
            "sections_included": len(sections_body),
            "estimated_pages": estimated_pages,
            "files": out_files,
        }, indent=2)

    @tool
    def request_human_decision(
        decision_key: str,
        question: str,
        options: Optional[List[str]] = None,
        approve_domain: str = "",
        reject_domain: str = "",
        reject_source: str = "",
        approve_names: Optional[List[str]] = None,
        exclude_names: Optional[List[str]] = None,
        modified_scope: str = "",
    ) -> str:
        """Ask the human for approval on a risky decision (identity, source domain, outline, flagged section, or publish).

        Persists approvals, rejections, and scope modifications in session state and run manifest.
        When executed under DeepAgents interrupt_on, execution pauses before this tool runs for human review.
        """
        # If already persisted in session, return cached decision without re-asking
        if decision_key in session.human_decisions:
            cached = session.human_decisions[decision_key]
            return json.dumps({
                "decision_key": decision_key,
                "status": "CACHED_DECISION",
                "decision": cached,
            }, indent=2)

        if approve_domain:
            session.approve_domain(approve_domain)
        if reject_domain:
            session.reject_domain(reject_domain)
        if reject_source:
            session.reject_source(reject_source)
        if approve_names:
            for n in approve_names:
                if n and n not in session.approved_names:
                    session.approved_names.append(n)
        if exclude_names:
            for n in exclude_names:
                if n and n not in session.excluded_names:
                    session.excluded_names.append(n)
        if modified_scope:
            session.reporting_scope = modified_scope

        is_approved = not bool(reject_domain or reject_source)
        decision_record = {
            "approved": is_approved,
            "question": question,
            "options": options or ["approve", "reject", "modify"],
            "approved_domain": approve_domain,
            "rejected_domain": reject_domain,
            "rejected_source": reject_source,
            "modified_scope": modified_scope,
        }
        session.human_decisions[decision_key] = decision_record
        session.save_decisions()
        session.update_manifest("human_decision", {
            "key": decision_key,
            "approved": is_approved,
            "approved_domain": approve_domain,
            "rejected_domain": reject_domain,
        })

        return json.dumps({
            "decision_key": decision_key,
            "status": "APPROVED" if is_approved else "REJECTED",
            "decision": decision_record,
        }, indent=2)

    return [save_section_draft, make_charts, assemble_report, request_human_decision]
