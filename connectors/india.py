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
                hits = self.tools.web_search(q, max_results=4) if self.tools else []
                for h in hits:
                    url = h.get("href", "")
                    title = h.get("title", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    is_pdf = url.lower().split("?")[0].endswith(".pdf")
                    domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url.lower()).split("/", 1)[0])
                    official_domain = any(domain == d or domain.endswith("." + d)
                                          for d in ("bseindia.com", "nseindia.com", "mca.gov.in"))
                    title_text = f"{title} {h.get('body', '')}"
                    entity_tokens = [t.lower() for t in re.findall(r"[a-z0-9]+", company)
                                     if t.lower() not in {"limited", "ltd", "private", "pvt", "company", "co"}]
                    entity_match = bool(entity_tokens) and all(t in title_text.lower() or t in url.lower() for t in entity_tokens)
                    period_match = bool(target_year and target_year in (title_text + " " + url))

                    if official_domain and is_pdf and entity_match:
                        conf = 0.90 if period_match else 0.65
                        candidates.append(CandidateDocument(
                            url=url,
                            title=title or f"{company} Annual Report ({fy})",
                            period=target_year or fy,
                            confidence=conf,
                            source_name="BSE / NSE India",
                            raw_data={"snippet": h.get("body", ""), "entity_match": entity_match,
                                      "period_match": period_match, "official_domain": official_domain}
                        ))

            return candidates[:5]
        except Exception:
            return []
