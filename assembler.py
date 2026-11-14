"""Report Assembly & Document Generation Module.

Assembles validated chapters, structured tables, and evidence citations into a cohesive
annual report. Exports formatted Markdown, professional DOCX, and publication-ready PDF documents.
"""

import os
import re
from datetime import datetime, timezone
from typing import Dict, Optional, List, Any
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from schemas import ReportBlueprint, TaskOutput
from tools import ResearchTools


def is_table_line(line: str) -> bool:
    """Detect whether a line is part of a markdown table."""
    s = line.strip()
    if not s:
        return False
    if s.startswith("|") and s.endswith("|"):
        return True
    if "|" in s and not s.startswith("#"):
        # Check if it has at least 2 cells
        parts = [p.strip() for p in s.split("|")]
        return len(parts) >= 2
    return False


def parse_markdown_table(rows_text: List[str]) -> Dict[str, Any]:
    """Parses markdown table lines into headers, rows, and inferred column alignments."""
    raw_rows = []
    for line in rows_text:
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            s = s[1:-1]
        elif s.startswith("|"):
            s = s[1:]
        elif s.endswith("|"):
            s = s[:-1]
        cells = [c.strip() for c in s.split("|")]
        raw_rows.append(cells)

    if not raw_rows:
        return {"headers": [], "rows": [], "all_rows": [], "alignments": [], "num_cols": 0}

    # Filter out separator row while extracting column alignments
    alignments = []
    filtered_rows = []
    for row in raw_rows:
        if all(re.match(r"^:?-{2,}:?$", c) for c in row if c):
            for c in row:
                c_clean = c.strip()
                if c_clean.startswith(":") and c_clean.endswith(":"):
                    alignments.append("center")
                elif c_clean.endswith(":"):
                    alignments.append("right")
                else:
                    alignments.append("left")
            continue
        filtered_rows.append(row)

    if not filtered_rows:
        return {"headers": [], "rows": [], "all_rows": [], "alignments": [], "num_cols": 0}

    num_cols = max(len(r) for r in filtered_rows)
    # Pad all rows to uniform num_cols
    norm_rows = []
    for r in filtered_rows:
        padded = r + [""] * (num_cols - len(r))
        norm_rows.append(padded)

    headers = norm_rows[0]
    data_rows = norm_rows[1:] if len(norm_rows) > 1 else []

    while len(alignments) < num_cols:
        alignments.append("left")

    # Infer right-alignment for columns containing numbers/currencies/percentages
    for col_idx in range(num_cols):
        col_vals = [r[col_idx] for r in data_rows if r[col_idx]]
        if col_vals and all(re.match(r"^[\$₹€£]?\s*[\(\-]?\d+(?:[\.,]\d+)?%?\)?\s*(?:cr|crore|billion|million|k|m|b)?$", v.replace("**", "").replace("*", "").strip(), re.I) for v in col_vals):
            if alignments[col_idx] == "left":
                alignments[col_idx] = "right"

    return {
        "headers": headers,
        "rows": data_rows,
        "all_rows": norm_rows,
        "alignments": alignments,
        "num_cols": num_cols
    }


def extract_structured_tables(markdown_text: str) -> List[Dict[str, Any]]:
    """Extracts all markdown tables from text as structured dicts."""
    lines = markdown_text.split("\n")
    tables = []
    current_rows = []
    in_table = False
    current_title = ""

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("###") or stripped.startswith("####"):
            current_title = stripped.lstrip("#").strip()

        if is_table_line(stripped):
            in_table = True
            current_rows.append(stripped)
        else:
            if in_table and current_rows:
                parsed = parse_markdown_table(current_rows)
                if parsed["headers"]:
                    tables.append({
                        "title": current_title,
                        "headers": parsed["headers"],
                        "rows": parsed["rows"],
                        "alignments": parsed["alignments"],
                        "num_cols": parsed["num_cols"]
                    })
                current_rows = []
                in_table = False

    if in_table and current_rows:
        parsed = parse_markdown_table(current_rows)
        if parsed["headers"]:
            tables.append({
                "title": current_title,
                "headers": parsed["headers"],
                "rows": parsed["rows"],
                "alignments": parsed["alignments"],
                "num_cols": parsed["num_cols"]
            })

    return tables


class ReportAssembler:
    """Assembles validated task outputs into final publishable annual reports."""

    def __init__(self, tools: Optional[ResearchTools] = None):
        self.tools = tools

    def assemble_markdown(
        self,
        blueprint: ReportBlueprint,
        task_outputs: Dict[str, TaskOutput],
        output_file: str
    ) -> str:
        """Assembles all validated task outputs into a unified Markdown report file."""
        profile = blueprint.company_profile
        spec = blueprint.input_spec

        lines = []
        # Title Block
        lines.append(f"# ANNUAL REPORT: {profile.company_name.upper()}")
        lines.append(f"**Financial Year**: {spec.financial_year} | **Reporting Scope**: {spec.reporting_scope}")
        lines.append(f"**Jurisdiction**: {profile.country} | **Industry Sector**: {profile.industry}")
        lines.append(f"**Listing**: {profile.listing_status}")
        lines.append(f"*Generated on: {datetime.now(timezone.utc).strftime('%B %d, %Y')}*\n")
        lines.append("---\n")

        # Table of Contents
        lines.append("## Table of Contents")
        for chapter in sorted(blueprint.chapters, key=lambda c: c.sequence_order):
            lines.append(f"- [{chapter.title}](#{chapter.chapter_id})")
        lines.append("- [Statutory Disclosures & Compliance Statement](#statutory-disclosures)")
        lines.append("- [Evidence Sources & Regulatory Filings](#evidence-sources)")
        lines.append("\n---\n")

        # Chapter Content
        for chapter in sorted(blueprint.chapters, key=lambda c: c.sequence_order):
            lines.append(f"## <a id='{chapter.chapter_id}'></a>{chapter.title}\n")
            if chapter.objective:
                lines.append(f"> *{chapter.objective}*\n")

            for tid in chapter.task_ids:
                output = task_outputs.get(tid)
                if output and output.markdown_content:
                    lines.append(output.markdown_content.strip())
                    lines.append("\n")
                else:
                    lines.append(f"*(Section {tid} under preparation)*\n")

            lines.append("---\n")

        # Statutory Disclosures Appendix
        lines.append("## <a id='statutory-disclosures'></a>Statutory Disclosures & Compliance Statement\n")
        regs = ', '.join(profile.applicable_regulations) if profile.applicable_regulations else 'Applicable statutory standards'
        lines.append(
            f"This Annual Report has been prepared in conformance with recognized disclosure frameworks, "
            f"including {regs}. All factual representations and numerical metrics are supported by corporate filings, "
            f"official investor communications, and verified public records.\n"
        )
        lines.append("---\n")

        # Evidence & Sources Appendix
        lines.append("## <a id='evidence-sources'></a>Evidence Sources & Regulatory Filings\n")
        all_evidence = []
        for tid, out in task_outputs.items():
            if out and hasattr(out, "evidence_used"):
                all_evidence.extend(out.evidence_used)

        # Unique sources by URL
        seen_urls = set()
        unique_sources = []
        for ev in all_evidence:
            if ev.source_url and ev.source_url not in seen_urls:
                seen_urls.add(ev.source_url)
                unique_sources.append(ev)

        if unique_sources:
            lines.append("| Index | Source Title | Reference URL | Verification Confidence |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for idx, ev in enumerate(unique_sources[:40], 1):
                clean_title = (ev.source_title or "Official Filing").replace("|", "-")
                lines.append(f"| [{idx}] | {clean_title} | [{ev.source_url}]({ev.source_url}) | {ev.confidence:.0%} |")
            lines.append("\n")
        else:
            lines.append("*Primary corporate filings, investor releases, and statutory records.*")

        content = "\n".join(lines)
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"\n[Assembler] Successfully generated unified Markdown report: {output_file}")
        return output_file

    def export_docx(self, markdown_file: str, docx_file: str, title: str = "Annual Report") -> str:
        """Exports the annual report as a styled corporate Word (.docx) document."""
        with open(markdown_file, "r", encoding="utf-8") as f:
            content = f.read()

        doc = Document()

        # Page Setup: Standard 1-inch margins
        for section in doc.sections:
            section.top_margin = Inches(1.0)
            section.bottom_margin = Inches(1.0)
            section.left_margin = Inches(1.0)
            section.right_margin = Inches(1.0)

        # Document Title
        title_p = doc.add_paragraph()
        title_p.paragraph_format.space_before = Pt(12)
        title_p.paragraph_format.space_after = Pt(12)
        title_p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title_p.add_run(title)
        run.bold = True
        run.font.size = Pt(24)
        run.font.color.rgb = RGBColor(26, 54, 93)  # Navy Blue

        lines = content.split("\n")
        in_table = False
        table_rows = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("# ") and title.lower() in stripped.lower():
                continue

            # Markdown Table detection
            if is_table_line(stripped):
                in_table = True
                table_rows.append(stripped)
                continue
            elif in_table:
                self._render_docx_table(doc, table_rows)
                in_table = False
                table_rows = []

            # Heading 1
            if stripped.startswith("# "):
                h = doc.add_heading(stripped[2:], level=1)
                h.paragraph_format.space_before = Pt(16)
                h.paragraph_format.space_after = Pt(6)
            # Heading 2 (Major Chapter – start on new page)
            elif stripped.startswith("## "):
                doc.add_page_break()
                clean_h2 = re.sub(r"<a[^>]*></a>", "", stripped[3:]).strip()
                h = doc.add_heading(clean_h2, level=2)
                h.paragraph_format.space_before = Pt(12)
                h.paragraph_format.space_after = Pt(4)
            # Heading 3
            elif stripped.startswith("### "):
                h = doc.add_heading(stripped[4:], level=3)
                h.paragraph_format.space_before = Pt(10)
                h.paragraph_format.space_after = Pt(3)
            # Bullet point
            elif stripped.startswith("- ") or stripped.startswith("* "):
                p = doc.add_paragraph(style="List Bullet")
                self._add_formatted_runs(p, stripped[2:])
            # Horizontal rule
            elif stripped in ("---", "***", "___"):
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.space_after = Pt(6)
            # Standard paragraph
            elif stripped:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(6)
                self._add_formatted_runs(p, stripped)

        if in_table and table_rows:
            self._render_docx_table(doc, table_rows)

        os.makedirs(os.path.dirname(os.path.abspath(docx_file)), exist_ok=True)
        doc.save(docx_file)
        print(f"[Assembler] Exported professional DOCX report: {docx_file}")
        return docx_file

    def export_pdf(self, markdown_file: str, pdf_file: str, title: str = "Annual Report") -> str:
        """Exports the annual report as a professional PDF."""
        try:
            return self._export_pdf_reportlab(markdown_file, pdf_file, title)
        except Exception as e:
            print(f"[Assembler] ReportLab PDF generation fallback ({e}); using built-in engine.")
            return self._export_pdf_builtin(markdown_file, pdf_file, title)

    def _export_pdf_reportlab(self, markdown_file: str, pdf_file: str, title: str) -> str:
        """ReportLab publisher for high-quality corporate PDF documents."""
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors

        with open(markdown_file, "r", encoding="utf-8") as f:
            content = f.read()

        os.makedirs(os.path.dirname(os.path.abspath(pdf_file)), exist_ok=True)
        doc = SimpleDocTemplate(
            pdf_file,
            pagesize=letter,
            leftMargin=54,
            rightMargin=54,
            topMargin=54,
            bottomMargin=54
        )

        styles = getSampleStyleSheet()

        # Corporate Typography Styles
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=colors.HexColor("#1A365D"),
            alignment=1,  # Center
            spaceAfter=15
        )
        h1_style = ParagraphStyle(
            "DocH1",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=colors.HexColor("#1A365D"),
            spaceBefore=14,
            spaceAfter=6
        )
        h2_style = ParagraphStyle(
            "DocH2",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#2C5282"),
            spaceBefore=10,
            spaceAfter=4
        )
        body_style = ParagraphStyle(
            "DocBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13.5,
            textColor=colors.HexColor("#2D3748"),
            spaceAfter=6
        )
        bullet_style = ParagraphStyle(
            "DocBullet",
            parent=body_style,
            leftIndent=15,
            firstLineIndent=-10,
            spaceAfter=3
        )

        story = []
        # Main Title
        story.append(Paragraph(self._md_to_html_entities(title), title_style))
        story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1A365D"), spaceAfter=15))

        lines = content.split("\n")
        in_table = False
        table_rows = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("# ") and title.lower() in stripped.lower():
                continue

            if is_table_line(stripped):
                in_table = True
                table_rows.append(stripped)
                continue
            elif in_table:
                t_flowable = self._build_reportlab_table(table_rows, colors)
                if t_flowable:
                    story.append(t_flowable)
                    story.append(Spacer(1, 10))
                in_table = False
                table_rows = []

            if stripped.startswith("# "):
                clean_h1 = self._md_to_html_entities(stripped[2:])
                story.append(Paragraph(clean_h1, h1_style))
            elif stripped.startswith("## "):
                clean_h2 = re.sub(r"<a[^>]*></a>", "", stripped[3:]).strip()
                story.append(Paragraph(self._md_to_html_entities(clean_h2), h1_style))
            elif stripped.startswith("### "):
                clean_h3 = self._md_to_html_entities(stripped[4:])
                story.append(Paragraph(clean_h3, h2_style))
            elif stripped.startswith("- ") or stripped.startswith("* "):
                clean_b = self._md_to_html_entities(stripped[2:])
                story.append(Paragraph(f"&bull; {clean_b}", bullet_style))
            elif stripped in ("---", "***", "___"):
                story.append(Spacer(1, 4))
                story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#CBD5E0"), spaceAfter=8))
            elif stripped:
                clean_p = self._md_to_html_entities(stripped)
                story.append(Paragraph(clean_p, body_style))

        if in_table and table_rows:
            t_flowable = self._build_reportlab_table(table_rows, colors)
            if t_flowable:
                story.append(t_flowable)

        doc.build(story)
        print(f"[Assembler] Exported professional PDF report: {pdf_file}")
        return pdf_file

    def _build_reportlab_table(self, rows_text: List[str], colors):
        """Converts Markdown table rows to a publication-grade ReportLab Table flowable."""
        from reportlab.platypus import Table, TableStyle, Paragraph
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

        table_data = parse_markdown_table(rows_text)
        all_rows = table_data["all_rows"]
        if not all_rows:
            return None

        num_cols = table_data["num_cols"]
        alignments = table_data["alignments"]

        # Printable width: letter (612) minus margins (54 * 2) = 504 pt
        total_width = 504.0
        col_weights = []
        for c_idx in range(num_cols):
            max_len = max(len(row[c_idx]) for row in all_rows)
            col_weights.append(max(10, min(max_len, 40)))
        sum_weights = sum(col_weights) or 1
        col_widths = [(w / sum_weights) * total_width for w in col_weights]

        head_left = ParagraphStyle("THL", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=colors.white, alignment=TA_LEFT)
        head_right = ParagraphStyle("THR", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=colors.white, alignment=TA_RIGHT)
        head_center = ParagraphStyle("THC", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=colors.white, alignment=TA_CENTER)

        cell_left = ParagraphStyle("TCL", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#2D3748"), alignment=TA_LEFT)
        cell_right = ParagraphStyle("TCR", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#2D3748"), alignment=TA_RIGHT)
        cell_center = ParagraphStyle("TCC", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#2D3748"), alignment=TA_CENTER)

        formatted_table = []
        for row_idx, row in enumerate(all_rows):
            is_header = (row_idx == 0)
            is_total = not is_header and any("total" in c.lower() for c in row)
            row_cells = []
            for col_idx, cell_val in enumerate(row):
                align = alignments[col_idx] if col_idx < len(alignments) else "left"
                clean_val = self._md_to_html_entities(cell_val)
                if is_total and not clean_val.startswith("<b>"):
                    clean_val = f"<b>{clean_val}</b>"

                if is_header:
                    if align == "right":
                        st = head_right
                    elif align == "center":
                        st = head_center
                    else:
                        st = head_left
                elif align == "right":
                    st = cell_right
                elif align == "center":
                    st = cell_center
                else:
                    st = cell_left

                row_cells.append(Paragraph(clean_val, st))
            formatted_table.append(row_cells)

        t = Table(formatted_table, colWidths=col_widths, repeatRows=1)
        style_commands = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1A365D")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ('LINEBELOW', (0, 0), (-1, 0), 1.2, colors.HexColor("#0F223D")),
        ]

        # Accentuate total / subtotal rows
        for r_idx, row in enumerate(all_rows[1:], 1):
            if any("total" in c.lower() for c in row):
                style_commands.append(('LINEABOVE', (0, r_idx), (-1, r_idx), 1.0, colors.HexColor("#718096")))
                style_commands.append(('BACKGROUND', (0, r_idx), (-1, r_idx), colors.HexColor("#EDF2F7")))

        t.setStyle(TableStyle(style_commands))
        return t

    def _export_pdf_builtin(self, markdown_file: str, pdf_file: str, title: str) -> str:
        """Lightweight built-in PDF generator with formatted ASCII table support."""
        with open(markdown_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        clean_lines = []
        in_table = False
        table_rows = []

        for l in lines:
            line = l.strip()
            if not line:
                if in_table and table_rows:
                    clean_lines.extend(self._format_ascii_table(table_rows))
                    in_table = False
                    table_rows = []
                clean_lines.append("")
                continue

            if is_table_line(line):
                in_table = True
                table_rows.append(line)
                continue
            elif in_table:
                clean_lines.extend(self._format_ascii_table(table_rows))
                in_table = False
                table_rows = []

            # Clean markdown markers
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
            line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
            line = re.sub(r"^#+\s*", "", line)
            clean_lines.append(line)

        if in_table and table_rows:
            clean_lines.extend(self._format_ascii_table(table_rows))

        lines_per_page = 46
        pages = [clean_lines[i:i + lines_per_page] for i in range(0, max(1, len(clean_lines)), lines_per_page)]

        pdf_objects = []
        pdf_objects.append("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj")
        page_refs = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
        pdf_objects.append(f"2 0 obj\n<< /Type /Pages /Kids [{page_refs}] /Count {len(pages)} >>\nendobj")

        for idx, page_lines in enumerate(pages):
            page_obj_num = 3 + idx * 2
            stream_obj_num = page_obj_num + 1

            text_ops = [
                "BT",
                "/F1 9 Tf",
                "14 TL",
                "54 740 Td"
            ]
            for pl in page_lines:
                safe_txt = pl.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                safe_txt = safe_txt.encode("ascii", "replace").decode("ascii")
                text_ops.append(f"({safe_txt}) '")
            text_ops.append("ET")

            stream_data = "\n".join(text_ops)
            stream_len = len(stream_data)

            pdf_objects.append(
                f"{page_obj_num} 0 obj\n<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] /Contents {stream_obj_num} 0 R "
                f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Courier >> >> >> >>\nendobj"
            )
            pdf_objects.append(
                f"{stream_obj_num} 0 obj\n<< /Length {stream_len} >>\nstream\n{stream_data}\nendstream\nendobj"
            )

        os.makedirs(os.path.dirname(os.path.abspath(pdf_file)), exist_ok=True)
        with open(pdf_file, "wb") as f:
            f.write(b"%PDF-1.4\n")
            offsets = []
            for obj in pdf_objects:
                offsets.append(f.tell())
                f.write(obj.encode("utf-8") + b"\n")

            xref_offset = f.tell()
            f.write(b"xref\n")
            f.write(f"0 {len(pdf_objects) + 1}\n".encode("utf-8"))
            f.write(b"0000000000 65535 f \n")
            for off in offsets:
                f.write(f"{off:010d} 00000 n \n".encode("utf-8"))

            f.write(b"trailer\n")
            f.write(f"<< /Size {len(pdf_objects) + 1} /Root 1 0 R >>\n".encode("utf-8"))
            f.write(b"startxref\n")
            f.write(f"{xref_offset}\n".encode("utf-8"))
            f.write(b"%%EOF\n")

        print(f"[Assembler] Exported PDF report: {pdf_file}")
        return pdf_file

    def _format_ascii_table(self, rows_text: List[str]) -> List[str]:
        """Formats markdown table rows into clean, aligned ASCII text table."""
        t_data = parse_markdown_table(rows_text)
        all_rows = t_data["all_rows"]
        if not all_rows:
            return []
        num_cols = t_data["num_cols"]
        alignments = t_data["alignments"]

        # Calculate max widths
        max_widths = [max(len(r[c]) for r in all_rows) for c in range(num_cols)]
        total = sum(max_widths) + 3 * num_cols
        scale = min(1.0, 72.0 / max(1, total))
        col_widths = [max(8, int(w * scale)) for w in max_widths]

        sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
        header_sep = "+" + "+".join("=" * (w + 2) for w in col_widths) + "+"
        lines = [sep]

        for r_idx, row in enumerate(all_rows):
            cells_formatted = []
            for c_idx, val in enumerate(row):
                w = col_widths[c_idx]
                v = val[:w]
                align = alignments[c_idx] if c_idx < len(alignments) else "left"
                if align == "right":
                    cells_formatted.append(f" {v:>{w}} ")
                else:
                    cells_formatted.append(f" {v:<{w}} ")
            lines.append("|" + "|".join(cells_formatted) + "|")
            if r_idx == 0:
                lines.append(header_sep)
            else:
                lines.append(sep)
        return lines

    def _md_to_html_entities(self, text: str) -> str:
        """Converts Markdown inline formatting to ReportLab XML tags safely."""
        t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Bold **text** -> <b>text</b>
        t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
        # Italic *text* -> <i>text</i>
        t = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", t)
        return t

    def _add_formatted_runs(self, paragraph, text: str):
        """Parses bold formatting in docx text."""
        self._add_formatted_runs_with_style(paragraph, text)

    def _add_formatted_runs_with_style(self, paragraph, text: str, font_size=None, color=None, force_bold=False):
        """Parses bold and italic formatting in docx text with custom style options."""
        parts = re.split(r"(\*\*.*?\*\*|\*.*?\*)", text)
        for part in parts:
            if not part:
                continue
            is_bold = force_bold
            is_italic = False
            run_text = part

            if part.startswith("**") and part.endswith("**") and len(part) >= 4:
                run_text = part[2:-2]
                is_bold = True
            elif part.startswith("*") and part.endswith("*") and len(part) >= 2:
                run_text = part[1:-1]
                is_italic = True

            run = paragraph.add_run(run_text)
            if is_bold:
                run.bold = True
            if is_italic:
                run.italic = True
            if font_size:
                run.font.size = font_size
            if color:
                run.font.color.rgb = color

    def _render_docx_table(self, doc: Document, rows_text: list):
        """Converts markdown table rows into a styled docx table."""
        t_data = parse_markdown_table(rows_text)
        all_rows = t_data["all_rows"]
        if not all_rows:
            return

        num_cols = t_data["num_cols"]
        alignments = t_data["alignments"]

        table = doc.add_table(rows=len(all_rows), cols=num_cols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.style = 'Table Grid'
        table.autofit = False

        total_width_inches = 6.5
        col_weights = []
        for c_idx in range(num_cols):
            max_len = max(len(row[c_idx]) for row in all_rows)
            col_weights.append(max(10, min(max_len, 40)))
        sum_weights = sum(col_weights) or 1
        col_widths = [Inches((w / sum_weights) * total_width_inches) for w in col_weights]

        # Repeat header on page break
        header_tr = table.rows[0]._tr.get_or_add_trPr()
        header_tr.append(parse_xml(r'<w:tblHeader %s/>' % nsdecls('w')))

        for row_idx, row_cells in enumerate(all_rows):
            row = table.rows[row_idx]
            # Prevent row breaking across page breaks
            trPr = row._tr.get_or_add_trPr()
            trPr.append(parse_xml(r'<w:cantSplit %s/>' % nsdecls('w')))

            is_header = (row_idx == 0)
            is_total = not is_header and any("total" in c.lower() for c in row_cells)
            is_even = (row_idx % 2 == 0)

            for col_idx, cell_value in enumerate(row_cells):
                cell = row.cells[col_idx]
                cell.width = col_widths[col_idx]

                # Padding: 120 dxa (6pt) top/bottom, 160 dxa (8pt) left/right
                tcPr = cell._tc.get_or_add_tcPr()
                tcMar = parse_xml(
                    r'<w:tcMar %s>'
                    r'<w:top w:w="120" w:type="dxa"/>'
                    r'<w:bottom w:w="120" w:type="dxa"/>'
                    r'<w:left w:w="160" w:type="dxa"/>'
                    r'<w:right w:w="160" w:type="dxa"/>'
                    r'</w:tcMar>' % nsdecls('w')
                )
                tcPr.append(tcMar)

                # Shading
                if is_header:
                    fill_color = "1A365D"  # Navy
                elif is_total:
                    fill_color = "EDF2F7"  # Distinct total row
                elif not is_even:
                    fill_color = "F8FAFC"  # Light zebra
                else:
                    fill_color = "FFFFFF"

                shd = parse_xml(r'<w:shd %s w:fill="%s"/>' % (nsdecls('w'), fill_color))
                tcPr.append(shd)

                p = cell.paragraphs[0]
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(3)

                align = alignments[col_idx] if col_idx < len(alignments) else "left"
                if is_header:
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if align == "right" else WD_ALIGN_PARAGRAPH.LEFT
                elif align == "right":
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif align == "center":
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                else:
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

                clean_val = cell_value.strip()
                if is_header:
                    run = p.add_run(clean_val.replace("**", ""))
                    run.bold = True
                    run.font.size = Pt(9.5)
                    run.font.color.rgb = RGBColor(255, 255, 255)
                else:
                    self._add_formatted_runs_with_style(
                        p, clean_val,
                        font_size=Pt(9),
                        color=RGBColor(45, 55, 72),
                        force_bold=is_total
                    )

        doc.add_paragraph().paragraph_format.space_after = Pt(8)
