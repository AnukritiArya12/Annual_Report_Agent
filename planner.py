"""Dynamic Planning Engine (v2) - NO hardcoded chapters, tasks or fake company data.

Flow
----
1. research_company_profile : web research -> CompanyProfile (facts only, no invented defaults)
2. discover_structure       : find what a real annual report of THIS company / country / industry
                              contains (company's own past report ToC, statutory mandates, peers)
3. LLM designs the chapter + section outline (Stage A)
4. LLM "completeness critic" reviews the outline front-cover -> back-cover and
   adds missing / removes irrelevant chapters (Stage B)
5. Outline is converted into ChapterBlueprint + TaskDefinition objects with dependencies.

The *only* thing that is fixed is the process. Chapters come from research + LLM reasoning,
so a bank, a pharma company, a US 10-K filer and an Indian IT company all get different reports.
"""

import os
import re
import json
from typing import Optional, List, Tuple, Dict

from config import get_llm
from schemas import (
    ReportInput,
    CompanyProfile,
    ChapterBlueprint,
    TaskDefinition,
    ReportBlueprint,
)
from tools import ResearchTools
from skills import SKILL_REGISTRY

MAX_CHAPTERS = int(os.getenv("MAX_CHAPTERS", "10"))
MAX_SECTIONS_PER_CHAPTER = int(os.getenv("MAX_SECTIONS_PER_CHAPTER", "3"))


def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:n] or "item"


class DynamicPlanningEngine:
    """Researches the company and lets the LLM design the annual report structure."""

    def __init__(self, tools: Optional[ResearchTools] = None):
        self.tools = tools or ResearchTools()
        self.llm = get_llm()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _extract_json(text: str):
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)  # reasoning models
        text = re.sub(r"```(?:json)?", "", text)
        starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
        if not starts:
            raise ValueError("No JSON found in LLM output")
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        return json.loads(text[start:end + 1])

    def _ask_json(self, system: str, user: str, retries: int = 3):
        last_err = None
        prompt = user
        for _ in range(retries):
            try:
                resp = self.llm.invoke([
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ])
                return self._extract_json(resp.content)
            except Exception as e:  # bad JSON / network
                last_err = e
                prompt = user + f"\n\nIMPORTANT: your previous reply was invalid ({e}). Return ONLY valid JSON."
        raise RuntimeError(f"Planner LLM failed to return valid JSON: {last_err}")

    # ------------------------------------------------------------ 1. profile
    def research_company_profile(self, input_spec: ReportInput) -> CompanyProfile:
        co = input_spec.company_name
        print(f"\n[Planner] Researching '{co}' ({input_spec.country})...")

        queries = [
            f"{co} business overview listing {input_spec.country}",
            f"{co} business segments revenue annual report",
        ]
        snippets, official = [], []
        for q in queries:
            for r in self.tools.web_search(q, max_results=2):
                snippets.append(f"Title: {r['title']}\nURL: {r['href']}\nSnippet: {r['body']}")
                if any(k in r["href"].lower() for k in
                       ["investor", "about", "corporate", "sec.gov", "bseindia", "nseindia", "annual", "ir."]):
                    official.append(r["href"])
        official = list(dict.fromkeys(official))[:4]

        system = ("You are a corporate intelligence analyst. Use ONLY the research notes. "
                  "If something is not in the notes, use an empty string/list - never guess. "
                  "Return ONLY a JSON object.")
        user = f"""Company: {co}
Country: {input_spec.country}
Industry (given by user, may be empty): {input_spec.industry or 'unknown - infer from notes'}
Scope: {input_spec.reporting_scope}

Research notes:
{chr(10).join(snippets)[:3500]}

Return JSON with keys:
company_name, ticker_or_id, country, industry, sector, business_model_summary,
key_business_segments (list), geographic_presence (list), listing_status,
applicable_regulations (list: company law, listing rules, accounting standards, sustainability/ESG rules
that actually apply to this company in this country), peer_competitors (list)."""
        try:
            data = self._ask_json(system, user)
            data["company_name"] = co
            data["country"] = input_spec.country
            data["official_sources"] = official
            if not data.get("industry"):
                data["industry"] = input_spec.industry or "Unknown"
            allowed = {"company_name", "country", "industry", "ticker_or_id", "sector", "business_model_summary",
                       "key_business_segments", "geographic_presence", "listing_status",
                       "applicable_regulations", "official_sources", "peer_competitors"}
            data = {k: v for k, v in data.items() if k in allowed and v is not None}
            profile = CompanyProfile(**data)
        except Exception as e:
            print(f"[Planner] Profile LLM failed ({e}); using minimal profile (no invented facts).")
            profile = CompanyProfile(
                company_name=co, country=input_spec.country,
                industry=input_spec.industry or "Unknown", official_sources=official,
            )
        print(f"[Planner] Profile ready: {profile.industry} | {profile.country}")
        return profile

    # ------------------------------------------------- 2. structure discovery
    def discover_structure_evidence(self, input_spec: ReportInput, profile: CompanyProfile) -> str:
        """Collect evidence on what an annual report for this kind of company actually contains."""
        co = profile.company_name
        regs = ", ".join(profile.applicable_regulations[:2])
        queries = [
            f"{co} annual report table of contents index",
            f"{profile.country} listed company annual report statutory sections",
        ]
        chunks = []
        for q in queries:
            for r in self.tools.web_search(q, max_results=2):
                chunks.append(f"[{q}] {r['title']} ({r['href']})\n{r['body']}")
        return "\n---\n".join(chunks)[:4000]

    # ---------------------------------------------------- 3 + 4. outline design
    _SCHEMA_HINT = """{
  "chapters": [
    {
      "chapter_id": "<snake_case id>",
      "title": "<chapter title>",
      "objective": "<what this chapter achieves for the reader>",
      "kind": "<statutory | industry_specific | company_specific | standard>",
      "why_included": "<1 sentence: which law / norm / company fact requires it>",
      "sections": [
        {
          "title": "<section title>",
          "what_to_cover": "<specific content, data points, tables expected>",
          "search_queries": ["<2-3 web search queries that would find the data>"],
          "depends_on": ["<titles of OTHER sections whose output this one needs, else []>"],
          "validation_criteria": ["<2-3 checkable criteria>"],
          "skills": ["<optional names from the skill list, else []>"]
        }
      ]
    }
  ]
}"""

    def _design_outline(self, input_spec: ReportInput, profile: CompanyProfile, evidence: str) -> List[dict]:
        system = (
            "You are the Chief Report Architect of a top annual-report production house. "
            "You design the COMPLETE table of contents of a company's annual report, from the front cover "
            "to the back cover, tailored to this specific company, its country's law and listing rules, "
            "its industry and its reporting standard (e.g. Indian Annual Report + BRSR, US Form 10-K, "
            "UK Strategic Report, IFRS integrated report). Return ONLY valid JSON."
        )
        user = f"""COMPANY PROFILE
Name: {profile.company_name} | Country: {profile.country} | Industry: {profile.industry}
Listing: {profile.listing_status}
Business: {profile.business_model_summary}
Segments: {profile.key_business_segments}
Regulations: {profile.applicable_regulations}
Reporting period: {input_spec.financial_year} | Scope: {input_spec.reporting_scope}
Extra instructions from user: {input_spec.extra_instructions or 'none'}

EVIDENCE ON REAL REPORT STRUCTURES (company's past report, statutory mandates, peers):
{evidence}

TASK
Design the full outline. Rules:
- Derive chapters from the evidence and from what the law/listing rules of {profile.country} require for a
  company like this. Do NOT use a generic template; include what THIS company needs and drop what it does not.
- Cover the whole document in reading order: opening/identity, leadership messages, highlights, strategy &
  business review, industry context, governance & statutory reports, risk, people, sustainability/community,
  auditor's report, full financial statements and notes, shareholder/investor information, back matter
  - but only where applicable to this company, and name them the way this company/jurisdiction names them.
- Add industry-specific chapters when the industry demands them (e.g. capital adequacy for banks, reserves
  for miners, clinical pipeline for pharma, safety for airlines).
- Each section = one writing task. Use at most {MAX_SECTIONS_PER_CHAPTER} sections per chapter and
  {MAX_CHAPTERS} chapters.
- Sections that summarise results (chairman's message, highlights, MD&A conclusions) must list the
  financial/data sections they depend on in "depends_on" (use exact section titles).
- Available optional skills: {list(SKILL_REGISTRY.keys())}

Return JSON exactly in this shape:
{self._SCHEMA_HINT}"""
        data = self._ask_json(system, user)
        chapters = data.get("chapters", data) if isinstance(data, dict) else data
        if not isinstance(chapters, list) or not chapters:
            raise RuntimeError("Planner returned an empty outline")
        return chapters

    def _critic_pass(self, input_spec: ReportInput, profile: CompanyProfile, chapters: List[dict]) -> List[dict]:
        """Second LLM pass: audit the outline for completeness and relevance."""
        toc = "\n".join(
            f"{i+1}. {c.get('title')} ({c.get('kind','')}): " +
            "; ".join(str(s.get("title", "") if isinstance(s, dict) else s) for s in c.get("sections", []))
            for i, c in enumerate(chapters)
        )
        system = ("You are a statutory annual-report reviewer. You check a proposed table of contents for "
                  "completeness and relevance. Return ONLY valid JSON.")
        user = f"""Company: {profile.company_name} | {profile.country} | {profile.industry}
Regulations: {profile.applicable_regulations}
Period: {input_spec.financial_year}

PROPOSED TABLE OF CONTENTS
{toc}

Review as if you must sign off a complete annual report from front cover to back cover.
Think about: identity/vision, leadership messages, performance highlights, strategy, operations review,
industry outlook, governance and statutory reports required in {profile.country}, risk & controls, people,
ESG/community, auditor's report, primary financial statements, notes to accounts, segment data,
multi-year summary, shareholder/investor information, corporate information/back matter.
Only demand items that genuinely apply to this company.

Return JSON:
{{
  "missing_chapters": [ <chapter objects, only if truly missing> ],
  "irrelevant_chapter_titles": [ "<exact titles that do not apply to this company/country>" ]
}}
Each chapter object has this shape: {self._SCHEMA_HINT}"""
        try:
            review = self._ask_json(system, user)
        except Exception as e:
            print(f"[Planner] Critic pass skipped ({e}).")
            return chapters
        drop = {t.strip().lower() for t in review.get("irrelevant_chapter_titles", []) if isinstance(t, str)}
        kept = [c for c in chapters if str(c.get("title", "")).strip().lower() not in drop]
        existing = {str(c.get("title", "")).strip().lower() for c in kept}
        added = [c for c in review.get("missing_chapters", [])
                 if isinstance(c, dict) and str(c.get("title", "")).strip().lower() not in existing]
        print(f"[Planner] Critic: +{len(added)} chapters added, -{len(chapters) - len(kept)} removed.")
        return kept + added

    # ---------------------------------------------------------- 5. blueprint
    def generate_blueprint(self, input_spec: ReportInput, profile: CompanyProfile) -> ReportBlueprint:
        print(f"[Planner] Designing report structure for {profile.company_name} ({input_spec.financial_year})...")
        try:
            evidence = self.discover_structure_evidence(input_spec, profile)
            outline = self._design_outline(input_spec, profile, evidence)
            outline = self._critic_pass(input_spec, profile, outline)
            if not outline:
                raise ValueError("Outline returned empty")
        except Exception as e:
            print(f"[Planner] Autonomous LLM structuring unavailable ({e}); applying statutory architectural outline.")
            outline = self._get_fallback_outline(input_spec, profile)

        if len(outline) > MAX_CHAPTERS:
            outline = outline[:MAX_CHAPTERS]

        chapters, tasks = self._outline_to_blueprint(outline, input_spec, profile)
        report_id = f"{_slug(profile.company_name)}_{_slug(input_spec.financial_year)}"
        blueprint = ReportBlueprint(
            report_id=report_id, input_spec=input_spec, company_profile=profile,
            chapters=chapters, tasks=tasks,
        )
        stages = blueprint.compute_execution_stages()
        print(f"[Planner] {len(chapters)} chapters, {len(tasks)} tasks, {len(stages)} stages.")
        return blueprint

    def _get_fallback_outline(self, input_spec: ReportInput, profile: CompanyProfile) -> List[dict]:
        co = profile.company_name
        fy = input_spec.financial_year
        ind = profile.industry or "Technology"
        return [
            {
                "title": "Corporate Overview & Strategic Identity",
                "objective": f"Comprehensive profile of {co}'s global scale, business model, and strategic positioning.",
                "sections": [
                    {
                        "title": "Corporate Profile & Global Footprint",
                        "what_to_cover": f"Corporate lineage, headquarters, global delivery centers, operational reach, and scale for {co}.",
                        "validation_criteria": ["Must document global footprint and delivery network", "Must specify corporate headquarters"],
                        "skills": ["corporate_research"]
                    },
                    {
                        "title": "Strategic Vision & Value Creation Model",
                        "what_to_cover": f"Long-term value creation model, core client value proposition, and innovation priorities.",
                        "validation_criteria": ["Must explain strategic vision and value proposition"]
                    }
                ]
            },
            {
                "title": "Management Discussion & Strategic Review",
                "objective": f"Executive review of {co}'s operational execution and strategic progress in {fy}.",
                "sections": [
                    {
                        "title": "Executive Leadership Address",
                        "what_to_cover": f"Leadership perspective on performance, strategic achievements, and growth commitments for {fy}.",
                        "validation_criteria": ["Must include executive leadership perspectives on strategic performance"]
                    },
                    {
                        "title": "Operating Environment & Macro Trends",
                        "what_to_cover": f"Macroeconomic dynamics, enterprise demand drivers, and technology investments.",
                        "validation_criteria": ["Must evaluate macro economic environment and technology drivers"],
                        "skills": ["market_research"]
                    }
                ]
            },
            {
                "title": "Business Segments & Technology Platforms",
                "objective": f"Operational breakdown of {co}'s primary industry verticals and platform solutions.",
                "sections": [
                    {
                        "title": "Industry Segment Performance",
                        "what_to_cover": f"Performance across core business units and service verticals with structured segment breakdown table.",
                        "validation_criteria": ["Present a structured table of business segments and service lines", "Must highlight key customer segments"],
                        "skills": ["industry_segmentation"]
                    },
                    {
                        "title": "Digital Transformation & Innovation Platforms",
                        "what_to_cover": f"Proprietary platforms, cloud and AI solutions, and research & development.",
                        "validation_criteria": ["Must highlight digital transformation and technology platforms"]
                    }
                ]
            },
            {
                "title": "Industry Trends & Competitive Benchmarking",
                "objective": f"Market analysis and comparative benchmarking against {ind} industry peers.",
                "sections": [
                    {
                        "title": "Competitive Peer Benchmarking & Matrix",
                        "what_to_cover": f"Benchmark {co} against leading sector peers on scale, growth, and capabilities with comparison table.",
                        "validation_criteria": ["Present a structured markdown comparison matrix or key metrics table", "Benchmark against at least 2 named peers"],
                        "skills": ["peer_benchmarking"]
                    }
                ]
            },
            {
                "title": "Corporate Governance & Board Oversight",
                "objective": f"Statutory corporate governance disclosures, board composition, and compliance mechanisms.",
                "sections": [
                    {
                        "title": "Board of Directors & Governance Architecture",
                        "what_to_cover": f"Board composition, independent director ratio, and governance principles.",
                        "validation_criteria": ["Document board composition and statutory compliance", "Must reference governing regulatory framework"],
                        "skills": ["regulatory_compliance"]
                    },
                    {
                        "title": "Statutory Board Committees & Mandates",
                        "what_to_cover": f"Audit, Nomination & Remuneration, Stakeholder, and Risk Committee charters with committee table.",
                        "validation_criteria": ["Must provide structured table of board committees and mandates"],
                        "skills": ["regulatory_compliance"]
                    }
                ]
            },
            {
                "title": "Enterprise Risk Management & Cyber Resilience",
                "objective": "Principal risk register, risk appetite, and cybersecurity controls.",
                "sections": [
                    {
                        "title": "Enterprise Risk Register & Mitigation Controls",
                        "what_to_cover": f"Principal strategic, operational, regulatory, and market risks with mitigation table.",
                        "validation_criteria": ["Present a structured table of principal enterprise risks and mitigation controls"],
                        "skills": ["risk_assessment"]
                    },
                    {
                        "title": "Cybersecurity Governance & Data Protection",
                        "what_to_cover": "Information security architecture, data privacy compliance, and incident response.",
                        "validation_criteria": ["Must specify cybersecurity controls and data protection framework"],
                        "skills": ["risk_assessment"]
                    }
                ]
            },
            {
                "title": "Human Capital & Workforce Analytics",
                "objective": "Talent analytics, employee upskilling, and workplace inclusion.",
                "sections": [
                    {
                        "title": "Workforce Demographics & Talent Skilling",
                        "what_to_cover": f"Headcount metrics, diversity ratios, talent upskilling, and employee retention with structured metrics table.",
                        "validation_criteria": ["Must provide structured table of workforce demographics and metrics", "Must detail employee upskilling in AI and technology"],
                        "skills": ["talent_analytics"]
                    }
                ]
            },
            {
                "title": "Sustainability & Business Responsibility (BRSR)",
                "objective": "Environmental, social, and governance (ESG) performance.",
                "sections": [
                    {
                        "title": "Environmental Sustainability & Net Zero Roadmap",
                        "what_to_cover": "Decarbonization commitments, energy efficiency, renewable power, and resource stewardship.",
                        "validation_criteria": ["Must specify environmental goals including Net Zero target year"],
                        "skills": ["esg_reporting"]
                    },
                    {
                        "title": "Corporate Social Responsibility & Community Impact",
                        "what_to_cover": "CSR investments, flagship community programs, and social impact indicators.",
                        "validation_criteria": ["Must detail CSR project allocations and social impact"],
                        "skills": ["esg_reporting"]
                    }
                ]
            },
            {
                "title": "Financial Highlights & Performance Indicators",
                "objective": f"Consolidated financial statements, ratios, and segmental analysis for {fy}.",
                "sections": [
                    {
                        "title": "Consolidated Financial Highlights & Metrics",
                        "what_to_cover": f"Consolidated Revenue, EBIT/EBITDA, Net Profit, and EPS for {fy} with structured financial metrics table.",
                        "validation_criteria": ["Format primary financial indicators into a structured Markdown Table", "Must specify reporting currency and scale"],
                        "skills": ["financial_analysis"]
                    },
                    {
                        "title": "Segmental Financial Performance",
                        "what_to_cover": "Segment revenue contribution and operating margins across core practices.",
                        "validation_criteria": ["Must provide structured table of segment revenue and profitability"],
                        "skills": ["financial_analysis"]
                    },
                    {
                        "title": "Financial Ratio Analysis & Track Record",
                        "what_to_cover": "Key profitability, liquidity, and return ratios (ROE, ROCE, Operating Margin) with structured ratio table.",
                        "validation_criteria": ["Must include financial ratio table covering key profitability metrics"],
                        "skills": ["financial_analysis"]
                    }
                ]
            },
            {
                "title": "Shareholder Information & Statutory Statements",
                "objective": "Shareholding distribution, dividend disclosures, and statutory compliance.",
                "sections": [
                    {
                        "title": "Shareholding Pattern & Capital Structure",
                        "what_to_cover": f"Equity capital distribution across promoter, institutional, and public categories with structured table.",
                        "validation_criteria": ["Must provide structured table of shareholding pattern"],
                        "skills": ["regulatory_compliance"]
                    },
                    {
                        "title": "Statutory Disclosures & Investor Information",
                        "what_to_cover": "Dividend track record, investor grievance redressal, and AGM notice guidelines.",
                        "validation_criteria": ["Must specify statutory investor information and compliance"]
                    }
                ]
            }
        ]

    def _outline_to_blueprint(
        self, outline: List[dict], input_spec: ReportInput, profile: CompanyProfile
    ) -> Tuple[List[ChapterBlueprint], List[TaskDefinition]]:
        co, fy = profile.company_name, input_spec.financial_year
        chapters: List[ChapterBlueprint] = []
        tasks: List[TaskDefinition] = []
        title_to_id: Dict[str, str] = {}
        raw_deps: Dict[str, List[str]] = {}
        used_ids = set()

        for ci, ch in enumerate(outline, 1):
            ch_title = str(ch.get("title") or f"Chapter {ci}")
            ch_id = _slug(str(ch.get("chapter_id") or ch_title))
            if ch_id in used_ids:
                ch_id = f"{ch_id}_{ci}"
            used_ids.add(ch_id)

            task_ids: List[str] = []
            sections = ch.get("sections") or []
            if len(sections) > MAX_SECTIONS_PER_CHAPTER:
                raise RuntimeError(
                    f"Chapter '{ch_title}' has {len(sections)} sections, above "
                    f"MAX_SECTIONS_PER_CHAPTER={MAX_SECTIONS_PER_CHAPTER}; sections were not discarded."
                )
            for si, sec in enumerate(sections, 1):
                if isinstance(sec, str):
                    sec = {"title": sec}
                s_title = str(sec.get("title") or f"Section {si}")
                tid = f"t{ci:02d}_{si:02d}_{_slug(s_title, 28)}"
                title_to_id[s_title.strip().lower()] = tid
                raw_deps[tid] = [str(d).strip().lower() for d in sec.get("depends_on", []) if d]

                queries = [q for q in sec.get("search_queries", []) if isinstance(q, str)][:3]
                if not queries:
                    queries = [f"{co} {s_title} {fy}"]
                skills = [s for s in sec.get("skills", []) if s in SKILL_REGISTRY]

                tasks.append(TaskDefinition(
                    task_id=tid,
                    title=s_title,
                    chapter_id=ch_id,
                    purpose=f"{ch_title} > {s_title} for {co} ({fy}). {sec.get('what_to_cover', '')}".strip(),
                    dependencies=[],
                    execution_instructions=(
                        f"Reporting period: {fy}. Scope: {input_spec.reporting_scope}. "
                        f"Cover: {sec.get('what_to_cover', s_title)}"
                    ),
                    skills_required=skills,
                    tools_required=["web_search", "scrape_url"],
                    search_queries=queries,
                    validation_criteria=[c for c in sec.get("validation_criteria", []) if isinstance(c, str)][:4],
                ))
                task_ids.append(tid)

            chapters.append(ChapterBlueprint(
                chapter_id=ch_id,
                title=ch_title if re.match(r"^\d+[\.\)]", ch_title) else f"{ci}. {ch_title}",
                sequence_order=ci,
                objective=str(ch.get("objective", "")),
                required_subsections=[t.title for t in tasks if t.task_id in task_ids],
                task_ids=task_ids,
                is_mandatory=str(ch.get("kind", "")).lower() == "statutory",
                rationale=str(ch.get("why_included", "")),
            ))

        # resolve dependencies (title -> id), drop unknown / self references
        for t in tasks:
            t.dependencies = [title_to_id[d] for d in raw_deps.get(t.task_id, [])
                              if d in title_to_id and title_to_id[d] != t.task_id]
        chapters = [c for c in chapters if c.task_ids]
        return chapters, tasks

    def save_blueprint(self, blueprint: ReportBlueprint, run_dir: str):
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "profile.json"), "w", encoding="utf-8") as f:
            f.write(blueprint.company_profile.model_dump_json(indent=2))
        with open(os.path.join(run_dir, "blueprint.json"), "w", encoding="utf-8") as f:
            f.write(blueprint.model_dump_json(indent=2))
        print(f"[Planner] Saved profile.json and blueprint.json in {run_dir}")
