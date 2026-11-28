"""Jurisdiction and regulatory compliance registry for corporate reporting.

Maintains data-driven reporting requirements across global jurisdictions
(US, IN, UK, EU, SG, JP, GENERIC) without hardcoding statutory acts in orchestrator logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MandatorySection:
    """A required statutory disclosure section in an annual report."""
    title: str
    purpose: str
    kind: str  # narrative, financial_statement, notes_to_accounts, statutory, data_table


@dataclass
class FirstYearRules:
    """Statutory and accounting guidelines for first-year / inaugural entities."""
    comparative_data_rule: str
    statutory_governance_focus: str
    notes_to_accounts_emphasis: str
    required_filings: List[str] = field(default_factory=list)


@dataclass
class JurisdictionConfig:
    """Configuration defining the statutory regime and reporting framework of a jurisdiction."""
    code: str
    country_name: str
    regime_name: str
    governing_statute: str
    accounting_frameworks: List[str]
    fiscal_year_convention: str
    primary_regulators: List[str]
    official_sources: List[str]
    mandatory_sections: List[MandatorySection]
    first_year_rules: FirstYearRules
    keywords: List[str] = field(default_factory=list)


JURISDICTION_REGISTRY: Dict[str, JurisdictionConfig] = {
    "US": JurisdictionConfig(
        code="US",
        country_name="United States",
        regime_name="SEC Form 10-K / Regulation S-K & S-X",
        governing_statute="Securities Exchange Act of 1934 / Sarbanes-Oxley Act",
        accounting_frameworks=["US GAAP"],
        fiscal_year_convention="Annual (Calendar Year or 52/53-week fiscal year ending September/December/January)",
        primary_regulators=["Securities and Exchange Commission (SEC)"],
        official_sources=["sec.gov", "edgar"],
        mandatory_sections=[
            MandatorySection("Item 1. Business", "Operational overview, segments, products, markets", "narrative"),
            MandatorySection("Item 1A. Risk Factors", "Material risks to business, operations, and financial condition", "narrative"),
            MandatorySection("Item 7. Management's Discussion and Analysis (MD&A)", "Financial condition, liquidity, capital resources, results of operations", "narrative"),
            MandatorySection("Item 8. Financial Statements and Supplementary Data", "Audited balance sheets, statements of income, cash flows, stockholders equity", "financial_statement"),
            MandatorySection("Notes to Consolidated Financial Statements", "Accounting policies, segmental details, commitments, contingencies", "notes_to_accounts"),
            MandatorySection("Item 9A. Controls and Procedures", "Evaluation of internal control over financial reporting (ICFR)", "statutory"),
        ],
        first_year_rules=FirstYearRules(
            comparative_data_rule="Preceding comparative periods marked 'N/A (Initial Period of Operations)'.",
            statutory_governance_focus="Initial corporate charter, founders' equity capitalization, registration of securities.",
            notes_to_accounts_emphasis="Note on basis of presentation from inception date through fiscal year end.",
            required_filings=["Form 10-K", "Form 8-K", "Form S-1 (historical)"]
        ),
        keywords=["usa", "united states", "america", "us", "delaware", "nasdaq", "nyse"]
    ),

    "IN": JurisdictionConfig(
        code="IN",
        country_name="India",
        regime_name="Companies Act & SEBI (LODR) Framework",
        governing_statute="Companies Act, 2013 and SEBI (Listing Obligations and Disclosure Requirements)",
        accounting_frameworks=["Ind AS", "Indian GAAP (AS)"],
        fiscal_year_convention="1st April to 31st March (Financial Year ended March 31)",
        primary_regulators=["Ministry of Corporate Affairs (MCA)", "Securities and Exchange Board of India (SEBI)", "Reserve Bank of India (RBI)"],
        official_sources=["bseindia.com", "nseindia.com", "mca.gov.in"],
        mandatory_sections=[
            MandatorySection("Board's Report", "State of company affairs, meetings held, directors responsibility, risk policy", "statutory"),
            MandatorySection("Management Discussion and Analysis (MD&A)", "Industry structure, opportunities, threats, segment performance, internal controls", "narrative"),
            MandatorySection("Report on Corporate Governance", "Board composition, committees, stakeholder grievance, compliance certificate", "statutory"),
            MandatorySection("Standalone & Consolidated Financial Statements", "Balance Sheet, Statement of Profit and Loss, Cash Flow Statement", "financial_statement"),
            MandatorySection("Notes Forming Part of the Financial Statements", "Significant accounting policies, share capital notes, related party disclosures", "notes_to_accounts"),
            MandatorySection("Independent Auditor's Report", "Auditors opinion on true and fair view and internal financial controls", "statutory"),
        ],
        first_year_rules=FirstYearRules(
            comparative_data_rule="Preceding comparative columns marked 'N/A (First Year of Operations)'.",
            statutory_governance_focus="Statutory incorporation disclosures, capital subscription by promoters, first board meetings.",
            notes_to_accounts_emphasis="Note 1 Basis of Preparation from date of incorporation to close of maiden financial year.",
            required_filings=["Form AOC-4", "Form MGT-7", "Audited Financial Statements"]
        ),
        keywords=["india", "indian", "mca", "bse", "nse", "sebi", "rbi"]
    ),

    "UK": JurisdictionConfig(
        code="UK",
        country_name="United Kingdom",
        regime_name="UK Companies Act Strategic Report & Corporate Governance Code",
        governing_statute="Companies Act 2006 / UK Corporate Governance Code",
        accounting_frameworks=["UK-adopted IFRS", "FRS 102"],
        fiscal_year_convention="Annual reporting period (commonly ending December 31 or March 31)",
        primary_regulators=["Financial Reporting Council (FRC)", "Companies House", "Financial Conduct Authority (FCA)"],
        official_sources=["gov.uk/companies-house", "londonstockexchange.com"],
        mandatory_sections=[
            MandatorySection("Strategic Report", "Business model, strategy, key performance indicators, principal risks, Section 172 statement", "narrative"),
            MandatorySection("Directors' Report", "Dividends, political donations, greenhouse gas emissions, financial instruments", "statutory"),
            MandatorySection("Corporate Governance Report", "Application of UK Corporate Governance Code principles, leadership, audit committee report", "statutory"),
            MandatorySection("Primary Financial Statements", "Consolidated income statement, statement of financial position, statement of cash flows", "financial_statement"),
            MandatorySection("Notes to the Financial Statements", "Accounting policies, critical accounting judgments, segmental reporting", "notes_to_accounts"),
            MandatorySection("Independent Auditor's Report", "Auditor's report to the members of the company", "statutory"),
        ],
        first_year_rules=FirstYearRules(
            comparative_data_rule="Prior-period comparative columns marked 'N/A (First Year)'.",
            statutory_governance_focus="Incorporation at Companies House, initial subscriber shares, initial director appointments.",
            notes_to_accounts_emphasis="Basis of preparation for the inaugural accounting reference period.",
            required_filings=["Confirmation Statement", "Annual Accounts to Companies House"]
        ),
        keywords=["united kingdom", "uk", "great britain", "england", "scotland", "wales", "london", "lse", "frc"]
    ),

    "EU": JurisdictionConfig(
        code="EU",
        country_name="European Union",
        regime_name="EU CSRD / Transparency Directive & IFRS",
        governing_statute="EU Accounting Directive (2013/34/EU) and Corporate Sustainability Due Diligence Directive",
        accounting_frameworks=["EU-endorsed IFRS", "Local Member State GAAP"],
        fiscal_year_convention="Annual financial year (predominantly January 1 to December 31)",
        primary_regulators=["European Securities and Markets Authority (ESMA)", "National Competent Authorities"],
        official_sources=["esma.europa.eu", "euronext.com", "bundesanzeiger.de"],
        mandatory_sections=[
            MandatorySection("Management Report", "Analysis of business development, performance, principal risks, and future outlook", "narrative"),
            MandatorySection("Corporate Governance Statement", "Governance code compliance, internal control and risk management systems", "statutory"),
            MandatorySection("Sustainability Statement (ESRS)", "Environmental, social, and governance disclosures in accordance with ESRS", "narrative"),
            MandatorySection("Consolidated Financial Statements", "Statement of financial position, statement of profit or loss, statement of cash flows", "financial_statement"),
            MandatorySection("Notes to the Consolidated Financial Statements", "Accounting policies, segmental information, financial risk management", "notes_to_accounts"),
            MandatorySection("Audit Report", "Independent auditor's report on financial statements and management report", "statutory"),
        ],
        first_year_rules=FirstYearRules(
            comparative_data_rule="Prior year comparative figures marked 'N/A (Inaugural Period)'.",
            statutory_governance_focus="National commercial register entry, registered capital subscription, founding executive board.",
            notes_to_accounts_emphasis="First-time adoption disclosures and initial financial reporting period specification.",
            required_filings=["Commercial Register Annual Filing (ESEF format)"]
        ),
        keywords=["european union", "eu", "germany", "france", "netherlands", "ireland", "spain", "italy", "esma", "esef"]
    ),

    "SG": JurisdictionConfig(
        code="SG",
        country_name="Singapore",
        regime_name="Singapore Companies Act & SGX Rulebook",
        governing_statute="Companies Act (Cap. 50) and SGX Listing Manual",
        accounting_frameworks=["SFRS(I) - Singapore Financial Reporting Standards (International)"],
        fiscal_year_convention="Annual financial year (commonly December 31 or March 31)",
        primary_regulators=["Accounting and Corporate Regulatory Authority (ACRA)", "Monetary Authority of Singapore (MAS)", "SGX Group"],
        official_sources=["sgx.com", "acra.gov.sg"],
        mandatory_sections=[
            MandatorySection("Directors' Statement", "Directors' opinion on financial statements, share options, and arrangements", "statutory"),
            MandatorySection("Corporate Governance Report", "Adherence to Code of Corporate Governance 2018 principles", "statutory"),
            MandatorySection("Operational and Financial Review", "Review of performance, segment operations, and financial condition", "narrative"),
            MandatorySection("Audited Financial Statements", "Statements of financial position, consolidated statement of profit or loss, cash flows", "financial_statement"),
            MandatorySection("Notes to the Financial Statements", "Summary of significant accounting policies and explanatory notes", "notes_to_accounts"),
            MandatorySection("Independent Auditor's Report", "Statutory auditor's report to the members", "statutory"),
        ],
        first_year_rules=FirstYearRules(
            comparative_data_rule="Preceding comparative period marked 'N/A (First Financial Period)'.",
            statutory_governance_focus="ACRA incorporation particulars, issued paid-up capital, initial director declarations.",
            notes_to_accounts_emphasis="Initial period accounting policies from registration date.",
            required_filings=["Annual Return via ACRA BizFile+"]
        ),
        keywords=["singapore", "sg", "sgx", "acra", "mas"]
    ),

    "JP": JurisdictionConfig(
        code="JP",
        country_name="Japan",
        regime_name="Financial Instruments and Exchange Act (FIEA) & Companies Act",
        governing_statute="Financial Instruments and Exchange Act / Companies Act of Japan",
        accounting_frameworks=["J-GAAP", "IFRS", "US GAAP"],
        fiscal_year_convention="April 1 to March 31 (common) or Calendar Year",
        primary_regulators=["Financial Services Agency (FSA)", "Tokyo Stock Exchange (JPX)"],
        official_sources=["jpx.co.jp", "edinet-fsa.go.jp"],
        mandatory_sections=[
            MandatorySection("Overview of the Company and Business", "History, business description, subsidiaries, employees", "narrative"),
            MandatorySection("Business Overview and MD&A", "Management policy, business risks, analysis of operating results", "narrative"),
            MandatorySection("Corporate Governance and Status of Officers", "Corporate governance overview, status of officers, audit status", "statutory"),
            MandatorySection("Financial Statements", "Balance sheet, statement of income, statement of cash flows", "financial_statement"),
            MandatorySection("Notes to Financial Statements", "Significant accounting policies, segment information, related party transactions", "notes_to_accounts"),
            MandatorySection("Independent Auditor's Report", "Audit report by statutory audit corporation", "statutory"),
        ],
        first_year_rules=FirstYearRules(
            comparative_data_rule="Prior-period comparisons marked 'N/A (First Accounting Period)'.",
            statutory_governance_focus="Commercial registration, promoter capital contribution, inaugural board.",
            notes_to_accounts_emphasis="Initial accounting period convention from registration date.",
            required_filings=["Yukashoken Hokokusho (Annual Securities Report) via EDINET"]
        ),
        keywords=["japan", "japanese", "tokyo", "jpx", "tse", "fsa", "edinet"]
    ),

    "GENERIC": JurisdictionConfig(
        code="GENERIC",
        country_name="Global / International",
        regime_name="International Corporate Reporting Standard (IFRS / ISA)",
        governing_statute="General Corporate Reporting and International Financial Reporting Standards",
        accounting_frameworks=["IFRS", "National GAAP"],
        fiscal_year_convention="Annual financial reporting period (12 months)",
        primary_regulators=["National Corporate Registrar / Securities Regulator"],
        official_sources=["official company investor relations portal"],
        mandatory_sections=[
            MandatorySection("Strategic & Operational Review", "Business model, strategy, operational highlights, and key markets", "narrative"),
            MandatorySection("Corporate Governance & Leadership", "Board of directors, executive management, and oversight principles", "statutory"),
            MandatorySection("Principal Risks & Uncertainties", "Operational, financial, and strategic risk factors and mitigations", "narrative"),
            MandatorySection("Financial Statements", "Balance Sheet, Statement of Comprehensive Income, Cash Flow Statement", "financial_statement"),
            MandatorySection("Notes to the Financial Statements", "Accounting policies, segmental performance, commitments, contingencies", "notes_to_accounts"),
            MandatorySection("Audit & Assurance Report", "Independent auditor's report on true and fair presentation", "statutory"),
        ],
        first_year_rules=FirstYearRules(
            comparative_data_rule="Prior-period comparative columns marked 'N/A (First Year of Operations)'.",
            statutory_governance_focus="Corporate genesis, charter details, founding promoter capital, inaugural milestones.",
            notes_to_accounts_emphasis="Basis of preparation from corporate incorporation date through period end.",
            required_filings=["Statutory annual accounts and governance reports"]
        ),
        keywords=["global", "international", "generic"]
    )
}


def get_jurisdiction(country: Optional[str] = None, profile: Optional[Any] = None) -> JurisdictionConfig:
    """Resolve the best matching jurisdiction configuration based on country, text hints, or profile.
    
    Falls back gracefully to GENERIC if no specific jurisdiction is matched.
    """
    candidates: List[str] = []
    if country:
        candidates.append(str(country).strip().lower())
    if profile:
        c = getattr(profile, "country", "") or ""
        if c:
            candidates.append(c.strip().lower())
        rf = getattr(profile, "reporting_framework", "") or ""
        if rf:
            candidates.append(rf.strip().lower())
        regs = getattr(profile, "applicable_regulations", []) or []
        for r in regs:
            candidates.append(str(r).strip().lower())

    # Direct code match
    for cand in candidates:
        cand_upper = cand.upper()
        if cand_upper in JURISDICTION_REGISTRY:
            return JURISDICTION_REGISTRY[cand_upper]

    # Keyword search across registry
    for cand in candidates:
        if not cand:
            continue
        toks = re.findall(r"[a-z0-9]+", cand)
        for code, j_cfg in JURISDICTION_REGISTRY.items():
            if code == "GENERIC":
                continue
            if any(k in cand for k in j_cfg.keywords):
                return j_cfg
            if any(t in j_cfg.keywords for t in toks):
                return j_cfg

    return JURISDICTION_REGISTRY["GENERIC"]


@dataclass
class IndustryBenchmark:
    """Standard reporting structure, KPIs, and disclosure requirements for an industry sector."""
    sector_name: str
    recommended_chapters: List[str]
    key_metrics_and_kpis: List[str]
    specific_risk_factors: List[str]
    peer_structure_notes: str


INDUSTRY_SECTOR_BENCHMARKS: Dict[str, IndustryBenchmark] = {
    "banking": IndustryBenchmark(
        sector_name="Banking, Financial Institutions & Lending",
        recommended_chapters=[
            "Corporate Overview & Banking Franchise",
            "Executive Leadership & Strategic Priorities",
            "Macroeconomic Environment & Monetary Policy Impact",
            "Credit Risk, Asset Quality & Provisioning (GNPA / NNPA)",
            "Capital Adequacy & Basel III Disclosures (CET1, Tier 1, CAR)",
            "Treasury, Investment Portfolio & Net Interest Margin (NIM)",
            "Liquidity Risk Management (LCR & NSFR)",
            "Digital Banking, Payments & Technology Infrastructure",
            "Financial Highlights & Core Banking Metrics",
            "Audited Financial Statement Highlights (Balance Sheet, P&L, Cash Flow)",
            "Notes to Accounts & Regulatory Capital Appendix"
        ],
        key_metrics_and_kpis=[
            "Net Interest Margin (NIM)", "Return on Assets (ROA)", "Return on Equity (ROE)",
            "Gross NPA Ratio", "Net NPA Ratio", "Provision Coverage Ratio (PCR)",
            "Capital Adequacy Ratio (CAR / CRAR)", "Cost-to-Income Ratio",
            "CASA Ratio", "Liquidity Coverage Ratio (LCR)"
        ],
        specific_risk_factors=[
            "Credit default risk and systemic loan book concentration",
            "Interest rate volatility and yield curve shifts",
            "Regulatory compliance, capital conservation buffers, and central bank directives",
            "Cybersecurity threats on digital payment gateways and core banking switches"
        ],
        peer_structure_notes="Peer banks prioritize credit quality schedules, NPA movement, asset liability matching (ALM), and Basel III regulatory capital tables."
    ),
    "technology": IndustryBenchmark(
        sector_name="Technology, Software, SaaS & IT Services",
        recommended_chapters=[
            "Corporate Profile, Vision & Innovation Charter",
            "CEO & Board Strategic Review",
            "Market Opportunity, Total Addressable Market (TAM) & Ecosystem",
            "Product Portfolio, Cloud Platforms & Architecture",
            "Operational Performance & Go-To-Market Traction",
            "Research & Development (R&D) & Intellectual Property",
            "Human Capital, Talent Engineering & Developer Retention",
            "Cybersecurity, Privacy, Data Governance & AI Ethics",
            "Financial Performance & Highlights",
            "Statement Highlights (Balance Sheet, Income Statement, Cash Flow)",
            "Accounting Disclosures & Canonical Facts Appendix"
        ],
        key_metrics_and_kpis=[
            "Annual Recurring Revenue (ARR)", "Net Revenue Retention (NRR)",
            "Gross Margin", "R&D as % of Revenue", "Customer Acquisition Cost (CAC)",
            "Lifetime Value (LTV)", "Active Users / Subscription Scale", "EBITDA Margin"
        ],
        specific_risk_factors=[
            "Rapid technological obsolescence and generative AI disruption",
            "Cloud infrastructure concentration and platform outage exposure",
            "Data privacy regulation (GDPR, CCPA) and cybersecurity breaches",
            "Competition for specialist engineering talent and compensation inflation"
        ],
        peer_structure_notes="Peer technology firms emphasize ARR metrics, customer expansion rates, cloud infrastructure efficiency, R&D capitalization policies, and IP portfolios."
    ),
    "manufacturing": IndustryBenchmark(
        sector_name="Manufacturing, Industrial & Automotive",
        recommended_chapters=[
            "Enterprise Profile, Manufacturing Footprint & Global Facilities",
            "Chairman & Leadership Operational Message",
            "Industrial Industry Trends & Raw Material Dynamics",
            "Manufacturing Operations, Plant Utilization & Production Capacity",
            "Supply Chain Resilience, Logistics & Strategic Sourcing",
            "Product Quality, Safety & Lean Engineering",
            "Environmental Sustainability, Emissions & Energy Transition",
            "Workforce Health, Safety & Operational Governance",
            "Financial Performance & Highlights",
            "Financial Summary Tables (Balance Sheet, P&L, Cash Flow)",
            "Statutory Notes & Canonical Ledger Appendix"
        ],
        key_metrics_and_kpis=[
            "Production Output / Installed vs Utilized Capacity", "Gross Margin",
            "EBITDA Margin", "Inventory Turnover Days", "Order Book / Backlog",
            "Capital Expenditure (CapEx)", "Lost Time Injury Frequency Rate (LTIFR)",
            "Carbon / Energy Intensity per unit produced"
        ],
        specific_risk_factors=[
            "Raw material commodity price volatility and supply chain disruption",
            "Factory downtime, machine failure, and labor disputes",
            "Environmental compliance, carbon tax, and emissions mandates",
            "Cyclical demand slowdown in industrial and consumer end-markets"
        ],
        peer_structure_notes="Peer manufacturers emphasize plant capacity utilization, CapEx modernization projects, inventory cycle management, and health & safety metrics."
    ),
    "healthcare": IndustryBenchmark(
        sector_name="Healthcare, Pharmaceuticals & Biotechnology",
        recommended_chapters=[
            "Company Profile, Therapeutic Areas & Patient Mission",
            "Executive Leadership Review",
            "Therapeutic Market Dynamics & Clinical Need",
            "R&D Pipeline, Clinical Trials & Development Milestones",
            "Regulatory Approvals, Pharmacovigilance & Compliance",
            "Manufacturing Quality (cGMP), Supply Chain & Distribution",
            "Commercial Portfolio & Market Penetration",
            "Intellectual Property, Patent Portfolio & Expiry Schedules",
            "Financial Results & Capital Allocation",
            "Audited Statement Highlights",
            "Canonical Ledger & Clinical Governance Appendix"
        ],
        key_metrics_and_kpis=[
            "R&D Pipeline Phase I/II/III Assets", "R&D Spend as % of Revenue",
            "Top Drug / Product Concentration", "Patent Expiry Timeline",
            "Regulatory Inspection Clearance Rate", "Gross Margin", "Operating Margin"
        ],
        specific_risk_factors=[
            "Clinical trial failures or efficacy endpoints not met",
            "Regulatory rejection or delay (FDA, EMA, national regulators)",
            "Patent expirations and generic pricing erosion",
            "Product liability, pharmacovigilance recalls, and litigation"
        ],
        peer_structure_notes="Peer pharmaceutical reports dedicate substantial depth to pipeline progression, patent protection cliffs, and regulatory inspection compliance."
    ),
    "retail": IndustryBenchmark(
        sector_name="Retail, Consumer Goods & E-Commerce",
        recommended_chapters=[
            "Brand Profile, Customer Proposition & Market Reach",
            "Leadership Review & Omnichannel Strategy",
            "Consumer Macro Environment & Spending Trends",
            "Omnichannel Network, Store Operations & Digital Platforms",
            "Merchandising, Private Labels & Supply Chain Logistics",
            "Customer Engagement, Loyalty Programs & Retention",
            "Sustainability, Ethical Sourcing & Packaging",
            "Financial Highlights & Store Economics",
            "Audited Statement Highlights",
            "Canonical Ledger Appendix"
        ],
        key_metrics_and_kpis=[
            "Same-Store Sales Growth (SSSG)", "Average Order Value (AOV)",
            "Store Footprint / Retail Area (Sq. Ft.)", "Inventory Days",
            "Digital / Online Sales Contribution %", "Gross Margin", "EBITDA Margin"
        ],
        specific_risk_factors=[
            "Consumer discretionary spending contraction",
            "Freight and logistics cost inflation",
            "Inventory markdowns and seasonal unsold stock write-offs",
            "Intense competition from pure-play e-commerce and price-matching"
        ],
        peer_structure_notes="Peer consumer reports emphasize same-store growth, store expansion plans, digital mix percentages, and working capital efficiency."
    ),
    "energy": IndustryBenchmark(
        sector_name="Energy, Oil & Gas, Renewables & Utilities",
        recommended_chapters=[
            "Corporate Profile, Energy Assets & Decarbonization Charter",
            "Executive Review & Energy Transition Strategy",
            "Global Energy Commodity Markets & Macro Supply Dynamics",
            "Power Generation, Renewable Capacity (Solar/Wind) & Upstream Exploration",
            "Transmission, Refining, Distribution & Grid Infrastructure",
            "Health, Safety & Environment (HSE) & Scope 1/2/3 Emissions",
            "Capital Allocation, Asset Integrity & Project Development",
            "Corporate Governance & ESG Compliance",
            "Financial Performance & Segment Profitability",
            "Audited Financial Highlights",
            "Canonical Ledger & Reserve Replacement Appendix"
        ],
        key_metrics_and_kpis=[
            "Power Generation Output (MWh / GW)", "Installed Renewable Capacity (MW)",
            "Plant Load Factor (PLF)", "Scope 1 & 2 Emissions Intensity",
            "Gross Refining Margin (GRM)", "Capital Expenditure (CapEx)", "EBITDA Margin"
        ],
        specific_risk_factors=[
            "Crude oil and energy commodity price volatility",
            "Carbon emission taxation and environmental transition mandates",
            "Grid connectivity delays and renewable curtailment risks",
            "Major operational accidents, asset integrity and safety hazards"
        ],
        peer_structure_notes="Peer energy firms prioritize installed generation capacity, transition CapEx, plant load factors, and greenhouse gas metrics."
    ),
    "infrastructure": IndustryBenchmark(
        sector_name="Real Estate, Construction & Infrastructure",
        recommended_chapters=[
            "Enterprise Overview & Infrastructure Project Footprint",
            "Leadership Strategic Review & Project Pipeline",
            "Urbanization, Infrastructure Policy & Construction Environment",
            "Project Development, Construction Execution & EPC Delivery",
            "Order Book Health, Bidding Pipeline & Milestone Progress",
            "Commercial Leasing, Occupancy Rates & Rental Realizations",
            "Quality Engineering, Contractor Safety & ESG Standards",
            "Corporate Governance & Statutory Approvals",
            "Financial Performance, Project Debt & Capitalized Financing",
            "Audited Financial Statement Highlights",
            "Canonical Facts & Project Order Book Appendix"
        ],
        key_metrics_and_kpis=[
            "Order Book / Backlog", "Book-to-Bill Ratio", "Area Under Development (Sq. Ft.)",
            "Presales / Booking Value", "Occupancy Rate %", "Debt-to-Equity", "EBITDA Margin"
        ],
        specific_risk_factors=[
            "Project execution delays, cost escalation, and contractor disputes",
            "Interest rate cycles impacting financing and debt servicing",
            "Land acquisition clearances, zoning, and environmental permits",
            "Working capital liquidity constraints and billing milestones"
        ],
        peer_structure_notes="Peer infrastructure reports emphasize unexecuted order backlog, book-to-bill ratios, construction execution velocity, and debt structure."
    ),
    "telecom": IndustryBenchmark(
        sector_name="Telecommunications, 5G & Digital Connectivity",
        recommended_chapters=[
            "Corporate Profile, Digital Vision & Network Architecture",
            "CEO & Board Strategic Review",
            "Telecom Industry Trends, Data Consumption & Spectrum Landscape",
            "Wireless, Broadband & Enterprise Network Operations",
            "5G Rollout, Fiber Footprint & Network Coverage",
            "Digital Ecosystem, Platforms & Customer Experience",
            "Cybersecurity, Network Resilience & Privacy Governance",
            "Corporate Governance & Telecom Regulatory Compliance",
            "Financial Performance, ARPU & Operating Returns",
            "Audited Financial Statement Highlights",
            "Canonical Ledger & Spectrum Disclosures Appendix"
        ],
        key_metrics_and_kpis=[
            "Average Revenue Per User (ARPU)", "Total Subscriber Base",
            "Monthly Data Usage per Customer (GB)", "5G Tower Deployments",
            "Fiber Home Passes", "Churn Rate %", "EBITDA Margin"
        ],
        specific_risk_factors=[
            "High spectrum acquisition debt and capital expenditure requirements",
            "Intense tariff competition and promotional pricing pressures",
            "Regulatory license fees, spectrum compliance, and AGR obligations",
            "Critical network outages and cyber attack threats on telecom cores"
        ],
        peer_structure_notes="Peer telecom companies focus heavily on ARPU trajectory, data usage growth, spectrum holding schedules, and 5G deployment progress."
    ),
    "logistics": IndustryBenchmark(
        sector_name="Logistics, Transportation & Supply Chain",
        recommended_chapters=[
            "Enterprise Profile, Global Multimodal Network & Hubs",
            "Leadership Review & Supply Chain Strategy",
            "Global Trade Corridors, Freight Rates & Freight Environment",
            "Multimodal Operations (Air, Ocean, Road, Rail) & Warehousing",
            "Fleet Management, Asset Utilization & Turnaround Efficiency",
            "Digital Logistics, Real-Time Tracking & Warehouse Automation",
            "Operational Safety, Fleet Decarbonization & Green Logistics",
            "Corporate Governance & Customs Compliance",
            "Financial Results, Freight Margins & Working Capital",
            "Audited Statement Highlights",
            "Canonical Ledger Appendix"
        ],
        key_metrics_and_kpis=[
            "Freight Volume Handled (TEUs / Tons)", "Warehousing Area (Sq. Ft.)",
            "Fleet Utilization Rate %", "Fuel Surcharge Recovery %",
            "On-Time In-Full (OTIF) Delivery %", "Operating Margin", "Cash Flow from Operations"
        ],
        specific_risk_factors=[
            "Global freight rate swings and container shipping bottlenecks",
            "Fuel price volatility and fleet inflation",
            "Driver/crew availability and cross-border trade tariffs",
            "Warehouse cargo security and supply chain disruption"
        ],
        peer_structure_notes="Peer logistics companies detail warehousing capacity, freight volumes, fleet utilization, and fuel pass-through mechanisms."
    ),
    "generic": IndustryBenchmark(
        sector_name="General Corporate Enterprise",
        recommended_chapters=[
            "Corporate Profile, Vision & Operating Model",
            "Executive Review & Strategic Priorities",
            "Operational Review & Key Business Segments",
            "Corporate Governance & Leadership Structure",
            "Risk Management & Internal Control Framework",
            "Financial Performance & Key Financial Metrics",
            "Deterministic Financial Statement Highlights",
            "Canonical Facts Ledger & Audit Appendix"
        ],
        key_metrics_and_kpis=[
            "Total Revenue", "Operating Profit / EBITDA", "Net Profit",
            "Total Assets", "Shareholders Equity", "Cash & Cash Equivalents"
        ],
        specific_risk_factors=[
            "Macroeconomic demand fluctuation and competitive pressures",
            "Operational execution and cost management",
            "Regulatory compliance and governance"
        ],
        peer_structure_notes="Standard general corporate reporting structure focusing on operating review, governance, and audited financial statements."
    )
}


def get_industry_benchmark(sector_or_industry: Optional[str] = None) -> IndustryBenchmark:
    """Resolve the industry reporting benchmark, typical peer chapters, and KPIs for any sector."""
    if not sector_or_industry:
        return INDUSTRY_SECTOR_BENCHMARKS["generic"]
    
    sec_clean = re.sub(r"[^a-z0-9]+", " ", sector_or_industry.lower()).strip()
    toks = sec_clean.split()

    # 1. Healthcare & Life Sciences (checked before Tech to capture Biotech correctly)
    if any(k in sec_clean for k in ("pharma", "biotech", "health", "hospital", "drug", "clinical", "therapeutic", "medical", "medicine")):
        return INDUSTRY_SECTOR_BENCHMARKS["healthcare"]

    # 2. Banking, Lending & Financial Services
    if any(k in sec_clean for k in ("bank", "finance", "lending", "credit", "nbfc", "nbf", "loan", "fund", "wealth", "asset management", "insurance")):
        return INDUSTRY_SECTOR_BENCHMARKS["banking"]

    # 3. Energy, Oil, Gas & Utilities
    if any(k in sec_clean for k in ("energy", "oil", "gas", "power", "renewable", "solar", "wind", "utility", "utilities", "petroleum", "petro")):
        return INDUSTRY_SECTOR_BENCHMARKS["energy"]

    # 4. Infrastructure, Real Estate & Construction
    if any(k in sec_clean for k in ("infra", "infrastructure", "construction", "real estate", "property", "reit", "building", "epc")):
        return INDUSTRY_SECTOR_BENCHMARKS["infrastructure"]

    # 5. Telecommunications & Connectivity
    if any(k in sec_clean for k in ("telecom", "telecommunication", "broadband", "cellular", "wireless", "spectrum", "5g", "network")):
        return INDUSTRY_SECTOR_BENCHMARKS["telecom"]

    # 6. Logistics, Freight & Transportation
    if any(k in sec_clean for k in ("logistic", "logistics", "transport", "freight", "shipping", "cargo", "courier", "aviation", "airline", "fleet")):
        return INDUSTRY_SECTOR_BENCHMARKS["logistics"]

    # 7. Retail, Consumer Goods & E-Commerce
    if any(k in sec_clean for k in ("retail", "consumer", "commerce", "store", "fmcg", "apparel", "food", "fashion")):
        return INDUSTRY_SECTOR_BENCHMARKS["retail"]

    # 8. Technology, Software, SaaS & IT Services
    if any(k in sec_clean for k in ("tech", "software", "saas", "it services", "cloud", "digital", "data", "robotics", "ai", "hardware", "cyber")):
        return INDUSTRY_SECTOR_BENCHMARKS["technology"]

    # 9. Manufacturing & Automotive
    if any(k in sec_clean for k in ("manufactur", "auto", "automotive", "industrial", "steel", "engineering", "cement", "chemical", "factory")):
        return INDUSTRY_SECTOR_BENCHMARKS["manufacturing"]

    return INDUSTRY_SECTOR_BENCHMARKS["generic"]
