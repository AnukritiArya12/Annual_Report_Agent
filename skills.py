"""Writing-style guidance by section KIND.

These are neutral, jurisdiction-free and industry-free writing rules. They do NOT decide what the
report contains (the planner does that, per company); they only tell the writer HOW to write a
section once the planner has labelled its kind.
"""

from typing import Dict

SECTION_KINDS = ["cover", "message", "narrative", "summary", "data_table",
                 "financial_statement", "statutory", "list", "appendix"]

STYLE_GUIDES: Dict[str, str] = {
    "cover": "Short, factual front-matter. Names, dates, identifiers only as found in the evidence.",
    "message": ("A leadership message in the first person plural/singular as a real report would phrase it. "
                "Ground every claim in results or plans that appear in the evidence or earlier sections; "
                "do not invent quotes or personal anecdotes."),
    "narrative": ("Analytical prose in the formal voice of a published annual report. Explain what happened, "
                  "why it matters and what comes next. Use sub-headings (####) when the section is long."),
    "summary": ("Condense results already established elsewhere. Reuse the figures given in the earlier-section "
                "context exactly; do not introduce new numbers."),
    "data_table": ("Lead with a Markdown table of the metrics (consistent columns, units and period in headers), "
                   "then a short commentary. Missing cells: 'Not disclosed'."),
    "financial_statement": ("Reproduce the statement faithfully from the evidence: same line items, same order, "
                            "same columns and units. Do not recompute, round or merge lines. If only part of the "
                            "statement is in the evidence, reproduce that part and say what is missing."),
    "statutory": ("Follow the structure the governing law / listing rules / reporting standard prescribes for this "
                  "disclosure. State facts precisely (names, dates, counts, percentages). Tables where the "
                  "evidence is tabular."),
    "list": "Concise structured list or table; one line per item, each item grounded in the evidence.",
    "appendix": "Reference material: precise, compact, tabular where possible.",
}


def get_style_guidance(kind: str) -> str:
    k = (kind or "narrative").lower().strip()
    if k in STYLE_GUIDES:
        return STYLE_GUIDES[k]
    for name, text in STYLE_GUIDES.items():
        if name in k or k in name:
            return text
    return STYLE_GUIDES["narrative"]