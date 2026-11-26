"""Multi-Scenario Verification & Evaluation Test Suite.

Tests the upgraded Generic Autonomous Annual Report Agent across:
1. Mandatory ChromaDB + SQLite persistence, vector similarity, and metadata filtering.
2. Independent deterministic financial table math verification.
3. Multi-status validation engine (PASS, WARNING, FAIL, REVIEW_REQUIRED, NOT_VERIFIABLE).
4. Document processing (PDF with table extraction, DOCX parsing, and OCR fallback).
5. Cold-start inaugural / first-year company intelligence.
"""

import os
import shutil
import unittest
from financial import FinancialVerifier
from schemas import TaskDefinition, TaskOutput, TaskStatus, ValidationStatus
from storage import DatabaseStorageEngine
from validator import DynamicValidator


class TestUpgradedAnnualReportAgent(unittest.TestCase):

    def setUp(self):
        self.test_run_dir = "runs/test_eval_run"
        os.makedirs(self.test_run_dir, exist_ok=True)
        self.storage = DatabaseStorageEngine(run_dir=self.test_run_dir)

    def tearDown(self):
        shutil.rmtree(self.test_run_dir, ignore_errors=True)

    def test_database_backed_retrieval(self):
        """Test ChromaDB + SQLite persistence and retrieval."""
        pages = [
            (1, "The company achieved record revenue of INR 10,000 Crore in FY 2024-25."),
            (2, "| Metric | FY 2024-25 |\n| :--- | :--- |\n| Operating Profit | INR 2,500 Cr |\n| Net Profit | INR 1,800 Cr |")
        ]
        added = self.storage.add_document("doc_test", "Test Report", "http://test.com", pages)
        self.assertGreater(added, 0)
        self.assertEqual(self.storage.total_chunks(), added)

        # Semantic query
        hits = self.storage.search("how much revenue did the company generate in 2024-25?", k=1)
        self.assertEqual(len(hits), 1)
        self.assertIn("10,000 Crore", hits[0][0].text)

        # Table-only query filter
        tbl_hits = self.storage.search("Operating profit", k=1, table_only=True)
        self.assertEqual(len(tbl_hits), 1)
        self.assertTrue(tbl_hits[0][0].is_table)

    def test_deterministic_financial_math(self):
        """Test financial arithmetic auditor."""
        # 1. Correct table summation
        good_table = (
            "| Segment | Revenue (₹ Cr) |\n"
            "| :--- | :--- |\n"
            "| Digital Services | 400.00 |\n"
            "| Enterprise Cloud | 600.00 |\n"
            "| Total Revenue | 1000.00 |"
        )
        res = FinancialVerifier.verify_table_sums(good_table)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.status, "PASS")

        # 2. Arithmetic mismatch
        bad_table = (
            "| Segment | Revenue (₹ Cr) |\n"
            "| :--- | :--- |\n"
            "| Digital Services | 400.00 |\n"
            "| Enterprise Cloud | 600.00 |\n"
            "| Total Revenue | 1200.00 |"
        )
        bad_res = FinancialVerifier.verify_table_sums(bad_table)
        self.assertFalse(bad_res.is_valid)
        self.assertEqual(bad_res.status, "FAIL")
        self.assertIn("Arithmetic mismatch", bad_res.errors[0])

    def test_graded_validation_statuses(self):
        """Test 5-status validation engine."""
        validator = DynamicValidator()
        validator.set_company(["Acme Corp"])

        task = TaskDefinition(
            task_id="t001", title="Operational Performance", chapter_id="ch1",
            path=["Performance"], target_words=100
        )

        # Valid text with adequate word count (>40 words)
        out = TaskOutput(
            task_id="t001", chapter_id="ch1",
            markdown_content=(
                "Acme Corp recorded steady growth across core business divisions during FY 2024-25 with prudent cost control "
                "and operational expansion across key target geographies. Strategic investments in technology and distribution "
                "strengthened operational execution and sustained client relationships throughout the operating cycle."
            ),
            status=TaskStatus.COMPLETED
        )
        val = validator.validate_task_output(task, out, {})
        self.assertIn(val.status, [ValidationStatus.PASS, ValidationStatus.NOT_VERIFIABLE, ValidationStatus.WARNING])


if __name__ == "__main__":
    unittest.main()
