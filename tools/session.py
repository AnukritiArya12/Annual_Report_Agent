"""Session state container for deterministic DeepAgent tools.

Replaces module-level globals with an explicit, thread-safe session object
injected into tools via closures.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

from ledger import FactsLedger
from storage import DatabaseStorageEngine
from tools.web import ResearchTools


@dataclass
class ReportSession:
    """Explicit state handle for a single annual report generation run."""
    run_dir: str
    company_name: str
    financial_year: str
    country: str = ""
    reporting_scope: str = "consolidated"
    auto_approve: bool = False

    # Lazy / initialized engines
    storage: Optional[DatabaseStorageEngine] = None
    ledger: Optional[FactsLedger] = None
    research_tools: Optional[ResearchTools] = None
    llm: Optional[object] = None

    # Identity & guardrail state
    approved_names: List[str] = field(default_factory=list)
    excluded_names: List[str] = field(default_factory=list)
    approved_domains: Set[str] = field(default_factory=set)
    entity_statuses: Dict[str, str] = field(default_factory=dict)
    human_decisions: Dict[str, object] = field(default_factory=dict)

    # Cross-section state for repetition & statements
    structured_statements: Dict[str, str] = field(default_factory=dict)
    completed_sections: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.run_dir = os.path.abspath(self.run_dir)
        os.makedirs(self.run_dir, exist_ok=True)
        if self.research_tools is None:
            self.research_tools = ResearchTools(run_dir=self.run_dir)
        if self.storage is None:
            self.storage = DatabaseStorageEngine(run_dir=self.run_dir)
        if self.ledger is None:
            self.ledger = FactsLedger(storage=self.storage, llm=self.llm)
        if not self.approved_names and self.company_name:
            self.approved_names = [self.company_name]
        self._load_persisted_decisions()

    @property
    def decisions_path(self) -> str:
        return os.path.join(self.run_dir, "approvals.json")

    def _load_persisted_decisions(self) -> None:
        if os.path.exists(self.decisions_path):
            try:
                with open(self.decisions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.human_decisions = data
                    for dom in data.get("_approved_domains", []):
                        if dom:
                            self.approved_domains.add(dom.lower())
                    if "identity" in data and isinstance(data["identity"], dict):
                        self.approved_names = data["identity"].get("names", self.approved_names)
                        self.excluded_names = data["identity"].get("excluded", self.excluded_names)
            except Exception:
                pass

    def save_decisions(self) -> None:
        data = dict(self.human_decisions)
        data["_approved_domains"] = sorted(self.approved_domains)
        if self.approved_names or self.excluded_names:
            data["identity"] = {
                "names": list(self.approved_names),
                "excluded": list(self.excluded_names),
            }
        try:
            with open(self.decisions_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def approve_domain(self, url_or_domain: str) -> None:
        """Register an approved web domain from a URL or hostname."""
        raw = (url_or_domain or "").strip().lower()
        if not raw:
            return
        if raw.startswith(("http://", "https://")):
            host = (urlparse(raw).netloc or "").lower()
        else:
            host = raw
        if host.startswith("www."):
            host = host[4:]
        if host:
            self.approved_domains.add(host)
            self.save_decisions()

    def is_domain_approved(self, url: str) -> bool:
        """Check whether a URL belongs to a human-approved domain or local file."""
        if not url:
            return False
        low = url.strip().lower()
        if not low.startswith(("http://", "https://")):
            return True  # Local file or internal dossier
        if not self.approved_domains:
            return False
        host = (urlparse(low).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return any(host == d or host.endswith("." + d) for d in self.approved_domains)
