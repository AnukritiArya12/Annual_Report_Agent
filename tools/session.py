"""Session state container for deterministic DeepAgent tools.

Replaces module-level globals with an explicit, thread-safe session object
injected into tools via closures.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
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
    industry: str = ""
    is_first_year: bool = False
    auto_approve: bool = False

    # Lazy / initialized engines
    storage: Optional[DatabaseStorageEngine] = None
    ledger: Optional[FactsLedger] = None
    research_tools: Optional[ResearchTools] = None
    peer_report_path: Optional[str] = None
    peer_report_outline: str = ""

    # Identity & guardrail state
    approved_names: List[str] = field(default_factory=list)
    excluded_names: List[str] = field(default_factory=list)
    approved_domains: Set[str] = field(default_factory=set)
    rejected_domains: Set[str] = field(default_factory=set)
    rejected_sources: Set[str] = field(default_factory=set)
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
            self.ledger = FactsLedger(storage=self.storage)
        if not self.approved_names and self.company_name:
            self.approved_names = [self.company_name]
        self._load_persisted_decisions()

    @property
    def decisions_path(self) -> str:
        return os.path.join(self.run_dir, "approvals.json")

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.run_dir, "run_manifest.json")

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
                    for dom in data.get("_rejected_domains", []):
                        if dom:
                            self.rejected_domains.add(dom.lower())
                    for src in data.get("_rejected_sources", []):
                        if src:
                            self.rejected_sources.add(src.strip().lower())
                    if "identity" in data and isinstance(data["identity"], dict):
                        self.approved_names = data["identity"].get("names", self.approved_names)
                        self.excluded_names = data["identity"].get("excluded", self.excluded_names)
            except Exception:
                pass

    def save_decisions(self) -> None:
        data = dict(self.human_decisions)
        data["_approved_domains"] = sorted(self.approved_domains)
        data["_rejected_domains"] = sorted(self.rejected_domains)
        data["_rejected_sources"] = sorted(self.rejected_sources)
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
            self.rejected_domains.discard(host)
            self.save_decisions()
            self.update_manifest("domain_approved", {"domain": host})

    def reject_domain(self, url_or_domain: str) -> None:
        """Register an explicitly rejected web domain."""
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
            self.rejected_domains.add(host)
            self.approved_domains.discard(host)
            self.save_decisions()
            self.update_manifest("domain_rejected", {"domain": host})

    def reject_source(self, source_url: str) -> None:
        """Register an explicitly rejected specific document URL."""
        clean = (source_url or "").strip().lower()
        if clean:
            self.rejected_sources.add(clean)
            self.save_decisions()
            self.update_manifest("source_rejected", {"source": clean})

    def is_domain_approved(self, url: str) -> bool:
        """Check whether a URL belongs to a human-approved domain or local file.

        GUARDRAIL: Strictly returns False if source URL or its domain is in the rejected list.
        """
        if not url:
            return False
        low = url.strip().lower()
        if not low.startswith(("http://", "https://")):
            return True  # Local file or internal dossier

        if low in self.rejected_sources:
            return False

        host = (urlparse(low).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]

        # Rejection check has priority
        if any(host == d or host.endswith("." + d) for d in self.rejected_domains):
            return False

        if not self.approved_domains:
            return False

        return any(host == d or host.endswith("." + d) for d in self.approved_domains)

    def update_manifest(self, event_type: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Update or create the run manifest documenting source-to-evidence traceability and decisions."""
        import datetime
        import hashlib

        manifest: Dict[str, Any] = {
            "company_name": self.company_name,
            "financial_year": self.financial_year,
            "country": self.country,
            "reporting_scope": self.reporting_scope,
            "input_hash": hashlib.sha256(
                f"{self.company_name}|{self.financial_year}|{self.country}|{self.reporting_scope}".encode()
            ).hexdigest(),
            "approved_domains": sorted(self.approved_domains),
            "rejected_domains": sorted(self.rejected_domains),
            "rejected_sources": sorted(self.rejected_sources),
            "approved_names": self.approved_names,
            "excluded_names": self.excluded_names,
            "total_chunks_in_storage": self.storage.total_chunks() if self.storage else 0,
            "events": [],
        }

        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    if isinstance(old_data, dict):
                        manifest["events"] = old_data.get("events", [])
            except Exception:
                pass

        manifest["events"].append({
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event_type": event_type,
            "details": details or {},
        })
        manifest["last_updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        try:
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def validate_resume_manifest(self) -> Tuple[bool, str]:
        """Verify whether an existing run manifest matches the current execution targets for resume."""
        if not os.path.exists(self.manifest_path):
            return True, "No prior manifest found; fresh run authorized."
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return True, "Invalid prior manifest format."

            # Verify target company match
            prev_co = data.get("company_name", "").strip().lower()
            curr_co = self.company_name.strip().lower()
            if prev_co and prev_co != curr_co and not any(curr_co in n.lower() for n in data.get("approved_names", [])):
                return False, f"Manifest company mismatch: run directory was created for '{data.get('company_name')}', but requested company is '{self.company_name}'."

            # Verify fiscal year match
            prev_fy = re.sub(r"[^\d]", "", data.get("financial_year", ""))
            curr_fy = re.sub(r"[^\d]", "", self.financial_year)
            if prev_fy and curr_fy and prev_fy != curr_fy:
                return False, f"Manifest fiscal year mismatch: run directory was created for '{data.get('financial_year')}', but requested FY is '{self.financial_year}'."

            return True, "Manifest parameters match previous run."
        except Exception as e:
            return False, f"Error validating manifest: {e}"
