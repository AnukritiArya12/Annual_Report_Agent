"""Pydantic data contracts. Nothing here is company-, industry- or country-specific."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportInput(BaseModel):
    company_name: str
    financial_year: str
    country: str = ""
    industry: Optional[str] = None
    reporting_scope: str = "Consolidated"
    extra_instructions: Optional[str] = None
    target_pages: Optional[int] = None          # override the automatic length estimate
    source_documents: List[str] = Field(default_factory=list)  # local PDFs / URLs supplied by the user


class CompanyProfile(BaseModel):
    company_name: str
    country: str = ""
    industry: str = "Unknown"
    ticker_or_id: Optional[str] = None
    sector: Optional[str] = None
    business_model_summary: str = ""
    key_business_segments: List[str] = Field(default_factory=list)
    geographic_presence: List[str] = Field(default_factory=list)
    listing_status: str = ""
    applicable_regulations: List[str] = Field(default_factory=list)
    reporting_framework: str = ""
    official_sources: List[str] = Field(default_factory=list)
    peer_competitors: List[str] = Field(default_factory=list)
    researched_at: datetime = Field(default_factory=_now)


class SourceDocument(BaseModel):
    """A reference document (e.g. the company's real annual report) loaded into the knowledge base."""
    doc_id: str
    title: str
    url: str = ""
    local_path: str = ""
    kind: str = "reference_report"
    fiscal_year: str = ""
    matches_requested_period: Optional[bool] = None
    pages: int = 0


class TaskDefinition(BaseModel):
    """One atomic writing task (a 'leaf' of the report tree)."""
    task_id: str
    title: str
    chapter_id: str
    path: List[str] = Field(default_factory=list)   # chapter > section > subsection titles
    purpose: str = ""
    what_to_cover: str = ""
    kind: str = "narrative"
    dependencies: List[str] = Field(default_factory=list)
    search_queries: List[str] = Field(default_factory=list)
    validation_criteria: List[str] = Field(default_factory=list)
    target_words: int = 400
    status: TaskStatus = TaskStatus.PENDING


class ChapterBlueprint(BaseModel):
    chapter_id: str
    title: str
    sequence_order: int
    objective: str = ""
    kind: str = ""
    rationale: str = ""
    est_pages: float = 0
    task_ids: List[str] = Field(default_factory=list)


class ReportBlueprint(BaseModel):
    report_id: str
    input_spec: ReportInput
    company_profile: CompanyProfile
    reference_documents: List[SourceDocument] = Field(default_factory=list)
    target_pages: int = 0
    chapters: List[ChapterBlueprint] = Field(default_factory=list)
    tasks: List[TaskDefinition] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)

    def get_task(self, task_id: str) -> Optional[TaskDefinition]:
        return next((t for t in self.tasks if t.task_id == task_id), None)

    def compute_execution_stages(self) -> List[List[str]]:
        """Topological stages: tasks in one stage only depend on earlier stages."""
        ids = {t.task_id for t in self.tasks}
        deps = {t.task_id: set(t.dependencies) & ids for t in self.tasks}
        order = {t.task_id: i for i, t in enumerate(self.tasks)}
        done: set = set()
        remaining = set(ids)
        stages: List[List[str]] = []
        while remaining:
            ready = [t for t in remaining if deps[t] <= done]
            if not ready:                       # dependency cycle: run what is left together
                ready = list(remaining)
            ready.sort(key=lambda t: order[t])
            stages.append(ready)
            done.update(ready)
            remaining.difference_update(ready)
        return stages


class EvidenceItem(BaseModel):
    evidence_id: str
    task_id: str
    text: str
    source_title: str = ""
    source_url: str = ""
    page: Optional[int] = None
    origin: str = "knowledge_base"      # knowledge_base | web
    score: float = 0.0


class TaskOutput(BaseModel):
    task_id: str
    chapter_id: str
    markdown_content: str = ""
    evidence_used: List[EvidenceItem] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.COMPLETED
    error_message: Optional[str] = None
    word_count: int = 0


class ValidationResult(BaseModel):
    target_id: str
    is_valid: bool
    issues_found: List[str] = Field(default_factory=list)
    revision_instructions: Optional[str] = None
    verified_at: datetime = Field(default_factory=_now)


class ExecutionState(BaseModel):
    report_id: str
    blueprint: ReportBlueprint
    task_outputs: Dict[str, TaskOutput] = Field(default_factory=dict)
    validation_results: Dict[str, ValidationResult] = Field(default_factory=dict)
    final_report_path: Optional[str] = None
    started_at: datetime = Field(default_factory=_now)