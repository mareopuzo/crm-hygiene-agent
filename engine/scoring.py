"""
Health score and the cost model.

This module is where the tool stops being a linter and starts being a RevOps
deliverable. It answers two questions an ops leader actually asks: *how bad is
it* (a score) and *what is it costing me* (dollars).

Three rules govern everything here, because a single number a sharp CRO can
poke a hole in discredits the whole report:

1. **Two numbers, two methods, never mixed.**
   - *Direct cost* is avoidable work: every finding carries an estimate of the
     minutes to remediate it or wasted working around it, priced at a loaded
     hourly rate. One consistent unit, summable, easy to defend.
   - *At-risk pipeline* is forecast exposure: deal value multiplied by a
     risk factor. It is labelled **at risk**, never "lost" — a stale deal is
     unreliable, not dead.

2. **Risk-adjusted, never face value.** Claiming a 90-day-silent $150k deal is
   a straight $150k loss is exactly the overreach that gets a report dismissed.
   The factor is capped below 1.0 for that reason: even a badly neglected deal
   isn't certainly gone.

3. **Additive by construction.** A deal that is both stale *and* past its close
   date has one exposure, not two. The at-risk amount is attributed to the
   single worst finding for that deal and zeroed on the others, so summing any
   subset of findings is always correct.

Every assumption lives in `ImpactAssumptions` so a reader can disagree with a
number and change it, rather than disbelieving the output.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from engine.models import DEALS, Finding, OBJECT_TYPES, Severity

# --------------------------------------------------------------------------- #
# Cost assumptions
# --------------------------------------------------------------------------- #

# Minutes of human effort attributable to one finding, by check and severity.
# These are remediation estimates, deliberately modest — under-claiming is the
# right bias when the goal is a number nobody can dismiss.
DEFAULT_REMEDIATION_MINUTES: dict[str, dict[Severity, float]] = {
    # Merging is slow: reconcile activity history, re-point associations.
    "duplicate_contacts": {Severity.HIGH: 15.0},
    "duplicate_companies": {Severity.HIGH: 20.0, Severity.MEDIUM: 20.0},
    # Finding a missing value means leaving the CRM to go look it up.
    "missing_fields_contacts": {Severity.HIGH: 10.0, Severity.MEDIUM: 4.0},
    "missing_fields_companies": {Severity.HIGH: 10.0, Severity.MEDIUM: 4.0},
    "missing_fields_deals": {Severity.HIGH: 10.0, Severity.MEDIUM: 4.0},
    # A decision per record: re-engage or suppress.
    "decayed_contacts": {Severity.MEDIUM: 3.0},
    "email_deliverability": {Severity.HIGH: 5.0, Severity.MEDIUM: 3.0},
    # Reassignment carries a context handoff, not just a field change.
    "routing_contacts": {Severity.HIGH: 20.0},
    "routing_companies": {Severity.HIGH: 20.0},
    # Pipeline review: chase, re-date, or close.
    "stale_deals": {Severity.HIGH: 15.0},
    "past_close_date": {Severity.HIGH: 10.0},
}

# Any check not listed above still gets priced, so adding a check never
# silently drops out of the cost model.
FALLBACK_REMEDIATION_MINUTES = 5.0


@dataclass
class ImpactAssumptions:
    """
    Every number behind the dollar figures, in one place.

    `loaded_rep_hourly_cost` is fully-loaded cost, not salary: roughly a $120k
    all-in rep across ~1,600 productive hours a year. Adjust to your own comp
    bands — the report prints whatever is set here so the arithmetic is always
    inspectable.
    """

    loaded_rep_hourly_cost: float = 75.0

    remediation_minutes: dict[str, dict[Severity, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_REMEDIATION_MINUTES.items()}
    )
    fallback_minutes: float = FALLBACK_REMEDIATION_MINUTES

    # Forecast risk bands: (days_neglected, factor), highest threshold first.
    # A deal untouched for 90+ days has 75% of its value in question; one at 30
    # days only 25%. Capped below 1.0 on purpose — see rule 2 above.
    forecast_risk_bands: tuple[tuple[int, float], ...] = (
        (90, 0.75),
        (60, 0.50),
        (30, 0.25),
    )

    def minutes_for(self, finding: Finding) -> float:
        by_severity = self.remediation_minutes.get(finding.check_id)
        if not by_severity:
            return self.fallback_minutes
        return by_severity.get(finding.severity, self.fallback_minutes)

    def risk_factor(self, days_neglected: float | None) -> float:
        if days_neglected is None:
            return 0.0
        for threshold, factor in self.forecast_risk_bands:
            if days_neglected >= threshold:
                return factor
        return 0.0


# --------------------------------------------------------------------------- #
# Health score
# --------------------------------------------------------------------------- #

# A record's health is exhausted by two critical issues (3 + 3). Capping per
# record stops a handful of catastrophic records from dominating the score, and
# pooling across all records makes the score volume-normalized — a bigger CRM
# isn't automatically a worse one.
MAX_RECORD_PENALTY = 6.0


@dataclass
class ScoreBreakdown:
    """The health score, overall and per object type."""

    overall: float
    by_object: dict[str, float]
    clean_records: int
    affected_records: int
    total_records: int

    @property
    def grade(self) -> str:
        return grade_for(self.overall)

    @property
    def clean_rate(self) -> float:
        return self.clean_records / self.total_records if self.total_records else 1.0


def grade_for(score: float) -> str:
    for threshold, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= threshold:
            return letter
    return "F"


def _record_penalties(findings: list[Finding]) -> dict[tuple[str, str], float]:
    """(object_type, record_id) -> capped severity-weighted penalty."""
    raw: dict[tuple[str, str], float] = {}
    for finding in findings:
        key = (finding.object_type, finding.record_id)
        raw[key] = raw.get(key, 0.0) + float(int(finding.severity))
    return {key: min(value, MAX_RECORD_PENALTY) for key, value in raw.items()}


def compute_score(findings: list[Finding], counts: dict[str, int]) -> ScoreBreakdown:
    """
    Score every record from 1.0 (clean) down to 0.0 (two or more critical
    issues), then average. Records with no findings contribute a perfect score,
    which is what keeps a large, mostly-healthy CRM from being punished for
    its size.
    """
    penalties = _record_penalties(findings)
    total_records = sum(counts.get(ot, 0) for ot in OBJECT_TYPES)

    by_object: dict[str, float] = {}
    for object_type in OBJECT_TYPES:
        record_count = counts.get(object_type, 0)
        if not record_count:
            by_object[object_type] = 100.0
            continue
        lost = sum(v for (ot, _), v in penalties.items() if ot == object_type)
        health = record_count - (lost / MAX_RECORD_PENALTY)
        by_object[object_type] = round(100.0 * health / record_count, 1)

    if total_records:
        total_lost = sum(penalties.values())
        overall = 100.0 * (total_records - total_lost / MAX_RECORD_PENALTY) / total_records
    else:
        overall = 100.0

    return ScoreBreakdown(
        overall=round(overall, 1),
        by_object=by_object,
        clean_records=total_records - len(penalties),
        affected_records=len(penalties),
        total_records=total_records,
    )


# --------------------------------------------------------------------------- #
# Dollar attribution
# --------------------------------------------------------------------------- #

def _deal_neglect_days(finding: Finding) -> float | None:
    """How long a deal has been neglected, whichever way it's failing."""
    for key in ("days_inactive", "days_overdue"):
        value = finding.meta.get(key)
        if value is not None:
            return float(value)
    return None


def price_findings(
    findings: list[Finding],
    assumptions: ImpactAssumptions,
) -> list[Finding]:
    """
    Return priced copies of `findings` (they're immutable, so this replaces
    rather than mutates).

    Each finding gets:
      - `revenue_impact_usd` — direct remediation cost, always populated.
      - `meta['at_risk_usd']` — forecast exposure, populated on at most one
        finding per deal so the column can be summed without double-counting.
      - `meta['remediation_minutes']` — the input to the cost, kept visible so
        the arithmetic can be checked.
    """
    # Decide which finding owns each deal's exposure before pricing anything:
    # the one with the highest risk factor, ties broken by deal value.
    exposure_owner: dict[str, tuple[str, float]] = {}  # record_id -> (check_id, at_risk)
    for finding in findings:
        if finding.object_type != DEALS:
            continue
        amount = finding.meta.get("amount")
        if amount is None:
            continue  # unpriceable; the missing Amount is its own finding
        factor = assumptions.risk_factor(_deal_neglect_days(finding))
        if factor <= 0:
            continue
        at_risk = float(amount) * factor
        current = exposure_owner.get(finding.record_id)
        if current is None or at_risk > current[1]:
            exposure_owner[finding.record_id] = (finding.check_id, at_risk)

    priced: list[Finding] = []
    for finding in findings:
        minutes = assumptions.minutes_for(finding)
        direct = minutes / 60.0 * assumptions.loaded_rep_hourly_cost

        at_risk = 0.0
        owner = exposure_owner.get(finding.record_id)
        if owner is not None and owner[0] == finding.check_id and finding.object_type == DEALS:
            at_risk = owner[1]

        priced.append(replace(
            finding,
            revenue_impact_usd=round(direct, 2),
            meta={
                **finding.meta,
                "remediation_minutes": minutes,
                "at_risk_usd": round(at_risk, 2),
            },
        ))
    return priced


@dataclass
class CostSummary:
    """Totals, kept separate so the two methods never blur together."""

    remediation_hours: float
    direct_cost_usd: float
    at_risk_pipeline_usd: float
    total_open_pipeline_usd: float

    @property
    def at_risk_share(self) -> float:
        if not self.total_open_pipeline_usd:
            return 0.0
        return self.at_risk_pipeline_usd / self.total_open_pipeline_usd


def summarize_cost(
    priced_findings: list[Finding],
    open_pipeline_usd: float,
    assumptions: ImpactAssumptions,
) -> CostSummary:
    minutes = sum(f.meta.get("remediation_minutes", 0.0) for f in priced_findings)
    direct = sum(f.revenue_impact_usd for f in priced_findings)
    at_risk = sum(f.meta.get("at_risk_usd", 0.0) for f in priced_findings)
    return CostSummary(
        remediation_hours=round(minutes / 60.0, 1),
        direct_cost_usd=round(direct, 2),
        at_risk_pipeline_usd=round(at_risk, 2),
        total_open_pipeline_usd=round(open_pipeline_usd, 2),
    )


def open_pipeline_value(deals: pd.DataFrame) -> float:
    """Total value of open deals — the denominator for 'share at risk'."""
    if deals.empty or "deal_stage" not in deals.columns:
        return 0.0
    stage = deals["deal_stage"].astype("object").where(deals["deal_stage"].notna(), "")
    normalized = stage.map(lambda s: str(s).strip().lower().replace(" ", "").replace("_", ""))
    open_deals = deals[~normalized.str.startswith("closed")]
    return float(pd.to_numeric(open_deals["amount"], errors="coerce").fillna(0).sum())
