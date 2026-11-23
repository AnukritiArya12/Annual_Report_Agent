"""Run this FIRST:  python check_setup.py
Tells you which files are missing/outdated and which packages are not installed."""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# file -> text that only the NEW version contains (detects old files left over from the first version)
FILES = {
    "main.py": "class MainOrchestrator", "planner.py": "_split_node", "agent.py": "class RunContext",
    "knowledge.py": "class ReferenceIngestor", "validator.py": "_foreign_entities", "assembler.py": "parse_markdown_blocks",
    "config.py": "class AnthropicClient", "schemas.py": "class SourceDocument", "tools.py": "def download_pdf",
    "skills.py": "STYLE_GUIDES", "approvals.py": "class ApprovalAborted", "utils.py": "def foreign_entities",
}
PACKAGES = [("requests", "requests"), ("bs4", "beautifulsoup4"), ("ddgs", "ddgs"), ("pydantic", "pydantic"),
            ("dotenv", "python-dotenv"), ("docx", "python-docx"), ("reportlab", "reportlab")]

ok = True
print(f"Folder: {HERE}\n\nFILES")
for name, marker in FILES.items():
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        print(f"  MISSING   {name}"); ok = False; continue
    text = open(path, encoding="utf-8", errors="ignore").read()
    if marker not in text:
        print(f"  OUTDATED  {name}  (old version - replace it with the new one)"); ok = False
    else:
        print(f"  ok        {name}")

print("\nPACKAGES")
for mod, pip_name in PACKAGES:
    if importlib.util.find_spec(mod) is None:
        print(f"  MISSING   {pip_name}"); ok = False
    else:
        print(f"  ok        {pip_name}")
if importlib.util.find_spec("pymupdf") is None and importlib.util.find_spec("fitz") is None:
    print("  MISSING   pymupdf"); ok = False
else:
    print("  ok        pymupdf")

print("\nSETTINGS (.env)")
env = os.path.join(HERE, ".env")
if not os.path.exists(env):
    print("  MISSING   .env  (copy .env.example to .env and fill in your provider/key)"); ok = False
else:
    txt = open(env, encoding="utf-8", errors="ignore").read().lower()
    has_key = any(k in txt for k in ("gemini_api_key=a", "openai_api_key=s", "anthropic_api_key=s")) \
        or "llm_provider=ollama" in txt.replace(" ", "")
    print("  ok        .env found" if has_key else "  WARNING   .env found but no API key / ollama provider detected")

print("\n" + ("ALL GOOD. Run:  python main.py \"Apple\" --fy \"FY 2025\" --country USA --pages 60" if ok else
      "FIX THE ITEMS ABOVE, then run this check again.\n(packages:  pip install -r requirements.txt)"))
sys.exit(0 if ok else 1)