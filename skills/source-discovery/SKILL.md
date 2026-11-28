---
name: source-discovery
description: Protocols for primary source discovery, local file ingestion, cold-start handling, and building the canonical facts ledger without relying on internet access.
---

# Source Discovery & Knowledge Grounding Skill

## Objective
Establish an authentic, verifiable knowledge base for the target company and reporting period. Maximize utilization of local documents, discover verified statutory filings when available, handle private/new entities through structured cold-start intake, and build the canonical facts ledger.

---

## 1. Operating Protocol

### Step 1: Local Documents First (Air-Gapped Operation)
- If the user provides local files (PDF, DOCX, TXT) via the CLI or UI, ingest them immediately:
  `ingest_documents([local_file_paths])`
- **Zero Approval Needed**: Local files do not require internet access or human approval.
- The storage engine extracts chunks, extracts tables, and populates the SQLite evidence database (`evidence.db`).

### Step 2: Identity Resolution
- Call `resolve_company_identity(name, country)` to determine:
  - Official registered legal entity name.
  - Primary operating country and governing corporate statute (e.g., US SEC, India MCA, UK Companies House).
  - Corporate structure and sector categorization.

### Step 3: Statutory Document Search (If Online & Remote Mode Permitted)
- Call `find_source_documents(company, fy)` to discover official filings:
  - Prioritize authoritative statutory filings: Form 10-K, Annual Report & Accounts, Integrated Annual Report.
- **Human Decision Gateway**:
  - Before ingesting any remote web domain, call `request_human_decision` for domain approval (e.g., `approve_domain="sec.gov"`).
  - If `auto_approve=True` is enabled in settings, domain approval is automatically granted.

### Step 4: Cold-Start Protocol (For Companies with Zero Filings)
- If no public documents exist:
  - Call `create_cold_start_dossier(fields=..., source_label="management-provided")`.
  - Call `request_management_input(question, respondent_role, report_topics)` to interview management on the 5 essential pillars:
    1. Legal profile and genesis
    2. Operations and products
    3. Financial performance and capitalization
    4. Board governance and leadership
    5. Key risks and mitigation strategies

### Step 5: Canonical Facts Ledger Construction
- Call `build_facts_ledger(force=True)`.
- The ledger extracts verified numeric values with strict digit verification against source text.
- Inspect `missing_core_keys` and `conflicts` to identify areas requiring management clarification or highlighted disclosures.

---

## 2. Guardrails

- **Zero Fabricated Sources**: Never cite simulated URLs, phantom filings, or unverified documents.
- **Scope Isolation**: Ensure reporting scope (standalone vs consolidated) matches user intent.
- **Evidence Boundary**: If evidence volume is low, state the limitation rather than attempting to fill gaps with hallucinated sources.
