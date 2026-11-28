"""Deterministic Financial Chart Generation Module.

Generates high-resolution visualization charts (revenue/profit trends, segment breakdowns)
from verified FactsLedger metrics and authoritative table chunks using matplotlib.
Charts are only generated when at least 2 authentic data points exist (zero fabricated points).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

if "MPLCONFIGDIR" not in os.environ:
    _mpl_cache = os.path.join(tempfile.gettempdir(), "matplotlib_cache")
    os.makedirs(_mpl_cache, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = _mpl_cache

from config import settings
from financial import clean_number
from ledger import FactsLedger


@dataclass
class ChartArtifact:
    """A generated visualization chart with publication metadata."""
    chart_id: str
    title: str
    caption: str
    image_path: str
    source_line: str


class ChartGenerator:
    """Generates publication-quality charts from grounded financial records."""

    @staticmethod
    def generate_financial_trend_chart(ledger: FactsLedger, output_dir: str) -> Optional[ChartArtifact]:
        """Plot multi-period Revenue and Net Profit trend if at least 2 periods exist."""
        if not getattr(settings, "CHARTS_ENABLED", True):
            return None

        facts = ledger.get_all_facts()
        if not facts:
            return None

        # Gather multi-period revenue and profit points
        rev_points: Dict[str, float] = {}
        prof_points: Dict[str, float] = {}
        currency = ""
        unit = ""

        for f in facts:
            if (f.numeric_value is not None and f.period
                    and getattr(f.status, "value", str(f.status)) in {"REPORTED", "DERIVED"}):
                if f.key in ("revenue", "net_profit", "operating_profit"):
                    currency = currency or f.currency
                    unit = unit or f.unit
                if f.key == "revenue":
                    rev_points[f.period] = f.numeric_value
                elif f.key in ("net_profit", "operating_profit"):
                    prof_points[f.period] = f.numeric_value

        # Only generate chart if at least 2 distinct data points exist for a metric
        active_points = rev_points if len(rev_points) >= 2 else (prof_points if len(prof_points) >= 2 else None)
        if not active_points or len(active_points) < 2:
            return None

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            os.makedirs(output_dir, exist_ok=True)
            img_path = os.path.join(output_dir, "chart_financial_trend.png")

            # Sort periods chronologically
            periods = sorted(list(active_points.keys()))
            x = list(range(len(periods)))

            fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
            
            # Styling
            fig.patch.set_facecolor("#FFFFFF")
            ax.set_facecolor("#F8FAFC")
            ax.grid(True, linestyle="--", alpha=0.5, color="#CBD5E1")

            metric_name = "Revenue" if active_points is rev_points else "Operating / Net Profit"
            y_vals = [active_points[p] for p in periods]
            
            # Plot line and points
            ax.plot(x, y_vals, marker="o", markersize=8, color="#1E3A8A", linewidth=2.5, label=metric_name)
            for i, val in enumerate(y_vals):
                ax.annotate(f"{val:,.1f}", (x[i], y_vals[i]), textcoords="offset points",
                            xytext=(0, 10), ha="center", fontsize=9, fontweight="bold", color="#1E293B")

            if rev_points and prof_points and len(prof_points) >= 2:
                # Plot second series if available
                common_periods = [p for p in periods if p in prof_points]
                if len(common_periods) >= 2:
                    y_prof = [prof_points[p] for p in common_periods]
                    x_prof = [periods.index(p) for p in common_periods]
                    ax.plot(x_prof, y_prof, marker="s", markersize=7, color="#059669", linewidth=2.0, label="Profit")
                    for xi, val in zip(x_prof, y_prof):
                        ax.annotate(f"{val:,.1f}", (xi, val), textcoords="offset points",
                                    xytext=(0, -15), ha="center", fontsize=8, color="#047857")

            ax.set_xticks(x)
            ax.set_xticklabels(periods, fontsize=10, fontweight="medium")
            y_label_str = f"Amount ({currency} {unit})".strip() if (currency or unit) else "Amount"
            ax.set_ylabel(y_label_str, fontsize=10, fontweight="medium", color="#334155")
            ax.set_title(f"Multi-Period Financial Performance Trend", fontsize=12, fontweight="bold", color="#0F172A", pad=12)
            ax.legend(loc="upper left", framealpha=0.9)
            
            # Remove top and right spines
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["left"].set_color("#94A3B8")
            ax.spines["bottom"].set_color("#94A3B8")

            plt.tight_layout()
            plt.savefig(img_path, format="png", bbox_inches="tight")
            plt.close(fig)

            return ChartArtifact(
                chart_id="chart_fin_trend",
                title="Financial Performance & Operating Trend",
                caption=f"Multi-year trajectory of reported {metric_name.lower()} across documented reporting cycles.",
                image_path=img_path,
                source_line="Source: Target-company verified facts ledger; periods and units as reported."
            )
        except Exception as e:
            print(f"[Chart Error] {e}")
            return None

    @staticmethod
    def generate_segment_mix_chart(storage: Any, output_dir: str) -> Optional[ChartArtifact]:
        """Extract business/geographic segment mix from table chunks and plot breakdown."""
        if not getattr(settings, "CHARTS_ENABLED", True) or not storage:
            return None

        segments: List[Tuple[str, float]] = []
        metric_label = ""
        try:
            with sqlite3.connect(storage.db_path, timeout=30) as conn:
                cur = conn.cursor()
                cur.execute("SELECT text FROM chunks WHERE is_table = 1")
                rows = cur.fetchall()

            for (text,) in rows:
                lines = [l.strip() for l in text.splitlines() if l.strip().startswith("|")]
                if len(lines) < 3:
                    continue
                header = lines[0].lower()
                if any(k in header for k in ("segment", "division", "business line", "geography")):
                    headers = [c.strip().lower() for c in lines[0].strip("|").split("|")]
                    metric_idx = next((i for i, h in enumerate(headers[1:], start=1)
                                       if any(k in h for k in ("revenue", "sales", "assets", "profit", "income", "amount", "value"))), None)
                    if metric_idx is None:
                        continue
                    metric_label = headers[metric_idx]
                    # Parse data rows
                    for l in lines[2:]:
                        parts = [c.strip() for c in l.strip("|").split("|")]
                        if len(parts) > metric_idx:
                            name = parts[0]
                            if any(k in name.lower() for k in ("total", "sum", "elimination", "unallocated", "notes")):
                                continue
                            val = clean_number(parts[metric_idx])
                            if val is not None and val > 0:
                                segments.append((name, val))
                    if len(segments) >= 2:
                        break
        except Exception as e:
            print(f"[Chart Error] {e}")
            return None

        if len(segments) < 2:
            return None

        # Take top 5 segments
        segments = sorted(segments, key=lambda s: s[1], reverse=True)[:5]

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            os.makedirs(output_dir, exist_ok=True)
            img_path = os.path.join(output_dir, "chart_segment_mix.png")

            names = [s[0][:25] for s in segments]
            values = [s[1] for s in segments]

            fig, ax = plt.subplots(figsize=(7, 3.8), dpi=200)
            fig.patch.set_facecolor("#FFFFFF")
            ax.set_facecolor("#F8FAFC")
            ax.grid(axis="x", linestyle="--", alpha=0.5, color="#CBD5E1")

            y_pos = list(range(len(names)))
            colors = ["#2563EB", "#3B82F6", "#60A5FA", "#93C5FD", "#BFDBFE"][:len(names)]
            bars = ax.barh(y_pos, values, color=colors, height=0.55, edgecolor="#1E40AF", linewidth=0.8)

            ax.set_yticks(y_pos)
            ax.set_yticklabels(names, fontsize=9, fontweight="medium")
            ax.invert_yaxis()
            metric_title = metric_label.title() or "Reported Amount"
            ax.set_xlabel(metric_title, fontsize=9, fontweight="medium", color="#334155")
            ax.set_title("Operational & Segment Contribution Breakdown", fontsize=11, fontweight="bold", color="#0F172A", pad=10)

            for bar in bars:
                w = bar.get_width()
                ax.text(w + (max(values) * 0.02), bar.get_y() + bar.get_height() / 2, f"{w:,.1f}",
                        ha="left", va="center", fontsize=8, fontweight="bold", color="#1E293B")

            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["left"].set_color("#94A3B8")
            ax.spines["bottom"].set_color("#94A3B8")

            plt.tight_layout()
            plt.savefig(img_path, format="png", bbox_inches="tight")
            plt.close(fig)

            return ChartArtifact(
                chart_id="chart_seg_mix",
                title=f"Segment Breakdown by {metric_title}",
                caption=f"Reported {metric_title.lower()} by disclosed business or geographic segment.",
                image_path=img_path,
                source_line="Source: Target-company segment table in indexed source documents."
            )
        except Exception as e:
            print(f"[Chart Error] {e}")
            return None


def generate_all_report_charts(ledger: FactsLedger, storage: Any, output_dir: str) -> List[ChartArtifact]:
    """Generate all grounded charts where at least 2 data points exist."""
    charts: List[ChartArtifact] = []
    c1 = ChartGenerator.generate_financial_trend_chart(ledger, output_dir)
    if c1:
        charts.append(c1)
    c2 = ChartGenerator.generate_segment_mix_chart(storage, output_dir)
    if c2:
        charts.append(c2)
    return charts