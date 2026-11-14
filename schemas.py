"""Pydantic schemas for AI-Based Generic Annual Report Generation System.

Defines all core data contracts for dynamic planning, generic task execution,
validation, and report assembly. All models are reusable across any company,
industry, country, and reporting period.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

# Resilient Pydantic v2 import with pure-Python fallback for environments
# where compiled DLL loading is restricted by OS Application Control policies.
try:
    from pydantic import BaseModel as _PydanticBaseModel, Field, ConfigDict
    
    class BaseModel(_PydanticBaseModel):
        """Standard Pydantic BaseModel."""
        pass

except Exception:
    def Field(default=..., default_factory=None, **kwargs):
        if default_factory is not None:
            return default_factory()
        if default is ...:
            return None
        return default

    def ConfigDict(**kwargs):
        return kwargs

    class BaseModel:
        """Lightweight pure-python fallback for BaseModel."""
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self) -> dict:
            result = {}
            for k, v in self.__dict__.items():
                if hasattr(v, "model_dump"):
                    result[k] = v.model_dump()
                elif isinstance(v, list):
                    result[k] = [item.model_dump() if hasattr(item, "model_dump") else item for item in v]
                elif isinstance(v, datetime):
                    result[k] = v.isoformat()
                elif isinstance(v, Enum):
                    result[k] = v.value
                else:
                    result[k] = v
            return result

        def model_dump_json(self, indent: Optional[int] = None) -> str:
            def default_serializer(o):
                if isinstance(o, datetime):
                    return o.isoformat()
                if isinstance(o, Enum):
                    return o.value
                if hasattr(o, "model_dump"):
                    return o.model_dump()
                return str(o)
            return json.dumps(self.model_dump(), indent=indent, default=default_serializer)


class TaskStatus(str, Enum):
    """Execution status of a report task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVISION = "needs_revision"


class ReportInput(BaseModel):
    """User input specifying the target company and reporting requirements."""
    company_name: str
    financial_year: str
    country: str
    industry: Optional[str] = None
    reporting_scope: str = "Consolidated"
    currency: Optional[str] = None
    extra_instructions: Optional[str] = None


class CompanyProfile(BaseModel):
    """Dynamically researched profile of the company.
    
    Discovered autonomously by the planning engine using online sources.
    Used to inform the structure, regulatory mandates, and chapters of the annual report.
    """
    company_name: str
    country: str
    industry: str
    ticker_or_id: Optional[str] = None
    sector: Optional[str] = None
    business_model_summary: str = ""
    key_business_segments: List[str] = Field(default_factory=list)
    geographic_presence: List[str] = Field(default_factory=list)
    listing_status: str = ""
    applicable_regulations: List[str] = Field(default_factory=list)
    official_sources: List[str] = Field(default_factory=list)
    peer_competitors: List[str] = Field(default_factory=list)
    researched_at: Optional[Any] = None

    def __init__(self, **kwargs):
        if "researched_at" not in kwargs or kwargs["researched_at"] is None:
            kwargs["researched_at"] = datetime.now(timezone.utc)
        super().__init__(**kwargs)


class TaskDefinition(BaseModel):
    """Specification of a single atomic task executed by the Generic Task Execution Agent.
    
    Contains everything the agent needs: instructions, context, required tools,
    skills, search queries, and validation criteria.
    """
    task_id: str
    title: str
    chapter_id: str
    purpose: str
    dependencies: List[str] = Field(default_factory=list)
    execution_instructions: str = ""
    skills_required: List[str] = Field(default_factory=list)
    tools_required: List[str] = Field(default_factory=list)
    search_queries: List[str] = Field(default_factory=list)
    validation_criteria: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3


class ChapterBlueprint(BaseModel):
    """Structural blueprint for an annual report chapter.
    
    Dynamically generated based on the company's profile, industry norms,
    and statutory reporting standards.
    """
    chapter_id: str
    title: str
    sequence_order: int
    objective: str
    required_subsections: List[str] = Field(default_factory=list)
    task_ids: List[str] = Field(default_factory=list)
    is_mandatory: bool = True
    rationale: str = ""


class ReportBlueprint(BaseModel):
    """The master plan generated by the Dynamic Planning Engine.
    
    Contains the researched company profile, full chapter structure,
    atomic tasks, and computed execution stages.
    """
    report_id: str
    input_spec: ReportInput
    company_profile: CompanyProfile
    chapters: List[ChapterBlueprint] = Field(default_factory=list)
    tasks: List[TaskDefinition] = Field(default_factory=list)
    created_at: Optional[Any] = None

    def __init__(self, **kwargs):
        if "created_at" not in kwargs or kwargs["created_at"] is None:
            kwargs["created_at"] = datetime.now(timezone.utc)
        super().__init__(**kwargs)

    def get_task(self, task_id: str) -> Optional[TaskDefinition]:
        """Fetch a task definition by its ID."""
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def get_chapter(self, chapter_id: str) -> Optional[ChapterBlueprint]:
        """Fetch a chapter blueprint by its ID."""
        for c in self.chapters:
            if c.chapter_id == chapter_id:
                return c
        return None

    def compute_execution_stages(self) -> List[List[str]]:
        """Compute parallel execution stages using topological sorting (Kahn's algorithm).
        
        Tasks within the same stage can safely run in parallel because all their
        dependencies are satisfied in prior stages.
        """
        task_ids = {t.task_id for t in self.tasks}
        deps = {t.task_id: set(t.dependencies) & task_ids for t in self.tasks}
        completed: set[str] = set()
        stages: List[List[str]] = []

        remaining = set(task_ids)
        while remaining:
            ready = [tid for tid in remaining if deps[tid].issubset(completed)]
            if not ready:
                ready = list(remaining)
            stages.append(sorted(ready))
            completed.update(ready)
            remaining.difference_update(ready)

        return stages

    def get_runnable_tasks(self, completed_task_ids: List[str]) -> List[TaskDefinition]:
        """Get tasks ready to run given the list of completed task IDs."""
        completed_set = set(completed_task_ids)
        runnable = []
        for t in self.tasks:
            if t.task_id in completed_set:
                continue
            if t.status in (TaskStatus.PENDING, TaskStatus.NEEDS_REVISION):
                if set(t.dependencies).issubset(completed_set):
                    runnable.append(t)
        return runnable


class EvidenceItem(BaseModel):
    """An atomic verified fact or data point harvested during online research."""
    evidence_id: str
    task_id: str
    fact_or_data: str
    source_url: str
    source_title: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[str] = None
    confidence: float = 1.0
    harvested_at: Optional[Any] = None

    def __init__(self, **kwargs):
        if "harvested_at" not in kwargs or kwargs["harvested_at"] is None:
            kwargs["harvested_at"] = datetime.now(timezone.utc)
        super().__init__(**kwargs)


class TaskOutput(BaseModel):
    """The result produced by the Generic Task Execution Agent for a single task."""
    task_id: str
    chapter_id: str
    markdown_content: str
    structured_tables: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_used: List[EvidenceItem] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.COMPLETED
    error_message: Optional[str] = None


class ValidationResult(BaseModel):
    """Result of validation performed by the Dynamic Validation Engine."""
    target_id: str
    is_valid: bool
    factual_accuracy_score: float = 1.0
    issues_found: List[str] = Field(default_factory=list)
    revision_instructions: Optional[str] = None
    verified_at: Optional[Any] = None

    def __init__(self, **kwargs):
        if "verified_at" not in kwargs or kwargs["verified_at"] is None:
            kwargs["verified_at"] = datetime.now(timezone.utc)
        super().__init__(**kwargs)


class ExecutionState(BaseModel):
    """Global execution state tracked across the entire report lifecycle."""
    report_id: str
    blueprint: ReportBlueprint
    current_stage: int = 0
    task_outputs: Dict[str, TaskOutput] = Field(default_factory=dict)
    validation_results: Dict[str, ValidationResult] = Field(default_factory=dict)
    chapter_drafts: Dict[str, str] = Field(default_factory=dict)
    final_report_path: Optional[str] = None
    started_at: Optional[Any] = None
    updated_at: Optional[Any] = None

    def __init__(self, **kwargs):
        now = datetime.now(timezone.utc)
        if "started_at" not in kwargs or kwargs["started_at"] is None:
            kwargs["started_at"] = now
        if "updated_at" not in kwargs or kwargs["updated_at"] is None:
            kwargs["updated_at"] = now
        if "task_outputs" not in kwargs:
            kwargs["task_outputs"] = {}
        if "validation_results" not in kwargs:
            kwargs["validation_results"] = {}
        if "chapter_drafts" not in kwargs:
            kwargs["chapter_drafts"] = {}
        super().__init__(**kwargs)
