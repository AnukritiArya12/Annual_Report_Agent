"""Chart generation, report assembly, and human approval tools."""

from __future__ import annotations

import json
import os
from typing import List, Optional
from langchain_core.tools import BaseTool, tool

from assembler import ReportAssembler
from charts import generate_all_report_charts
from tools.session import ReportSession
from utils import slug


def build_output_tools(session: ReportSession) -> List[BaseTool]:
    """Create chart generation, report assembly, and HITL decision tools bound to `session`."""

    @tool
    def make_charts() -> str:
        """Generate PNG financial trend charts from verified facts in the ledger.

        Charts are created ONLY when 2 or more authentic data points exist. Zero fabrication.
        """
        charts = generate_all_report_charts(session.ledger, session.storage, session.run_dir)
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
    def assemble_report(section_files: List[str], report_title: str = "") -> str:
        """Compile drafted Markdown section files on disk into final Markdown, DOCX, and PDF reports.

        Includes a Table of Contents, embedded charts, and a Canonical Facts Ledger audit appendix.
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
            "\n---\n",
            "## Table of Contents",
        ]

        sections_body = []
        for fpath in section_files:
            resolved = fpath if os.path.isabs(fpath) else os.path.join(session.run_dir, fpath)
            if not os.path.exists(resolved):
                continue
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read().strip()
            if not txt:
                continue
            first_h = next((l.lstrip("#").strip() for l in txt.splitlines() if l.startswith("#")), os.path.basename(fpath))
            lines.append(f"- {first_h}")
            sections_body.append(txt)

        charts = generate_all_report_charts(session.ledger, session.storage, session.run_dir)
        if charts:
            lines.append("- Visual Analytics & Charts")
        facts = session.ledger.get_all_facts()
        if facts:
            lines.append("- Canonical Facts Ledger Appendix")
        lines.append("\n---\n")

        for sec in sections_body:
            lines.append(sec)
            lines.append("\n---\n")

        if charts:
            lines.append("## Visual Analytics & Charts\n")
            for ch in charts:
                lines.append(f"### {ch.title}\n")
                if os.path.exists(ch.image_path):
                    lines.append(f"![{ch.title}]({ch.image_path})\n")
                lines.append(f"*{ch.caption}*\n")

        if facts:
            lines.append("## Canonical Facts Ledger Appendix\n")
            lines.append("| Metric | Value | Period | Source | Status |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for f in facts:
                st = f.status.value if hasattr(f.status, "value") else str(f.status)
                lines.append(f"| {f.key} | {f.value} | {f.period} | {f.source_doc_id} | {st} |")
            lines.append("")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        assembler = ReportAssembler(tools=session.research_tools)
        out_files = {"markdown": md_path}
        try:
            assembler.export_docx(md_path, docx_path, title)
            out_files["docx"] = docx_path
        except Exception as e:
            out_files["docx_error"] = str(e)
        try:
            assembler.export_pdf(md_path, pdf_path, title)
            out_files["pdf"] = pdf_path
        except Exception as e:
            out_files["pdf_error"] = str(e)

        session.update_manifest("report_assembled", {
            "status": "COMPLETED",
            "sections_included": len(sections_body),
            "files": out_files,
        })

        return json.dumps({
            "status": "ASSEMBLED",
            "sections_included": len(sections_body),
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

    return [make_charts, assemble_report, request_human_decision]
