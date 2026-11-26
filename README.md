# Generic Autonomous Annual Report Generation Agent
*Universal Enterprise-Grade Autonomous Annual Report Architect*

An autonomous agent that synthesizes publication-grade annual reports for **any company, industry, country, or jurisdiction** (listed, unlisted private, or maiden/first-year startups) from verified corporate records and regulatory evidence. **Nothing about the report structure is hardcoded.**

---

## 1. Architecture Overview (Phases 1-4)

```
main.py          Orchestrator — coordinates all phases, human checkpoints, resume logic
├── knowledge.py     Ingest PDFs/DOCX/web; OCR fallback; connector-sourced filings
├── connectors/      Jurisdiction-aware source discovery (SEC, BSE/NSE, Companies House, IR crawler)
├── planner.py       Dynamic blueprint (chapters/tasks) sized to evidence capacity
├── ledger.py        FactsLedger — SQLite-persisted canonical facts with conflict detection
├── agent.py         Parallel task execution; injects canonical facts; injection-safe evidence blocks
├── statements.py    Deterministic financial statement builder (no LLM rewrites numbers)
├── financial.py     Reconciliation engine (BS, P&L, CF); sub-total & % column aware
├── validator.py     5-tier draft audit (PASS/WARNING/REVIEW_REQUIRED/FAIL/NOT_VERIFIABLE)
├── assembler.py     MD → DOCX + PDF; embeds charts, auditability appendix
├── charts.py        Headless matplotlib charts from ledger facts (≥2 points required)
├── jurisdictions.py Registry (US/IN/UK/EU/SG/JP/GENERIC) — no statutory text in code
├── utils.py         Layered org-name guard (allow-list → spaCy NER → regex); prompt-injection sanitizer
├── storage.py       ChromaDB + SQLite hybrid search (FTS5 BM25 + vector → RRF k=60)
├── schemas.py       Pydantic models for all data contracts
├── skills.py        LLM prompt building; jurisdiction-aware mandatory sections
├── config.py        All settings with env overrides
├── approvals.py     Risk-based human checkpoints (zero trivial prompts)
├── tools.py         Web search, PDF download, scrape (all untrusted text sanitized)
└── eval/            Evaluation harness
    ├── eval_config.json   6+ diverse test cases (names only here, never in code)
    └── run_eval.py        Runs main.py --auto-approve; computes 8 quality metrics
```

---

## 2. What's New Per Phase

### Phase 1 — Data Foundation
| Item | Description |
| :--- | :--- |
| `jurisdictions.py` | Registry keyed by code (US, IN, UK, EU, SG, JP, GENERIC). `get_jurisdiction(country)` returns best match. No statutory text hardcoded in code. |
| `ledger.py` | `FactsLedger` persisted in SQLite `facts` table. Statuses: `REPORTED`, `DERIVED`, `CONFLICTING`, `UNVERIFIED`. Same key+period+different value → `CONFLICTING`. |
| `storage.py` (hybrid search) | SQLite FTS5 table `chunks_fts` + ChromaDB vector search merged via Reciprocal Rank Fusion (k=60). Optional cross-encoder reranker. Table chunks boosted 1.3×. |
| OCR fallback | `parse_pdf` renders pages via PyMuPDF pixmap → PyTesseract for image-only pages. One clear warning if Tesseract binary is missing. |

### Phase 2 — Financial Correctness & Safety
| Item | Description |
| :--- | :--- |
| `statements.py` | `FinancialStatementBuilder` builds Balance Sheet, Income Statement, Cash Flow from XBRL/structured data. LLM receives the table as read-only context — it cannot alter numbers. |
| `financial.py` | Real reconciliation: Assets = Liabilities + Equity, Net Profit = Revenue − Expenses, CF opening + net change = closing. `verify_table_sums` handles sub-totals, % columns, `% of` rows. |
| Entity guard | Layered org-name detection: (a) allow-list from confirmed identity, (b) spaCy NER (optional), (c) regex suffix fallback. Suffix-free names (e.g. "Microsoft") detected. |
| Prompt-injection defence | `sanitize_untrusted()` strips instruction-like lines from all scraped text. Evidence wrapped in `EVIDENCE` delimiters in writing prompts. |

### Phase 3 — Coverage & Output Quality
| Item | Description |
| :--- | :--- |
| `connectors/` package | `SecEdgarConnector`, `IndiaBseNseConnector`, `CompaniesHouseConnector`, `InvestorRelationsCrawlerConnector`. Each degrades gracefully. `get_connectors_for_jurisdiction()` selects by jurisdiction. |
| `charts.py` | Headless matplotlib (Agg backend). Revenue/profit trend and segment mix charts. Generated **only** when ≥2 authentic data points exist. Zero fabricated points. |
| Evidence-based length | `estimate_evidence_capacity()` bounds report length to `evidence_words × EVIDENCE_WORDS_RATIO / WORDS_PER_PAGE`. Soft floor: `MIN_PAGES`. |
| Run metadata | `run_report.json` now records `planned_pages`, `evidence_supported_pages`, `evidence_capacity_note`, `charts_generated`. |

### Phase 4 — Evaluation Harness
| Item | Description |
| :--- | :--- |
| `eval/eval_config.json` | 7 diverse cases: US tech, India IT, India bank, US pharma, India manufacturer, UK bank, India first-year startup. All company names live **only** in this file. |
| `eval/run_eval.py` | Runs each case via `subprocess` with `--auto-approve`. Computes: section count, words/pages vs target, repetition rate (5-shingle overlap), % numbers grounded in evidence/ledger, ledger-consistency violations, foreign-entity flags, validation status distribution, not-disclosed count. Writes `eval/results.json` + `eval/results_summary.md`. `--compare-with` diffs two result files. |

---

## 3. Settings Reference

All settings live in `config.py` and can be overridden via environment variables in `.env`.

| Setting (env var) | Default | Description |
| :--- | :--- | :--- |
| `LLM_PROVIDER` | `gemini` | `gemini`, `openai`, or `anthropic` |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | API key for the chosen provider |
| `LLM_MODEL` | provider default | Model name override |
| `MAX_PARALLEL_WORKERS` | `4` | Concurrent section-writing threads |
| `WORDS_PER_PAGE` | `300` | Words per page for page-count estimates |
| `MIN_PAGES` | `30` | Minimum report length (soft floor) |
| `MAX_PAGES` | `400` | Maximum report length cap |
| `MIN_LEAF_WORDS` | `200` | Minimum words per leaf task |
| `LEAF_MAX_WORDS` | `800` | Maximum words per leaf task |
| `VALIDATE_WITH_LLM` | `true` | Enable LLM-assisted draft validation |
| `HYBRID_SEARCH` | `true` | Enable FTS5+vector hybrid search |
| `RRF_K` | `60` | Reciprocal Rank Fusion k parameter |
| `RERANK` | `false` | Enable cross-encoder reranking |
| `RERANK_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker model name |
| `KEYWORD_TOP_N` | `20` | FTS5 candidate count before RRF |
| `VECTOR_TOP_N` | `20` | Vector candidate count before RRF |
| `EVIDENCE_WORDS_RATIO` | `2.5` | Max report words as multiple of evidence words |
| `MIN_EVIDENCE_WORDS_PER_PAGE` | `150` | Per-task downscaling threshold |
| `CHARTS_ENABLED` | `true` | Generate matplotlib charts |
| `COMPANIES_HOUSE_API_KEY` | — | UK Companies House REST API key (optional) |
| `AUTO_APPROVE` | `false` | Accept all checkpoints automatically (`--auto-approve` flag) |

---

## 4. Human Approval Checkpoints

The system uses **risk-based** checkpoints — only triggers for genuinely uncertain decisions:

1. **Identity Confirmation** — ambiguous company names, parent/subsidiary disambiguation.
2. **Source Confirmation** — multiple candidate PDFs or low-confidence search results.
3. **Article Triage** — web-only evidence (no PDFs found).
4. **First-Year Dossier Review** — missing or incomplete internal dossier fields.
5. **Outline Approval (Step 2b)** — approve chapters/sections before expensive writing starts; shows evidence capacity note if pages were downscaled.
6. **Facts & Conflicts Review** — CONFLICTING or missing core metric review before writing.
7. **Entity Flags** — foreign org names detected in draft content.
8. **Risk-Based Financial Review** — arithmetic mismatches, regulatory uncertainty.
9. **Final Publication Approval** — high-level sign-off before PDF/DOCX export.

*All decisions stored in `runs/<company>_<fy>/approvals.json` for seamless `--resume`.*

---

## 5. Setup & Execution

### Prerequisites
```powershell
pip install -r requirements.txt
python check_setup.py        # Verifies all files, packages, and .env
```

> [!IMPORTANT]
> For OCR on scanned PDFs, install the Tesseract-OCR binary and add it to your `PATH`.
> Download from https://github.com/UB-Mannheim/tesseract/wiki (Windows).

### Standard Run Commands
```powershell
# Any listed or private company in any country
python main.py "Infosys" --fy "FY 2024-25" --country India

# Specify industry and target page count
python main.py "HDFC Bank" --fy "FY 2024-25" --country India --industry "Banking" --pages 60

# Provide a reference PDF directly
python main.py "Apple Inc" --fy "FY 2024" --country USA --pdf "apple_ar_2024.pdf"

# First-year / inaugural company (no online presence)
python main.py "NovaSpark Technologies Pvt Ltd" --fy "FY 2024-25" --country India --first-year --dossier dossier.json

# Use a peer company report for structure reference only
python main.py "NewCo Ltd" --fy "FY 2024-25" --first-year --peer-report "peer_annual_report.pdf"

# Headless / fully automated (no human prompts)
python main.py "Company Name" --fy "FY 2024-25" --auto-approve

# Planning only — stop after outline, before writing
python main.py "Company Name" --fy "FY 2024-25" --plan-only

# Resume a stopped or partial run
python main.py "Company Name" --fy "FY 2024-25" --resume
```

### CLI Flags Reference
| Flag | Description |
| :--- | :--- |
| `--fy FY` | Financial year (e.g. `FY 2024-25`, `2024`) |
| `--country COUNTRY` | Country for jurisdiction resolution (e.g. `India`, `USA`, `UK`) |
| `--industry INDUSTRY` | Industry sector hint for planner |
| `--scope SCOPE` | `consolidated` or `standalone` |
| `--pages N` | Target page count override |
| `--pdf FILE_OR_URL` | Pre-supply a local PDF path or URL |
| `--extra EXTRA` | Additional context or supplementary document path |
| `--workers N` | Parallel writing workers (overrides `MAX_PARALLEL_WORKERS`) |
| `--low-quota` | Minimise LLM calls (fewer research queries) |
| `--auto-approve` | Accept all human checkpoints automatically |
| `--plan-only` | Stop after planning; save outline and blueprint |
| `--resume` | Resume from last saved state |
| `--first-year` | Activate first-year / inaugural mode (dossier intake) |
| `--dossier PATH` | Path to first-year dossier JSON |
| `--peer-report PATH_OR_URL` | Peer company report for structure reference only |

### Output Files
All outputs are saved to `runs/<company_slug>_<fy_slug>/`:
| File | Description |
| :--- | :--- |
| `<report_id>.pdf` | Publication-grade PDF with cover page, TOC, and styled tables |
| `<report_id>.docx` | Editable Word document with native TOC field |
| `<report_id>.md` | Complete Markdown source |
| `charts/*.png` | Generated financial trend and segment charts |
| `blueprint.json` | Full structural plan (editable for `--resume`) |
| `run_report.json` | Execution audit: words, pages, planned vs evidence-supported pages, charts |
| `approvals.json` | All human checkpoint decisions (for `--resume`) |
| `outline.txt` | Human-readable chapter/section outline |

---

## 6. Running the Evaluation Harness

```powershell
# Run all 7 eval cases (requires API key; uses --auto-approve)
python eval/run_eval.py

# Run only specific cases
python eval/run_eval.py --cases us_tech india_it india_bank

# Dry-run: print commands without executing
python eval/run_eval.py --dry-run

# Compare two result files (regressions/improvements)
python eval/run_eval.py --compare-with eval/results_baseline.json
```

**Eval outputs:**
- `eval/results.json` — full structured metrics per case
- `eval/results_summary.md` — markdown table with per-case and aggregate stats
- `eval/results_comparison.md` — diff table (when `--compare-with` is used)

**Metrics computed per case:**
| Metric | Description |
| :--- | :--- |
| `section_count` | Number of `##`/`###` headings in the report |
| `words_written` | Total word count |
| `approx_pages` | Estimated pages (words ÷ WORDS_PER_PAGE) |
| `repetition_rate` | 5-word shingle overlap across sections (lower is better) |
| `evidence_grounding_pct` | % of numeric tokens found in source chunks or ledger |
| `ledger_violations` | CONFLICTING facts in the ledger |
| `foreign_entity_flags` | Unknown org names flagged during entity guard |
| `not_disclosed_count` | Sections marked as undisclosed / no source material |
| `validation_distribution` | Count of PASS/WARNING/REVIEW_REQUIRED/FAIL/NOT_VERIFIABLE |

---

## 7. Running the Unit Test Suite

```powershell
python -m unittest test_suite.py
```

**Tests covered (15 total):**
- ChromaDB + SQLite persistence and hybrid retrieval
- Deterministic financial arithmetic (table sums, sub-totals, % columns)
- 5-tier validation engine
- Document processing (PDF tables, DOCX, OCR fallback)
- First-year dossier intake
- Jurisdiction registry
- Facts ledger (record, conflict detection, missing core keys)
- Financial reconciliation (BS, P&L, CF)
- Sub-totals and percentage column handling
- Layered entity guard (allow-list, NER, suffix-free names)
- Prompt-injection defence
- Source connector registry (US, India, UK, generic)
- Headless chart generation (≥2 points required)
- Evidence-based capacity calibration and downscaling
