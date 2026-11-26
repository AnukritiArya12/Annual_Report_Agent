"""Connector Registry and Jurisdiction Dispatcher.

Maps jurisdiction configurations and official sources to active SourceConnector implementations.
"""

from __future__ import annotations

from typing import Any, List

from connectors.base import SourceConnector
from connectors.india import IndiaBseNseConnector
from connectors.investor_relations import InvestorRelationsCrawlerConnector
from connectors.sec_edgar import SecEdgarConnector
from connectors.uk_companies_house import CompaniesHouseConnector
from jurisdictions import JurisdictionConfig


def get_connectors_for_jurisdiction(jurisdiction: JurisdictionConfig,
                                    tools: Any = None,
                                    run_dir: str = "") -> List[SourceConnector]:
    """Resolve active official source discovery connectors for a jurisdiction."""
    connectors: List[SourceConnector] = []
    code = getattr(jurisdiction, "code", "").upper()
    sources = {s.lower() for s in getattr(jurisdiction, "official_sources", [])}

    if code == "US" or "sec.gov" in sources or "edgar" in sources:
        connectors.append(SecEdgarConnector(tools=tools, run_dir=run_dir))

    if code == "IN" or "bseindia.com" in sources or "nseindia.com" in sources or "mca.gov.in" in sources:
        connectors.append(IndiaBseNseConnector(tools=tools, run_dir=run_dir))

    if code in ("UK", "GB") or "companies-house.gov.uk" in sources or "londonstockexchange.com" in sources:
        connectors.append(CompaniesHouseConnector(tools=tools, run_dir=run_dir))

    # Generic IR crawler active for EU, SG, JP, GENERIC and as supplemental source
    connectors.append(InvestorRelationsCrawlerConnector(tools=tools, run_dir=run_dir))

    return connectors
