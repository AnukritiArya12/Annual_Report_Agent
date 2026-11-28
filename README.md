# Autonomous Annual Report Agent

An autonomous agent built on LangChain **DeepAgents** (`deepagents.create_deep_agent`) that researches, writes, reviews, and compiles annual reports. It adapts the report structure and depth to the company, applicable reporting requirements, user purpose, and evidence available. Jurisdiction connectors and requirement data are currently limited to those implemented in this repository; unsupported cases use the generic fallback and require source-based judgment.

Given a company name, financial year, and optional country, the agent follows the recognizable annual-report arc—from company overview to strategy, operations, governance, risks, financial performance, and applicable disclosures—while adapting its sections to the business and reporting framework. It can use local company records without internet access, and can use another company's report as a structure-only reference. Its configurable maximum is 500 pages; that is a ceiling, not a target, and the evidence must support the report's depth.

---

## Architecture Overview

```
app.py                CLI runner, human-approval prompts, page-count progress
agent.py              build_annual_report_agent — single DeepAgent via create_deep_agent
├── tools/            Deterministic tool layer (code owns facts)
│   ├── discovery_tools.py   resolve_company_identity, find_source_documents, ingest_documents, etc.
│   ├── retrieval_tools.py   search_evidence (hybrid FTS5+ChromaDB), get_evidence_capacity
│   ├── financial_tools.py   build_facts_ledger, get_canonical_facts, get_statement_table, verify_financials
│   ├── validation_tools.py  check_entities, check_section (duplication & ledger mismatch)
│   ├── output_tools.py      make_charts, assemble_report, request_human_decision
│   ├── checkpointer.py      SqliteCheckpointSaver (persistent resume for interruptions)
│   ├── session.py           ReportSession state handle (thread-safe, replaces globals)
│   └── web.py               Downloader, web search, scraper with prompt injection sanitization
├── skills/           Agent Skills standard (SKILL.md with YAML frontmatter)
│   ├── source-discovery/    Official filing discovery & source ranking
│   ├── cold-start-company/  Flexible information intake for first-time/private entities
│   ├── report-planning/     Dynamic chapter architecture calibrated to evidence capacity
│   ├── section-writing/     Strict evidence-grounded drafting and citation
│   ├── financial-reporting/ Canonical ledger adherence and financial tables
│   ├── validation-and-revision/ 5-tier audit rules & revision directives
│   └── human-approval/      Risk-based human decision escalation protocols
├── connectors/       Jurisdiction filing connectors plus generic investor-relations discovery
├── jurisdictions.py  Finite jurisdiction registry; unsupported regimes require source verification
├── ledger.py         FactsLedger (SQLite-backed canonical facts, regex-extracted, digit-verified)
├── statements.py     Deterministic financial highlights summaries (derived from verified facts)
├── financial.py      Arithmetic reconciliation engine (Assets = Liabilities + Equity, P&L, Cash Flow)
├── charts.py         Headless matplotlib charts (generated ONLY when >= 2 authentic data points exist)
└── assembler.py      Markdown, DOCX, and PDF export utilities
```

---

## Non-Negotiable Core Principles

1. **Adaptive Agent**: DeepAgents chooses research, drafting, and delegation based on the company and source material.
2. **No Company-Specific Hardcoding**: Company names are not embedded in entity checks. Applicable laws, industry disclosures, and accounting rules must be verified from authoritative sources; local registries are discovery aids, not universal coverage.
3. **Code Owns Facts**: The LLM writes prose and plans. All mathematical verifications, domain whitelists, entity guardrails, and digit audits live inside deterministic tools.
4. **Evidence-Led Scope**: Report length and organization follow the user's purpose, applicable requirements, and available evidence. Gaps are stated rather than guessed or padded.
5. **Human-in-the-Loop (HITL)**: The agent requests human input when source-domain restrictions or material ambiguity require it. Decisions and LangGraph checkpoints are persisted to support resuming work.

---

## Installation & Setup

### 1. Prerequisites
- Python 3.10+
- Virtual environment recommended

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Verify Setup
Run the setup verification script:
```bash
python check_setup.py
```

### 4. Configure Environment
Create a `.env` file in the root directory:
```env
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-flash-lite
# Requests per minute sent to Gemini. Lower it (e.g. 5) if you still see 429 errors.
LLM_RPM=8
```

---

## Running the Agent

### Command-Line Interface
```bash
python app.py --company "Apple Inc" --fy "FY 2024" --country "United States"
```

### Key CLI Options
| Flag | Description | Default |
| :--- | :--- | :--- |
| `--company` | Target company name | *(Required)* |
| `--fy` | Target reporting period / fiscal year | *(Required)* |
| `--country` | Governing jurisdiction / country | `""` |
| `--scope` | Reporting scope (`consolidated` or `standalone`) | `consolidated` |
| `--model` | Gemini model name (e.g. `gemini-2.5-flash`) | `GEMINI_MODEL` from `.env` |
| `--auto-approve` | Bypass HITL prompts (intended for automated testing/eval) | `False` |
| `--resume` | Resume an interrupted run from existing thread checkpointer | `False` |
| `--pdf` | Optional local company PDF to use as source material | `None` |
| `--dossier` | Path to a local JSON/text dossier for company-provided information | `None` |
| `--peer-report` | Optional local report used for structure and presentation only | `None` |

---

## Verification
```bash
python check_setup.py
```
While it runs, app.py prints a size line after every draft, e.g. `Drafted 12 sections, 18,400 words = about 53 pages (limit 500)`.
