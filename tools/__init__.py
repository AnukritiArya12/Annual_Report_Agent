"""Deterministic tool suite for the autonomous DeepAgent + shared web research helpers."""

from __future__ import annotations

from typing import List
from langchain_core.tools import BaseTool

from tools.web import ResearchTools, rows_to_markdown
from tools.session import ReportSession
from tools.discovery_tools import build_discovery_tools
from tools.retrieval_tools import build_retrieval_tools
from tools.financial_tools import build_financial_tools
from tools.validation_tools import build_validation_tools
from tools.output_tools import build_output_tools


def create_report_tools(session: ReportSession) -> List[BaseTool]:
    """Build all deterministic @tool functions bound to an explicit ReportSession."""
    tools: List[BaseTool] = []
    tools.extend(build_discovery_tools(session))
    tools.extend(build_retrieval_tools(session))
    tools.extend(build_financial_tools(session))
    tools.extend(build_validation_tools(session))
    tools.extend(build_output_tools(session))
    return tools


__all__ = [
    "ResearchTools",
    "rows_to_markdown",
    "ReportSession",
    "create_report_tools",
]
