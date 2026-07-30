"""
The health score and the cost model.

The claims worth proving here are the ones a skeptical reader would attack:
that the score is volume-normalized, that dollars are additive, and that a deal
failing two ways is only counted as one exposure.
"""

from __future__ import annotations

from engine.models import CONTACTS, DEALS, Finding, Severity
from engine.scoring import (
    ImpactAssumptions,
    compute_score,
    grade_for,
    open_pipeline_value,
    price_findings,
    summarize_cost,
)


def _finding(check_id="stale_deals", object_type=DEALS, record_id="1",
             severity=Severity.HIGH, **meta) -> Finding:
    return Finding(
        check_id=check_id,
        object_type=object_type,
        record_id=record_id,
        severity=severity,
        message="test finding",
        meta=meta,
    )


# --------------------------------------------------------------------------- #
# Score
# --------------------------------------------------------------------------- #

def test_clean_data_scores_100():
    assert compute_score([], {CONTACTS: 100, DEALS: 50}).overall == 100.0


def test_no_records_does_not_divide_by_zero():
    score = compute_score([], {})
    assert score.overall == 100.0
    assert score.total_records == 0


def test_score_falls_as_issues_accumulate():
    counts = {CONTACTS: 100}
    one = compute_score([_finding(object_type=CONTACTS, record_id="1")], counts).overall
    many = compute_score(
        [_finding(object_type=CONTACTS, record_id=str(i)) for i in range(10)], counts
    ).overall
    assert 100.0 > one > many


def test_penalty_is_capped_per_record():
    """One catastrophic record can't drag the whole score down."""
    counts = {CONTACTS: 100}
    two_criticals = [
        _finding(object_type=CONTACTS, record_id="1", check_id="a"),
        _finding(object_type=CONTACTS, record_id="1", check_id="b"),
    ]
    ten_criticals = two_criticals + [
        _finding(object_type=CONTACTS, record_id="1", check_id=f"c{i}") for i in range(8)
    ]
    assert compute_score(two_criticals, counts).overall == compute_score(ten_criticals, counts).overall


def test_score_is_volume_normalized():
    """The same issue *rate* scores the same at any CRM size."""
    small = compute_score([_finding(object_type=CONTACTS, record_id="1")], {CONTACTS: 10})
    large = compute_score(
        [_finding(object_type=CONTACTS, record_id=str(i)) for i in range(10)], {CONTACTS: 100}
    )
    assert small.overall == large.overall


def test_severity_weighting_matters():
    counts = {CONTACTS: 100}
    high = compute_score([_finding(object_type=CONTACTS, severity=Severity.HIGH)], counts).overall
    low = compute_score([_finding(object_type=CONTACTS, severity=Severity.LOW)], counts).overall
    assert low > high


def test_clean_and_affected_record_counts():
    score = compute_score(
        [_finding(object_type=CONTACTS, record_id="1", check_id="a"),
         _finding(object_type=CONTACTS, record_id="1", check_id="b"),
         _finding(object_type=CONTACTS, record_id="2", check_id="a")],
        {CONTACTS: 10},
    )
    assert score.affected_records == 2   # two distinct records, not three findings
    assert score.clean_records == 8


def test_grade_boundaries():
    assert grade_for(100) == "A"
    assert grade_for(90) == "A"
    assert grade_for(89.9) == "B"
    assert grade_for(70) == "C"
    assert grade_for(59.9) == "F"


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #

def test_direct_cost_is_hours_times_rate():
    assumptions = ImpactAssumptions(loaded_rep_hourly_cost=60.0)
    # duplicate_contacts is 15 minutes = 0.25h; at $60/h that's exactly $15.
    priced = price_findings([_finding(check_id="duplicate_contacts", object_type=CONTACTS)], assumptions)
    assert priced[0].revenue_impact_usd == 15.0
    assert priced[0].meta["remediation_minutes"] == 15.0


def test_unknown_check_still_gets_priced():
    """Adding a check must never silently drop out of the cost model."""
    assumptions = ImpactAssumptions(loaded_rep_hourly_cost=60.0)
    priced = price_findings([_finding(check_id="brand_new_check", object_type=CONTACTS)], assumptions)
    assert priced[0].revenue_impact_usd > 0


def test_forecast_risk_is_banded_and_capped_below_face_value():
    assumptions = ImpactAssumptions()
    priced = price_findings([
        _finding(record_id="1", amount=100_000.0, days_inactive=100),  # 90+ band
        _finding(record_id="2", amount=100_000.0, days_inactive=70),   # 60+ band
        _finding(record_id="3", amount=100_000.0, days_inactive=40),   # 30+ band
        _finding(record_id="4", amount=100_000.0, days_inactive=10),   # below all bands
    ], assumptions)
    at_risk = {f.record_id: f.meta["at_risk_usd"] for f in priced}
    assert at_risk["1"] == 75_000.0     # never the full amount
    assert at_risk["2"] == 50_000.0
    assert at_risk["3"] == 25_000.0
    assert at_risk["4"] == 0.0


def test_a_deal_failing_two_ways_is_one_exposure():
    """
    The core anti-double-counting guarantee: a deal that is both stale and past
    its close date puts one deal's value at risk, not two.
    """
    assumptions = ImpactAssumptions()
    priced = price_findings([
        _finding(check_id="stale_deals", record_id="9001", amount=100_000.0, days_inactive=100),
        _finding(check_id="past_close_date", record_id="9001", amount=100_000.0, days_overdue=40),
    ], assumptions)

    total = sum(f.meta["at_risk_usd"] for f in priced)
    assert total == 75_000.0  # the worse of the two factors, counted once
    # Exactly one finding carries it, so summing any subset stays correct.
    assert sorted(f.meta["at_risk_usd"] for f in priced) == [0.0, 75_000.0]
    # Both still carry their own remediation cost — that work is genuinely doubled.
    assert all(f.revenue_impact_usd > 0 for f in priced)


def test_deals_without_an_amount_contribute_no_risk():
    """Unpriceable is not zero-risk hand-waving — it's reported as a missing field."""
    priced = price_findings([_finding(record_id="1", days_inactive=200)], ImpactAssumptions())
    assert priced[0].meta["at_risk_usd"] == 0.0


def test_non_deal_findings_never_carry_pipeline_risk():
    priced = price_findings(
        [_finding(check_id="duplicate_contacts", object_type=CONTACTS, amount=50_000.0, days_inactive=200)],
        ImpactAssumptions(),
    )
    assert priced[0].meta["at_risk_usd"] == 0.0


def test_summary_totals_match_the_findings():
    assumptions = ImpactAssumptions(loaded_rep_hourly_cost=60.0)
    priced = price_findings([
        _finding(check_id="duplicate_contacts", object_type=CONTACTS, record_id="1"),
        _finding(check_id="stale_deals", record_id="9001", amount=100_000.0, days_inactive=100),
    ], assumptions)
    summary = summarize_cost(priced, open_pipeline_usd=500_000.0, assumptions=assumptions)

    assert summary.direct_cost_usd == sum(f.revenue_impact_usd for f in priced)
    assert summary.at_risk_pipeline_usd == 75_000.0
    assert summary.at_risk_share == 0.15
    assert summary.remediation_hours == 0.5  # 15 + 15 minutes


def test_open_pipeline_excludes_closed_deals(sample_data):
    deals = sample_data.deals
    total_all = float(deals["amount"].fillna(0).sum())
    assert 0 < open_pipeline_value(deals) < total_all


def test_assumptions_are_tunable():
    """Disagreeing with a number changes the output, no code change needed."""
    cheap = price_findings([_finding(check_id="duplicate_contacts", object_type=CONTACTS)],
                           ImpactAssumptions(loaded_rep_hourly_cost=50.0))
    pricey = price_findings([_finding(check_id="duplicate_contacts", object_type=CONTACTS)],
                            ImpactAssumptions(loaded_rep_hourly_cost=150.0))
    assert pricey[0].revenue_impact_usd == 3 * cheap[0].revenue_impact_usd
