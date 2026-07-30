"""
Assemble findings into a decision-ready report.

The organizing idea: an ops leader doesn't act on 246 findings, they act on a
handful of *remediation actions*. So the report groups findings by check into
prioritized items, each carrying what it costs, what it puts at risk, and —
the part that makes it actionable — **how many score points fixing it would
recover**.

That last number turns the report from a complaint into a plan: "merge these 33
duplicate contacts, recover 4.2 points and ~6 hours" is a sentence someone can
take to a standup.

Priority ranks by remediation value rather than raw count, so a category with
few but expensive findings outranks a large pile of cosmetic ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from engine.checks import build_checks, run_all_checks
from engine.models import CRMData, Finding, OBJECT_TYPES, Severity
from engine.scoring import (
    CostSummary,
    ImpactAssumptions,
    ScoreBreakdown,
    compute_score,
    open_pipeline_value,
    price_findings,
    summarize_cost,
)


@dataclass
class RemediationItem:
    """One category of work, with what fixing it buys you."""

    check_id: str
    name: str
    object_type: str
    count: int
    worst_severity: Severity
    remediation_hours: float
    direct_cost_usd: float
    at_risk_usd: float
    score_gain: float
    example_message: str

    @property
    def severity_label(self) -> str:
        return self.worst_severity.label

    def to_row(self) -> dict:
        return {
            "priority_check": self.check_id,
            "issue": self.name,
            "object": self.object_type,
            "records": self.count,
            "severity": self.severity_label,
            "hours_to_fix": self.remediation_hours,
            "cost_usd": round(self.direct_cost_usd, 2),
            "at_risk_usd": round(self.at_risk_usd, 2),
            "score_gain": self.score_gain,
            "example": self.example_message,
        }


@dataclass
class Report:
    """Everything the app or a CLI needs to render, already computed."""

    score: ScoreBreakdown
    cost: CostSummary
    findings: list[Finding]
    items: list[RemediationItem]
    record_counts: dict[str, int]
    as_of: pd.Timestamp
    assumptions: ImpactAssumptions
    checks_run: int = 0
    findings_by_severity: dict[str, int] = field(default_factory=dict)

    @property
    def headline(self) -> str:
        """The one sentence the report leads with."""
        return (
            f"CRM Health Score {self.score.overall:.0f}/100 (grade {self.score.grade}) — "
            f"{len(self.findings):,} issues across {self.score.affected_records:,} of "
            f"{self.score.total_records:,} records, representing ~{self.cost.remediation_hours:,.0f} hours "
            f"(≈${self.cost.direct_cost_usd:,.0f}) of avoidable work and "
            f"${self.cost.at_risk_pipeline_usd:,.0f} of pipeline at risk."
        )

    def findings_dataframe(self) -> pd.DataFrame:
        if not self.findings:
            return pd.DataFrame(columns=[
                "check_id", "object_type", "record_id", "severity",
                "severity_rank", "message", "field", "revenue_impact_usd",
                "related_record_ids",
            ])
        frame = pd.DataFrame([f.to_row() for f in self.findings])
        frame["at_risk_usd"] = [f.meta.get("at_risk_usd", 0.0) for f in self.findings]
        return frame

    def punch_list_dataframe(self) -> pd.DataFrame:
        if not self.items:
            return pd.DataFrame(columns=[
                "priority_check", "issue", "object", "records", "severity",
                "hours_to_fix", "cost_usd", "at_risk_usd", "score_gain", "example",
            ])
        return pd.DataFrame([i.to_row() for i in self.items])

    def to_dict(self) -> dict:
        """JSON-friendly summary, for export or a non-Python consumer."""
        return {
            "headline": self.headline,
            "as_of": self.as_of.isoformat(),
            "score": {
                "overall": self.score.overall,
                "grade": self.score.grade,
                "by_object": self.score.by_object,
                "clean_records": self.score.clean_records,
                "affected_records": self.score.affected_records,
                "total_records": self.score.total_records,
            },
            "cost": {
                "remediation_hours": self.cost.remediation_hours,
                "direct_cost_usd": self.cost.direct_cost_usd,
                "at_risk_pipeline_usd": self.cost.at_risk_pipeline_usd,
                "total_open_pipeline_usd": self.cost.total_open_pipeline_usd,
                "at_risk_share": round(self.cost.at_risk_share, 4),
            },
            "record_counts": self.record_counts,
            "findings_total": len(self.findings),
            "findings_by_severity": self.findings_by_severity,
            "checks_run": self.checks_run,
            "punch_list": [item.to_row() for item in self.items],
            "assumptions": {
                "loaded_rep_hourly_cost": self.assumptions.loaded_rep_hourly_cost,
                "forecast_risk_bands": [list(b) for b in self.assumptions.forecast_risk_bands],
            },
        }


def _build_items(
    priced: list[Finding],
    counts: dict[str, int],
    baseline_score: float,
    check_names: dict[str, str],
) -> list[RemediationItem]:
    """
    Group findings by check and compute what resolving each group recovers.

    The score gain is measured, not estimated: re-run the scoring function with
    that check's findings removed and take the difference. That keeps the number
    honest even though per-record penalty capping makes the relationship between
    findings and score non-linear.
    """
    grouped: dict[str, list[Finding]] = {}
    for finding in priced:
        grouped.setdefault(finding.check_id, []).append(finding)

    items: list[RemediationItem] = []
    for check_id, group in grouped.items():
        remaining = [f for f in priced if f.check_id != check_id]
        gain = compute_score(remaining, counts).overall - baseline_score

        worst = max(group, key=lambda f: (int(f.severity), f.revenue_impact_usd))
        items.append(RemediationItem(
            check_id=check_id,
            name=check_names.get(check_id, check_id.replace("_", " ").title()),
            object_type=group[0].object_type,
            count=len(group),
            worst_severity=worst.severity,
            remediation_hours=round(sum(f.meta.get("remediation_minutes", 0.0) for f in group) / 60.0, 1),
            direct_cost_usd=round(sum(f.revenue_impact_usd for f in group), 2),
            at_risk_usd=round(sum(f.meta.get("at_risk_usd", 0.0) for f in group), 2),
            score_gain=round(gain, 1),
            example_message=worst.message,
        ))

    # Rank by what fixing it is worth, not by how many rows it produced.
    items.sort(key=lambda i: (i.at_risk_usd + i.direct_cost_usd, i.score_gain, i.count), reverse=True)
    return items


def build_report(
    data: CRMData,
    config,
    assumptions: ImpactAssumptions | None = None,
) -> Report:
    """Run every check, price the findings, and assemble the report."""
    assumptions = assumptions or ImpactAssumptions()
    checks = build_checks()

    findings = run_all_checks(data, config, checks=checks)
    priced = price_findings(findings, assumptions)

    counts = data.counts
    score = compute_score(priced, counts)
    cost = summarize_cost(priced, open_pipeline_value(data.deals), assumptions)

    by_severity: dict[str, int] = {}
    for finding in priced:
        by_severity[finding.severity.label] = by_severity.get(finding.severity.label, 0) + 1

    items = _build_items(
        priced,
        counts,
        score.overall,
        {check.id: check.name for check in checks},
    )

    return Report(
        score=score,
        cost=cost,
        findings=priced,
        items=items,
        record_counts={ot: counts.get(ot, 0) for ot in OBJECT_TYPES},
        as_of=data.as_of,
        assumptions=assumptions,
        checks_run=len(checks),
        findings_by_severity=by_severity,
    )
