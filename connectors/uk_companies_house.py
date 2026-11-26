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
                    target_toks = {t.lower() for t in re.findall(r"[a-zA-Z0-9]+", company)
                                   if t.lower() not in ("ltd", "limited", "plc", "llp", "co", "uk")}
                    m_year = re.search(r"\b(20\d\d)\b", fy)
                    target_year = m_year.group(1) if m_year else ""
                    matched_item = None
                    for it in items:
                        title_toks = {t.lower() for t in re.findall(r"[a-zA-Z0-9]+", it.get("title", ""))}
                        if target_toks and target_toks.issubset(title_toks):
                            matched_item = it
                            break
                    if matched_item:
                        co_num = matched_item.get("company_number")
                        co_title = matched_item.get("title", company)
                        if co_num:
                            # Fetch filing history for accounts
                            filing_url = f"https://api.company-information.service.gov.uk/company/{co_num}/filing-history?category=accounts"
                            rf = requests.get(filing_url, headers=hdr, timeout=12)
                            if rf.ok:
                                filings = rf.json().get("items", [])
                                for f in filings[:8]:
                                    date_str = f.get("date", "")
                                    desc = f.get("description", "accounts")
                                    matches_fy = bool(target_year and (target_year in date_str or str(int(target_year) + 1) in date_str))
                                    conf = 0.92 if matches_fy else 0.72
                                    view_url = f"https://find-and-update.company-information.service.gov.uk/company/{co_num}/filing-history"
                                    candidates.append(CandidateDocument(
                                        url=view_url,
                                        title=f"{co_title} ({co_num}) - {desc.replace('-', ' ').title()} ({date_str})",
                                        period=date_str[:4],
                                        confidence=conf,
                                        source_name="UK Companies House API",
                                        raw_data=f
                                    ))
                                candidates.sort(key=lambda c: -c.confidence)
            except Exception:
                pass

        if candidates:
            return candidates

        # 2. Web Search Fallback on find-and-update.company-information.service.gov.uk
        try:
            m_year = re.search(r"\b(20\d\d)\b", fy)
            target_year = m_year.group(1) if m_year else ""
            query = f'"{company}" accounts OR "annual report" site:find-and-update.company-information.service.gov.uk'
            hits = self.tools.web_search(query, max_results=3) if self.tools else []
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
