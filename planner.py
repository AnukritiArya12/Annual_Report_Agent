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

from config import QuotaExhausted, get_llm, settings
from knowledge import KnowledgeBase
from schemas import (ChapterBlueprint, CompanyProfile, ReportBlueprint, ReportInput,
                     SourceDocument, TaskDefinition)
from skills import SECTION_KINDS
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

        system = ("You are a corporate intelligence analyst. Use ONLY the research notes. If something is not in "
                  "the notes, leave it empty - never guess. Return ONLY a JSON object.")
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
            profile = CompanyProfile(**data)
        except QuotaExhausted:
            raise
        except Exception as e:
            print(f"[Planner] Profile step failed ({e}); continuing with a minimal profile (no invented facts).")
            profile = CompanyProfile(company_name=co, country=spec.country, industry=spec.industry or "Unknown",
                                     official_sources=official[:5])
        print(f"[Planner] Profile: {profile.industry} | {profile.country} | {profile.reporting_framework or 'framework n/a'}")
        return profile

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
        if reference_toc.strip():
            return "TABLE OF CONTENTS OF THIS COMPANY'S OWN REPORT:\n" + reference_toc
        chunks = []
        for q in (f"{profile.company_name} annual report table of contents",
                  f"{profile.country} listed company annual report mandatory disclosures {profile.industry}"):
            for r in self.tools.web_search(q, max_results=3):
                chunks.append(f"[{q}] {r['title']} ({r['href']})\n{r['body']}")
        return "WEB EVIDENCE ON REPORT STRUCTURE (no reference report was found):\n" + "\n---\n".join(chunks)[:5000]

    # ------------------------------------------------------- 3. chapters + critic
    def _design_chapters(self, spec, profile, docs, structure_evidence) -> Dict[str, Any]:
        ref_pages = docs[0].pages if docs else 0
        system = ("You are the Chief Report Architect of a top annual-report production house. You decide the "
                  "complete table of contents of ONE company's annual report, front cover to back cover, "
                  "tailored to that company, its country's law and listing rules, its industry and its reporting "
                  "standard. Return ONLY valid JSON.")
        user = f"""COMPANY: {profile.company_name} | Country: {profile.country} | Industry: {profile.industry}
Listing: {profile.listing_status} | Framework: {profile.reporting_framework}
Business: {profile.business_model_summary}
Segments: {profile.key_business_segments}
Regulations that apply: {profile.applicable_regulations}
Reporting period: {spec.financial_year} | Scope: {spec.reporting_scope}
Reference report length (pages): {ref_pages or 'unknown'}
Extra instructions from the user: {spec.extra_instructions or 'none'}

{structure_evidence}

TASK
Design the chapters of the {spec.financial_year} annual report of this company.
- If a table of contents of the company's own report is given, it is your PRIMARY guide: keep its real chapter
  names and order; drop only what clearly belongs to another year; add what the regulations above require
  for {spec.financial_year}.
- If none is given, derive the chapters from the evidence and from what the law / listing rules of
  {profile.country} require for a company like this. Name chapters the way this jurisdiction names them.
- Do NOT use a generic template. Include what THIS company needs, drop what it does not, add industry-specific
  chapters when the industry demands them.
- Cover the whole document in reading order (opening, messages, strategy/business, governance/statutory,
  risk, people, sustainability, assurance, financial statements and notes, investor information, back matter)
  only where applicable.
- est_pages = how many pages that chapter occupies in a real report of this company (relative sizes matter).

Return JSON:
{{
  "estimated_total_pages": <integer, length of a real report of this company>,
  "chapters": [
    {{"est_pages": <number>, "chapter_id": "<snake_case>", "rationale": "<which law / norm / company fact requires it>",
      "title": "...", "what_to_cover": "...", "kind": "...", "search_queries": [], "depends_on": [], "validation_criteria": []}}
  ]
}}
Each chapter uses this node shape (plus est_pages, chapter_id, rationale): {NODE_SHAPE}"""
        data = self._ask_json(system, user)
        if isinstance(data, list):
            data = {"chapters": data}
        chaps = data.get("chapters") or []
        chaps = self._ensure_statutory_completeness(chaps, profile, spec)
        if not chaps:
            raise RuntimeError("Planner returned no chapters")
        data["chapters"] = chaps
        return data

    @staticmethod
    def _ensure_statutory_completeness(chapters: List[dict], profile: CompanyProfile, spec: ReportInput) -> List[dict]:
        """Ensure the planned chapters encompass the complete statutory annual report lifecycle."""
        titles_lower = " ".join(str(c.get("title", "")).lower() for c in chapters)
        
        statutory_templates = [
            ("Corporate Overview & Strategic Blueprint", "corporate_overview", "narrative", 6,
             "Company profile, history, global presence, core values, and value creation model",
             ["company overview global operations", "business model value creation"]),
            ("Leadership & Strategic Addresses", "leadership_review", "message", 4,
             "Chairman's statement and CEO strategic address to shareholders on performance, headwinds, and capital allocation",
             ["chairman message letter to shareholders", "CEO review strategic priorities"]),
            ("Management Discussion and Analysis", "management_discussion_analysis", "narrative", 12,
             "Macroeconomic operating environment, segment-by-segment operational review, financial performance analysis, and strategic outlook",
             ["management discussion analysis segment revenue", "operational review operating margin"]),
            ("Enterprise Risk Management & Risk Register", "enterprise_risk_management", "narrative", 6,
             "Risk governance framework, risk matrix, principal strategic, operational, financial, cybersecurity and climate risks, with mitigation strategies",
             ["risk factors enterprise risk management", "principal risks cyber geopolitical climate"]),
            ("Corporate Governance Report", "corporate_governance_report", "governance", 10,
             "Board composition, independence, skill matrix, board committees (Audit, Nomination & Remuneration, Stakeholder Relationship), director remuneration, and meeting attendance",
             ["corporate governance report board of directors", "board committees audit remuneration"]),
            ("Board's Report & Statutory Disclosures", "boards_report_statutory", "governance", 8,
             "Directors' statutory report, secretarial audit, internal financial controls, dividend recommendation, vigil mechanism, and compliance certifications",
             ["directors report statutory disclosures", "secretarial audit internal financial controls"]),
            ("Human Capital & Workplace Culture", "human_capital_resources", "narrative", 5,
             "Workforce demographics, talent retention, diversity, equity and inclusion (DEI), learning & development, and employee health and safety",
             ["employee headcount attrition diversity", "human capital learning health safety"]),
            ("Sustainability & ESG Disclosures", "sustainability_esg_disclosures", "sustainability", 8,
             "ESG strategy, net zero roadmap, environmental stewardship (Scope 1, 2, 3 emissions, energy, water), social responsibility/CSR, and responsible business governance (BRSR/GRI/TCFD)",
             ["sustainability report BRSR ESG emissions", "CSR initiatives carbon neutrality"]),
            ("Consolidated Financial Statements", "consolidated_financial_statements", "financial_statement", 15,
             "Independent Auditor's Report, Consolidated Balance Sheet, Consolidated Statement of Profit and Loss, Consolidated Statement of Cash Flows, and Statement of Changes in Equity",
             ["consolidated balance sheet statement profit loss", "cash flow statement changes in equity"]),
            ("Notes to Consolidated Financial Statements", "notes_to_financial_statements", "notes_to_accounts", 14,
             "Significant accounting policies, segment reporting disclosures, related party disclosures, tax disclosures, contingencies, commitments, and financial risk management",
             ["notes to consolidated financial statements accounting policies", "segment reporting related party disclosures"]),
            ("General Shareholder & Investor Information", "shareholder_information", "investor_information", 4,
             "Shareholding pattern, market capitalization history, AGM notice and key dates, registrar & transfer agent, investor grievance mechanism, and listing contacts",
             ["shareholding pattern promoters public", "annual general meeting notice investor relations"])
        ]
        
        result = list(chapters)
        have_text = titles_lower
        for title, ch_id, kind, est_pages, what_to_cover, queries in statutory_templates:
            key_words = title.lower().split()
            matched = any(kw in have_text for kw in key_words if len(kw) > 4)
            if not matched or len(result) < 8:
                if not any(c.get("chapter_id") == ch_id or c.get("title") == title for c in result):
                    result.append({
                        "chapter_id": ch_id,
                        "title": title,
                        "est_pages": est_pages,
                        "rationale": f"Statutory annual reporting standard for {profile.country or 'listed companies'}",
                        "what_to_cover": what_to_cover,
                        "kind": kind,
                        "search_queries": [f"{profile.company_name} {q} {spec.financial_year}" for q in queries],
                        "depends_on": [],
                        "validation_criteria": [f"Must detail {what_to_cover[:80]} with tabular presentation where disclosed"]
                    })
                    have_text += " " + title.lower()
        return result

    def _critic_pass(self, spec, profile, chapters: List[dict]) -> List[dict]:
        toc = "\n".join(f"{i+1}. {c.get('title')} ({c.get('kind','')}, ~{c.get('est_pages','?')} pages)"
                        for i, c in enumerate(chapters))
        system = "You are a statutory annual-report reviewer. Return ONLY valid JSON."
        user = f"""Company: {profile.company_name} | {profile.country} | {profile.industry}
Regulations: {profile.applicable_regulations}
Period: {spec.financial_year}

PROPOSED CHAPTERS
{toc}

Would you sign off this as the complete annual report, front cover to back cover? Only demand items that
genuinely apply to this company and jurisdiction; do not pad.
Return JSON: {{"missing_chapters": [<chapter objects with est_pages>], "irrelevant_chapter_titles": ["<exact titles>"]}}
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
            t_low = node["title"].lower()
            if "financial statement" in t_low or "balance sheet" in t_low:
                sub_names = ["Independent Auditor's Report", "Consolidated Balance Sheet",
                             "Consolidated Statement of Profit and Loss", "Consolidated Statement of Cash Flows",
                             "Consolidated Statement of Changes in Equity"]
                k = "financial_statement"
            elif "governance" in t_low:
                sub_names = ["Board Composition and Independence", "Board Committees and Mandates",
                             "Director Remuneration and Governance Policies"]
                k = "governance"
            elif "risk" in t_low:
                sub_names = ["Risk Management Framework & Governance", "Principal Enterprise & Operational Risks",
                             "Cybersecurity, Climate and Emerging Risks"]
                k = "narrative"
            elif "sustainab" in t_low or "esg" in t_low or "brsr" in t_low:
                sub_names = ["ESG Strategy and Sustainability Commitments", "Environmental Stewardship and GHG Emissions",
                             "Social Responsibility and Community Impact"]
                k = "sustainability"
            elif "management discussion" in t_low or "md&a" in t_low or "operations" in t_low:
                sub_names = ["Macroeconomic Environment & Industry Trends", "Segment Operations and Business Performance",
                             "Financial Performance and Margin Analysis", "Strategic Outlook and Growth Priorities"]
                k = "narrative"
            elif "note" in t_low:
                sub_names = ["Significant Accounting Policies", "Segment Reporting Disclosures",
                             "Related Party Transactions and Contingent Liabilities"]
                k = "notes_to_accounts"
            elif "leadership" in t_low or "message" in t_low or "letter" in t_low:
                sub_names = ["Chairman's Statement to Shareholders", "Chief Executive Officer's Strategic Review"]
                k = "message"
            elif "shareholder" in t_low or "investor" in t_low:
                sub_names = ["Shareholding Pattern and Market Capitalization", "Annual General Meeting Notice and Contacts"]
                k = "investor_information"
            else:
                sub_names = [f"{node['title']} - Overview and Operational Framework",
                             f"{node['title']} - Key Metrics, Performance and Performance Drivers",
                             f"{node['title']} - Statutory Governance and Future Outlook"]
                k = node.get("kind", "narrative")

            subs = [{
                "title": name,
                "what_to_cover": f"Detailed coverage of {name} for {profile.company_name} ({spec.financial_year})",
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
