"""Reusable Skills Library for the Generic Task Execution Agent.

Provides domain-specific analytical guidelines and instructions dynamically injected
into the Generic Task Execution Agent based on task requirements.
No fixed specialist agents are needed; this single library empowers the generic agent.
"""

from typing import Dict, List

SKILL_REGISTRY: Dict[str, str] = {
    "corporate_research": (
        "SKILL GUIDELINES: Corporate Research & Identity\n"
        "- Detail the corporate lineage, date of incorporation, and headquarters.\n"
        "- Identify global delivery centers, international offices, and total employee footprint.\n"
        "- Highlight core brand identity, vision, and corporate mission.\n"
        "- Ensure all operational metrics have an attributable source URL or official filing reference."
    ),
    "industry_segmentation": (
        "SKILL GUIDELINES: Business Unit & Industry Segmentation\n"
        "- Break down operations into distinct business segments / service lines.\n"
        "- Describe the customer value proposition, target clientele, and core technologies of each segment.\n"
        "- Highlight new digital / AI transformation service offerings launched in the reporting period."
    ),
    "market_research": (
        "SKILL GUIDELINES: Macroeconomic & Market Trend Analysis\n"
        "- Analyze macroeconomic factors affecting demand (enterprise IT budgets, inflation, interest rates).\n"
        "- Identify emerging technology tailwinds (Cloud transformation, Generative AI, cybersecurity).\n"
        "- Synthesize industry analyst consensus on sector growth rates."
    ),
    "peer_benchmarking": (
        "SKILL GUIDELINES: Competitive Peer Benchmarking\n"
        "- Benchmark the company against at least 2-3 named primary competitors.\n"
        "- Compare scale, revenue growth velocity, geographic concentration, and margin profile.\n"
        "- Present a structured markdown comparison matrix or key metrics table."
    ),
    "financial_analysis": (
        "SKILL GUIDELINES: Financial Statement & Ratio Analysis\n"
        "- Report Consolidated figures: Total Revenue, Operating Profit (EBIT/EBITDA), Net Profit (PAT), and EPS.\n"
        "- Calculate Year-over-Year (YoY) percentage changes where both current and prior periods are available.\n"
        "- Clearly specify currency and scale (e.g., INR Crores, USD Millions, EUR Billions).\n"
        "- Format primary financial indicators into a structured Markdown Table."
    ),
    "regulatory_compliance": (
        "SKILL GUIDELINES: Statutory & Listing Compliance Audit\n"
        "- Reference governing regulatory frameworks (e.g., Companies Act 2013, SEBI LODR Regulations 2015).\n"
        "- Document Board of Directors composition, independent director ratio, and key statutory committees.\n"
        "- Note compliance with applicable accounting standards (Ind AS / IFRS / US GAAP)."
    ),
    "risk_assessment": (
        "SKILL GUIDELINES: Enterprise Risk Management (ERM)\n"
        "- Identify principal strategic, operational, regulatory, and cybersecurity risks.\n"
        "- Detail specific risk mitigation policies, internal controls, and disaster recovery architectures.\n"
        "- Highlight exposure to foreign currency exchange volatility and geopolitical dynamics."
    ),
    "talent_analytics": (
        "SKILL GUIDELINES: Human Capital & Workforce Analytics\n"
        "- Detail total headcount, net additions, and annualized employee attrition rates.\n"
        "- Document investments in employee upskilling (especially in AI, Cloud, and emerging technology).\n"
        "- State diversity ratios, gender inclusion statistics, and employee wellbeing programs."
    ),
    "esg_reporting": (
        "SKILL GUIDELINES: Sustainability, CSR & Environmental Impact (BRSR)\n"
        "- Report on environmental sustainability goals: Net Zero target year, renewable energy consumption %, water positivity.\n"
        "- Detail Corporate Social Responsibility (CSR) project allocations and beneficiary impact.\n"
        "- Align disclosures with statutory sustainability reporting norms (e.g. SEBI BRSR Core, GRI Standards)."
    )
}


def get_skill_guidance(skill_names: List[str]) -> str:
    """Combines analytical guidelines for all skills requested by a task."""
    guidelines = []
    for name in skill_names:
        key = name.lower().strip()
        if key in SKILL_REGISTRY:
            guidelines.append(SKILL_REGISTRY[key])
    return "\n\n".join(guidelines)
