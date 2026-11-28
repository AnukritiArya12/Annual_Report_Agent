"""Report assembly and export (Markdown -> DOCX and PDF).

Headings follow the planner's tree (chapter > section > sub-section), so the document structure is
whatever the planner decided for this company. Both exporters build a real table of contents with
page numbers (PDF) or a Word TOC field (DOCX).
"""

from __future__ import annotations

import os
import re
from typing import Any, List, Tuple



# ------------------------------------------------------------------------------
# Markdown block parser (shared by DOCX and PDF exporters)
# ------------------------------------------------------------------------------
def _split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_markdown_blocks(md: str) -> List[Tuple]:
    """Returns blocks: ('h', level, text) ('p', text) ('ul', [items]) ('ol', [items])
    ('table', rows, aligns) ('hr',) ('quote', text)."""
    lines = md.splitlines()
    blocks: List[Tuple] = []
    i, para = 0, []

    def flush():
        nonlocal para
        if para:
            blocks.append(("p", " ".join(s.strip() for s in para)))
            para = []

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            flush(); i += 1; continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush(); blocks.append(("h", len(m.group(1)), m.group(2).strip())); i += 1; continue
        m_img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", s)
        if m_img:
            flush(); blocks.append(("image", m_img.group(1).strip(), m_img.group(2).strip())); i += 1; continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush(); blocks.append(("hr",)); i += 1; continue
        if s.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i]); i += 1
            parsed, aligns = [], []
            for r in rows:
                cells = _split_row(r)
                if cells and all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
                    aligns = ["right" if c.endswith(":") and not c.startswith(":") else
                              "center" if c.startswith(":") and c.endswith(":") else "left" for c in cells]
                    continue
                parsed.append(cells)
            if parsed:
                n = max(len(r) for r in parsed)
                parsed = [r + [""] * (n - len(r)) for r in parsed]
                aligns = (aligns + ["left"] * n)[:n]
                blocks.append(("table", parsed, aligns))
            continue
        if re.match(r"^[-*+]\s+", s):
            flush(); items = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]).strip()); i += 1
            blocks.append(("ul", items)); continue
        if re.match(r"^\d+[\.\)]\s+", s):
            flush(); items = []
            while i < len(lines) and re.match(r"^\s*\d+[\.\)]\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+[\.\)]\s+", "", lines[i]).strip()); i += 1
            blocks.append(("ol", items)); continue
        if s.startswith(">"):
            flush(); blocks.append(("quote", s.lstrip("> ").strip())); i += 1; continue
        para.append(ln); i += 1
    flush()
    return blocks


def _inline_runs(text: str) -> List[Tuple[str, bool, bool]]:
    """Split inline markdown into (text, bold, italic) runs. Links keep their text only."""
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)
    text = text.replace("`", "")
    runs: List[Tuple[str, bool, bool]] = []
    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            runs.append((part[2:-2], True, False))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            runs.append((part[1:-1], False, True))
        else:
            runs.append((part, False, False))
    return runs


def _replace_toc_with_marker(blocks: List[Tuple]) -> List[Tuple]:
    """Replace the static 'Table of Contents' list in the markdown with a ('toc',) marker."""
    for idx, b in enumerate(blocks):
        if b[0] == "h" and b[2].strip().lower() == "table of contents":
            end = idx + 1
            while end < len(blocks) and blocks[end][0] != "hr":
                end += 1
            return blocks[:idx] + [("toc",)] + blocks[end + 1:]
    return blocks


# ------------------------------------------------------------------------------
class ReportAssembler:
    """Exports a finished Markdown report to DOCX and PDF."""

    # -------------------------------------------------------------------- DOCX
    def export_docx(self, markdown_file: str, docx_file: str, title: str) -> str:
        from docx import Document
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement, parse_xml
        from docx.oxml.ns import nsdecls, qn
        from docx.shared import Inches, Pt, RGBColor

        with open(markdown_file, "r", encoding="utf-8") as f:
            blocks = _replace_toc_with_marker(parse_markdown_blocks(f.read()))

        doc = Document()
        sec = doc.sections[0]
        sec.left_margin = sec.right_margin = Inches(1)
        sec.top_margin = sec.bottom_margin = Inches(0.9)
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(10.5)
        for lvl, size in ((1, 20), (2, 15), (3, 12.5)):
            st = doc.styles[f"Heading {lvl}"]
            st.font.name = "Calibri"
            st.font.size = Pt(size)
            st.font.color.rgb = RGBColor(0x1A, 0x36, 0x5D)

        def field(par, instr: str, placeholder: str = ""):
            r = par.add_run()
            for kind in ("begin", None, "separate", "text", "end"):
                if kind == "text":
                    t = OxmlElement("w:t"); t.text = placeholder; r._r.append(t)
                elif kind is None:
                    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = instr; r._r.append(it)
                else:
                    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), kind); r._r.append(fc)

        # footer with page numbers
        fp = sec.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fp.add_run(f"{title}  |  Page ").font.size = Pt(8)
        field(fp, "PAGE", "1")

        def add_runs(par, text, size=None, color=None, force_bold=False):
            for t, b, it in _inline_runs(text):
                r = par.add_run(t)
                r.bold = b or force_bold
                r.italic = it
                if size:
                    r.font.size = size
                if color:
                    r.font.color.rgb = color

        for b in blocks:
            kind = b[0]
            if kind == "h":
                lvl, text = b[1], re.sub(r"<[^>]+>", "", b[2])
                if lvl == 1:
                    p = doc.add_paragraph(style="Title")
                    add_runs(p, text)
                elif lvl == 2:
                    h = doc.add_heading(level=1)
                    add_runs(h, text)
                    h.paragraph_format.page_break_before = True
                elif lvl == 3:
                    add_runs(doc.add_heading(level=2), text)
                else:
                    add_runs(doc.add_heading(level=3), text)
            elif kind == "toc":
                h = doc.add_paragraph()
                add_runs(h, "Table of Contents", size=Pt(16), color=RGBColor(0x1A, 0x36, 0x5D), force_bold=True)
                h.paragraph_format.page_break_before = True
                p = doc.add_paragraph()
                field(p, 'TOC \\o "1-2" \\h \\z \\u', "Right-click here and choose 'Update Field' to build the table of contents.")
                settings_el = doc.settings.element
                upd = OxmlElement("w:updateFields"); upd.set(qn("w:val"), "true")
                settings_el.append(upd)
            elif kind == "p":
                p = doc.add_paragraph()
                add_runs(p, b[1])
                p.paragraph_format.space_after = Pt(6)
            elif kind in ("ul", "ol"):
                for item in b[1]:
                    p = doc.add_paragraph(style="List Bullet" if kind == "ul" else "List Number")
                    add_runs(p, item)
            elif kind == "quote":
                p = doc.add_paragraph()
                add_runs(p, b[1])
                for r in p.runs:
                    r.italic = True
            elif kind == "table":
                self._docx_table(doc, b[1], b[2], parse_xml, nsdecls, Inches, Pt, RGBColor, WD_TABLE_ALIGNMENT, add_runs)
            elif kind == "image":
                alt, img_path = b[1], b[2]
                if os.path.exists(img_path):
                    try:
                        doc.add_picture(img_path, width=Inches(5.8))
                        if alt:
                            cap_p = doc.add_paragraph()
                            cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            r = cap_p.add_run(f"Figure: {alt}")
                            r.font.size = Pt(8.5)
                            r.font.italic = True
                    except Exception:
                        pass
        os.makedirs(os.path.dirname(os.path.abspath(docx_file)), exist_ok=True)
        doc.save(docx_file)
        print(f"[Assembler] DOCX written: {docx_file}")
        return docx_file

    @staticmethod
    def _docx_table(doc, rows, aligns, parse_xml, nsdecls, Inches, Pt, RGBColor, WD_TABLE_ALIGNMENT, add_runs):
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        n = len(rows[0])
        table = doc.add_table(rows=len(rows), cols=n)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        weights = [max(8, min(40, max(len(r[c]) for r in rows))) for c in range(n)]
        total = sum(weights)
        widths = [Inches(6.5 * w / total) for w in weights]
        for ri, row in enumerate(rows):
            tr = table.rows[ri]._tr.get_or_add_trPr()
            tr.append(parse_xml(r'<w:cantSplit %s/>' % nsdecls("w")))
            if ri == 0:
                tr.append(parse_xml(r'<w:tblHeader %s/>' % nsdecls("w")))
            for ci, val in enumerate(row):
                cell = table.rows[ri].cells[ci]
                cell.width = widths[ci]
                fill = "1A365D" if ri == 0 else ("F4F6F9" if ri % 2 == 0 else "FFFFFF")
                cell._tc.get_or_add_tcPr().append(parse_xml(r'<w:shd %s w:fill="%s"/>' % (nsdecls("w"), fill)))
                par = cell.paragraphs[0]
                par.paragraph_format.space_after = Pt(2)
                if aligns[ci] == "right":
                    par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif aligns[ci] == "center":
                    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                if ri == 0:
                    add_runs(par, val.replace("**", ""), size=Pt(9), color=RGBColor(255, 255, 255), force_bold=True)
                else:
                    add_runs(par, val, size=Pt(9))
        doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # --------------------------------------------------------------------- PDF
    def export_pdf(self, markdown_file: str, pdf_file: str, title: str) -> str:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
                                        ListFlowable, ListItem)
        from reportlab.platypus.tableofcontents import TableOfContents

        with open(markdown_file, "r", encoding="utf-8") as f:
            blocks = _replace_toc_with_marker(parse_markdown_blocks(f.read()))

        regular, bold, italic, unicode_ok = self._register_fonts()

        def clean(t: str) -> str:
            if not unicode_ok:
                t = t.replace("\u20b9", "Rs.")
                t = t.encode("cp1252", errors="replace").decode("cp1252")
            return t

        def inline(t: str) -> str:
            out = []
            for txt, b, it in _inline_runs(clean(re.sub(r"<[^>]+>", "", t))):
                esc = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                if b:
                    esc = f"<b>{esc}</b>"
                if it:
                    esc = f"<i>{esc}</i>"
                out.append(esc)
            return "".join(out)

        navy = colors.HexColor("#1A365D")
        S = {
            "title": ParagraphStyle("RTitle", fontName=bold, fontSize=26, leading=32, alignment=TA_CENTER, textColor=navy, spaceBefore=140, spaceAfter=24),
            "cover": ParagraphStyle("RCover", fontName=regular, fontSize=11, leading=16, alignment=TA_CENTER),
            "h2": ParagraphStyle("RH2", fontName=bold, fontSize=20, leading=24, textColor=navy, spaceAfter=14),
            "h3": ParagraphStyle("RH3", fontName=bold, fontSize=14, leading=18, textColor=navy, spaceBefore=12, spaceAfter=6),
            "h4": ParagraphStyle("RH4", fontName=bold, fontSize=11.5, leading=15, textColor=navy, spaceBefore=8, spaceAfter=4),
            "toctitle": ParagraphStyle("RTocTitle", fontName=bold, fontSize=16, leading=20, textColor=navy, spaceAfter=10),
            "h5": ParagraphStyle("RH5", fontName=bold, fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=3),
            "p": ParagraphStyle("RP", fontName=regular, fontSize=9.8, leading=13.6, spaceAfter=6),
            "quote": ParagraphStyle("RQ", fontName=italic, fontSize=9.8, leading=13.6, leftIndent=14, spaceAfter=6),
            "cell": ParagraphStyle("RC", fontName=regular, fontSize=8, leading=10),
            "cellh": ParagraphStyle("RCH", fontName=bold, fontSize=8, leading=10, textColor=colors.white),
            "cellr": ParagraphStyle("RCR", fontName=regular, fontSize=8, leading=10, alignment=TA_RIGHT),
        }

        def safe_para(text: str, style: Any) -> Paragraph:
            try:
                return Paragraph(inline(text), style)
            except Exception:
                safe_text = clean(re.sub(r"<[^>]+>", "", text)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                return Paragraph(safe_text, style)

        class _Doc(SimpleDocTemplate):
            def beforeDocument(self):
                self._toc_n = 0                    # keys must be identical on every pass or the TOC never settles

            def afterFlowable(self, fl):
                if isinstance(fl, Paragraph):
                    name = fl.style.name
                    lvl = {"RH2": 0, "RH3": 1}.get(name)      # chapters + sections keep the TOC readable
                    if lvl is not None:
                        self._toc_n = getattr(self, "_toc_n", 0) + 1
                        key = f"toc{self._toc_n}"
                        self.canv.bookmarkPage(key)
                        self.notify("TOCEntry", (lvl, fl.getPlainText(), self.page, key))

        def footer(canv, d):
            if d.page > 1:
                canv.saveState()
                canv.setFont(regular, 8)
                canv.setFillColor(colors.grey)
                canv.drawString(2 * cm, 1.2 * cm, clean(title)[:90])
                canv.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {d.page}")
                canv.restoreState()

        toc = TableOfContents()
        toc.levelStyles = [
            ParagraphStyle("TOC0", fontName=bold, fontSize=10.5, leading=15, leftIndent=0, spaceBefore=4),
            ParagraphStyle("TOC1", fontName=regular, fontSize=9.5, leading=13, leftIndent=14),
            ParagraphStyle("TOC2", fontName=regular, fontSize=8.5, leading=12, leftIndent=28),
        ]
        avail = A4[0] - 4 * cm
        story: List[Any] = []
        in_cover = True
        for b in blocks:
            kind = b[0]
            if kind == "h":
                lvl, text = b[1], b[2]
                if lvl == 1:
                    story.append(safe_para(text, S["title"]))
                elif lvl == 2:
                    story.append(PageBreak())
                    story.append(safe_para(text, S["h2"]))
                    in_cover = False
                else:
                    story.append(safe_para(text, S[{3: "h3", 4: "h4"}.get(lvl, "h5")]))
            elif kind == "toc":
                in_cover = False
                story.append(PageBreak())
                story.append(safe_para("Table of Contents", S["toctitle"]))
                story.append(toc)
            elif kind == "p":
                story.append(safe_para(b[1], S["cover"] if in_cover else S["p"]))
            elif kind in ("ul", "ol"):
                items = [ListItem(safe_para(i, S["p"]), leftIndent=12) for i in b[1]]
                story.append(ListFlowable(items, bulletType="bullet" if kind == "ul" else "1", start="\u2022" if kind == "ul" else 1,
                                          leftIndent=14, bulletFontName=regular))
            elif kind == "quote":
                story.append(safe_para(b[1], S["quote"]))
            elif kind == "table":
                rows, aligns = b[1], b[2]
                if not rows or not rows[0]:
                    continue
                max_cols = max(len(r) for r in rows)
                if max_cols == 0:
                    continue
                padded_rows = [list(r) + [""] * (max_cols - len(r)) for r in rows]
                padded_aligns = (list(aligns) + ["left"] * max_cols)[:max_cols]
                weights = [max(6, min(40, max(len(str(r[c])) for r in padded_rows))) for c in range(max_cols)]
                tot = sum(weights) or 1
                widths = [avail * w / tot for w in weights]
                data = []
                for ri, row in enumerate(padded_rows):
                    row_cells = []
                    for ci, c in enumerate(row):
                        val_str = str(c).replace("**", "") if ri == 0 else str(c)
                        c_style = S["cellh"] if ri == 0 else (S["cellr"] if padded_aligns[ci] == "right" else S["cell"])
                        row_cells.append(safe_para(val_str, c_style))
                    data.append(row_cells)
                t = Table(data, colWidths=widths, repeatRows=1, splitByRow=1)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), navy),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6F9")]),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C5CCD6")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]))
                story.append(t)
                story.append(Spacer(1, 8))
            elif kind == "image":
                alt, img_path = b[1], b[2]
                if os.path.exists(img_path):
                    try:
                        from reportlab.platypus import Image as RLImage
                        story.append(Spacer(1, 8))
                        story.append(RLImage(img_path, width=avail, height=avail * 0.52))
                        if alt:
                            story.append(safe_para(f"<i>Figure: {alt}</i>", S["quote"]))
                        story.append(Spacer(1, 8))
                    except Exception:
                        pass

        os.makedirs(os.path.dirname(os.path.abspath(pdf_file)), exist_ok=True)
        target_pdf = pdf_file
        try:
            with open(target_pdf, "ab") as test_f:
                pass
        except PermissionError:
            base, ext = os.path.splitext(pdf_file)
            target_pdf = f"{base}_new{ext}"
            print(f"[Assembler] Notice: '{os.path.basename(pdf_file)}' is currently open in another program. Saving PDF to: '{os.path.basename(target_pdf)}'")

        doc = _Doc(target_pdf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                   bottomMargin=2 * cm, title=clean(title))
        try:
            doc.multiBuild(story, onFirstPage=footer, onLaterPages=footer)
        except Exception as e:
            print(f"[Assembler] Notice: Multi-pass TOC layout encountered an issue ({e}); generating via single-pass build...")
            simple_doc = SimpleDocTemplate(target_pdf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                                           topMargin=2 * cm, bottomMargin=2 * cm, title=clean(title))
            fallback_story = [f for f in story if not isinstance(f, TableOfContents)]
            simple_doc.build(fallback_story, onFirstPage=footer, onLaterPages=footer)

        print(f"[Assembler] PDF written: {target_pdf}")
        return target_pdf

    @staticmethod
    def _register_fonts() -> Tuple[str, str, str, bool]:
        """Register a Unicode TTF family if one is installed (needed for symbols such as the rupee sign)."""
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        candidates = [
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
            ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeuii.ttf"),
            ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
            ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
        ]
        for reg, bld, ita in candidates:
            if all(os.path.exists(p) for p in (reg, bld, ita)):
                try:
                    pdfmetrics.registerFont(TTFont("RptRegular", reg))
                    pdfmetrics.registerFont(TTFont("RptBold", bld))
                    pdfmetrics.registerFont(TTFont("RptItalic", ita))
                    pdfmetrics.registerFontFamily("RptRegular", normal="RptRegular", bold="RptBold",
                                                  italic="RptItalic", boldItalic="RptBold")
                    return "RptRegular", "RptBold", "RptItalic", True
                except Exception:
                    continue
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False
