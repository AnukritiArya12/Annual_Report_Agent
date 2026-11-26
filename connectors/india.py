"""India Regulatory & Stock Exchange Source Discovery Connector.

Discovers official annual reports and financial filings from BSE India, NSE India, and MCA portals.
"""

from __future__ import annotations

import re
from typing import List

from connectors.base import CandidateDocument, SourceConnector


class IndiaBseNseConnector(SourceConnector):
    """Discovers Indian corporate filings from BSE (Bombay Stock Exchange) and NSE India."""

    def search(self, company: str, fy: str) -> List[CandidateDocument]:
        """Search BSE and NSE announcement and filing directories for company reports."""
        candidates: List[CandidateDocument] = []
        try:
            m_year = re.search(r"\b(20\d\d)\b", fy)
            target_year = m_year.group(1) if m_year else ""

            queries = [
                f'"{company}" annual report filetype:pdf site:bseindia.com',
                f'"{company}" annual report {target_year} filetype:pdf site:nseindia.com',
                f'"{company}" financial statements {fy} site:bseindia.com OR site:nseindia.com'
            ]

            seen_urls = set()
            for q in queries:
                hits = self.tools.search(q, max_results=4) if self.tools else []
                for h in hits:
                    url = h.get("href", "")
                    title = h.get("title", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    is_pdf = url.lower().split("?")[0].endswith(".pdf")
                    is_announcement = any(d in url.lower() for d in ("bseindia.com", "nseindia.com", "mca.gov.in"))

                    if is_announcement or is_pdf:
                        conf = 0.88 if (target_year and target_year in (title + " " + url)) else 0.70
                        candidates.append(CandidateDocument(
                            url=url,
                            title=title or f"{company} Annual Report ({fy})",
                            period=target_year or fy,
                            confidence=conf,
                            source_name="BSE / NSE India",
                            raw_data={"snippet": h.get("body", "")}
                        ))

            return candidates[:5]
        except Exception:
            return []
