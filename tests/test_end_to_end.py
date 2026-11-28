"""End-to-end autonomous integration test for ONE DeepAgent writing an annual report."""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from typing import Any, List

from agent import build_annual_report_agent
from tools import ReportSession
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult


class EndToEndMockModel(BaseChatModel):
    """Deterministic Mock Chat Model that simulates realistic autonomous DeepAgent decisions."""
    step: int = 0

    def _generate(self, messages: List[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs) -> ChatResult:
        self.step += 1
        
        # Step 1: Discover Identity
        if self.step == 1:
            tc = ToolCall(name="resolve_company_identity", args={"name": "Aether Dynamics Inc", "country": "United States"}, id="call_1")
            msg = AIMessage(content="I will first resolve the company identity and governing jurisdiction.", tool_calls=[tc])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        # Step 2: Ingest Cold Start Dossier
        if self.step == 2:
            fields = {
                "registration_id": "US-DEL-2024-884",
                "incorporation_date": "15-January-2024",
                "business_model": "Autonomous robotics and industrial edge computing systems.",
                "initial_capital": "$25,000,000",
                "revenue": "$12,450,000",
                "expenses": "$9,200,000",
                "net_profit": "$3,250,000",
                "cash_at_bank": "$18,500,000"
            }
            tc = ToolCall(name="create_cold_start_dossier", args={"fields": fields}, id="call_2")
            msg = AIMessage(content="Indexing company-provided corporate information for Aether Dynamics Inc.", tool_calls=[tc])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        # Step 3: Check capacity and build ledger
        if self.step == 3:
            tc = ToolCall(name="build_facts_ledger", args={"force": True}, id="call_3")
            msg = AIMessage(content="Building canonical facts ledger.", tool_calls=[tc])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        # Step 4: Write section 1 to disk
        if self.step == 4:
            content = """# Corporate Overview & Genesis Charter

Aether Dynamics Inc is a pioneering enterprise incorporated on 15-January-2024 (ID: US-DEL-2024-884).
The company specializes in autonomous robotics and industrial edge computing systems.
Initial paid-up equity capital committed was $25,000,000 with strong closing cash liquidity of $18,500,000.
"""
            tc = ToolCall(name="write_file", args={"file_path": "drafts/01_overview.md", "content": content}, id="call_4")
            msg = AIMessage(content="Writing Chapter 1: Corporate Overview to disk.", tool_calls=[tc])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        # Step 5: Write section 2 to disk
        if self.step == 5:
            content = """# Financial Performance & Capital Position

During FY 2024, Aether Dynamics Inc recorded Total Revenue of $12,450,000 against operating expenses of $9,200,000.
Net profit for the inaugural operating period reached $3,250,000.
Closing cash at bank stood at $18,500,000, ensuring operational runway.

| Financial Metric | FY 2024 As Reported | Verification Status |
| :--- | :--- | :--- |
| Revenue from Operations | $12,450,000 | Primary Reported Metric |
| Total Operating Expenses | $9,200,000 | Primary Reported Metric |
| Net Profit for Period | $3,250,000 | Primary Reported Metric |
| Cash and Cash Equivalents | $18,500,000 | Primary Reported Metric |
"""
            tc = ToolCall(name="write_file", args={"file_path": "drafts/02_financials.md", "content": content}, id="call_5")
            msg = AIMessage(content="Writing Chapter 2: Financial Performance to disk.", tool_calls=[tc])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        # Step 6: Validate section 2
        if self.step == 6:
            text = """During FY 2024, Aether Dynamics Inc recorded Total Revenue of $12,450,000 against operating expenses of $9,200,000.
Net profit for the inaugural operating period reached $3,250,000.
Closing cash at bank stood at $18,500,000, ensuring operational runway.

| Financial Metric | FY 2024 As Reported | Verification Status |
| :--- | :--- | :--- |
| Revenue from Operations | $12,450,000 | Primary Reported Metric |
| Total Operating Expenses | $9,200,000 | Primary Reported Metric |
| Net Profit for Period | $3,250,000 | Primary Reported Metric |
| Cash and Cash Equivalents | $18,500,000 | Primary Reported Metric |
"""
            tc = ToolCall(name="check_section", args={"markdown": text, "section_name": "Financials"}, id="call_6")
            msg = AIMessage(content="Validating Chapter 2 draft.", tool_calls=[tc])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        # Step 7: Assemble report
        if self.step == 7:
            files = ["drafts/01_overview.md", "drafts/02_financials.md"]
            tc = ToolCall(name="assemble_report", args={"section_files": files, "report_title": "Aether Dynamics Inc - Inaugural Annual Report 2024"}, id="call_7")
            msg = AIMessage(content="Assembling final publication.", tool_calls=[tc])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        # Step 8: Done
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Annual report generation completed successfully. All artifacts assembled."))])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        return self

    @property
    def _llm_type(self) -> str:
        return "mock_deepagent_model"


class TestPhase5EndToEnd(unittest.TestCase):
    def setUp(self):
        self.run_dir = os.path.join(os.path.dirname(__file__), f"_tmp_e2e_{uuid.uuid4().hex[:8]}")
        os.makedirs(os.path.join(self.run_dir, "drafts"), exist_ok=True)
        self.session = ReportSession(
            run_dir=self.run_dir,
            company_name="Aether Dynamics Inc",
            financial_year="FY 2024",
            country="United States",
            auto_approve=True,
        )

    def tearDown(self):
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_autonomous_deepagent_execution(self):
        mock_model = EndToEndMockModel()
        agent = build_annual_report_agent(
            session=self.session,
            model=mock_model,
            interrupt_on_human_decisions=False,
        )

        config = {"configurable": {"thread_id": "test_e2e_thread"}, "recursion_limit": 50}
        res = agent.invoke({"messages": [{"role": "user", "content": "Generate full annual report for Aether Dynamics Inc FY 2024"}]}, config=config)

        # 1. Verify files drafted on disk
        sec1 = os.path.join(self.run_dir, "drafts", "01_overview.md")
        sec2 = os.path.join(self.run_dir, "drafts", "02_financials.md")
        self.assertTrue(os.path.exists(sec1), "01_overview.md was not written to disk")
        self.assertTrue(os.path.exists(sec2), "02_financials.md was not written to disk")

        # 2. Verify assembled reports
        md_file = os.path.join(self.run_dir, "aether_dynamics_inc_fy_2024.md")
        self.assertTrue(os.path.exists(md_file), "Consolidated report Markdown file was not created")

        with open(md_file, "r", encoding="utf-8") as f:
            full_text = f.read()

        self.assertIn("Aether Dynamics Inc - Inaugural Annual Report 2024", full_text)
        self.assertIn("US-DEL-2024-884", full_text)
        self.assertIn("$12,450,000", full_text)
        self.assertIn("Table of Contents", full_text)


if __name__ == "__main__":
    unittest.main()
