"""Unit tests for Phase 1 tools/ package and deterministic guardrails."""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid

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


if __name__ == "__main__":
    unittest.main()
