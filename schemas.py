"""Data contract for one piece of retrieved evidence."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class EvidenceItem(BaseModel):
    evidence_id: str
    task_id: str
    text: str
    source_title: str = ""
    source_url: str = ""
    page: Optional[int] = None
    origin: str = "knowledge_base"      # knowledge_base | web
    score: float = 0.0
