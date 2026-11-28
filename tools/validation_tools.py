"""Entity guard, duplication, and section validation tools."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional
from langchain_core.tools import BaseTool, tool

from config import settings
from financial import FinancialVerifier
from jurisdictions import get_jurisdiction, get_industry_benchmark
from tools.session import ReportSession
from utils import foreign_entities, shingle_set, word_count


def build_validation_tools(session: ReportSession) -> List[BaseTool]:
    """Create entity guard and section quality validation tools bound to `session`."""

    @tool
    def check_entities(markdown: str) -> str:
        """Detect any foreign, competitor, or unapproved organization names in a drafted section.

        Uses the confirmed company identity and human-approved allow/exclude lists.
        """
        flagged = foreign_entities(
            markdown,
            names=session.approved_names,
            excluded=session.excluded_names,
            statuses=session.entity_statuses,
            check_evidence=False,
        )
        return json.dumps({
            "is_clean": len(flagged) == 0,
            "flagged_count": len(flagged),
            "flagged_entities": [
                {"name": disp, "key": key, "reason": reason}
                for disp, key, reason in flagged
            ],
        }, indent=2)

    @tool
    def check_section(
        markdown: str,
        section_name: str,
        evidence_excerpts: Optional[List[str]] = None,
    ) -> str:
        """Validate a drafted section for ungrounded numbers, duplication across sections, blank cells, and ledger mismatches.

        If validation passes, registers this section's text so future sections are checked for repetition against it.
        """
        issues: List[str] = []
        body = (markdown or "").strip()
        words = word_count(body)
        if words < 30:
            issues.append(f"Section '{section_name}' is very short ({words} words).")

        # 1. Blank table cells
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("|") and not re.match(r"^\|[\s:\-|]+\|$", s):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if any(c == "" for c in cells):
                    issues.append(f"Blank table cell found in row: {s[:80]}")
                    break

        # 2. Duplication / shingle overlap against earlier sections
        sh_new = shingle_set(body)
        if sh_new:
            dup_limit = getattr(settings, "MAX_SECTION_OVERLAP", 0.35)
            for prev_name, prev_text in session.completed_sections.items():
                if prev_name == section_name:
                    continue
                sh_old = shingle_set(prev_text)
                if sh_old:
                    overlap = len(sh_new & sh_old) / max(1, len(sh_new))
                    if overlap >= dup_limit:
                        issues.append(
                            f"High repetition ({overlap:.0%}) with earlier section '{prev_name}'. "
                            "Summarize instead of restating."
                        )

        # 3. Canonical ledger consistency
        facts = [
            f for f in session.ledger.get_all_facts()
            if f.numeric_value is not None and getattr(f.status, "value", str(f.status)) != "CONFLICTING"
        ]
        for fact in facts:
            term = fact.key.replace("_", " ").lower()
            for line in body.splitlines():
                if term in line.lower():
                    nums = [
                        float(n.replace(",", ""))
                        for n in re.findall(r"\b\d+(?:,\d+)*(?:\.\d+)?\b", line)
                        if len(n.replace(".", "").replace(",", "")) >= 2
                    ]
                    nums = [n for n in nums if not (1900 <= n <= 2100 and n.is_integer())]
                    if nums:
                        matches = any(
                            abs(n - fact.numeric_value) < 0.01 * (abs(fact.numeric_value) + 1e-5)
                            for n in nums
                        )
                        if not matches:
                            main_digits = re.sub(r"[^\d]", "", str(int(abs(fact.numeric_value))))
                            line_digits = re.sub(r"[^\d]", "", line)
                            if len(main_digits) >= 2 and main_digits not in line_digits:
                                issues.append(
                                    f"Ledger mismatch for {fact.key}: draft says '{line.strip()[:80]}' "
                                    f"vs canonical '{fact.value}' ({fact.period})."
                                )
                                break

        # 4. Arithmetic check
        fin_res = FinancialVerifier.audit_draft_financials(body, evidence_text=" ".join(evidence_excerpts or []))
        if not fin_res.is_valid:
            issues.extend(fin_res.errors)

        # 5. Entity check
        flagged = foreign_entities(
            body,
            names=session.approved_names,
            excluded=session.excluded_names,
            evidence_text=" ".join(evidence_excerpts or []),
            statuses=session.entity_statuses,
            check_evidence=bool(evidence_excerpts),
        )
        for disp, _, reason in flagged:
            issues.append(f"Entity guard flagged '{disp}': {reason}")

        passed = len(issues) == 0
        if passed:
            session.completed_sections[section_name] = body

        return json.dumps({
            "section": section_name,
            "status": "PASS" if passed else "FAIL",
            "word_count": words,
            "issues": issues,
            "warnings": fin_res.warnings,
            "reconciled_items": fin_res.reconciled_items,
        }, indent=2)

    @tool
    def check_regulatory_coverage(sector: str = "") -> str:
        """Compare drafts with the local jurisdiction registry and optional sector guidance.

        Registry and industry matches are screening aids only. Verify applicability against
        authoritative rules before treating anything as mandatory or complete.
        """
        import glob
        juri = get_jurisdiction(session.country)
        sec_bm = get_industry_benchmark(sector)

        drafts_dir = os.path.join(session.run_dir, "drafts")
        draft_files = glob.glob(os.path.join(drafts_dir, "*.md")) if os.path.exists(drafts_dir) else []

        drafts_text: Dict[str, str] = {}
        for df in draft_files:
            try:
                with open(df, "r", encoding="utf-8", errors="replace") as f:
                    drafts_text[os.path.basename(df)] = f.read()
            except Exception:
                pass

        # Also consider sections registered in session
        for s_name, s_txt in session.completed_sections.items():
            if s_name not in drafts_text:
                drafts_text[s_name] = s_txt

        combined_text = "\n\n".join(drafts_text.values()).lower()

        met_sections: List[Dict[str, Any]] = []
        missing_sections: List[Dict[str, Any]] = []
        uncertain_sections: List[Dict[str, Any]] = []

        # 1. Statutory mandatory sections check
        for m in juri.mandatory_sections:
            title_clean = m.title.lower()
            toks = [t for t in re.findall(r"[a-z0-9]+", title_clean) if len(t) >= 4]
            matched_file = None
            for fname, ftxt in drafts_text.items():
                if any(t in fname.lower() or t in ftxt[:500].lower() for t in toks):
                    matched_file = fname
                    break

            if matched_file:
                wc = word_count(drafts_text[matched_file])
                if wc >= 100:
                    met_sections.append({"requirement": m.title, "type": "Statutory Mandate", "file": matched_file, "word_count": wc})
                else:
                    uncertain_sections.append({
                        "requirement": m.title,
                        "type": "Statutory Mandate",
                        "file": matched_file,
                        "issue": f"Drafted section is thin ({wc} words < 100 words requirement).",
                    })
            else:
                missing_sections.append({"requirement": m.title, "type": "Statutory Mandate", "purpose": m.purpose, "kind": m.kind})

        # 2. Industry guidance is advisory and never counted as a statutory requirement.
        industry_topics: List[Dict[str, Any]] = []
        for ch in sec_bm.recommended_chapters:
            ch_clean = ch.lower()
            toks = [t for t in re.findall(r"[a-z0-9]+", ch_clean) if len(t) >= 4]
            matched_file = None
            for fname, ftxt in drafts_text.items():
                if any(t in fname.lower() or t in ftxt[:500].lower() for t in toks):
                    matched_file = fname
                    break

            if matched_file:
                wc = word_count(drafts_text[matched_file])
                if wc >= 80:
                    industry_topics.append({"topic": ch, "status": "mentioned", "file": matched_file, "word_count": wc})
                else:
                    industry_topics.append({"topic": ch, "status": "briefly_mentioned", "file": matched_file, "word_count": wc})
            else:
                industry_topics.append({"topic": ch, "status": "not_found", "advisory": True})

        total_req = len(juri.mandatory_sections)
        cov_score = round(len(met_sections) / max(1, total_req), 2)

        return json.dumps({
            "jurisdiction": f"{juri.country_name} ({juri.regime_name})",
            "statute": juri.governing_statute,
            "industry_sector": sec_bm.sector_name,
            "registry_match_ratio": cov_score,
            "is_legal_compliance_certified": False,
            "review_note": "Local registry coverage is incomplete and matching is heuristic. Check authoritative rules for entity status, framework, period, and applicability. This tool cannot certify compliance.",
            "met_registry_matches_count": len(met_sections),
            "missing_registry_matches_count": len(missing_sections),
            "uncertain_count": len(uncertain_sections),
            "met_sample": met_sections[:8],
            "missing_sections": missing_sections[:8],
            "uncertain_sections": uncertain_sections,
            "industry_guidance_is_advisory": True,
            "industry_topics": industry_topics,
        }, indent=2)

    return [check_entities, check_section, check_regulatory_coverage]
