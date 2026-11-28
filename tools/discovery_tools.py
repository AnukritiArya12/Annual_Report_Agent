"""Source discovery, identity resolution, and document ingestion tools."""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from langchain_core.tools import BaseTool, tool

from connectors.registry import get_connectors_for_jurisdiction
from jurisdictions import get_jurisdiction, get_industry_benchmark
from tools.session import ReportSession
from utils import chunk_foreign_reason, mentions_company, sanitize_untrusted, slug


def score_and_rank_candidates(
    candidates: List[Dict[str, Any]],
    company_name: str,
    target_fy: str,
    target_scope: str = "consolidated",
    approved_names: Optional[List[str]] = None,
    excluded_names: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Score discovered candidates using multi-factor explainable criteria.

    Factors:
    - official_status (0.25): Regulatory filing repository (SEC, Companies House, BSE/NSE) vs IR vs Web.
    - exact_entity (0.25): Exact legal entity match vs core tokens vs unapproved look-alike.
    - reporting_year (0.20): Target fiscal year match vs off-year vs historical.
    - reporting_scope (0.10): Consolidated vs standalone alignment.
    - completeness (0.15): Full Annual Report / 10-K vs 10-Q vs presentation.
    - recency (0.05): Document publication recency relative to period end.
    """
    appr_names = [n.lower() for n in (approved_names or [company_name])]
    excl_names = [n.lower() for n in (excluded_names or [])]
    target_years = re.findall(r"\d{4}", target_fy)
    target_year_str = target_years[-1] if target_years else ""

    selected: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for cand in candidates:
        title = cand.get("title", "")
        url = cand.get("url", "")
        period = cand.get("period", "")
        source = cand.get("source", "")
        raw = cand.get("raw_data") or {}

        text_for_eval = f"{title} {url} {period} {source}".lower()

        # 1. Official status
        if any(k in text_for_eval for k in ("sec_edgar", "sec.gov", "bseindia.com", "nseindia.com", "companieshouse.gov.uk", "official")):
            score_official = 1.0
            official_note = "Official regulatory statutory filing"
        elif any(k in text_for_eval for k in ("investor_relations", "ir.", "investor")):
            score_official = 0.8
            official_note = "Official corporate investor relations channel"
        else:
            score_official = 0.35
            official_note = "Secondary web/media candidate"

        # 2. Exact entity
        score_entity = 0.1
        entity_note = "Name not prominent in candidate metadata"
        entity_reject = None

        # Check excluded names first
        for ex in excl_names:
            if ex and ex in text_for_eval:
                score_entity = 0.0
                entity_reject = f"Matches excluded look-alike or separate legal entity '{ex}'"
                break

        if score_entity > 0.0:
            if any(an in text_for_eval for an in appr_names):
                score_entity = 1.0
                entity_note = "Exact target entity match"
            else:
                # Token overlap without corp words
                corp_stopwords = {"inc", "ltd", "corp", "corporation", "limited", "plc", "llc", "company", "co"}
                co_toks = [t for t in re.findall(r"[a-z0-9]+", company_name.lower()) if t not in corp_stopwords and len(t) >= 3]
                if co_toks and any(t in text_for_eval for t in co_toks):
                    score_entity = 0.75
                    entity_note = "Partial core token match"

        # 3. Reporting year
        cand_years = re.findall(r"\b(20\d{2})\b", f"{period} {title}")
        score_year = 0.3
        year_note = "Fiscal year unspecified in metadata"
        year_reject = None

        if target_year_str:
            if target_year_str in cand_years or target_year_str in period:
                score_year = 1.0
                year_note = f"Exact target period match ({target_fy})"
            elif cand_years:
                diff = min(abs(int(y) - int(target_year_str)) for y in cand_years)
                if diff == 1:
                    score_year = 0.4
                    year_note = f"Adjacent period filing ({cand_years[0]})"
                else:
                    score_year = 0.0
                    year_reject = f"Mismatched fiscal year: filing is for {cand_years[0]}, expected {target_fy}"

        # 4. Reporting scope
        score_scope = 0.7
        scope_note = "Scope not explicitly differentiated"
        if target_scope.lower() == "consolidated":
            if "consolidated" in text_for_eval:
                score_scope = 1.0
                scope_note = "Matches requested consolidated reporting scope"
            elif "standalone" in text_for_eval and "consolidated" not in text_for_eval:
                score_scope = 0.35
                scope_note = "Standalone filing when consolidated requested"
        elif target_scope.lower() == "standalone":
            if "standalone" in text_for_eval:
                score_scope = 1.0
                scope_note = "Matches requested standalone reporting scope"
            elif "consolidated" in text_for_eval:
                score_scope = 0.45
                scope_note = "Consolidated filing when standalone requested"

        # 5. Completeness
        score_complete = 0.6
        complete_note = "Standard filing document"
        complete_reject = None
        if any(k in text_for_eval for k in ("10-k", "20-f", "annual report", "annual accounts", "integrated report")):
            score_complete = 1.0
            complete_note = "Comprehensive full annual report / 10-K filing"
        elif any(k in text_for_eval for k in ("10-q", "quarterly", "interim", "half-year")):
            score_complete = 0.35
            complete_reject = "Interim or quarterly disclosure rather than full annual report"
        elif any(k in text_for_eval for k in ("presentation", "transcript", "earnings call", "factsheet", "press release")):
            score_complete = 0.2
            complete_reject = "Incomplete presentation, earnings deck, or press release"

        # 6. Recency
        score_recency = 1.0 if score_year >= 0.8 else (0.5 if score_year > 0 else 0.2)

        # Composite score calculation
        composite = round(
            0.25 * score_official
            + 0.25 * score_entity
            + 0.20 * score_year
            + 0.10 * score_scope
            + 0.15 * score_complete
            + 0.05 * score_recency,
            3,
        )

        breakdown = {
            "official_status": score_official,
            "entity_match": score_entity,
            "reporting_year": score_year,
            "reporting_scope": score_scope,
            "completeness": score_complete,
            "recency": score_recency,
        }

        eval_record = {
            "title": cand.get("title", ""),
            "url": cand.get("url", ""),
            "period": cand.get("period", ""),
            "source": cand.get("source", ""),
            "score": composite,
            "breakdown": breakdown,
        }

        # Decision thresholding
        if entity_reject:
            eval_record["rejection_reason"] = entity_reject
            rejected.append(eval_record)
        elif year_reject:
            eval_record["rejection_reason"] = year_reject
            rejected.append(eval_record)
        elif complete_reject and composite < 0.50:
            eval_record["rejection_reason"] = complete_reject
            rejected.append(eval_record)
        elif composite < 0.45:
            eval_record["rejection_reason"] = f"Composite confidence below threshold ({composite} < 0.45)"
            rejected.append(eval_record)
        else:
            eval_record["selection_reason"] = (
                f"{official_note}; {entity_note}; {year_note} (Composite score: {composite})"
            )
            selected.append(eval_record)

    selected.sort(key=lambda x: x["score"], reverse=True)
    rejected.sort(key=lambda x: x["score"], reverse=True)
    return selected, rejected


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
        """Query official regulatory connectors and IR pages with explainable multi-factor ranking.

        Evaluates candidates across official status, exact entity, reporting year, scope,
        completeness, and recency. Saves evaluated candidates to disk with explicit reasons.
        """
        juri = get_jurisdiction(session.country)
        connectors = get_connectors_for_jurisdiction(juri, tools=session.research_tools, run_dir=session.run_dir)
        raw_candidates: List[Dict[str, Any]] = []

        for conn in connectors:
            try:
                found = conn.search(company, fy)
                for c in found:
                    raw_candidates.append({
                        "title": sanitize_untrusted(c.title),
                        "url": c.url,
                        "period": c.period,
                        "confidence": round(c.confidence, 2),
                        "source": c.source_name,
                        "raw_data": c.raw_data or {},
                        "domain_approved": session.is_domain_approved(c.url),
                    })
            except Exception as e:
                raw_candidates.append({
                    "source": conn.__class__.__name__,
                    "error": str(e),
                })

        selected, rejected = score_and_rank_candidates(
            raw_candidates,
            company_name=company,
            target_fy=fy,
            target_scope=session.reporting_scope,
            approved_names=session.approved_names,
            excluded_names=session.excluded_names,
        )

        # Persist candidate ranking and reasons to disk
        candidates_record = {
            "company": company,
            "financial_year": fy,
            "reporting_scope": session.reporting_scope,
            "jurisdiction": juri.code,
            "evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "selected_count": len(selected),
            "rejected_count": len(rejected),
            "selected_candidates": selected,
            "rejected_candidates": rejected,
        }
        cand_path = os.path.join(session.run_dir, "source_candidates.json")
        try:
            with open(cand_path, "w", encoding="utf-8") as f:
                json.dump(candidates_record, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        session.update_manifest("sources_evaluated", {
            "selected_count": len(selected),
            "rejected_count": len(rejected),
            "top_source": selected[0]["url"] if selected else "none",
        })

        return json.dumps({
            "company": company,
            "financial_year": fy,
            "jurisdiction": juri.code,
            "selected_count": len(selected),
            "rejected_count": len(rejected),
            "selected_candidates": selected[:8],
            "rejected_sample": rejected[:4],
            "candidates_file": cand_path,
        }, indent=2)

    @tool
    def search_company_sources(query: str, max_results: int = 8) -> str:
        """Search broadly for company information and annual-report sources in any jurisdiction.

        Returns candidate URLs and snippets only. Review the entity, reporting period, and source
        before ingestion. Remote ingestion still requires domain approval.
        """
        raw_hits = session.research_tools.web_search(query, max_results=max(1, min(max_results, 20)))
        results = []
        for hit in raw_hits:
            title = sanitize_untrusted(hit.get("title", ""))
            snippet = sanitize_untrusted(hit.get("body", ""))
            url = hit.get("href", "")
            if not url:
                continue
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet[:600],
                "domain_approved": session.is_domain_approved(url),
                "target_company_match": mentions_company(
                    f"{title} {snippet}", session.approved_names
                ),
            })
        return json.dumps({"query": query, "results": results}, indent=2)

    @tool
    def ingest_documents(urls_or_paths: List[str]) -> str:
        """Download (if URL), parse PDF/DOCX (with OCR fallback), sanitize untrusted text, chunk, and index into storage.

        GUARDRAIL: Remote URLs are strictly blocked unless their domain has been approved by a human.
        Any URL or domain on the human rejection list is permanently refused.
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
                        "reason": "Domain not approved by human or explicitly rejected. Call request_human_decision to resolve.",
                    })
                    continue
                local_path = session.research_tools.download_pdf(src_clean, session.run_dir) or ""
                if not local_path or not os.path.exists(local_path):
                    txt = session.research_tools.fetch_text(src_clean) or session.research_tools.scrape_url(src_clean)
                    if not txt:
                        rejected.append({"source": src_clean, "reason": "Failed to download or scrape content."})
                        continue
                    safe_txt = sanitize_untrusted(txt)
                    if not mentions_company(safe_txt, session.approved_names):
                        rejected.append({"source": src_clean, "reason": "Content does not mention approved company."})
                        continue
                    foreign_reason = chunk_foreign_reason(
                        safe_txt, session.approved_names, session.excluded_names, session.entity_statuses
                    )
                    if foreign_reason:
                        rejected.append({"source": src_clean, "reason": f"Content failed the entity guard: {foreign_reason}."})
                        continue
                    doc_id = slug(src_clean)[:48]
                    from knowledge import _html_to_pages
                    web_pages = _html_to_pages(safe_txt) or [(1, safe_txt)]
                    session.storage.add_document(doc_id, src_clean[:80], src_clean, web_pages)
                    ingested.append({"doc_id": doc_id, "source": src_clean, "pages": len(web_pages)})
                    session.update_manifest("document_ingested", {"doc_id": doc_id, "source": src_clean, "pages": len(web_pages)})
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
                source_text = "\n".join(text for _, text in safe_pages)
                if not mentions_company(source_text, session.approved_names):
                    rejected.append({
                        "source": src_clean,
                        "reason": (
                            "Document content does not identify the target company. It was not ingested. "
                            "Verify the legal entity or confirm a legitimate company alias before retrying."
                        ),
                    })
                    continue
                foreign_reason = chunk_foreign_reason(
                    source_text,
                    session.approved_names,
                    session.excluded_names,
                    session.entity_statuses,
                )
                if foreign_reason:
                    rejected.append({"source": src_clean, "reason": f"Document failed the entity guard: {foreign_reason}."})
                    continue
                doc_id = slug(os.path.basename(local_path))[:48]
                session.storage.add_document(doc_id, os.path.basename(local_path), src_clean, safe_pages)
                ingested.append({"doc_id": doc_id, "source": src_clean, "pages": n_pages})
                session.update_manifest("document_ingested", {"doc_id": doc_id, "source": src_clean, "pages": n_pages})
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
    def create_cold_start_dossier(fields: Dict[str, str], source_label: str = "user-provided") -> str:
        """Index any user-provided company information for a first-time or private-company report.

        Accepts whatever verified details are available (narrative, operating metrics, financial data,
        governance, risks, plans, or history). Missing fields are reported as gaps, not treated as blockers.
        Prior-period information is retained as management-provided and must be verified before use.
        """
        juri = get_jurisdiction(session.country)
        co = session.company_name
        fy = session.financial_year

        # Keep the intake open-ended: first-time companies differ in what information exists.
        safe_fields: Dict[str, str] = {}
        raw_dict = fields or {}
        for k, v in raw_dict.items():
            safe_fields[k] = sanitize_untrusted(str(v))

        lines = [
            f"# {co} - Company Information Dossier ({fy})",
            f"Jurisdiction: {juri.country_name} ({juri.regime_name})",
            f"Information source: {sanitize_untrusted(source_label)}",
            "Information status: Company-provided information; verify material claims against available records and identify unsupported items.",
            "",
        ]
        for k, v in safe_fields.items():
            lines.append(f"- **{k.replace('_', ' ').title()}**: {v}")

        lines.append("")
        lines.append("Information not supplied in this dossier is unknown; it is not evidence that the company had no activity or obligation.")

        doc_id = f"dossier_{slug(co)}_{slug(fy)}"
        session.storage.add_document(doc_id, f"Internal Dossier ({co})", "internal://dossier", [(1, "\n".join(lines))])
        session.update_manifest("dossier_indexed", {
            "doc_id": doc_id,
            "fields_count": len(safe_fields),
            "source_label": source_label,
        })

        return json.dumps({
            "status": "indexed",
            "doc_id": doc_id,
            "fields_recorded": len(safe_fields),
            "information_received": bool(safe_fields),
            "coverage_status": "PARTIAL; completeness depends on the report scope and source coverage.",
            "total_chunks_in_storage": session.storage.total_chunks(),
        }, indent=2)

    @tool
    def request_management_input(
        question: str,
        respondent_role: str,
        report_topics: Optional[List[str]] = None,
        why_needed: str = "",
    ) -> str:
        """Ask a company founder, executive, or team for missing first-report information.

        The CLI pauses for a free-text answer. The answer is indexed as management-provided
        information, not independently verified. Ask specific, material questions and identify
        the expected respondent. Use only when available records do not answer the question.
        """
        from langgraph.types import interrupt

        payload = {
            "kind": "management_input",
            "question": question,
            "respondent_role": respondent_role,
            "report_topics": report_topics or [],
            "why_needed": why_needed,
        }
        answer = interrupt(payload)
        answer_text = sanitize_untrusted(str(answer or "")).strip()
        if not answer_text:
            return json.dumps({"status": "NO_INFORMATION_PROVIDED", "question": question})

        answer_doc = "\n".join([
            f"# Management Interview: {respondent_role}",
            f"Company: {session.company_name}",
            f"Reporting period: {session.financial_year}",
            f"Question: {sanitize_untrusted(question)}",
            f"Topics: {', '.join(report_topics or [])}",
            "Source status: Management-provided; not independently verified.",
            "Answer:",
            answer_text,
        ])
        answer_id = f"management_input_{slug(respondent_role)}_{slug(question)[:32]}"
        chunks = session.storage.add_document(
            answer_id,
            f"Management interview - {respondent_role}",
            "internal://management-interview",
            [(1, answer_doc)],
            kind="management_interview",
            fiscal_year=session.financial_year,
            matches_period=True,
        )
        session.update_manifest("management_input_recorded", {
            "doc_id": answer_id,
            "respondent_role": respondent_role,
            "topics": report_topics or [],
            "why_needed": why_needed,
            "independently_verified": False,
        })
        return json.dumps({
            "status": "RECORDED_AS_MANAGEMENT_PROVIDED",
            "doc_id": answer_id,
            "respondent_role": respondent_role,
            "chunks_indexed": chunks,
            "verification_status": "NOT_INDEPENDENTLY_VERIFIED",
        }, indent=2)

    @tool
    def research_industry_peers(industry_or_sector: str) -> str:
        """Research standard reporting chapters, KPIs, and risk factors from peer companies in an industry.

        GUARDRAIL: Peer guidance is strictly structural. Peer company names, hypothetical metrics,
        or sample data MUST NEVER be recorded into the canonical facts ledger or cited as target facts.
        """
        bm = get_industry_benchmark(industry_or_sector)
        return json.dumps({
            "queried_sector": industry_or_sector,
            "matched_sector": bm.sector_name,
            "data_isolation_notice": (
                "PEER BENCHMARK ONLY: Use this structure to outline chapters and disclosure topics. "
                "Do NOT record peer metrics or peer company names into the target company's facts ledger."
            ),
            "peer_structure_notes": bm.peer_structure_notes,
            "recommended_chapters": bm.recommended_chapters,
            "key_metrics_and_kpis": bm.key_metrics_and_kpis,
            "specific_risk_factors": bm.specific_risk_factors,
        }, indent=2)

    return [
        resolve_company_identity,
        find_source_documents,
        search_company_sources,
        ingest_documents,
        web_search_approved,
        create_cold_start_dossier,
        request_management_input,
        research_industry_peers,
    ]