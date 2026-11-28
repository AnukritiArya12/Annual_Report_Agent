"""Knowledge ingestion and document parsing utilities.

Provides multi-format parsing for PDFs (with PyMuPDF table extraction and OCR fallback)
and Word documents (.docx) into structured page-tagged chunks.
"""

from __future__ import annotations

import io
import os
import re
from typing import List, Tuple

from config import settings
from tools import rows_to_markdown

# ------------------------------------------------------------------------------
# Document parsing
# ------------------------------------------------------------------------------
_OCR_WARNED = False


def _pdf_tables_markdown(page) -> str:
    """Real tables of a PDF page as Markdown (PyMuPDF find_tables). Appended to the page text."""
    try:
        found = page.find_tables()
        out = []
        for tb in getattr(found, "tables", []) or []:
            md = rows_to_markdown(tb.extract())
            if md:
                out.append(md)
        return ("\n\n" + "\n\n".join(out)) if out else ""
    except Exception:
        return ""


def parse_pdf(path: str) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str, int]], int]:
    """Return (pages, toc, page_count). pages = [(page_no, text)], toc = [(level, title, page)].
    Includes automatic OCR fallback when pages contain scanned/image content without text layer."""
    try:
        import pymupdf as fitz            # type: ignore
    except ImportError:
        try:
            import fitz                   # type: ignore
        except ImportError as e:
            raise ImportError("PyMuPDF is required to read PDFs: pip install pymupdf") from e
    doc = fitz.open(path)
    pages = []
    scanned_pages_detected = 0
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        if settings.PDF_TABLES:
            text += _pdf_tables_markdown(page)
        # OCR fallback if page has minimal text but contains image data
        if len(text.strip()) < 50 and getattr(settings, "OCR_ENABLED", True):
            global _OCR_WARNED
            try:
                pix = page.get_pixmap()
                from PIL import Image
                import pytesseract
                img = Image.open(io.BytesIO(pix.tobytes()))
                ocr_text = pytesseract.image_to_string(img)
                if ocr_text.strip():
                    text = ocr_text.strip() + "\n[OCR Extracted Content]"
                    scanned_pages_detected += 1
            except Exception as e:
                if not _OCR_WARNED:
                    _OCR_WARNED = True
                    print(f"  [Warning] OCR unavailable: pytesseract or Tesseract-OCR binary is not installed/configured ({e}). Scanned pages will retain raw text layer only.")
        pages.append((i + 1, text))
    if scanned_pages_detected:
        print(f"  [PDF Parser] OCR applied to {scanned_pages_detected} scanned page(s) in {os.path.basename(path)}")
    toc = [(int(l), str(t).strip(), int(p)) for l, t, p in (doc.get_toc() or [])]
    n = len(pages)
    doc.close()
    return pages, toc, n


def parse_docx(path: str) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str, int]], int]:
    """Parse DOCX documents preserving heading hierarchy, paragraphs, and tables as Markdown."""
    try:
        import docx
    except ImportError:
        raise ImportError("python-docx is required to read DOCX: pip install python-docx")
    
    doc = docx.Document(path)
    content_blocks = []
    toc = []
    page_approx = 1
    words_count = 0
    
    for element in doc.element.body:
        tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag
        if tag == "p":
            p = docx.text.paragraph.Paragraph(element, doc)
            text = p.text.strip()
            if not text:
                continue
            style_name = p.style.name.lower() if p.style else ""
            if "heading 1" in style_name:
                content_blocks.append(f"\n# {text}\n")
                toc.append((1, text, page_approx))
            elif "heading 2" in style_name:
                content_blocks.append(f"\n## {text}\n")
                toc.append((2, text, page_approx))
            elif "heading 3" in style_name:
                content_blocks.append(f"\n### {text}\n")
                toc.append((3, text, page_approx))
            else:
                content_blocks.append(text)
            words_count += len(text.split())
            if words_count > 450:
                page_approx += 1
                words_count = 0
        elif tag == "tbl":
            t = docx.table.Table(element, doc)
            rows = []
            for r in t.rows:
                rows.append([c.text.strip() for c in r.cells])
            md = rows_to_markdown(rows)
            if md:
                content_blocks.append("\n" + md + "\n")
                words_count += sum(len(c.split()) for row in rows for c in row)
                
    full_text = "\n\n".join(content_blocks)
    pages = _html_to_pages(full_text, words_per_page=450)
    return pages, toc, len(pages)


def _html_to_pages(text: str, words_per_page: int = 500) -> List[Tuple[int, str]]:
    """Pseudo-pages from structured text. Tables are never cut; long prose is split by words."""
    blocks: List[str] = []
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        words = block.split()
        if not any(l.lstrip().startswith("|") for l in block.splitlines()) and len(words) > words_per_page:
            blocks += [" ".join(words[i:i + words_per_page]) for i in range(0, len(words), words_per_page)]
        else:
            blocks.append(block)
    pages, cur, w = [], [], 0
    for b in blocks:
        bw = len(b.split())
        if cur and w + bw > words_per_page:
            pages.append("\n\n".join(cur))
            cur, w = [], 0
        cur.append(b)
        w += bw
    if cur:
        pages.append("\n\n".join(cur))
    return [(i + 1, p) for i, p in enumerate(pages)]


