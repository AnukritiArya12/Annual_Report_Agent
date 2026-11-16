# Generic Annual Report Agent

One agent that builds an annual report for ANY company. Nothing about the structure is hardcoded.

## Run
```
pip install -r requirements.txt
copy .env.example .env      # fill in your provider / key
python main.py "Company Name" --fy "FY 2024-25" --country India
python main.py "Company Name" --fy 2024 --country USA --pdf path/or/url/to/annual_report.pdf --pages 250
python main.py "Company Name" --fy "FY 2024-25" --resume     # continue / retry failed sections
```
Outputs go to `runs/<company>_<fy>/`: `.pdf`, `.docx`, `.md`, `blueprint.json` (the plan), `run_report.json`.

## How it stays generic
1. **Ingest** - finds (or you give with `--pdf`) the company's real annual report, reads it with PyMuPDF,
   stores page-tagged chunks in a BM25 knowledge base.
2. **Plan** - the LLM designs the chapters from the report's own table of contents, the country's rules and the
   industry. A critic pass adds/removes chapters. Each chapter gets a word budget (from the target page count) and is
   split recursively into sections until each writing task is small. No fallback outline exists.
3. **Write** - one generic agent writes each section from the evidence retrieved for it, knows its scope boundaries
   and what sibling sections already say (so nothing repeats), and writes "Not disclosed in the sources reviewed"
   instead of inventing.
4. **Validate** - grounding of numbers, duplication across sections, length vs evidence, planner-set criteria, LLM review.
5. **Assemble** - headings follow the plan; DOCX has a TOC field, PDF has a TOC with page numbers.

## Limits to know
- Length is bounded by the evidence. A 300-page report needs the company's real report/filings loaded; with web
  snippets only, sections stay short. Always pass `--pdf` when you have the file.
- If the loaded report is for a different year than requested, the output says so (check "Basis of Preparation").
- Output is AI-drafted from public sources and unaudited: verify figures before use.
- Use a strong model (Gemini/GPT/Claude, or a >=14B local model). Small local models struggle with JSON planning.