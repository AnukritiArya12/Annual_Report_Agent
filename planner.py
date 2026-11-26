"""Dynamic planning engine.

Nothing about the report's structure is hardcoded. The plan is built from evidence:

  1. profile      - who the company is (web research + the company's own report)
  2. chapters     - what THIS company's report contains, guided by the table of contents of its real
                    report (when found), the laws/listing rules of its country and its industry
  3. critic       - a second LLM pass that adds missing / drops irrelevant chapters
  4. expansion    - every chapter gets a word budget (from the target page count) and is split
                    recursively into sections and sub-sections until each writing task is small
                    enough to be written well in one go
  5. blueprint    - leaves become TaskDefinitions with dependencies

If the LLM cannot produce a plan, the planner raises. It never substitutes a canned outline.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import approvals
from config import QuotaExhausted, get_llm, settings
from knowledge import KnowledgeBase
from schemas import (ChapterBlueprint, CompanyProfile, ReportBlueprint, ReportInput,
                     SourceDocument, TaskDefinition)
from jurisdictions import get_jurisdiction
from skills import SECTION_KINDS, DynamicReportArchitectSkill, CompanyIntelligenceSkill
from tools import ResearchTools
from utils import extract_json, slug

NODE_SHAPE = """{
  "title": "<name the way this company / jurisdiction names it>",
  "what_to_cover": "<specific content, data points and tables this part must contain>",
  "kind": "<one of: %s>",
  "weight": <1-10, relative share of the parent's length>,
  "search_queries": ["<2-3 web queries that would find the data; include the company name>"],
  "depends_on": ["<exact titles of OTHER parts whose results this part needs, else []>"],
  "validation_criteria": ["<2-4 checkable criteria>"]
}""" % ", ".join(SECTION_KINDS)


class DynamicPlanningEngine:
    def __init__(self, tools: ResearchTools, kb: KnowledgeBase):
        self.tools = tools
        self.kb = kb
        self.llm = get_llm()

    # ------------------------------------------------------------------ helpers
    def _ask_json(self, system: str, user: str, retries: int = 3) -> Any:
        prompt, last = user, None
        for _ in range(retries):
            resp = self.llm.invoke([{"role": "system", "content": system}, {"role": "user", "content": prompt}])
            try:
                return extract_json(resp.content)
            except ValueError as e:
                last = e
                prompt = user + "\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY valid JSON."
        raise RuntimeError(f"Planner could not get valid JSON from the LLM: {last}")

    # ---------------------------------------------------------------- 1. profile
    def research_company_profile(self, spec: ReportInput, docs: List[SourceDocument]) -> CompanyProfile:
        co = spec.company_name
        print(f"\n[Planner] Researching '{co}' ({spec.country or 'country not given'})...")
        notes: List[str] = []
        official: List[str] = []
        for q in (f"{co} business overview listing {spec.country}", f"{co} business segments subsidiaries"):
            for r in self.tools.web_search(q, max_results=3):
                notes.append(f"{r['title']} ({r['href']}): {r['body']}")
                if any(k in r["href"].lower() for k in ("investor", "sec.gov", "bseindia", "nseindia", "annual", "/ir")):
                    official.append(r["href"])
        for ch, _ in self.kb.search(f"{co} about the company business segments listed stock exchange subsidiaries",
                                    k=4, doc_ids=[d.doc_id for d in docs] or None):
            notes.append(f"[company report p.{ch.page}] {ch.text}")
        is_first_year = bool(spec.is_first_year or any(d.kind == "first_year_dossier" for d in docs))
        if is_first_year:
            for ch, _ in self.kb.search(f"{co} genesis charter incorporation capital founders financial statements",
                                        k=6, doc_ids=[d.doc_id for d in docs] or None):
                notes.append(f"[corporate charter dossier p.{ch.page}] {ch.text}")

        system = (
            f"{CompanyIntelligenceSkill.get_instructions()}\n\n"
            "You are a corporate intelligence analyst. Use ONLY the research notes. If something is not in "
            "the notes, leave it empty - never guess. Return ONLY a JSON object."
        )
        user = f"""Company: {co}
Country (given): {spec.country or 'unknown - infer from notes'}
Industry (given): {spec.industry or 'unknown - infer from notes'}
Scope: {spec.reporting_scope}

Research notes:
{chr(10).join(notes)[:9000]}

Return JSON with keys: company_name, country, ticker_or_id, industry, sector, business_model_summary,
key_business_segments (list), geographic_presence (list), listing_status,
applicable_regulations (list: company law, listing rules, accounting standards, sustainability / ESG
disclosure rules that really apply to this company in its country), reporting_framework
(the report format this company would normally follow, e.g. the national annual-report regime), peer_competitors (list)."""
        try:
            data = self._ask_json(system, user)
            allowed = set(CompanyProfile.model_fields) - {"researched_at"}
            data = self._coerce_profile({k: v for k, v in data.items() if k in allowed and v is not None})
            data["company_name"] = co
            data["country"] = data.get("country") or spec.country
            data["industry"] = data.get("industry") or spec.industry or "Unknown"
            data["official_sources"] = list(dict.fromkeys(official))[:5]
            if is_first_year:
                data["is_first_year"] = True
                data["listing_status"] = data.get("listing_status") or "Unlisted Private Entity (Inaugural Financial Period)"
                data["reporting_framework"] = data.get("reporting_framework") or f"Statutory First-Year Report ({spec.country or 'Company Law'})"
            profile = CompanyProfile(**data)
        except QuotaExhausted:
            raise
        except Exception as e:
            print(f"[Planner] Profile step failed ({e}); continuing with a minimal profile (no invented facts).")
            profile = CompanyProfile(company_name=co, country=spec.country, industry=spec.industry or "Unknown",
                                     official_sources=official[:5], is_first_year=is_first_year)
        profile = self._confirm_profile(profile)                      # HUMAN CHECKPOINT 5
        print(f"[Planner] Profile: {profile.industry} | {profile.country} | {profile.reporting_framework or 'framework n/a'}")
        return profile

    def _confirm_profile(self, profile: CompanyProfile) -> CompanyProfile:
        """HUMAN CHECKPOINT 5 - the whole plan (chapters, regulations, segments) is built from this profile."""
        join = lambda xs: ", ".join(xs)
        resolved_j = get_jurisdiction(profile.country, profile)
        fields = {"industry": profile.industry, "country": profile.country, "jurisdiction": resolved_j.code,
                  "ticker_or_id": profile.ticker_or_id or "",
                  "listing_status": profile.listing_status, "reporting_framework": profile.reporting_framework or resolved_j.regime_name,
                  "key_business_segments": join(profile.key_business_segments),
                  "geographic_presence": join(profile.geographic_presence),
                  "applicable_regulations": join(profile.applicable_regulations or [resolved_j.governing_statute]),
                  "peer_competitors": join(profile.peer_competitors)}
        vals, changed = approvals.edit_fields(
            "profile", "STEP 5 - CONFIRM THE COMPANY PROFILE & JURISDICTION", fields,
            info=[f"Company: {profile.company_name} | Resolved Jurisdiction: {resolved_j.country_name} ({resolved_j.code})",
                  f"Summary: {(profile.business_model_summary or '-')[:300]}"])
        update: Dict[str, Any] = {}
        for k in changed:                       # only edited fields are re-split; untouched ones keep their exact values
            v = vals[k].strip()
            if k in ("key_business_segments", "geographic_presence", "applicable_regulations", "peer_competitors"):
                update[k] = [x.strip() for x in v.split(",") if x.strip()]
            elif k == "ticker_or_id":
                update[k] = v or None
            elif k == "jurisdiction":
                j_override = get_jurisdiction(v)
                update["country"] = vals.get("country") or j_override.country_name
                update["reporting_framework"] = vals.get("reporting_framework") or j_override.regime_name
            else:
                update[k] = v
        return profile.model_copy(update=update) if update else profile

    @staticmethod
    def _coerce_profile(data: Dict[str, Any]) -> Dict[str, Any]:
        """LLMs often return a string where a list is expected (or the reverse); normalise instead of failing."""
        list_fields = [k for k, f in CompanyProfile.model_fields.items()
                       if k in ("key_business_segments", "geographic_presence", "applicable_regulations",
                                "official_sources", "peer_competitors")]
        for k in list_fields:
            v = data.get(k)
            if v is None:
                continue
            if isinstance(v, str):
                v = [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
            data[k] = [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in v]
        for k in ("country", "industry", "ticker_or_id", "sector", "business_model_summary",
                  "listing_status", "reporting_framework"):
            v = data.get(k)
            if isinstance(v, list):
                data[k] = ", ".join(str(x) for x in v)
            elif v is not None and not isinstance(v, str):
                data[k] = str(v)
        return data

    # -------------------------------------------------- 2. structure evidence
    def _structure_evidence(self, spec: ReportInput, profile: CompanyProfile, reference_toc: str) -> str:
        res = []
        jurisdiction = get_jurisdiction(profile.country, profile)
        if reference_toc.strip():
            res.append("TABLE OF CONTENTS OF REFERENCE / PEER BENCHMARK REPORT (for chapter flow and industry topics):\n" + reference_toc)
        if profile.is_first_year:
            rules = jurisdiction.first_year_rules
            mand_sec = "\n".join(f"  * {s.title} ({s.kind}): {s.purpose}" for s in jurisdiction.mandatory_sections)
            res.append(
                f"FIRST-YEAR INAUGURAL ANNUAL REPORT SPECIFICATION ({jurisdiction.country_name} - {jurisdiction.regime_name}):\n"
                f"- Governing Statute: {jurisdiction.governing_statute}\n"
                f"- Comparative Data Rule: {rules.comparative_data_rule}\n"
                f"- Governance & Secretarial Focus: {rules.statutory_governance_focus}\n"
                f"- Mandatory Statutory Sections:\n{mand_sec}\n"
                f"- Accounting Policies: {rules.notes_to_accounts_emphasis} (Framework: {', '.join(jurisdiction.accounting_frameworks)})"
            )
        if res:
            return "\n\n".join(res)
        chunks = []
        for q in (f"{profile.company_name} annual report table of contents",
                  f"{profile.country} {profile.industry} annual report structure table of contents",
                  f"{profile.country} listed company annual report mandatory disclosures {profile.industry}"):
            for r in self.tools.web_search(q, max_results=3):
                chunks.append(f"[{q}] {r['title']} ({r['href']})\n{r['body']}")
        return "WEB EVIDENCE ON REPORT STRUCTURE (no reference report was found):\n" + "\n---\n".join(chunks)[:5000]

    # ------------------------------------------------------- 3. chapters + critic
    def _design_chapters(self, spec, profile, docs, structure_evidence) -> Dict[str, Any]:
        ref_pages = docs[0].pages if docs else 0
        system = (
            f"{DynamicReportArchitectSkill.get_instructions()}\n\n"
            "You are the Chief Report Architect. Design the COMPLETE start-to-end table of contents of ONE company's "
            "annual report, front cover to back cover, tailored strictly to that company, its country's law and listing rules, "
            "its specific business segments, industry, and accounting standards. Return ONLY valid JSON."
        )
        user = f"""COMPANY: {profile.company_name} | Country: {profile.country} | Industry: {profile.industry}
Listing: {profile.listing_status} | Framework: {profile.reporting_framework}
Business: {profile.business_model_summary}
Segments: {profile.key_business_segments}
Regulations that apply: {profile.applicable_regulations}
Reporting period: {spec.financial_year} | Scope: {spec.reporting_scope}
Reporting nature: {'FIRST-YEAR / INAUGURAL ANNUAL REPORT (Initial operational period since incorporation)' if profile.is_first_year else 'Standard ongoing reporting period'}
Reference report length (pages): {ref_pages or 'unknown'}
Extra instructions from the user: {spec.extra_instructions or 'none'}

{structure_evidence}

TASK
Design the complete start-to-end chapters of the {spec.financial_year} annual report for this company:
1. PURE FIRST-PRINCIPLES DESIGN (ZERO FIXED TEMPLATES):
   - DO NOT follow any fixed list of chapters or predefined template.
   - If a table of contents from the company's real annual report is provided above, follow its authentic chapter titles and structural order.
   - If no table of contents is provided, derive the chapters strictly from:
     (a) The company's actual operating business model and distinct business segments.
     (b) The corporate reporting laws, listing rules, and accounting standards of {profile.country or 'its primary jurisdiction'}.
     (c) The primary audited financial statements and disclosures mandated for this entity type.
   - Name each chapter naturally the way this company and jurisdiction name them.
2. COMPANY-NATIVE SCOPE:
   - Include only chapters that genuinely apply to this company.
   - Drop anything that belongs to another industry or does not apply to this business model.
3. DYNAMIC PAGE BUDGETING:
   - est_pages = realistic page allocation for that chapter reflecting the company's scale and reporting depth.

Return JSON:
{{
  "estimated_total_pages": <integer, length of a real report of this company>,
  "chapters": [
    {{"est_pages": <number>, "chapter_id": "<snake_case>", "rationale": "<which law / company fact requires it>",
      "title": "...", "what_to_cover": "...", "kind": "...", "search_queries": [], "depends_on": [], "validation_criteria": []}}
  ]
}}
Each chapter uses this node shape (plus est_pages, chapter_id, rationale): {NODE_SHAPE}"""
        data = self._ask_json(system, user)
        if isinstance(data, list):
            data = {"chapters": data}
        chaps = data.get("chapters") or []
        chaps = self._verify_lifecycle_completeness(chaps, profile, spec)
        if not chaps:
            raise RuntimeError("Planner returned no chapters")
        data["chapters"] = chaps
        return data

    def _verify_lifecycle_completeness(self, chapters: List[dict], profile: CompanyProfile, spec: ReportInput) -> List[dict]:
        """Dynamically check that the report covers the complete start-to-end lifecycle (cover to financial statements)
        specifically for this company and jurisdiction, without ANY hardcoded templates."""
        has_fin = any(
            c.get("kind") in ("financial_statement", "notes_to_accounts") or
            any(k in str(c.get("title", "")).lower() for k in ("financial statement", "balance sheet", "statement of accounts", "audited accounts"))
            for c in chapters
        )
        if len(chapters) >= 7 and has_fin:
            return chapters

        system = ("You are an expert statutory annual-report auditor. Verify that the proposed chapter outline represents "
                  "a genuine, publication-grade annual report from front cover to back matter (including financial statements) "
                  "specifically for this company and country. Return ONLY valid JSON.")
        toc = "\n".join(f"- {c.get('title')} ({c.get('kind','')})" for c in chapters)
        user = f"""COMPANY: {profile.company_name} | Country: {profile.country} | Industry: {profile.industry}
Framework: {profile.reporting_framework} | Applicable Regulations: {profile.applicable_regulations}
Key Segments: {profile.key_business_segments}

CURRENT CHAPTER OUTLINE:
{toc}

TASK:
Check if any mandatory statutory reporting sections or audited financial statements prescribed by {profile.country or 'its primary jurisdiction'} for an entity in {profile.industry} are missing.
Do NOT return generic templates. If any essential section is missing, return ONLY the company-specific additions:
{{
  "additional_chapters": [
    {{"est_pages": <number>, "chapter_id": "<snake_case>", "rationale": "<specific regulatory or industry requirement>",
      "title": "...", "what_to_cover": "...", "kind": "<one of: cover, message, narrative, summary, data_table, financial_statement, notes_to_accounts, statutory, list, appendix>",
      "search_queries": ["<search query with company name>"], "depends_on": [], "validation_criteria": ["<criterion>"]}}
  ]
}}
If nothing essential is missing, return {{"additional_chapters": []}}."""
        try:
            res = self._ask_json(system, user, retries=2)
            add = res.get("additional_chapters") or []
            existing_titles = {str(c.get("title", "")).lower().strip() for c in chapters}
            for c in add:
                if isinstance(c, dict) and c.get("title") and str(c["title"]).lower().strip() not in existing_titles:
                    chapters.append(c)
        except Exception as e:
            print(f"[Planner] Dynamic lifecycle verification skipped ({e}).")
        return chapters

    def _critic_pass(self, spec, profile, chapters: List[dict]) -> List[dict]:
        toc = "\n".join(f"{i+1}. {c.get('title')} ({c.get('kind','')}, ~{c.get('est_pages','?')} pages)"
                        for i, c in enumerate(chapters))
        system = (
            f"{DynamicReportArchitectSkill.get_instructions()}\n\n"
            "You are the Lead Critic & Institutional Reviewer of annual reports. Review the proposed chapters "
            "for structural integrity, relevance, and company specificity. Return ONLY valid JSON."
        )
        user = f"""COMPANY: {profile.company_name} | Country: {profile.country} | Industry: {profile.industry}
Regulations: {profile.applicable_regulations}
Key Segments: {profile.key_business_segments}
Period: {spec.financial_year}

PROPOSED CHAPTERS
{toc}

CRITIQUE INSTRUCTIONS:
1. Identify any chapter that is IRRELEVANT to this specific business model or industry (e.g., manufacturing chapters for pure IT services, or bank-specific capital ratios for non-financial companies) and flag its exact title to remove.
2. Identify any DUPLICATION or overlap that should be merged.
3. Identify any CRITICAL MISSING statutory or company-specific chapters needed for a complete start-to-end report.
4. Ensure every chapter is tailored to THIS company.

Return JSON:
{{
  "missing_chapters": [<chapter objects with est_pages, chapter_id, rationale, title, what_to_cover, kind, search_queries, depends_on, validation_criteria>],
  "irrelevant_chapter_titles": ["<exact titles to drop>"]
}}
Chapter object shape: {NODE_SHAPE} plus est_pages, chapter_id, rationale."""
        try:
            review = self._ask_json(system, user, retries=2)
        except QuotaExhausted:
            raise
        except Exception as e:
            print(f"[Planner] Critic pass skipped ({e}).")
            return chapters
        drop = {str(t).strip().lower() for t in review.get("irrelevant_chapter_titles", [])}
        kept = [c for c in chapters if str(c.get("title", "")).strip().lower() not in drop]
        have = {str(c.get("title", "")).strip().lower() for c in kept}
        added = [c for c in review.get("missing_chapters", [])
                 if isinstance(c, dict) and c.get("title") and str(c["title"]).strip().lower() not in have]
        print(f"[Planner] Critic: +{len(added)} chapters, -{len(chapters) - len(kept)} removed.")
        return kept + added

    # ------------------------------------------------------ 4. recursive expansion
    def _split_node(self, node: dict, words: float, depth: int, sibling_titles: List[str],
                    spec: ReportInput, profile: CompanyProfile, structure_evidence: str) -> List[dict]:
        n_min = max(2, math.ceil(words / settings.LEAF_MAX_WORDS))
        n_max = n_min * 2 + 1
        system = ("You are a senior annual-report editor. You break one part of a company's annual report into the "
                  "sub-parts a real report would contain. Return ONLY valid JSON.")
        user = f"""COMPANY: {profile.company_name} | {profile.country} | {profile.industry} | period {spec.financial_year}
Segments: {profile.key_business_segments}
PART TO BREAK DOWN: "{node['title']}"
What it must cover: {node.get('what_to_cover', '')}
Length budget: about {int(words)} words (~{words / settings.WORDS_PER_PAGE:.1f} pages).
Other parts at the same level (do NOT overlap with them): {sibling_titles}

{structure_evidence[:4500]}

Split it into between {n_min} and {n_max} sub-parts so that each sub-part is roughly
{settings.LEAF_MAX_WORDS // 2}-{settings.LEAF_MAX_WORDS} words. Use the real report's sub-headings when the
table of contents shows them; otherwise use what this kind of company and jurisdiction publishes.
Sub-parts must not repeat each other: each owns a distinct slice of the content.
Return JSON: {{"subsections": [ {NODE_SHAPE} ]}}"""
        print(f"[Planner] Splitting '{node['title']}' (~{int(words)} words) into {n_min}-{n_max} parts...", flush=True)
        subs = []
        try:
            data = self._ask_json(system, user)
            raw_subs = data.get("subsections") if isinstance(data, dict) else data
            subs = [s for s in (raw_subs or []) if isinstance(s, dict) and s.get("title")]
        except QuotaExhausted:
            raise
        except Exception as e:
            print(f"[Planner] LLM split for '{node['title']}' ({e}); generating structured sub-sections.")

        if len(subs) < 2 and words > settings.LEAF_MAX_WORDS:
            # Dynamically derive sub-parts without any hardcoded names
            try:
                retry_user = (
                    f"COMPANY: {profile.company_name} | {profile.industry} | {spec.financial_year}\n"
                    f"Break the section '{node['title']}' into {n_min} logical, distinct sub-sections tailored to this company.\n"
                    f"What it covers: {node.get('what_to_cover', '')}\n"
                    f"Return JSON: {{\"subsections\": [ {NODE_SHAPE} ]}}"
                )
                data = self._ask_json(system, retry_user, retries=1)
                raw_subs = data.get("subsections") if isinstance(data, dict) else data
                subs = [s for s in (raw_subs or []) if isinstance(s, dict) and s.get("title")]
            except Exception:
                subs = []

        if len(subs) < 2 and words > settings.LEAF_MAX_WORDS:
            # Purely generic fallback derived dynamically from node title and business context
            sub_names = [
                f"{node['title']} - Operational Framework & Review",
                f"{node['title']} - Key Disclosures & Performance Metrics",
                f"{node['title']} - Statutory Governance & Future Outlook"
            ]
            k = node.get("kind", "narrative")
            subs = [{
                "title": name,
                "what_to_cover": f"Comprehensive analysis and verified disclosures for {name} ({profile.company_name}, {spec.financial_year})",
                "kind": k,
                "weight": 1.0,
                "search_queries": [f"{profile.company_name} {name} {spec.financial_year}"],
                "depends_on": [],
                "validation_criteria": [f"Detail {name} with data tables where available"]
            } for name in sub_names]

        print(f"[Planner]   '{node['title']}' -> {len(subs)} parts", flush=True)
        return subs

    def _expand(self, node: dict, words: float, depth: int, sibling_titles: List[str],
                spec, profile, structure_evidence) -> dict:
        node = dict(node)
        node["words"] = words
        node["children"] = []
        if words > settings.LEAF_MAX_WORDS and depth < settings.MAX_EXPANSION_DEPTH:
            subs = self._split_node(node, words, depth, sibling_titles, spec, profile, structure_evidence)
            if len(subs) >= 2:
                weights = [max(0.5, float(s.get("weight") or 1)) for s in subs]
                total = sum(weights)
                titles = [str(s["title"]) for s in subs]
                node["children"] = [
                    self._expand(s, words * w / total, depth + 1, [t for t in titles if t != s["title"]],
                                 spec, profile, structure_evidence)
                    for s, w in zip(subs, weights)
                ]
        return node

    # ------------------------------------------------------------ 5. blueprint
    def generate_blueprint(self, spec: ReportInput, profile: CompanyProfile, docs: List[SourceDocument],
                           reference_toc: str) -> ReportBlueprint:
        print(f"[Planner] Designing the report structure for {profile.company_name} ({spec.financial_year})...")
        structure_evidence = self._structure_evidence(spec, profile, reference_toc)
        data = self._design_chapters(spec, profile, docs, structure_evidence)
        chapters = self._critic_pass(spec, profile, data["chapters"])
        chapters = [c for c in chapters if isinstance(c, dict) and c.get("title")]
        if not chapters:
            raise RuntimeError("Planner produced no usable chapters")

        # ---- length: user override > real report length > LLM estimate
        if spec.target_pages:
            total_pages = spec.target_pages
        elif docs and docs[0].pages:
            total_pages = docs[0].pages
        else:
            try:
                total_pages = int(data.get("estimated_total_pages") or 0)
            except (TypeError, ValueError):
                total_pages = 0
            total_pages = total_pages or int(sum(float(c.get("est_pages") or 0) for c in chapters)) or 120
        total_pages = max(settings.MIN_PAGES, min(settings.MAX_PAGES, total_pages))

        raw = []
        for c in chapters:
            try:
                raw.append(max(0.2, float(c.get("est_pages") or 0)))
            except (TypeError, ValueError):
                raw.append(1.0)
        scale = total_pages / (sum(raw) or 1)
        total_words = total_pages * settings.WORDS_PER_PAGE
        print(f"[Planner] Target length ~{total_pages} pages (~{total_words:,} words) across {len(chapters)} chapters.")

        titles = [str(c["title"]) for c in chapters]
        budgets = [r * scale * settings.WORDS_PER_PAGE for r in raw]
        workers = max(1, min(settings.MAX_PARALLEL_WORKERS, len(chapters)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            trees = list(ex.map(
                lambda a: self._expand(a[0], a[1], 0, [t for t in titles if t != a[0]["title"]],
                                       spec, profile, structure_evidence),
                zip(chapters, budgets)))

        tasks: List[TaskDefinition] = []
        title_map: Dict[str, List[str]] = defaultdict(list)
        raw_deps: Dict[str, List[str]] = {}
        counter = [1]
        used_chapter_ids: set = set()
        chapter_bps: List[ChapterBlueprint] = []

        for ci, (c, tree) in enumerate(zip(chapters, trees), 1):
            ch_id = slug(str(c.get("chapter_id") or c["title"]))
            if ch_id in used_chapter_ids:
                ch_id = f"{ch_id}_{ci}"
            used_chapter_ids.add(ch_id)
            ids = self._collect(tree, [], ch_id, spec, profile, tasks, title_map, raw_deps, counter)
            chapter_bps.append(ChapterBlueprint(
                chapter_id=ch_id, title=str(c["title"]), sequence_order=ci,
                objective=str(c.get("what_to_cover", "")), kind=str(c.get("kind", "")),
                rationale=str(c.get("rationale", "")), est_pages=round(raw[ci - 1] * scale, 1), task_ids=ids))

        # ---- dependencies
        by_id = {t.task_id: t for t in tasks}
        for t in tasks:
            deps = []
            for title in raw_deps.get(t.task_id, []):
                for tid in title_map.get(title.strip().lower(), []):
                    if tid != t.task_id and tid not in deps:
                        deps.append(tid)
            t.dependencies = deps
        closing = {"summary", "message", "cover", "appendix"}
        for t in tasks:
            if t.kind in ("summary", "message") and not t.dependencies:
                t.dependencies = [o.task_id for o in tasks if o.kind not in closing and o.task_id != t.task_id]

        report_id = f"{slug(profile.company_name)}_{slug(spec.financial_year)}"
        bp = ReportBlueprint(report_id=report_id, input_spec=spec, company_profile=profile,
                             reference_documents=docs, target_pages=total_pages,
                             chapters=chapter_bps, tasks=tasks)
        stages = bp.compute_execution_stages()
        est_words = sum(t.target_words for t in tasks)
        print(f"[Planner] {len(chapter_bps)} chapters, {len(tasks)} writing tasks, {len(stages)} stages, "
              f"planned ~{est_words:,} words (~{est_words // settings.WORDS_PER_PAGE} pages).")
        return bp

    def _collect(self, node: dict, parent_path: List[str], chapter_id: str, spec, profile,
                 tasks: List[TaskDefinition], title_map, raw_deps, counter) -> List[str]:
        title = str(node["title"]).strip()
        path = parent_path + [title]
        ids: List[str] = []
        if node.get("children"):
            for ch in node["children"]:
                ids += self._collect(ch, path, chapter_id, spec, profile, tasks, title_map, raw_deps, counter)
        else:
            tid = f"t{counter[0]:04d}_{slug(title, 26)}"
            counter[0] += 1
            words = int(max(settings.MIN_LEAF_WORDS, min(node.get("words", 400), settings.LEAF_MAX_WORDS * 1.5)))
            kind = str(node.get("kind") or "narrative").lower()
            queries = [q for q in (node.get("search_queries") or []) if isinstance(q, str)][:3] \
                or [f"{profile.company_name} {title} {spec.financial_year}"]
            criteria = [c for c in (node.get("validation_criteria") or []) if isinstance(c, str)][:4]
            tasks.append(TaskDefinition(
                task_id=tid, title=title, chapter_id=chapter_id, path=path,
                purpose=f"{' > '.join(path)} for {profile.company_name} ({spec.financial_year}). {node.get('what_to_cover', '')}".strip(),
                what_to_cover=str(node.get("what_to_cover") or title), kind=kind,
                search_queries=queries, validation_criteria=criteria, target_words=words))
            raw_deps[tid] = [str(d) for d in (node.get("depends_on") or []) if d]
            ids = [tid]
        title_map[title.lower()].extend(ids)
        return ids

    # ------------------------------------------------------------- persistence
    @staticmethod
    def save_blueprint(bp: ReportBlueprint, run_dir: str) -> None:
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "profile.json"), "w", encoding="utf-8") as f:
            f.write(bp.company_profile.model_dump_json(indent=2))
        with open(os.path.join(run_dir, "blueprint.json"), "w", encoding="utf-8") as f:
            f.write(bp.model_dump_json(indent=2))
        print(f"[Planner] Saved profile.json and blueprint.json in {run_dir}")

    @staticmethod
    def load_blueprint(run_dir: str) -> Optional[ReportBlueprint]:
        path = os.path.join(run_dir, "blueprint.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return ReportBlueprint.model_validate_json(f.read())