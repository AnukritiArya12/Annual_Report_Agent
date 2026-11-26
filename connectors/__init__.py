"""Source Discovery Connectors Package.

Provides generic and jurisdiction-specific official filing connectors.
"""

from connectors.base import CandidateDocument, SourceConnector
from connectors.india import IndiaBseNseConnector
from connectors.investor_relations import InvestorRelationsCrawlerConnector
from connectors.registry import get_connectors_for_jurisdiction
from connectors.sec_edgar import SecEdgarConnector
from connectors.uk_companies_house import CompaniesHouseConnector

__all__ = [
    "CandidateDocument",
    "SourceConnector",
    "SecEdgarConnector",
    "IndiaBseNseConnector",
    "CompaniesHouseConnector",
    "InvestorRelationsCrawlerConnector",
    "get_connectors_for_jurisdiction",
]
