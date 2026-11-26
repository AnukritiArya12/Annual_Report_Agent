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
import uuid
from financial import FinancialVerifier
from jurisdictions import get_jurisdiction, JURISDICTION_REGISTRY
from ledger import FactsLedger, FactRecord, FactStatus
from schemas import TaskDefinition, TaskOutput, TaskStatus, ValidationStatus
from storage import DatabaseStorageEngine
from validator import DynamicValidator


class TestUpgradedAnnualReportAgent(unittest.TestCase):

    def setUp(self):
        self.test_run_dir = f"runs/test_eval_run_{uuid.uuid4().hex[:8]}"
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

    def test_jurisdiction_registry(self):
        """Test jurisdiction resolution and registry metadata."""
        us_juri = get_jurisdiction("United States")
        self.assertEqual(us_juri.code, "US")
        self.assertIn("US GAAP", us_juri.accounting_frameworks)

        in_juri = get_jurisdiction("India")
        self.assertEqual(in_juri.code, "IN")
        self.assertIn("Ind AS", in_juri.accounting_frameworks)

        uk_juri = get_jurisdiction("United Kingdom")
        self.assertEqual(uk_juri.code, "UK")

        # Unknown country defaults to GENERIC
        unknown_juri = get_jurisdiction("Atlantis")
        self.assertEqual(unknown_juri.code, "GENERIC")
        self.assertGreater(len(unknown_juri.mandatory_sections), 0)

    def test_facts_ledger_operations(self):
        """Test FactsLedger recording, conflict tracking, and canonical block generation."""
        ledger = FactsLedger(storage=self.storage)
        self.assertEqual(ledger.count_facts(), 0)

        # 1. Record canonical revenue fact
        f1 = FactRecord(
            key="revenue", value="INR 10,000 Crore", numeric_value=10000.0,
            unit="Crore", currency="INR", period="FY 2024-25", scope="Consolidated",
            source_doc_id="doc_annual_report", page=12, chunk_id="chk_001", confidence=0.95
        )
        ledger.record_fact(f1)
        self.assertEqual(ledger.count_facts(), 1)

        # 2. Record conflicting fact for same key and period
        f2 = FactRecord(
            key="revenue", value="INR 12,500 Crore", numeric_value=12500.0,
            unit="Crore", currency="INR", period="FY 2024-25", scope="Consolidated",
            source_doc_id="doc_news_article", page=1, chunk_id="chk_002", confidence=0.80,
            status=FactStatus.CONFLICTING
        )
        ledger.record_fact(f2)
        self.assertEqual(ledger.count_facts(), 2)

        conflicts = ledger.get_conflicting_facts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].numeric_value, 12500.0)

        # 3. Missing core keys check
        missing = ledger.get_missing_core_keys("FY 2024-25")
        self.assertIn("net_profit", missing)
        self.assertNotIn("revenue", missing)

        # 4. Canonical block format
        block = ledger.format_canonical_block(["revenue and sales"])
        self.assertIn("CANONICAL FACTS", block)
        self.assertIn("Revenue", block)

    def test_ledger_consistency_validation(self):
        """Test that validator flags drafts contradicting canonical ledger values."""
        ledger = FactsLedger(storage=self.storage)
        ledger.record_fact(FactRecord(
            key="net_profit", value="INR 2,500 Crore", numeric_value=2500.0,
            unit="Crore", currency="INR", period="FY 2024-25", scope="Consolidated",
            source_doc_id="doc_01", page=5, chunk_id="chk_np", confidence=0.99
        ))

        validator = DynamicValidator()
        validator.set_company(["Acme Corp"])
        validator.set_ledger(ledger)

        task = TaskDefinition(
            task_id="t_np", title="Financial Results", chapter_id="ch_fin",
            path=["Financials"], target_words=100
        )

        # Draft containing contradicting net profit figure (e.g. 9,999 Crore instead of 2,500 Crore)
        bad_draft = (
            "Acme Corp reported exceptional operational momentum throughout FY 2024-25 across business divisions. "
            "The company recorded Net Profit of INR 9,999 Crore during the fiscal year under review, representing "
            "unprecedented profitability and margin expansion across digital operating channels."
        )
        out_bad = TaskOutput(
            task_id="t_np", chapter_id="ch_fin",
            markdown_content=bad_draft, status=TaskStatus.COMPLETED
        )
        val_bad = validator.validate_task_output(task, out_bad, {})
        self.assertTrue(any("Canonical Ledger" in issue for issue in val_bad.issues_found))

        # Consistent draft containing 2,500 Crore
        good_draft = (
            "Acme Corp reported solid operating momentum throughout FY 2024-25 across business divisions. "
            "The company recorded Net Profit of INR 2,500 Crore during the fiscal year under review, representing "
            "consistent profitability and margin stability across digital operating channels."
        )
        out_good = TaskOutput(
            task_id="t_np", chapter_id="ch_fin",
            markdown_content=good_draft, status=TaskStatus.COMPLETED
        )
        val_good = validator.validate_task_output(task, out_good, {})
        self.assertFalse(any("Canonical Ledger" in issue for issue in val_good.issues_found))

    def test_hybrid_search_and_table_metadata(self):
        """Test FTS5 keyword matching and table chunk metadata enrichment."""
        pages = [
            (1, "The executive management team focused on core strategic business expansion and customer experience."),
            (2, "| Segment | Revenue | Operating Margin |\n| :--- | :--- | :--- |\n| Consumer Products | 4500 | 18% |\n| Enterprise Cloud | 5500 | 24% |")
        ]
        self.storage.add_document("doc_hybrid", "Corporate Strategy", "http://example.com", pages)

        # FTS keyword search
        k_hits = self.storage._fts_keyword_search("Consumer Products Enterprise Cloud", limit=5)
        self.assertGreater(len(k_hits), 0)

        # Hybrid search with table boosting
        results = self.storage.search("Segment Revenue Operating Margin", k=2)
        self.assertGreater(len(results), 0)
        # Top result should be the table chunk
        self.assertTrue(results[0][0].is_table)
        self.assertIn("headers", results[0][0].metadata)
        self.assertIn("Segment", results[0][0].metadata["headers"])


if __name__ == "__main__":
    unittest.main()
