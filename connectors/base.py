"""Base classes for Source Discovery Connectors.

All jurisdiction-specific regulatory and official filing connectors inherit from SourceConnector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CandidateDocument:
    """A discovered candidate filing or corporate document from an official source."""
    url: str
    title: str
    period: str
    confidence: float
    source_name: str
    raw_data: Optional[Dict[str, Any]] = field(default_factory=dict)


class SourceConnector:
    """Abstract base class for official regulatory and corporate registry connectors."""

    def __init__(self, tools: Any = None, run_dir: str = ""):
        self.tools = tools
        self.run_dir = run_dir

    def search(self, company: str, fy: str) -> List[CandidateDocument]:
        """Discover candidate annual reports or filings for the company in the fiscal year.
        
        Must degrade gracefully (catch exceptions, handle timeouts) and never raise unhandled crashes.
        """
        raise NotImplementedError("SourceConnector subclasses must implement search()")
