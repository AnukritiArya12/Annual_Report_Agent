"""Source discovery, identity resolution, and document ingestion tools."""

from __future__ import annotations

import json
import os
from typing import Dict, List
from langchain_core.tools import BaseTool, tool

from connectors.registry import get_connectors_for_jurisdiction
from jurisdictions import get_jurisdiction, get_industry_benchmark
from tools.session import ReportSession
from utils import chunk_foreign_reason, mentions_company, sanitize_untrusted, slug


def build_discovery_tools(session: ReportSession) -> List[BaseTool]:
    """Create identity resolution, source discovery, and ingestion tools bound to `session`."""

    @tool
    def resolve_company_identity(name: str, country: str = "") -> str:
        """Search public registries and web sources to identify candidate legal entities for the target company.

        Returns candidate legal names, tickers/registration IDs, and parent/subsidiary hints
        so the agent or human can confirm the exact reporting entity.
        """
        juri = get_jurisdiction(country or session.country)
        hits = session.research_tools.web_search(
            f'"{name}" legal entity OR parent OR subsidiary {juri.country_name}',
            max_results=5,
        )
        candidates = []
        for h in hits:
            candidates.append({
                "title": sanitize_untrusted(h.get("title", "")),
                "url": h.get("href", ""),
                "snippet": sanitize_untrusted(h.get("body", ""))[:300],
            })
        return json.dumps({
            "queried_name": name,
            "jurisdiction_code": juri.code,
            "regime": juri.regime_name,
            "governing_statute": juri.governing_statute,
            "mandatory_sections": [
                {"title": s.title, "purpose": s.purpose, "kind": s.kind}
                for s in juri.mandatory_sections
            ],
            "first_year_rules": {
                "comparative_data_rule": juri.first_year_rules.comparative_data_rule,
                "statutory_governance_focus": juri.first_year_rules.statutory_governance_focus,
            },
            "candidates": candidates,
        }, indent=2)

    @tool
    def find_source_documents(company: str, fy: str) -> str:
        """Query official regulatory connectors and IR pages to discover annual report candidates.

        Uses the jurisdiction's official connectors (e.g. SEC EDGAR, BSE/NSE, Companies House, IR crawler).
        Returns candidate URLs, titles, periods, and confidence scores.
        Note: Before ingesting any discovered web URL, you must obtain human approval for its domain.
        """
        juri = get_jurisdiction(session.country)
        connectors = get_connectors_for_jurisdiction(juri, tools=session.research_tools, run_dir=session.run_dir)
        all_candidates = []
        for conn in connectors:
            try:
                found = conn.search(company, fy)
                for c in found:
                    all_candidates.append({
                        "title": sanitize_untrusted(c.title),
                        "url": c.url,
                        "period": c.period,
                        "confidence": round(c.confidence, 2),
                        "source": c.source_name,
                        "domain_approved": session.is_domain_approved(c.url),
                    })
            except Exception as e:
                all_candidates.append({
                    "source": conn.__class__.__name__,
                    "error": str(e),
                })

        return json.dumps({
            "company": company,
            "financial_year": fy,
            "jurisdiction": juri.code,
            "candidates": all_candidates[:12],
        }, indent=2)

    @tool
    def ingest_documents(urls_or_paths: List[str]) -> str:
        """Download (if URL), parse PDF/DOCX (with OCR fallback), sanitize untrusted text, chunk, and index into storage.

        GUARDRAIL: Remote URLs are strictly blocked unless their domain has been approved by a human.
        Local file paths are always permitted.
        """
        ingested = []
        rejected = []

        for src in urls_or_paths:
            src_clean = (src or "").strip()
            if not src_clean:
                continue

            if src_clean.lower().startswith(("http://", "https://")):
                if not session.is_domain_approved(src_clean):
                    rejected.append({
                        "source": src_clean,
                        "reason": "Domain not approved by human. Call request_human_decision to approve domain first.",
                    })
                    continue
                local_path = session.research_tools.download_pdf(src_clean, session.run_dir) or ""
                if not local_path or not os.path.exists(local_path):
                    # Try fetching text/HTML if not a PDF
                    txt = session.research_tools.scrape_url(src_clean) or session.research_tools.fetch_text(src_clean)
                    if not txt:
                        rejected.append({"source": src_clean, "reason": "Failed to download or scrape content."})
                        continue
                    safe_txt = sanitize_untrusted(txt)
                    if not mentions_company(safe_txt, session.approved_names):
                        rejected.append({"source": src_clean, "reason": "Content does not mention approved company."})
                        continue
                    doc_id = slug(src_clean)[:48]
                    session.storage.add_document(doc_id, src_clean[:80], src_clean, [(1, safe_txt)])
                    ingested.append({"doc_id": doc_id, "source": src_clean, "pages": 1})
                    continue
            else:
                local_path = src_clean
                if not os.path.exists(local_path):
                    rejected.append({"source": src_clean, "reason": "Local file does not exist."})
                    continue

            ext = os.path.splitext(local_path)[1].lower()
            try:
                from knowledge import parse_docx, parse_pdf
                if ext == ".pdf":
                    pages, _, n_pages = parse_pdf(local_path)
                elif ext in (".docx", ".doc"):
                    pages, _, n_pages = parse_docx(local_path)
                elif ext in (".txt", ".md", ".json"):
                    with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                        pages = [(1, f.read())]
                    n_pages = 1
                else:
                    rejected.append({"source": src_clean, "reason": f"Unsupported file type: {ext}"})
                    continue

                safe_pages = [(p_no, sanitize_untrusted(txt)) for p_no, txt in pages]
                doc_id = slug(os.path.basename(local_path))[:48]
                session.storage.add_document(doc_id, os.path.basename(local_path), src_clean, safe_pages)
                ingested.append({"doc_id": doc_id, "source": src_clean, "pages": n_pages})
            except Exception as e:
                rejected.append({"source": src_clean, "reason": f"Parse error: {e}"})

        return json.dumps({
            "ingested": ingested,
            "rejected": rejected,
            "total_chunks_in_storage": session.storage.total_chunks(),
        }, indent=2)

    @tool
    def web_search_approved(query: str, max_results: int = 5) -> str:
        """Search the web and return only snippets from human-approved domains that mention the company.

        GUARDRAIL: Any result from an unapproved domain or mentioning an excluded/forbidden entity is dropped.
        """
        raw_hits = session.research_tools.web_search(query, max_results=max_results * 2)
        kept = []
        dropped_unapproved = 0
        for h in raw_hits:
            href = h.get("href", "")
            if not session.is_domain_approved(href):
                dropped_unapproved += 1
                continue
            title = sanitize_untrusted(h.get("title", ""))
            body = sanitize_untrusted(h.get("body", ""))
            combined = f"{title} {body}"
            if not mentions_company(combined, session.approved_names):
                continue
            reason = chunk_foreign_reason(
                combined, session.approved_names, session.excluded_names, session.entity_statuses, strict_org=True
            )
            if reason:
                continue
            kept.append({"title": title, "url": href, "snippet": body[:400]})
            if len(kept) >= max_results:
                break

        return json.dumps({
            "query": query,
            "results": kept,
            "dropped_unapproved_domains": dropped_unapproved,
        }, indent=2)

    @tool
    def create_cold_start_dossier(fields: Dict[str, str]) -> str:
        """Create and index an internal corporate dossier for a company with no public filings.

        Accepts verified fields (e.g. registration_id, incorporation_date, business_model,
        initial_capital, revenue, expenses, net_profit, cash_at_bank) and indexes them into storage.
        """
        juri = get_jurisdiction(session.country)
        co = session.company_name
        fy = session.financial_year
        safe_fields = {k: sanitize_untrusted(str(v)) for k, v in (fields or {}).items()}
        lines = [
            f"# {co} - Verified Internal Dossier ({fy})",
            f"Jurisdiction: {juri.country_name} ({juri.regime_name})",
            "",
        ]
        for k, v in safe_fields.items():
            lines.append(f"- **{k.replace('_', ' ').title()}**: {v}")
        doc_id = f"dossier_{slug(co)}"
        session.storage.add_document(doc_id, f"Internal Dossier ({co})", "internal://dossier", [(1, "\n".join(lines))])
        return json.dumps({
            "status": "indexed",
            "doc_id": doc_id,
            "fields_recorded": len(safe_fields),
            "total_chunks_in_storage": session.storage.total_chunks(),
        }, indent=2)

    @tool
    def research_industry_peers(industry_or_sector: str) -> str:
        """Research standard reporting chapters, KPIs, and risk factors from peer companies in an industry.

        Use this when planning a report for any company (especially new startups, private entities,
        or unfamiliar sectors like Banking, Manufacturing, SaaS, Pharma, or Retail) to adopt
        authoritative peer chapter structures.
        """
        bm = get_industry_benchmark(industry_or_sector)
        return json.dumps({
            "queried_sector": industry_or_sector,
            "matched_sector": bm.sector_name,
            "peer_structure_notes": bm.peer_structure_notes,
            "recommended_chapters": bm.recommended_chapters,
            "key_metrics_and_kpis": bm.key_metrics_and_kpis,
            "specific_risk_factors": bm.specific_risk_factors,
        }, indent=2)

    return [
        resolve_company_identity,
        find_source_documents,
        ingest_documents,
        web_search_approved,
        create_cold_start_dossier,
        research_industry_peers,
    ]
