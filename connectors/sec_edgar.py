"""SEC EDGAR Source Discovery Connector.

Discovers official 10-K, 20-F, and 40-F annual filings from the US SEC EDGAR system.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import settings
from connectors.base import CandidateDocument, SourceConnector
from utils import tokenize

_CORP_WORDS = {"inc", "incorporated", "corp", "corporation", "ltd", "limited", "plc", "llc", "llp", "co", "company"}


class SecEdgarConnector(SourceConnector):
    """Discovers SEC EDGAR annual filings (10-K, 20-F, 40-F)."""

    def search(self, company: str, fy: str) -> List[CandidateDocument]:
        """Search SEC company tickers and submissions for official annual reports."""
        candidates: List[CandidateDocument] = []
        try:
            hdr = {"User-Agent": settings.SEC_USER_AGENT}
            data = self.tools.fetch_json("https://www.sec.gov/files/company_tickers.json", hdr)
            if not data or not isinstance(data, dict):
                return []

            # 1. Match company name or ticker
            target_toks = [t for t in tokenize(company) if t not in _CORP_WORDS]
            if not target_toks:
                return []

            matches = []
            for v in data.values():
                title_toks = tokenize(v.get("title", ""))
                if all(t in title_toks for t in target_toks):
                    matches.append(v)

            if not matches:
                return []

            exact = [v for v in matches if [w for w in tokenize(v.get("title", "")) if w not in _CORP_WORDS] == target_toks]
            best = (exact or sorted(matches, key=lambda v: len(v.get("title", ""))))[0]

            cik10 = str(best.get("cik_str", "")).zfill(10)
            if not cik10 or cik10 == "0000000000":
                return []

            # 2. Fetch Submissions
            sub = self.tools.fetch_json(f"https://data.sec.gov/submissions/CIK{cik10}.json", hdr)
            if not sub or not isinstance(sub, dict):
                return []

            rec = sub.get("filings", {}).get("recent", {})
            rows = list(zip(rec.get("form", []), rec.get("accessionNumber", []), rec.get("primaryDocument", []),
                            rec.get("reportDate", []), rec.get("filingDate", [])))
            annual = [r for r in rows if r[0] in ("10-K", "20-F", "40-F", "10-K405") and r[2]]

            # Target year extraction (handle non-calendar fiscal years e.g. FY 2024 ending Jan 2025)
            years_in_fy = re.findall(r"\b(20\d\d)\b", fy)
            target_years = set(years_in_fy)
            if len(years_in_fy) == 1:
                # For e.g. "FY 2024" where fiscal year ends Jan/Feb/Mar 2025
                target_years.add(str(int(years_in_fy[0]) + 1))

            for form, acc, doc, rep, filed in annual[:8]:
                doc_year = str(rep)[:4]
                doc_month = int(str(rep)[5:7]) if len(str(rep)) >= 7 and str(rep)[5:7].isdigit() else 12
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{acc.replace('-', '')}/{doc}"
                title = f"{best.get('title', company)} Form {form} (Period ended {rep}, filed {filed})"

                exact_match = (years_in_fy and doc_year == years_in_fy[-1])
                off_cal_match = (len(years_in_fy) == 1 and doc_year == str(int(years_in_fy[0]) + 1) and doc_month <= 3)
                confidence = 0.95 if (exact_match or off_cal_match) else 0.75

                candidates.append(CandidateDocument(
                    url=url,
                    title=title,
                    period=doc_year,
                    confidence=confidence,
                    source_name="SEC EDGAR",
                    raw_data={"cik": cik10, "form": form, "accession": acc, "reportDate": rep, "filingDate": filed,
                              "registrant": best.get("title")}
                ))
            candidates.sort(key=lambda c: -c.confidence)
            return candidates[:5]
        except Exception:
            return []
