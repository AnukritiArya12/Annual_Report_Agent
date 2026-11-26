"""Generic Investor Relations Portal Source Discovery Connector.

Discovers official annual reports and financial statements published directly on corporate IR portals.
Used across EU, Singapore, Japan, and generic jurisdictions.
"""

from __future__ import annotations

import re
from typing import List

from connectors.base import CandidateDocument, SourceConnector


class InvestorRelationsCrawlerConnector(SourceConnector):
    """Discovers official annual reports directly from corporate investor relations portals."""

    def search(self, company: str, fy: str) -> List[CandidateDocument]:
        """Crawl and discover corporate annual report PDFs from company IR pages."""
        candidates: List[CandidateDocument] = []
        try:
            m_year = re.search(r"\b(20\d\d)\b", fy)
            target_year = m_year.group(1) if m_year else ""

            queries = [
                f'"{company}" "investor relations" annual report filetype:pdf',
                f'"{company}" "integrated report" {target_year} filetype:pdf',
                f'"{company}" "annual report" {fy} filetype:pdf'
            ]

            seen_urls = set()
            for q in queries:
                hits = self.tools.search(q, max_results=3) if self.tools else []
                for h in hits:
                    url = h.get("href", "")
                    title = h.get("title", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    is_pdf = url.lower().split("?")[0].endswith(".pdf")
                    has_annual = any(w in (url + " " + title).lower() for w in ("annual", "report", "integrated", "results"))

                    if is_pdf and has_annual:
                        matches_year = bool(target_year and target_year in (url + " " + title))
                        conf = 0.85 if matches_year else 0.65
                        candidates.append(CandidateDocument(
                            url=url,
                            title=title or f"{company} Annual Report",
                            period=target_year or fy,
                            confidence=conf,
                            source_name="Investor Relations Portal",
                            raw_data={"snippet": h.get("body", "")}
                        ))

            return candidates[:5]
        except Exception:
            return []
