---
name: source-discovery
description: Locate, filter, rank, and ingest authentic corporate filings and official documents for any company.
---

# Source Discovery Skill

## NAME
source-discovery

## OBJECTIVE
Discover and ingest high-confidence primary corporate documents (annual reports, 10-K/20-F filings, audited financial statements) for the specified company and reporting period while strictly excluding unrelated entities, competitors, and unapproved web domains.

## DECISION RULES
1. **Prioritize Explainable Ranking**: Always query `find_source_documents` to obtain multi-factor scored candidates (official status, exact entity, reporting year, scope, completeness, recency). Inspect selected candidates and review rejection reasons in `source_candidates.json`.
2. **Respect Human Decisions & Whitelisting**: Never ingest an unapproved or rejected web URL. If high-confidence filings reside on an external domain, call `request_human_decision` with `approve_domain="domain.com"` before calling `ingest_documents`. If a human rejects a domain or document, adhere to the rejection permanently.
3. **Verify Entity Matching**: Verify that candidate titles, registrant names, or accession metadata match the target entity. Treat subsidiaries as distinct from parent entities unless consolidated scope is explicitly requested.
4. **Local Over Web**: If the user provided local files, ingest them first as authoritative primary records.

## CONSTRAINTS
- Zero ingestion of unapproved domains. Unapproved URLs will be dropped by tool guardrails.
- No company, country, or industry names are hardcoded. Retrieve jurisdiction-specific rules dynamically via `resolve_company_identity`.
- Do not ingest duplicate filings for the same period.

## RECOVERY
- If regulatory connectors yield zero candidates, fall back to `web_search_approved` across already approved domains.
- If web search yields no candidates or the company is unlisted/pre-IPO, transition immediately to the `cold-start-company` skill.
