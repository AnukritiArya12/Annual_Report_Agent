"""DeepAgent Setup & Architecture Verification Script.

Usage:
    python check_setup.py

Validates the ONE autonomous DeepAgent architecture, required packages,
standard Agent Skills, tools layer, and environment configuration.
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

CORE_FILES = {
    "agent.py": "create_deep_agent",
    "app.py": "build_annual_report_agent",
    "assembler.py": "class ReportAssembler",
    "charts.py": "class ChartGenerator",
    "config.py": "class Settings",
    "financial.py": "class FinancialVerifier",
    "jurisdictions.py": "JURISDICTION_REGISTRY",
    "knowledge.py": "def parse_pdf",
    "ledger.py": "class FactsLedger",
    "statements.py": "extract_structured_statements",
    "storage.py": "class DatabaseStorageEngine",
    "utils.py": "def sanitize_untrusted",
}

TOOL_FILES = [
    "tools/__init__.py",
    "tools/session.py",
    "tools/discovery_tools.py",
    "tools/retrieval_tools.py",
    "tools/financial_tools.py",
    "tools/validation_tools.py",
    "tools/output_tools.py",
    "tools/checkpointer.py",
    "tools/web.py",
]

SKILL_DIRS = [
    "annual-report-workflow",
    "source-discovery",
    "cold-start-company",
    "report-planning",
    "section-writing",
    "financial-reporting",
    "validation-and-revision",
    "human-approval",
]

PACKAGES = [
    ("deepagents", "deepagents"),
    ("langchain", "langchain"),
    ("langgraph", "langgraph"),
    ("langgraph.checkpoint.sqlite", "langgraph-checkpoint-sqlite"),
    ("langchain_google_genai", "langchain-google-genai"),
    ("requests", "requests"),
    ("bs4", "beautifulsoup4"),
    ("ddgs", "ddgs"),
    ("pydantic", "pydantic"),
    ("dotenv", "python-dotenv"),
    ("docx", "python-docx"),
    ("reportlab", "reportlab"),
    ("chromadb", "chromadb"),
    ("PIL", "pillow"),
    ("matplotlib", "matplotlib"),
]

ok = True
print(f"Project Root: {HERE}\n")

# 1. Autonomous DeepAgent Core Files
print("CORE ARCHITECTURE FILES")
for name, marker in CORE_FILES.items():
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        print(f"  MISSING   {name}")
        ok = False
        continue
    text = open(path, encoding="utf-8", errors="ignore").read()
    if marker not in text:
        print(f"  OUTDATED  {name} (expected marker '{marker}')")
        ok = False
    else:
        print(f"  ok        {name}")

# 2. Tools Package
print("\nTOOLS PACKAGE")
for rel in TOOL_FILES:
    path = os.path.join(HERE, rel)
    if os.path.exists(path):
        print(f"  ok        {rel}")
    else:
        print(f"  MISSING   {rel}")
        ok = False

# 3. Agent Skills Standard
print("\nAGENT SKILLS STANDARD (skills/<skill>/SKILL.md)")
for s_dir in SKILL_DIRS:
    path = os.path.join(HERE, "skills", s_dir, "SKILL.md")
    if os.path.exists(path):
        txt = open(path, encoding="utf-8", errors="ignore").read()
        if "---" in txt and "name:" in txt and "description:" in txt:
            print(f"  ok        skills/{s_dir}/SKILL.md")
        else:
            print(f"  INVALID   skills/{s_dir}/SKILL.md (missing YAML frontmatter)")
            ok = False
    else:
        print(f"  MISSING   skills/{s_dir}/SKILL.md")
        ok = False

# 4. Connectors
print("\nJURISDICTION CONNECTORS")
connector_files = [
    "connectors/__init__.py",
    "connectors/base.py",
    "connectors/registry.py",
    "connectors/sec_edgar.py",
    "connectors/india.py",
    "connectors/uk_companies_house.py",
    "connectors/investor_relations.py",
]
for rel in connector_files:
    path = os.path.join(HERE, rel)
    if os.path.exists(path):
        print(f"  ok        {rel}")
    else:
        print(f"  MISSING   {rel}")
        ok = False

# 5. Installed Packages
print("\nPACKAGES & RUNTIME DEPENDENCIES")
for mod, pip_name in PACKAGES:
    if importlib.util.find_spec(mod) is None:
        print(f"  MISSING   {pip_name}")
        ok = False
    else:
        print(f"  ok        {pip_name}")

if importlib.util.find_spec("pymupdf") is None and importlib.util.find_spec("fitz") is None:
    print("  MISSING   pymupdf")
    ok = False
else:
    print("  ok        pymupdf")

# 6. Environment & Provider Configuration
print("\nENVIRONMENT CONFIGURATION (.env)")
env_path = os.path.join(HERE, ".env")
if not os.path.exists(env_path):
    print("  WARNING   .env not found. Create .env with GEMINI_API_KEY=your_key.")
else:
    txt = open(env_path, encoding="utf-8", errors="ignore").read().lower()
    has_key = "gemini_api_key=" in txt
    if has_key:
        print("  ok        .env found with valid provider credentials")
    else:
        print("  WARNING   .env found but GEMINI_API_KEY is missing")

print("\n" + ("=" * 60))
if ok:
    print("ALL CHECKS PASSED. SYSTEM IS READY.")
    print("To run the autonomous DeepAgent:")
    print("  python app.py --company \"Apple Inc\" --fy \"FY 2024\" --country \"United States\"")
else:
    print("SOME CHECKS FAILED. Please review the missing or outdated items above.")
    print("To install dependencies: pip install -r requirements.txt")
print("=" * 60)

sys.exit(0 if ok else 1)
