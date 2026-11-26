"""Validation engine: grounding, completeness, duplication and criteria checks.

Checks are generic. Nothing here assumes a particular section, industry or country; the
acceptance criteria come from the planner, per section.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from config import QuotaExhausted, get_llm, settings
from financial import FinancialVerifier
from schemas import TaskDefinition, TaskOutput, TaskStatus, ValidationResult, ValidationStatus
from statements import get_statement_for_task
from utils import extract_json, foreign_entities, shingle_set, word_count

REVIEW_GUIDANCE = (
    "FIVE-STATUS AUDIT RULES — classify this draft into exactly one status:\n\n"
    "PASS           : All facts grounded in EVIDENCE or CANONICAL FACTS. "
    "All table sums exact. Zero blank cells. No canonical fact contradicted.\n"
    "WARNING        : Grounded and truthful but minor non-blocking issues "
    "(mixed currency notation, slightly short, minor style).\n"
    "REVIEW_REQUIRED: Human confirmation needed — unfamiliar org name, conflicting dates, "
    "number disagrees with a canonical ledger fact, section under 40 words, "
    "pre-built financial table unavailable.\n"
    "FAIL           : Arithmetic mismatch (sum ≠ total), number absent from evidence AND "
    "not in canonical facts, blank table cell ('| |'), or canonical fact contradicted.\n"
    "NOT_VERIFIABLE : Material absent from all evidence; marked 'Not Disclosed in Sources "
    "Reviewed'. Nothing to validate against.\n\n"
    "NEVER mark contradictory or unverified work as PASS.\n"
    "NEVER accept blank table cells.\n"
    "NEVER accept a number that contradicts a REPORTED canonical fact without flagging it.\n"
    "ALWAYS return precise, corrective instructions for single-pass remediation."
)


class DynamicValidator:
    def __init__(self):
        self.llm = get_llm()
        self._shingles: Dict[str, set] = {}
        self.names: List[str] = []                 # approved names of THE company (set by main via set_company)
        self.excluded: List[str] = []
        self.allow_list: List[str] = []
        self.entity_flags: Dict[str, list] = {}    # task_id -> [(name, key, reason)] found in the latest draft
        self.ledger: Optional[Any] = None
        self.structured_statements: Dict[str, str] = {}

    def set_company(self, names: List[str], excluded: List[str] = (), allow_list: Sequence[str] = ()) -> None:
        self.names = [n for n in names if n]
        self.excluded = list(excluded)
        self.allow_list = [a for a in allow_list if a]
        self.entity_flags = {}

    def set_ledger(self, ledger: Optional[Any]) -> None:
        """Attach verified FactsLedger for quantitative consistency validation."""
        self.ledger = ledger

    def set_structured_statements(self, statements: Dict[str, str]) -> None:
        """Attach pre-built deterministic financial statements."""
        self.structured_statements = statements or {}

    # ------------------------------------------------------- other-company content
    def _foreign_entities(self, task: TaskDefinition, text: str, output: TaskOutput) -> List[str]:
        """Organisations in the draft that are not this company and not in the evidence it was written from."""
        if not self.names:
            return []
        evidence = " ".join(e.text for e in output.evidence_used)
        flags = foreign_entities(text, self.names, self.excluded, evidence, getattr(self, "entity_statuses", {}),
                                allow_list=self.allow_list)
        self.entity_flags[task.task_id] = flags
        if not flags:
            return []
        shown = "; ".join(f"{d} ({why})" for d, _, why in flags[:5])
        return [f"The section mentions organisation(s) that are not part of this report: {shown}. This report is ONLY "
                f"about {self.names[0]} (its own group). Delete every statement about other companies; use only "
                f"facts from the evidence about {self.names[0]}; write 'Not disclosed in the sources reviewed' otherwise."]

    # ----------------------------------------------------------- programmatic
    @staticmethod
    def _blank_table_cells(text: str) -> List[str]:
        """Verify that no Markdown table contains completely blank cells."""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("|") and s.endswith("|"):
                if re.match(r"^\|?[\s:\-|]+\|?$", s):
                    continue
                cells = [c.strip() for c in s.strip("|").split("|")]
                if any(c == "" for c in cells):
                    return ["Table contains blank cells ('| |'). Every cell must report grounded evidence or 'Not Disclosed'."]
        return []

    @staticmethod
    def _ungrounded_numbers(text: str, output: TaskOutput) -> List[str]:
        evidence = " ".join(e.text for e in output.evidence_used).replace(",", "")
        if not evidence:
            return []
        body = text.split("*Sources:")[0].replace(",", "")
        nums = {n for n in re.findall(r"\d+(?:\.\d+)?", body) if len(n.replace(".", "")) >= 3}
        nums = {n for n in nums if not (len(n) == 4 and n.startswith(("19", "20")))}   # plain years
        if len(nums) < 5:
            return []
        missing = [n for n in nums if n not in evidence]
        if len(missing) / len(nums) > 0.6:
            return [f"Most figures in this section do not appear in the collected evidence (e.g. "
                    f"{', '.join(sorted(missing)[:6])}). Use only figures present in the evidence, or write "
                    f"'Not disclosed in the sources reviewed'."]
        return []

    def _duplicates(self, task: TaskDefinition, text: str, others: Dict[str, str]) -> List[str]:
        mine = shingle_set(text)
        if len(mine) < 30:
            return []
        self._shingles[task.task_id] = mine
        for tid, other_text in others.items():
            if tid == task.task_id:
                continue
            theirs = self._shingles.get(tid)
            if theirs is None:
                theirs = self._shingles[tid] = shingle_set(other_text)
            if len(theirs) < 30:
                continue
            containment = len(mine & theirs) / len(mine)
            if containment > settings.DUPLICATE_CONTAINMENT_LIMIT:
                return [f"About {containment:.0%} of this section repeats passages already written in another "
                        f"section (task {tid}). Remove the repeated material, keep only what is unique to this part "
                        f"and refer to the other section in a clause instead."]
        return []

    def _ledger_consistency(self, text: str) -> List[str]:
        """Flags draft numbers that contradict verified facts in the canonical facts ledger."""
        if not self.ledger:
            return []
        issues: List[str] = []
        try:
            facts = [f for f in self.ledger.get_all_facts()
                     if f.numeric_value is not None and getattr(f.status, "value", str(f.status)) != "CONFLICTING"]
        except Exception:
            return []
        if not facts:
            return []

        lines = text.splitlines()
        for fact in facts:
            metric_term = fact.key.replace("_", " ").lower()
            for line in lines:
                l_lower = line.lower()
                if metric_term in l_lower:
                    line_nums = [float(n.replace(",", "")) for n in re.findall(r"\b\d+(?:,\d+)*(?:\.\d+)?\b", line)
                                 if len(n.replace(".", "").replace(",", "")) >= 2]
                    # Filter out standard 4-digit years
                    line_nums = [n for n in line_nums if not (1900 <= n <= 2100 and n.is_integer())]
                    if line_nums:
                        matches_fact = any(abs(n - fact.numeric_value) < 0.01 * (abs(fact.numeric_value) + 1e-5)
                                           for n in line_nums)
                        if not matches_fact:
                            main_digits = re.sub(r"[^\d]", "", str(int(abs(fact.numeric_value))))
                            line_digits = re.sub(r"[^\d]", "", line)
                            if len(main_digits) >= 2 and main_digits not in line_digits:
                                issues.append(
                                    f"Disagreement with Canonical Ledger: Draft reports '{line.strip()}' for {fact.key}, "
                                    f"which contradicts verified canonical fact '{fact.value}' ({fact.period})."
                                )
                                break
        return issues

    # ------------------------------------------------------------------ main
    def validate_task_output(self, task: TaskDefinition, output: TaskOutput,
                             completed_texts: Dict[str, str]) -> ValidationResult:
        issues: List[str] = []
        warnings: List[str] = []
        reconciled: List[str] = []

        if output.status == TaskStatus.FAILED:
            return ValidationResult(
                target_id=task.task_id, status=ValidationStatus.FAIL, is_valid=False,
                issues_found=[output.error_message or "generation failed"],
                revision_instructions=None
            )

        text = output.markdown_content.strip()
        words = word_count(text)
        strong_evidence = len(output.evidence_used) >= 6

        if words < 40:
            issues.append(f"Section is very short ({words} words).")
        elif strong_evidence and words < 0.35 * task.target_words:
            issues.append(f"Section has {words} words but about {task.target_words} are expected and the evidence "
                          f"contains more material. Cover every point in 'what to cover' using the evidence.")

        issues += self._ungrounded_numbers(text, output)
        issues += self._foreign_entities(task, text, output)
        issues += self._duplicates(task, text, completed_texts)
        issues += self._blank_table_cells(text)
        issues += self._ledger_consistency(text)

        # Structured financial statement check
        if task.kind == "financial_statement":
            if not get_statement_for_task(task.title, self.structured_statements):
                issues.append("Structured financial statement data was unavailable; figures were synthesized from unstructured text.")

        # Deterministic Financial Verification
        evidence_corpus = " ".join(e.text for e in output.evidence_used)
        fin_audit = FinancialVerifier.audit_draft_financials(text, evidence_corpus)
        if not fin_audit.is_valid:
            issues.extend(fin_audit.errors)
        warnings.extend(fin_audit.warnings)
        reconciled.extend(fin_audit.reconciled_items)

        for criterion in task.validation_criteria:
            if any(w in criterion.lower() for w in ("table", "matrix")):
                rows = [l for l in text.splitlines() if l.strip().startswith("|")]
                if len(rows) < 3 or "---" not in text:
                    issues.append(f"Missing required table: '{criterion}'. Provide a Markdown table (header, "
                                  f"separator, data rows) or write 'Not disclosed in the sources reviewed'.")

        if not issues and settings.VALIDATE_WITH_LLM and task.validation_criteria:
            issues += self._llm_review(task, output, text)

        # Assign Graded Status
        if issues:
            status = ValidationStatus.FAIL if any(
                "Arithmetic" in i or "do not appear" in i or "ungrounded" in i.lower()
                or "Disagreement with Canonical Ledger" in i or "discrepancy" in i.lower()
                for i in issues
            ) else ValidationStatus.REVIEW_REQUIRED
            ok = False
        elif warnings:
            status = ValidationStatus.WARNING
            ok = True
        elif not output.evidence_used:
            status = ValidationStatus.NOT_VERIFIABLE
            ok = True
        else:
            status = ValidationStatus.PASS
            ok = True

        revision = None if ok else "Revise this section to resolve:\n" + "\n".join(f"- {i}" for i in issues)
        print(f"[Validator] [{status.value}] '{task.title}'" + ("" if ok else f": {issues}"))
        return ValidationResult(
            target_id=task.task_id, status=status, is_valid=ok, issues_found=issues,
            warnings=warnings, reconciled_items=reconciled, revision_instructions=revision
        )

    def _llm_review(self, task: TaskDefinition, output: TaskOutput, text: str) -> List[str]:
        evid = "\n".join(f"[{i+1}] {e.text[:350]}" for i, e in enumerate(output.evidence_used[:8]))
        system = (
            "You are a strict annual-report audit reviewer. Judge ONLY the two things asked. "
            "Return ONLY JSON: {\"verdict\": \"PASS\" | \"FAIL\", \"issues\": [\"...\"]}.\n\n"
            + REVIEW_GUIDANCE
        )
        user = f"""Section: {task.title}
Acceptance criteria:
{chr(10).join("- " + c for c in task.validation_criteria)}

1. Does the draft satisfy every criterion (a criterion is also satisfied by an honest 'Not disclosed in the sources reviewed')?
2. Does the draft state specific facts or figures that clearly contradict or are absent from the evidence excerpts?

Evidence excerpts:
{evid or '(none)'}

Draft:
{text[:7000]}"""
        try:
            data = extract_json(self.llm.invoke([{"role": "system", "content": system},
                                                 {"role": "user", "content": user}], max_tokens=800).content)
            if isinstance(data, dict) and str(data.get("verdict", "")).upper().startswith("FAIL"):
                return [str(i) for i in (data.get("issues") or ["Reviewer rejected the section."])][:4]
        except QuotaExhausted:
            raise
        except Exception:
            pass          # reviewer unavailable: rely on the programmatic checks above
        return []
