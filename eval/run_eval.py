"""Evaluation Harness for the Generic Annual Report Agent.

Usage:
    # Run all eval cases defined in eval_config.json
    python eval/run_eval.py

    # Run a specific subset of cases by id
    python eval/run_eval.py --cases us_tech india_it

    # Dry-run (print what would be executed, no actual runs)
    python eval/run_eval.py --dry-run

    # Compare two eval result files and show regressions/improvements
    python eval/run_eval.py --compare-with eval/results_baseline.json

Outputs:
    eval/results.json          - Full structured metrics for each case
    eval/results_summary.md    - Markdown table for quick review
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow importing project modules from parent directory
_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_EVAL_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

PYTHON = sys.executable
MAIN_PY = os.path.join(_ROOT_DIR, "main.py")
RESULTS_JSON = os.path.join(_EVAL_DIR, "results.json")
RESULTS_MD = os.path.join(_EVAL_DIR, "results_summary.md")


# ---------------------------------------------------------------------------
# Metric Computation Helpers
# ---------------------------------------------------------------------------

def _shingle(text: str, n: int = 5) -> set:
    """Return set of n-word shingles for repetition rate calculation."""
    words = text.lower().split()
    if len(words) < n:
        return set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def compute_repetition_rate(section_texts: List[str]) -> float:
    """Compute cross-section shingling overlap (repetition rate).

    Returns the fraction of shingles in sections 2..N that also appear in
    sections 1..N-1.  A value of 0.0 means no cross-section duplication.
    """
    if len(section_texts) < 2:
        return 0.0
    seen: set = set()
    total = 0
    repeated = 0
    for text in section_texts:
        sh = _shingle(text)
        if not sh:
            continue
        repeated += len(sh & seen)
        total += len(sh)
        seen |= sh
    return round(repeated / total, 4) if total else 0.0


def compute_evidence_grounding(md_text: str, run_dir: str) -> float:
    """Estimate % of numeric tokens grounded in source evidence or ledger.

    Strategy:
      1. Collect all numbers in the markdown report.
      2. Cross-check against the ledger SQLite facts (if present).
      3. Fraction grounded = matched / total_numbers  (capped at 1.0).
    """
    numbers_in_report = set(re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", md_text))
    if not numbers_in_report:
        return 1.0  # No numbers → trivially grounded

    db_path = os.path.join(run_dir, "evidence.db") if os.path.exists(os.path.join(run_dir, "evidence.db")) else os.path.join(run_dir, "kb.db")
    ledger_numbers: set = set()
    if os.path.exists(db_path):
        try:
            import sqlite3
            with sqlite3.connect(db_path, timeout=10) as conn:
                cur = conn.cursor()
                # From ledger facts
                try:
                    cur.execute("SELECT value, numeric_value FROM facts")
                    for val, num in cur.fetchall():
                        if val:
                            ledger_numbers.update(re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", str(val)))
                        if num is not None:
                            ledger_numbers.add(str(int(num)) if num == int(num) else str(num))
                except Exception:
                    pass
                # From raw chunk text
                try:
                    cur.execute("SELECT text FROM chunks LIMIT 500")
                    for (chunk_text,) in cur.fetchall():
                        if chunk_text:
                            ledger_numbers.update(re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", chunk_text))
                except Exception:
                    pass
        except Exception:
            pass

    if not ledger_numbers:
        return 0.0
    matched = len(numbers_in_report & ledger_numbers)
    return round(min(1.0, matched / len(numbers_in_report)), 4)


def count_not_disclosed(md_text: str) -> int:
    """Count sections that contain the standard 'not disclosed' / sparse marker."""
    patterns = [
        r"not provided in the dossier",
        r"not disclosed",
        r"information not available",
        r"no source material",
        r"<!--\s*SPARSE\s*-->",
        r"\*\(This section could not be generated",
    ]
    total = 0
    for p in patterns:
        total += len(re.findall(p, md_text, re.IGNORECASE))
    return total


def parse_validation_distribution(run_dir: str) -> Dict[str, int]:
    """Extract validation status distribution from run_report.json and draft files."""
    dist = {"PASS": 0, "WARNING": 0, "REVIEW_REQUIRED": 0, "FAIL": 0, "NOT_VERIFIABLE": 0}
    # Approximate from drafts folder (validation results are logged to stdout,
    # but the status is embedded in draft filenames or run_report).
    drafts_dir = os.path.join(run_dir, "drafts")
    if os.path.isdir(drafts_dir):
        for fname in os.listdir(drafts_dir):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(drafts_dir, fname), encoding="utf-8") as f:
                        d = json.load(f)
                    status = d.get("validation_status", "")
                    if status in dist:
                        dist[status] += 1
                    else:
                        dist["PASS"] += 1
                except Exception:
                    pass
    return dist


def count_foreign_entity_flags(run_dir: str) -> int:
    """Count foreign-entity flags logged in the approvals or run_report."""
    # Foreign entity events are written to approvals.json
    approvals_path = os.path.join(run_dir, "approvals.json")
    if not os.path.exists(approvals_path):
        return 0
    try:
        with open(approvals_path, encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for entry in data.values() if isinstance(data, dict) else data:
            if isinstance(entry, dict) and "foreign_entity" in entry.get("checkpoint_id", ""):
                count += 1
        return count
    except Exception:
        return 0


def count_ledger_violations(run_dir: str) -> int:
    """Count ledger consistency violations by scanning ledger facts for CONFLICTING status."""
    db_path = os.path.join(run_dir, "evidence.db") if os.path.exists(os.path.join(run_dir, "evidence.db")) else os.path.join(run_dir, "kb.db")
    if not os.path.exists(db_path):
        return 0
    try:
        import sqlite3
        with sqlite3.connect(db_path, timeout=10) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM facts WHERE status = 'CONFLICTING'")
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def extract_metrics(case: Dict[str, Any], run_dir: str, elapsed: float, exit_code: int) -> Dict[str, Any]:
    """Compute all evaluation metrics for a completed run."""
    metrics: Dict[str, Any] = {
        "case_id": case["id"],
        "company": case["company"],
        "fy": case["fy"],
        "country": case["country"],
        "industry": case["industry"],
        "tags": case.get("tags", []),
        "elapsed_seconds": round(elapsed, 1),
        "exit_code": exit_code,
        "run_dir": run_dir,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    # Locate the markdown report
    md_path = None
    if os.path.isdir(run_dir):
        for fname in os.listdir(run_dir):
            if fname.endswith(".md") and not fname.startswith("outline"):
                md_path = os.path.join(run_dir, fname)
                break

    if md_path and os.path.exists(md_path):
        with open(md_path, encoding="utf-8", errors="replace") as f:
            md_text = f.read()

        # -- Word & page count
        words = len(md_text.split())
        from config import settings
        approx_pages = round(words / settings.WORDS_PER_PAGE)
        metrics["words_written"] = words
        metrics["approx_pages"] = approx_pages

        # -- Section count (## headings)
        sections = re.findall(r"^#{1,3} .+", md_text, re.MULTILINE)
        metrics["section_count"] = len(sections)

        # -- Split by section for repetition analysis
        section_bodies = re.split(r"^#{1,3} .+", md_text, flags=re.MULTILINE)
        section_bodies = [s.strip() for s in section_bodies if s.strip()]
        metrics["repetition_rate"] = compute_repetition_rate(section_bodies)

        # -- Evidence grounding
        metrics["evidence_grounding_pct"] = compute_evidence_grounding(md_text, run_dir)

        # -- Not-disclosed sections
        metrics["not_disclosed_count"] = count_not_disclosed(md_text)

        # -- Target vs actual pages
        target_pages = case.get("pages", 0)
        metrics["target_pages"] = target_pages
        metrics["pages_delta"] = approx_pages - target_pages

    else:
        metrics.update({
            "words_written": 0,
            "approx_pages": 0,
            "section_count": 0,
            "repetition_rate": 0.0,
            "evidence_grounding_pct": 0.0,
            "not_disclosed_count": 0,
            "target_pages": case.get("pages", 0),
            "pages_delta": 0,
        })

    # -- Ledger & entity
    metrics["ledger_violations"] = count_ledger_violations(run_dir)
    metrics["foreign_entity_flags"] = count_foreign_entity_flags(run_dir)

    # -- Validation distribution
    metrics["validation_distribution"] = parse_validation_distribution(run_dir)

    # -- Run report supplement
    rr_path = os.path.join(run_dir, "run_report.json")
    if os.path.exists(rr_path):
        try:
            with open(rr_path, encoding="utf-8") as f:
                rr = json.load(f)
            metrics["planned_pages"] = rr.get("planned_pages", rr.get("target_pages"))
            metrics["evidence_supported_pages"] = rr.get("evidence_supported_pages")
            metrics["evidence_capacity_note"] = rr.get("evidence_capacity_note", "")
            metrics["charts_generated"] = rr.get("charts_generated", 0)
            metrics["failed_sections"] = rr.get("failed_sections", [])
        except Exception:
            pass

    return metrics


# ---------------------------------------------------------------------------
# Threshold Checks
# ---------------------------------------------------------------------------

def _check_thresholds(m: Dict[str, Any], cfg_metrics: Dict[str, Any]) -> Dict[str, str]:
    """Return dict of field -> 'PASS'|'WARN'|'FAIL' for threshold checks."""
    results: Dict[str, str] = {}

    def _check(key: str, value: Any, threshold: Any, mode: str = "min") -> str:
        if value is None:
            return "SKIP"
        if mode == "min":
            return "PASS" if value >= threshold else "FAIL"
        return "PASS" if value <= threshold else "FAIL"

    results["section_count"] = _check("section_count", m.get("section_count"), cfg_metrics.get("min_sections", 10))
    results["words_written"] = _check("words_written", m.get("words_written"), cfg_metrics.get("min_words", 5000))
    results["repetition_rate"] = _check("repetition_rate", m.get("repetition_rate"), cfg_metrics.get("max_repetition_rate", 0.15), mode="max")
    results["evidence_grounding_pct"] = _check("evidence_grounding_pct", m.get("evidence_grounding_pct"), cfg_metrics.get("min_evidence_grounding_pct", 0.5))
    results["ledger_violations"] = _check("ledger_violations", m.get("ledger_violations"), cfg_metrics.get("max_ledger_violations", 5), mode="max")
    results["foreign_entity_flags"] = _check("foreign_entity_flags", m.get("foreign_entity_flags"), cfg_metrics.get("max_foreign_entity_flags", 10), mode="max")
    results["exit_code"] = "PASS" if m.get("exit_code", 1) == 0 else "FAIL"
    return results


# ---------------------------------------------------------------------------
# Run One Case
# ---------------------------------------------------------------------------

def run_case(case: Dict[str, Any], dry_run: bool = False) -> Tuple[str, int, float]:
    """Execute main.py for one eval case. Returns (run_dir, exit_code, elapsed_seconds)."""
    cmd = [PYTHON, MAIN_PY, case["company"],
           "--fy", case["fy"],
           "--country", case["country"],
           "--auto-approve"]

    if case.get("industry"):
        cmd += ["--industry", case["industry"]]
    if case.get("pages"):
        cmd += ["--pages", str(case["pages"])]
    if case.get("scope"):
        cmd += ["--scope", case["scope"]]
    if case.get("first_year"):
        cmd.append("--first-year")

    print(f"\n{'=' * 70}")
    print(f"[Eval] CASE: {case['id']} | {case['company']} | {case['fy']} | {case['country']}")
    print(f"[Eval] CMD:  {' '.join(cmd)}")
    if dry_run:
        print("[Eval] --dry-run: skipping execution.")
        return "", 0, 0.0

    t0 = time.time()
    try:
        result = subprocess.run(cmd, cwd=_ROOT_DIR, timeout=3600,
                                capture_output=False)
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        print("[Eval] TIMEOUT after 3600s")
        exit_code = 124
    except Exception as e:
        print(f"[Eval] ERROR: {e}")
        exit_code = 1
    elapsed = time.time() - t0

    # Determine where main.py wrote the run folder
    from utils import slug
    fy_slug = slug(case["fy"], 20)
    co_slug = slug(case["company"], 40)
    run_dir = os.path.join(_ROOT_DIR, "runs", f"{co_slug}_{fy_slug}")
    # If that exact path doesn't exist, search for the nearest match
    runs_root = os.path.join(_ROOT_DIR, "runs")
    if not os.path.isdir(run_dir) and os.path.isdir(runs_root):
        candidates = sorted(
            [d for d in os.listdir(runs_root) if co_slug[:8] in d],
            key=lambda d: os.path.getmtime(os.path.join(runs_root, d)),
            reverse=True
        )
        if candidates:
            run_dir = os.path.join(runs_root, candidates[0])

    print(f"[Eval] Finished in {elapsed:.1f}s | exit={exit_code} | dir={run_dir}")
    return run_dir, exit_code, elapsed


# ---------------------------------------------------------------------------
# Compare Two Result Files
# ---------------------------------------------------------------------------

def compare_results(baseline_path: str, current_path: str) -> str:
    """Diff two results.json files. Returns a markdown comparison table."""
    with open(baseline_path, encoding="utf-8") as f:
        baseline_cases = {r["case_id"]: r for r in json.load(f).get("cases", [])}
    with open(current_path, encoding="utf-8") as f:
        current_cases = {r["case_id"]: r for r in json.load(f).get("cases", [])}

    NUMERIC_METRICS = [
        "words_written", "approx_pages", "section_count",
        "repetition_rate", "evidence_grounding_pct",
        "ledger_violations", "foreign_entity_flags", "not_disclosed_count",
    ]

    rows = []
    rows.append("## Comparison: Baseline vs Current\n")
    rows.append("| Case | Metric | Baseline | Current | Delta | Status |")
    rows.append("| :--- | :--- | ---: | ---: | ---: | :---: |")

    for cid in sorted(set(baseline_cases) | set(current_cases)):
        b = baseline_cases.get(cid, {})
        c = current_cases.get(cid, {})
        for m in NUMERIC_METRICS:
            bv = b.get(m)
            cv = c.get(m)
            if bv is None and cv is None:
                continue
            bv = bv or 0
            cv = cv or 0
            delta = cv - bv
            # Determine improvement direction per metric
            higher_is_better = m in ("words_written", "approx_pages", "section_count", "evidence_grounding_pct")
            if delta == 0:
                status = "→"
            elif (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better):
                status = "✅"
            else:
                status = "⚠️"
            rows.append(f"| {cid} | {m} | {bv} | {cv} | {delta:+.4g} | {status} |")

    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Markdown Summary Table
# ---------------------------------------------------------------------------

def _status_icon(status: str) -> str:
    return {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "—"}.get(status, status)


def build_markdown_table(all_metrics: List[Dict[str, Any]],
                         cfg_metrics: Dict[str, Any]) -> str:
    """Render a compact markdown table for results_summary.md."""
    lines = [
        "# Annual Report Agent — Eval Results",
        f"_Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_\n",
        "## Per-Case Summary\n",
        "| Case ID | Company | FY | Sections | Words | Pages(T→A) | RepRate | Grounding | LedgerV | ForeignE | NotDisc | Exit |",
        "| :--- | :--- | :--- | ---: | ---: | :---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]

    for m in all_metrics:
        t = m.get("target_pages", "?")
        a = m.get("approx_pages", "?")
        ww = m.get("words_written")
        ww_str = f"{ww:,}" if isinstance(ww, int) else str(ww or "?")
        rr = m.get("repetition_rate", 0) or 0
        gp = m.get("evidence_grounding_pct", 0) or 0
        lines.append(
            f"| {m['case_id']} | {m['company'][:28]} | {m['fy']} "
            f"| {m.get('section_count', '?')} "
            f"| {ww_str} "
            f"| {t}→{a} "
            f"| {rr:.3f} "
            f"| {gp:.2%} "
            f"| {m.get('ledger_violations', '?')} "
            f"| {m.get('foreign_entity_flags', '?')} "
            f"| {m.get('not_disclosed_count', '?')} "
            f"| {m.get('exit_code', '?')} |"
        )

    lines.append("\n## Threshold Checks\n")
    lines.append("| Case | Sections | Words | RepRate | Grounding | LedgerV | ForeignE | Exit |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for m in all_metrics:
        t = _check_thresholds(m, cfg_metrics)
        lines.append(
            f"| {m['case_id']} "
            f"| {_status_icon(t.get('section_count',''))} "
            f"| {_status_icon(t.get('words_written',''))} "
            f"| {_status_icon(t.get('repetition_rate',''))} "
            f"| {_status_icon(t.get('evidence_grounding_pct',''))} "
            f"| {_status_icon(t.get('ledger_violations',''))} "
            f"| {_status_icon(t.get('foreign_entity_flags',''))} "
            f"| {_status_icon(t.get('exit_code',''))} |"
        )

    # Aggregate stats
    if all_metrics:
        lines.append("\n## Aggregate Statistics\n")
        def _avg(key):
            vals = [m.get(key) for m in all_metrics if m.get(key) is not None]
            return round(sum(vals) / len(vals), 4) if vals else "N/A"

        avg_words = _avg('words_written')
        avg_words_str = f"{avg_words:,}" if isinstance(avg_words, (int, float)) else str(avg_words)
        lines.append(f"- **Avg words/report**: {avg_words_str}")
        lines.append(f"- **Avg sections/report**: {_avg('section_count')}")
        lines.append(f"- **Avg repetition rate**: {_avg('repetition_rate')}")
        avg_grounding = _avg('evidence_grounding_pct')
        grounding_str = f"{avg_grounding:.2%}" if isinstance(avg_grounding, float) else str(avg_grounding)
        lines.append(f"- **Avg evidence grounding**: {grounding_str}")
        lines.append(f"- **Avg ledger violations**: {_avg('ledger_violations')}")
        lines.append(f"- **Total cases run**: {len(all_metrics)}")
        passed = sum(1 for m in all_metrics if m.get("exit_code") == 0)
        lines.append(f"- **Cases completed (exit 0)**: {passed}/{len(all_metrics)}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the evaluation harness."""
    parser = argparse.ArgumentParser(
        description="Evaluation harness for the Generic Annual Report Agent.")
    parser.add_argument("--cases", nargs="*", metavar="CASE_ID",
                        help="Restrict to specific case IDs (default: all).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing them.")
    parser.add_argument("--compare-with", metavar="BASELINE_JSON",
                        help="Diff current results.json against a baseline file.")
    parser.add_argument("--config", default=os.path.join(_EVAL_DIR, "eval_config.json"),
                        help="Path to eval_config.json (default: eval/eval_config.json).")
    parser.add_argument("--output", default=RESULTS_JSON,
                        help="Path for output results.json.")
    args = parser.parse_args()

    # -- Compare mode
    if args.compare_with:
        current = args.output if os.path.exists(args.output) else RESULTS_JSON
        if not os.path.exists(current):
            print(f"[Eval] No current results at {current}. Run eval first.")
            sys.exit(1)
        table = compare_results(args.compare_with, current)
        print(table)
        compare_md = os.path.join(_EVAL_DIR, "results_comparison.md")
        with open(compare_md, "w", encoding="utf-8") as f:
            f.write(table)
        print(f"\n[Eval] Comparison saved to {compare_md}")
        return

    # -- Load config
    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    all_cases: List[Dict[str, Any]] = config["eval_cases"]
    cfg_metrics: Dict[str, Any] = config.get("metrics", {})

    # -- Filter by --cases flag
    if args.cases:
        filtered = [c for c in all_cases if c["id"] in args.cases]
        if not filtered:
            print(f"[Eval] No cases matched: {args.cases}. Available: {[c['id'] for c in all_cases]}")
            sys.exit(1)
        all_cases = filtered

    print(f"[Eval] Starting evaluation: {len(all_cases)} case(s) | dry_run={args.dry_run}")
    t_start = time.time()

    all_metrics: List[Dict[str, Any]] = []
    for case in all_cases:
        run_dir, exit_code, elapsed = run_case(case, dry_run=args.dry_run)
        if args.dry_run:
            all_metrics.append({"case_id": case["id"], "company": case["company"],
                                 "fy": case["fy"], "country": case["country"],
                                 "industry": case["industry"], "tags": case.get("tags", []),
                                 "exit_code": 0, "dry_run": True})
            continue
        m = extract_metrics(case, run_dir, elapsed, exit_code)
        all_metrics.append(m)
        # Print per-case digest
        print(f"[Eval]   words={m.get('words_written', '?'):,}  "
              f"sections={m.get('section_count', '?')}  "
              f"rep={m.get('repetition_rate', 0):.3f}  "
              f"grounding={m.get('evidence_grounding_pct', 0):.1%}  "
              f"ledger_v={m.get('ledger_violations', '?')}")

    # -- Write results.json
    output = {
        "eval_timestamp": datetime.utcnow().isoformat() + "Z",
        "total_elapsed_seconds": round(time.time() - t_start, 1),
        "cases_run": len(all_metrics),
        "config_path": args.config,
        "cases": all_metrics,
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\n[Eval] Results written to {args.output}")

    # -- Write markdown summary
    md_table = build_markdown_table(all_metrics, cfg_metrics)
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write(md_table)
    print(f"[Eval] Summary table written to {RESULTS_MD}")

    # -- Print inline summary
    print("\n" + "=" * 70)
    print(md_table)
    print("=" * 70)


if __name__ == "__main__":
    main()
