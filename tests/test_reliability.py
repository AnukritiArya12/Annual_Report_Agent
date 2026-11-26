"""Focused tests for reliability across sources, entities, inaugural dossiers, HITL rejections, and peer isolation."""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from typing import Any, Dict, List

from tools import ReportSession, create_report_tools
from tools.discovery_tools import score_and_rank_candidates


class TestReliabilityEnhancements(unittest.TestCase):
    def setUp(self):
        self.run_dir = os.path.join(os.path.dirname(__file__), f"_tmp_rel_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.run_dir, exist_ok=True)
        self.session = ReportSession(
            run_dir=self.run_dir,
            company_name="Apex Global Technologies Inc",
            financial_year="FY 2024",
            country="United States",
            reporting_scope="consolidated",
        )
        self.tools = {t.name: t for t in create_report_tools(self.session)}

    def tearDown(self):
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_explainable_source_ranking_and_reasons(self):
        """Test multi-factor candidate scoring, ranking primary filing highest, and providing reasons."""
        candidates = [
            {
                "title": "Apex Global Technologies Inc - Annual Report Form 10-K (Consolidated)",
                "url": "https://www.sec.gov/edgar/data/12345/apex-10k-2024.pdf",
                "period": "FY 2024",
                "source": "sec_edgar",
            },
            {
                "title": "Apex Global Technologies Investor Presentation Q3 2024",
                "url": "https://ir.apexglobal.com/presentation-2024.pdf",
                "period": "Q3 2024",
                "source": "investor_relations",
            },
            {
                "title": "Apex Global Technologies Inc - Standalone Accounts 2020",
                "url": "https://sec.gov/edgar/apex-2020.pdf",
                "period": "FY 2020",
                "source": "sec_edgar",
            },
            {
                "title": "Apex Subsidiary Holdings Ltd - Annual Report 2024",
                "url": "https://companieshouse.gov.uk/apex-sub-2024.pdf",
                "period": "FY 2024",
                "source": "companieshouse",
            },
        ]

        selected, rejected = score_and_rank_candidates(
            candidates,
            company_name="Apex Global Technologies Inc",
            target_fy="FY 2024",
            target_scope="consolidated",
            approved_names=["Apex Global Technologies Inc"],
            excluded_names=["Apex Subsidiary Holdings Ltd"],
        )

        # 1. Top selected is official 10-K
        self.assertGreater(len(selected), 0)
        self.assertEqual(selected[0]["url"], "https://www.sec.gov/edgar/data/12345/apex-10k-2024.pdf")
        self.assertIn("selection_reason", selected[0])
        self.assertGreaterEqual(selected[0]["score"], 0.85)

        # 2. Excluded entity is rejected with explicit reason
        excl_rej = next((r for r in rejected if "Apex Subsidiary Holdings" in r["title"]), None)
        self.assertIsNotNone(excl_rej)
        self.assertIn("rejection_reason", excl_rej)
        self.assertIn("excluded", excl_rej["rejection_reason"].lower())

        # 3. Wrong year is rejected
        year_rej = next((r for r in rejected if "2020" in r["title"]), None)
        self.assertIsNotNone(year_rej)
        self.assertIn("rejection_reason", year_rej)
        self.assertIn("mismatched", year_rej["rejection_reason"].lower())

    def test_hitl_rejection_persistence_and_ingestion_block(self):
        """Test that rejected domains and sources are persisted and blocked from ingestion."""
        # 1. Approve legitimate domain, reject spam domain and specific file URL
        self.session.approve_domain("sec.gov")
        self.session.reject_domain("unverified-blog.com")
        self.session.reject_source("https://sec.gov/leaked-draft-2024.pdf")

        # 2. Verify check helper
        self.assertTrue(self.session.is_domain_approved("https://www.sec.gov/data/10k.htm"))
        self.assertFalse(self.session.is_domain_approved("https://unverified-blog.com/report.pdf"))
        self.assertFalse(self.session.is_domain_approved("https://sec.gov/leaked-draft-2024.pdf"))

        # 3. Verify ingestion tool strictly drops rejected URL
        res = json.loads(self.tools["ingest_documents"].invoke({
            "urls_or_paths": ["https://unverified-blog.com/report.pdf", "https://sec.gov/leaked-draft-2024.pdf"]
        }))
        self.assertEqual(len(res["ingested"]), 0)
        self.assertEqual(len(res["rejected"]), 2)
        self.assertTrue(any("rejected" in r["reason"].lower() or "not approved" in r["reason"].lower() for r in res["rejected"]))

        # 4. Verify reload persistence
        reloaded_session = ReportSession(
            run_dir=self.run_dir,
            company_name="Apex Global Technologies Inc",
            financial_year="FY 2024",
        )
        self.assertIn("unverified-blog.com", reloaded_session.rejected_domains)
        self.assertIn("https://sec.gov/leaked-draft-2024.pdf", reloaded_session.rejected_sources)
        self.assertFalse(reloaded_session.is_domain_approved("https://unverified-blog.com/news.html"))

    def test_first_year_dossier_validation_and_prior_year_guard(self):
        """Test inaugural dossier validation for missing fields and rejection of fabricated prior figures."""
        # Missing fields test
        incomplete_fields = {
            "business_model": "Autonomous cloud edge infrastructure solutions.",
            "revenue": "$5,000,000",
            # Missing registration_id, incorporation_date, initial_capital, expenses, net_profit, cash
        }
        res1 = json.loads(self.tools["create_cold_start_dossier"].invoke({"fields": incomplete_fields}))
        self.assertFalse(res1["dossier_complete"])
        missing_keys = [m["field"] for m in res1["missing_required_fields"]]
        self.assertIn("registration_id", missing_keys)
        self.assertIn("incorporation_date", missing_keys)
        self.assertIn("initial_capital", missing_keys)

        # Prior year fabrication guard test
        fields_with_prior = {
            "registration_id": "US-DEL-2024-991",
            "incorporation_date": "10-January-2024",
            "business_model": "Autonomous cloud edge infrastructure solutions.",
            "initial_capital": "$10,000,000",
            "revenue": "$5,000,000",
            "expenses": "$3,800,000",
            "net_profit": "$1,200,000",
            "cash_at_bank": "$7,500,000",
            "prior_year_revenue": "$3,000,000",  # Fabricated prior figure
        }
        res2 = json.loads(self.tools["create_cold_start_dossier"].invoke({"fields": fields_with_prior}))
        self.assertTrue(res2["dossier_complete"])
        self.assertEqual(len(res2["prior_year_violations_rejected"]), 1)
        self.assertIn("prior_year_revenue", res2["prior_year_violations_rejected"])

    def test_regulatory_coverage_matrix_tool(self):
        """Test statutory and industry benchmark coverage checking."""
        # 1. Draft a sample section in drafts/
        drafts_dir = os.path.join(self.run_dir, "drafts")
        os.makedirs(drafts_dir, exist_ok=True)
        mda_file = os.path.join(drafts_dir, "03_mda.md")
        with open(mda_file, "w", encoding="utf-8") as f:
            f.write("# Management Discussion and Analysis (MD&A)\n\n" + " ".join(["operational_word"] * 180))

        cov = json.loads(self.tools["check_regulatory_coverage"].invoke({"sector": "technology"}))
        self.assertIn("coverage_score", cov)
        self.assertGreater(cov["met_requirements_count"], 0)
        self.assertGreater(cov["missing_count"], 0)
        met_reqs = [m["requirement"] for m in cov["met_sample"]]
        self.assertTrue(any("Management Discussion" in r or "MD&A" in r for r in met_reqs))

    def test_peer_guidance_data_isolation(self):
        """Test peer structure guidance is marked structural and forbidden from target facts ledger."""
        # 1. Peer benchmark tool returns isolation notice
        peer_res = json.loads(self.tools["research_industry_peers"].invoke({"industry_or_sector": "banking"}))
        self.assertIn("data_isolation_notice", peer_res)
        self.assertIn("PEER BENCHMARK ONLY", peer_res["data_isolation_notice"])

        # 2. Attempt to record peer benchmark into canonical ledger is rejected
        rec_res = json.loads(self.tools["record_verified_fact"].invoke({
            "key": "revenue",
            "value": "$10,000,000",
            "period": "FY 2024",
            "source_doc_id": "peer_benchmark_sample",
        }))
        self.assertEqual(rec_res["status"], "REJECTED")
        self.assertIn("peer benchmark data", rec_res["reason"].lower())

    def test_run_manifest_traceability_and_resume_check(self):
        """Test run manifest records events and validates parameters on resume."""
        self.session.update_manifest("test_event", {"param": "val123"})
        self.assertTrue(os.path.exists(self.session.manifest_path))

        with open(self.session.manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        self.assertEqual(manifest_data["company_name"], "Apex Global Technologies Inc")
        self.assertEqual(manifest_data["financial_year"], "FY 2024")

        # Matching resume succeeds
        valid, _ = self.session.validate_resume_manifest()
        self.assertTrue(valid)

        # Mismatched company resume fails
        other_session = ReportSession(
            run_dir=self.run_dir,
            company_name="Different Corporate Rival",
            financial_year="FY 2024",
        )
        valid2, msg2 = other_session.validate_resume_manifest()
        self.assertFalse(valid2)
        self.assertIn("mismatch", msg2.lower())


if __name__ == "__main__":
    unittest.main()
