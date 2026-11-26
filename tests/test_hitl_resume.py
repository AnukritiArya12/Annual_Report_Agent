"""Unit tests for Phase 4: Human-in-the-loop interrupts and persistent resume."""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid

from agent import build_annual_report_agent
from tools import ReportSession
from tools.checkpointer import SqliteCheckpointSaver
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage, ToolCall
from langgraph.types import Command


class TestPhase4HITLAndResume(unittest.TestCase):
    def setUp(self):
        self.run_dir = os.path.join(os.path.dirname(__file__), f"_tmp_hitl_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.run_dir, exist_ok=True)
        self.session = ReportSession(
            run_dir=self.run_dir,
            company_name="Nova Robotics",
            financial_year="FY 2024",
            country="United States",
            auto_approve=False,
        )
        self.db_path = os.path.join(self.run_dir, "checkpoints.sqlite")
        self.checkpointer = SqliteCheckpointSaver(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_persisted_decisions_not_reasked(self):
        # Human approves SEC domain
        self.session.approve_domain("sec.gov")
        self.assertTrue(self.session.is_domain_approved("https://www.sec.gov/filing.htm"))

        # Reload session from disk
        new_session = ReportSession(
            run_dir=self.run_dir,
            company_name="Nova Robotics",
            financial_year="FY 2024",
        )
        self.assertTrue(new_session.is_domain_approved("https://www.sec.gov/filing.htm"))

    def test_checkpoint_saver_put_and_get(self):
        config = {"configurable": {"thread_id": "test_thread_1", "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": "chk_001",
            "ts": "2026-11-26T12:00:00Z",
            "channel_values": {"messages": ["hello world"]},
            "channel_versions": {"messages": 1},
            "versions_seen": {},
        }
        metadata = {"step": 1, "source": "input"}
        self.checkpointer.put(config, checkpoint, metadata, {})

        # Retrieve checkpoint
        saved = self.checkpointer.get_tuple(config)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.checkpoint["id"], "chk_001")
        self.assertEqual(saved.checkpoint["channel_values"]["messages"], ["hello world"])


if __name__ == "__main__":
    unittest.main()
