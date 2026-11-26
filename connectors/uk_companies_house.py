"""UK Companies House Source Discovery Connector.

Discovers official annual accounts and strategic reports from the UK Companies House registry.
"""

from __future__ import annotations

import base64
import re
from typing import List

import requests

from config import settings
from connectors.base import CandidateDocument, SourceConnector


class CompaniesHouseConnector(SourceConnector):
    """Discovers UK corporate filings via Companies House REST API and public registries."""

    def search(self, company: str, fy: str) -> List[CandidateDocument]:
        """Search Companies House records for statutory annual accounts."""
        candidates: List[CandidateDocument] = []
        api_key = getattr(settings, "COMPANIES_HOUSE_API_KEY", "").strip()

        # 1. Official REST API if API Key is configured
        if api_key:
            try:
                auth_str = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("utf-8")
                hdr = {"Authorization": f"Basic {auth_str}", "User-Agent": "AnnualReportAgent/1.0"}
                
                # Search company number
                search_url = f"https://api.company-information.service.gov.uk/search/companies?q={requests.utils.quote(company)}"
                r = requests.get(search_url, headers=hdr, timeout=12)
                if r.ok:
                    items = r.json().get("items", [])
                    if items:
                        co_num = items[0].get("company_number")
                        co_title = items[0].get("title", company)
                        if co_num:
                            # Fetch filing history for accounts
                            filing_url = f"https://api.company-information.service.gov.uk/company/{co_num}/filing-history?category=accounts"
                            rf = requests.get(filing_url, headers=hdr, timeout=12)
                            if rf.ok:
                                filings = rf.json().get("items", [])
                                for f in filings[:4]:
                                    date_str = f.get("date", "")
                                    desc = f.get("description", "accounts")
                                    view_url = f"https://find-and-update.company-information.service.gov.uk/company/{co_num}/filing-history"
                                    candidates.append(CandidateDocument(
                                        url=view_url,
                                        title=f"{co_title} ({co_num}) - {desc.replace('-', ' ').title()} ({date_str})",
                                        period=date_str[:4],
                                        confidence=0.92,
                                        source_name="UK Companies House API",
                                        raw_data=f
                                    ))
            except Exception:
                pass

        if candidates:
            return candidates

        # 2. Web Search Fallback on find-and-update.company-information.service.gov.uk
        try:
            m_year = re.search(r"\b(20\d\d)\b", fy)
            target_year = m_year.group(1) if m_year else ""
            query = f'"{company}" accounts OR "annual report" site:find-and-update.company-information.service.gov.uk'
            hits = self.tools.search(query, max_results=3) if self.tools else []
            for h in hits:
                candidates.append(CandidateDocument(
                    url=h.get("href", ""),
                    title=h.get("title", f"{company} UK Filing"),
                    period=target_year or fy,
                    confidence=0.75,
                    source_name="UK Companies House",
                    raw_data={"snippet": h.get("body", "")}
                ))
        except Exception:
            pass

        return candidates[:4]
