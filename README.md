# Autonomous DeepAgent Annual Report Architect
*Universal Enterprise-Grade DeepAgent for Corporate Reporting*

An autonomous deep agent built on LangChain **DeepAgents** (`deepagents.create_deep_agent`) that plans, researches, writes, audits, and compiles publication-grade annual reports for **any company in any jurisdiction**.

Given a company name, financial year, and country, the agent itself decides what to research, what to include, what to skip, how many chapters the report needs, how deep each chapter goes, and when to consult a human. Report length scales authentically with available evidence—a first-year startup or small private entity will produce a concise report (~5–15 pages), whereas a large listed enterprise may span 100 to 300+ pages. Zero boilerplate padding. Zero hallucinated numbers.

---

## Architecture Overview

```
app.py                CLI runner & Human-in-the-Loop interrupt handler
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
│   ├── cold-start-company/  Internal dossier compilation for first-year/unlisted entities
│   ├── report-planning/     Dynamic chapter architecture calibrated to evidence capacity
│   ├── section-writing/     Strict evidence-grounded drafting and citation
│   ├── financial-reporting/ Canonical ledger adherence and financial tables
│   ├── validation-and-revision/ 5-tier audit rules & revision directives
│   └── human-approval/      Risk-based human decision escalation protocols
├── connectors/       Jurisdiction filing connectors (SEC EDGAR, India BSE/NSE, UK Companies House, IR)
├── jurisdictions.py  Jurisdiction registry (US, IN, UK, EU, SG, JP, Generic)
├── ledger.py         FactsLedger (SQLite-backed multi-period canonical facts with digit verification)
├── statements.py     Deterministic financial highlights summaries (derived from verified facts)
├── financial.py      Arithmetic reconciliation engine (Assets = Liabilities + Equity, P&L, Cash Flow)
├── charts.py         Headless matplotlib charts (generated ONLY when >= 2 authentic data points exist)
└── assembler.py      Multi-format publisher (Markdown -> DOCX + PDF with TOC & Audit Appendix)
```

---

## Non-Negotiable Core Principles

1. **One Autonomous Deep Agent**: No fixed chapter list, no procedural pipeline scripts, and no hardcoded step sequences. The agent plans its own work using its todo list, saves drafts on disk via filesystem tools, and uses the built-in general-purpose subagent (`task`) when focused delegation is needed.
2. **Zero Hardcoding**: No company names, industry lists, country-specific rules, or statutory acts hardcoded in code. Jurisdiction rules are loaded at runtime from `jurisdictions.py`.
3. **Code Owns Facts**: The LLM writes prose and plans. All mathematical verifications, domain whitelists, entity guardrails, and digit audits live inside deterministic tools.
4. **Authentic Length Scaling**: Output length follows authentic evidence capacity. Gaps are explicitly stated as *"Not disclosed in the sources reviewed"* rather than guessed or padded with generic boilerplate.
5. **Human-in-the-Loop (HITL)**: Risky decisions pause execution using DeepAgents interrupt mechanisms (`interrupt_on`), waiting for human confirmation before confirming legal identities, adopting unapproved domains, committing outlines, or publishing. All answers persist in SQLite so runs can resume seamlessly.

---

## Installation & Setup

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.11 and 3.12)
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
# Choose provider: gemini | openai | anthropic | ollama
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key_here

# Optional: Set model override
# GEMINI_MODEL=gemini-2.5-pro
# OPENAI_API_KEY=your_openai_api_key_here
# ANTHROPIC_API_KEY=your_anthropic_api_key_here
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
| `--model` | Model specifier (e.g. `google_genai:gemini-3.8-flash`, `openai:gpt-4o`) | Auto-resolved from `.env` |
| `--auto-approve` | Bypass HITL prompts (intended for automated testing/eval) | `False` |
| `--resume` | Resume an interrupted run from existing thread checkpointer | `False` |
| `--pdf` | Generate high-resolution PDF publication | `True` |
| `--dossier` | Path to a local JSON/text dossier for cold-start companies | `None` |

---

## Verification & Testing

Run all unit and integration test suites:
```bash
# 1. Run tools unit tests (guards, domain whitelist, ledger digit verification)
python -m unittest tests/test_tools.py

# 2. Run human-in-the-loop & SQLite checkpointer resume test
python -m unittest tests/test_hitl_resume.py

# 3. Run autonomous end-to-end integration test
python -m unittest tests/test_end_to_end.py

# 4. Run full regression test suite
python -m unittest test_suite.py
```
