"""Validation engine: grounding, completeness, duplication and criteria checks.

Checks are generic. Nothing here assumes a particular section, industry or country; the
acceptance criteria come from the planner, per section.
"""

from __future__ import annotations

import re
from typing import Dict, List

import approvals
from config import QuotaExhausted, get_llm, settings
from schemas import TaskDefinition, TaskOutput, TaskStatus, ValidationResult
from skills import REVIEW_GUIDANCE
from utils import extract_json, foreign_entities, shingle_set, word_count


class DynamicValidator:
    def __init__(self):
        self.llm = get_llm()
        self._shingles: Dict[str, set] = {}
        self.names: List[str] = []                 # approved names of THE company (set by main via set_company)
        self.excluded: List[str] = []
        self.entity_flags: Dict[str, list] = {}    # task_id -> [(name, key, reason)] found in the latest draft

    def set_company(self, names: List[str], excluded: List[str] = ()) -> None:
        self.names = [n for n in names if n]
        self.excluded = list(excluded)
        self.entity_flags = {}

    # ------------------------------------------------------- other-company content
    def _foreign_entities(self, task: TaskDefinition, text: str, output: TaskOutput) -> List[str]:
        """Organisations in the draft that are not this company and not in the evidence it was written from."""
        if not self.names:
            return []
        evidence = " ".join(e.text for e in output.evidence_used)
        flags = foreign_entities(text, self.names, self.excluded, evidence, approvals.entity_statuses())
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

    # ------------------------------------------------------------------ main
    def validate_task_output(self, task: TaskDefinition, output: TaskOutput,
                             completed_texts: Dict[str, str]) -> ValidationResult:
        issues: List[str] = []
        if output.status == TaskStatus.FAILED:
            return ValidationResult(target_id=task.task_id, is_valid=False,
                                    issues_found=[output.error_message or "generation failed"],
                                    revision_instructions=None)
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

        for criterion in task.validation_criteria:
            if any(w in criterion.lower() for w in ("table", "matrix")):
                rows = [l for l in text.splitlines() if l.strip().startswith("|")]
                if len(rows) < 3 or "---" not in text:
                    issues.append(f"Missing required table: '{criterion}'. Provide a Markdown table (header, "
                                  f"separator, data rows) or write 'Not disclosed in the sources reviewed'.")

        if not issues and settings.VALIDATE_WITH_LLM and task.validation_criteria:
            issues += self._llm_review(task, output, text)

        ok = not issues
        revision = None if ok else "Revise this section to resolve:\n" + "\n".join(f"- {i}" for i in issues)
        print(f"[Validator] {'PASS' if ok else 'REVISE'} '{task.title}'" + ("" if ok else f": {issues}"))
        return ValidationResult(target_id=task.task_id, is_valid=ok, issues_found=issues,
                                revision_instructions=revision)

    def _llm_review(self, task: TaskDefinition, output: TaskOutput, text: str) -> List[str]:
        evid = "\n".join(f"[{i+1}] {e.text[:350]}" for i, e in enumerate(output.evidence_used[:8]))
        system = ("You are a strict annual-report audit reviewer. Judge ONLY the two things asked. "
                  "Return ONLY JSON: {\"verdict\": \"PASS\" | \"FAIL\", \"issues\": [\"...\"]}.\n" + REVIEW_GUIDANCE)
        user = f"""Section: {task.title}
Acceptance criteria:
{chr(10).join('- ' + c for c in task.validation_criteria)}

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
