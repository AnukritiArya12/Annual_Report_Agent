"""Unit tests for Phase 1 tools/ package and deterministic guardrails."""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from unittest.mock import patch

from tools import ReportSession, create_report_tools


class TestPhase1Tools(unittest.TestCase):
    def setUp(self):
        self.run_dir = os.path.join(os.path.dirname(__file__), f"_tmp_tools_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.run_dir, exist_ok=True)
        self.session = ReportSession(
            run_dir=self.run_dir,
            company_name="Acme Robotics Corp",
            financial_year="FY 2024",
            country="United States",
        )
        self.tools = {t.name: t for t in create_report_tools(self.session)}

    def tearDown(self):
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_all_expected_tools_present(self):
        expected = {
            "resolve_company_identity",
            "find_source_documents",
            "ingest_documents",
            "web_search_approved",
            "create_cold_start_dossier",
            "research_industry_peers",
            "search_evidence",
            "get_evidence_capacity",
            "build_facts_ledger",
            "get_canonical_facts",
            "record_verified_fact",
            "get_statement_table",
            "verify_financials",
            "check_entities",
            "check_section",
            "check_regulatory_coverage",
            "make_charts",
            "assemble_report",
            "request_human_decision",
        }
        self.assertTrue(expected.issubset(set(self.tools.keys())))

    def test_unapproved_domain_blocked(self):
        res = json.loads(self.tools["ingest_documents"].invoke({
            "urls_or_paths": ["https://unapproved-domain.example.com/annual.pdf"]
        }))
        self.assertEqual(len(res["ingested"]), 0)
        self.assertEqual(len(res["rejected"]), 1)
        self.assertIn("not approved", res["rejected"][0]["reason"].lower())

        self.session.approve_domain("sec.gov")
        self.assertTrue(self.session.is_domain_approved("https://www.sec.gov/Archives/edgar/10k.htm"))
        self.assertFalse(self.session.is_domain_approved("https://evil-site.com/10k.htm"))

    def test_cold_start_search_and_ledger_guardrails(self):
        # Index dossier
        res = json.loads(self.tools["create_cold_start_dossier"].invoke({
            "fields": {"revenue": "$450 Million", "net_profit": "$50 Million"}
        }))
        self.assertEqual(res["status"], "indexed")

        # Search evidence returns hits
        ev = json.loads(self.tools["search_evidence"].invoke({"query": "revenue", "k": 3}))
        self.assertGreater(ev["hits"], 0)
        chunk_id = ev["results"][0]["chunk_id"]

        # Reject hallucinated number not in chunk
        rej = json.loads(self.tools["record_verified_fact"].invoke({
            "key": "revenue",
            "value": "$99999 Million",
            "period": "FY 2024",
            "source_doc_id": "dossier",
            "chunk_id": chunk_id,
        }))
        self.assertEqual(rej["status"], "REJECTED")

        # Accept literal number present in chunk
        ok = json.loads(self.tools["record_verified_fact"].invoke({
            "key": "revenue",
            "value": "$450 Million",
            "period": "FY 2024",
            "source_doc_id": "dossier",
            "chunk_id": chunk_id,
        }))
        self.assertEqual(ok["status"], "RECORDED")

        # get_missing_core_keys respects period
        missing_2024 = self.session.ledger.get_missing_core_keys("FY 2024")
        missing_2023 = self.session.ledger.get_missing_core_keys("FY 2023")
        self.assertNotIn("revenue", missing_2024)
        self.assertIn("revenue", missing_2023)

    def test_first_report_dossier_accepts_partial_company_information(self):
        res = json.loads(self.tools["create_cold_start_dossier"].invoke({
            "fields": {"products": "Industrial water filtration systems", "employees": "42"},
            "source_label": "Founder interview",
        }))
        self.assertEqual(res["status"], "indexed")
        self.assertTrue(res["information_received"])
        self.assertEqual(res["fields_recorded"], 2)
        self.assertNotIn("missing_required_fields", res)
        hits = json.loads(self.tools["search_evidence"].invoke({"query": "water filtration", "k": 3}))
        self.assertGreater(hits["hits"], 0)
        self.assertIn("Founder interview", hits["results"][0]["text"])

    def test_assemble_report_includes_evidence_supported_charts(self):
        from charts import ChartArtifact
        from ledger import FactRecord, FactStatus
        from PIL import Image

        for period, value in (("FY 2023", 100.0), ("FY 2024", 120.0)):
            self.session.ledger.record_fact(FactRecord(
                key="revenue", value=str(value), numeric_value=value, unit="million", currency="USD",
                period=period, scope="consolidated", source_doc_id="target_filing", page=1,
                chunk_id="target_chunk", confidence=1.0, status=FactStatus.REPORTED,
            ))
        chart_path = os.path.join(self.run_dir, "chart_financial_trend.png")
        Image.new("RGB", (16, 16), "white").save(chart_path)
        with patch("tools.output_tools.generate_all_report_charts", return_value=[ChartArtifact(
            chart_id="trend", title="Revenue Trend", caption="Reported revenue by year.",
            image_path=chart_path, source_line="Source: Target filing.",
        )]):
            res = json.loads(self.tools["assemble_report"].invoke({"section_files": []}))
        self.assertEqual(res["status"], "ASSEMBLED")
        report_md = next(v for k, v in res["files"].items() if k == "markdown")
        with open(report_md, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("![Revenue Trend](chart_financial_trend.png)", text)
        self.assertNotIn(chart_path, text)

    def test_assemble_report_enforces_page_ceiling(self):
        with patch("assembler.ReportAssembler.export_pdf", return_value=os.path.join(self.run_dir, "fake.pdf")), \
             patch("knowledge.parse_pdf", return_value=([], [], 501)):
            res = json.loads(self.tools["assemble_report"].invoke({"section_files": []}))
        self.assertEqual(res["status"], "PAGE_LIMIT_EXCEEDED")
        self.assertIn("maximum is 500", res["files"]["pdf_error"])

    def test_section_validation_and_duplication_guard(self):
        self.tools["record_verified_fact"].invoke({
            "key": "revenue",
            "value": "450 Million",
            "period": "FY 2024",
            "source_doc_id": "filing",
        })

        # Ledger mismatch caught
        bad_sec = json.loads(self.tools["check_section"].invoke({
            "section_name": "Overview",
            "markdown": " ".join(["word"] * 35) + "\nAcme Robotics Corp recorded revenue of 999 Million in FY 2024.",
        }))
        self.assertEqual(bad_sec["status"], "FAIL")
        self.assertTrue(any("Ledger mismatch" in i for i in bad_sec["issues"]))

        # Clean section passes and registers
        good_text = " ".join(f"token{i}" for i in range(45)) + "\nAcme Robotics Corp recorded revenue of 450 Million in FY 2024."
        good_sec = json.loads(self.tools["check_section"].invoke({
            "section_name": "Overview",
            "markdown": good_text,
        }))
        self.assertEqual(good_sec["status"], "PASS")

        # Duplicate section caught
        dup_sec = json.loads(self.tools["check_section"].invoke({
            "section_name": "Summary",
            "markdown": good_text,
        }))
        self.assertEqual(dup_sec["status"], "FAIL")
        self.assertTrue(any("repetition" in i.lower() for i in dup_sec["issues"]))

    def test_entity_guard_flags_excluded(self):
        self.session.excluded_names = ["Rival Systems Ltd"]
        res = json.loads(self.tools["check_entities"].invoke({
            "markdown": "Rival Systems Ltd gained market share in 2024."
        }))
        self.assertFalse(res["is_clean"])
        self.assertEqual(res["flagged_count"], 1)

    def test_research_industry_peers(self):
        # 1. Bank benchmark
        bank_res = json.loads(self.tools["research_industry_peers"].invoke({"industry_or_sector": "banking"}))
        self.assertIn("Banking", bank_res["matched_sector"])
        self.assertTrue(any("NPA" in ch or "Capital" in ch for ch in bank_res["recommended_chapters"]))
        self.assertIn("Net Interest Margin (NIM)", bank_res["key_metrics_and_kpis"])

        # 2. Manufacturing benchmark
        mfg_res = json.loads(self.tools["research_industry_peers"].invoke({"industry_or_sector": "manufacturing"}))
        self.assertIn("Manufacturing", mfg_res["matched_sector"])
        self.assertTrue(any("Plant" in ch or "Capacity" in ch for ch in mfg_res["recommended_chapters"]))

    def test_regulatory_coverage_is_not_compliance_certification(self):
        res = json.loads(self.tools["check_regulatory_coverage"].invoke({"sector": "banking"}))
        self.assertFalse(res["is_legal_compliance_certified"])
        self.assertTrue(res["industry_guidance_is_advisory"])
        self.assertIn("industry_topics", res)

    def test_financial_summary_does_not_invent_reconciliation_total(self):
        from statements import FinancialStatementBuilder

        table = FinancialStatementBuilder.build_balance_sheet({
            "total_liabilities": "100",
            "shareholders_equity": "50",
            "total_assets": "900",
        })
        self.assertIn("Total Assets", table)
        self.assertNotIn("Total Liabilities and Stockholders' Equity", table)

    def test_india_connector_rejects_nonfiling_search_results(self):
        from connectors.india import IndiaBseNseConnector

        class FakeSearch:
            def search(self, query, max_results=4):
                return [
                    {"href": "https://news.example.com/acme-annual-report-2024.html", "title": "Acme Annual Report 2024", "body": ""},
                    {"href": "https://www.bseindia.com/files/other.pdf", "title": "Other Company Annual Report 2024", "body": ""},
                    {"href": "https://www.bseindia.com/files/acme-annual-report-2023.pdf", "title": "Acme Robotics annual report 2023", "body": ""},
                ]

        results = IndiaBseNseConnector(tools=FakeSearch()).search("Acme Robotics Ltd", "FY 2024")
        self.assertEqual(results, [])

    def test_replacing_document_removes_stale_chunks(self):
        long_text = "\n\n".join("historic disclosure " + ("oldword " * 40) + str(i) for i in range(8))
        self.session.storage.add_document("replace_me", "Source", "internal://source", [(1, long_text)])
        self.assertGreater(self.session.storage.total_chunks(), 0)
        current_text = "Current disclosure only. " + ("verified current source text " * 30)
        self.session.storage.add_document("replace_me", "Source", "internal://source", [(1, current_text)])
        with self.session.storage._lock:
            import sqlite3
            with sqlite3.connect(self.session.storage.db_path) as conn:
                texts = [row[0] for row in conn.execute("SELECT text FROM chunks WHERE doc_id='replace_me'")]
        self.assertEqual(len(texts), 1)
        self.assertNotIn("oldword", " ".join(texts))


if __name__ == "__main__":
    unittest.main()
