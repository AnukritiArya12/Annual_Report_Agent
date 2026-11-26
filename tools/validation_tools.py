"""Entity guard, duplication, and section validation tools."""

from __future__ import annotations

import json
import re
from typing import List, Optional
from langchain_core.tools import BaseTool, tool

from config import settings
from financial import FinancialVerifier
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
            statuses=session.entity_statuses,
            check_evidence=False,
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

    return [check_entities, check_section]
