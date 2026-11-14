"""Dynamic Validation Engine for AI-Based Annual Report Generation System.

Validates evidence coverage, factual accuracy, numerical integrity, and adherence
to task-specific validation criteria. If validation fails, provides concrete,
actionable feedback for agent revision.
"""

import re
from typing import List, Optional
from config import get_llm
from schemas import TaskDefinition, TaskOutput, ValidationResult


class DynamicValidator:
    """Evaluates task outputs against evidence and explicit validation criteria."""

    def __init__(self):
        self.llm = get_llm()

    @staticmethod
    def _ungrounded_numbers_issue(text: str, output: TaskOutput) -> List[str]:
        """Flag sections whose figures do not appear in any collected evidence (likely hallucinated)."""
        evidence_blob = " ".join(e.fact_or_data for e in output.evidence_used).replace(",", "")
        if not evidence_blob:
            return []
        body = text.split("*Sources:")[0].replace(",", "")
        nums = {n for n in re.findall(r"\d+(?:\.\d+)?", body) if len(n.replace(".", "")) >= 3}
        # years like 2024 / 2025 are not facts to verify
        nums = {n for n in nums if not (len(n) == 4 and n.startswith(("19", "20")))}
        if len(nums) < 5:
            return []
        missing = [n for n in nums if n not in evidence_blob]
        if len(missing) / len(nums) > 0.75:
            return [("Most figures in this section do not appear in collected evidence "
                     f"(e.g. {', '.join(sorted(missing)[:6])}). Use verified filing numbers or mark "
                     "'Not disclosed in the public sources reviewed'.")]
        return []

    def validate_task_output(self, task: TaskDefinition, output: TaskOutput) -> ValidationResult:
        """Validates task output against its specific validation criteria and evidence.
        
        Args:
            task: The original task specification with validation_criteria.
            output: The generated TaskOutput.
            
        Returns:
            ValidationResult with pass/fail status and revision instructions if needed.
        """
        print(f"[Validator] Auditing Task: '{task.title}' (ID: {task.task_id})...")

        # 1. Structural and completeness checks
        issues: List[str] = []
        text = output.markdown_content.strip()

        if len(text.split()) < 35:
            issues.append(f"Content is too brief ({len(text.split())} words); expected comprehensive analysis.")

        # 1b. Grounding check: numbers in the text must come from the gathered evidence
        issues.extend(self._ungrounded_numbers_issue(text, output))

        # 2. Check each validation criterion
        for criterion in task.validation_criteria:
            c_lower = criterion.lower()
            
            # Table / matrix check
            if any(term in c_lower for term in ["table", "matrix"]):
                has_table_pipes = ("|" in text and "---" in text)
                has_rows = len([l for l in text.splitlines() if "|" in l]) >= 3
                if not (has_table_pipes and has_rows):
                    issues.append(f"Missing required structured table or comparison matrix: '{criterion}'. Provide a Markdown table with headers, separator, and data rows.")
                
            # Currency or figures check
            if any(term in c_lower for term in ["revenue", "profit", "figures", "currency"]):
                has_numbers = bool(re.search(r"(\$|₹|€|£|\%|\d+\s*(?:cr|crore|billion|million))", text, re.IGNORECASE))
                if not has_numbers and not any(k in text.lower() for k in ["disclosed", "statutory", "n/a", "filing"]):
                    issues.append(f"Missing quantitative figures or disclosure status: '{criterion}'")

            # Footprint / identity check
            if "footprint" in c_lower and not any(term in text.lower() for term in ["global", "presence", "offices", "centers", "delivery", "headquarters"]):
                issues.append(f"Missing global footprint details: '{criterion}'")

        # 3. LLM verification if available
        if not issues:
            system_prompt = (
                "You are a Senior Regulatory Audit & Compliance Officer.\n"
                "Review the draft annual report section against the required validation criteria.\n"
                "If it satisfies all criteria, respond with 'PASS'.\n"
                "If it fails any criterion, respond with 'FAIL:' followed by specific revision instructions."
            )
            user_prompt = f"""Task: {task.title}
Required Criteria:
{chr(10).join(f"- {c}" for c in task.validation_criteria)}

Draft Section:
{text[:2000]}
"""
            try:
                resp = self.llm.invoke([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ])
                verdict = resp.content.strip()
                if verdict.upper().startswith("FAIL"):
                    issues.append(verdict.replace("FAIL:", "").strip())
            except Exception:
                # LLM offline or unreachable, rely on programmatic checks
                pass

        is_valid = len(issues) == 0
        score = 1.0 if is_valid else max(0.2, 1.0 - (len(issues) * 0.25))

        revision_text = None
        if not is_valid:
            revision_text = "Please revise this section to resolve the following issues:\n" + "\n".join(f"- {i}" for i in issues)
            print(f"[Validator] REVISION REQUESTED for '{task.title}': {issues}")
        else:
            print(f"[Validator] PASSED: Task '{task.title}' satisfies all validation criteria.")

        return ValidationResult(
            target_id=task.task_id,
            is_valid=is_valid,
            factual_accuracy_score=score,
            issues_found=issues,
            revision_instructions=revision_text
        )
