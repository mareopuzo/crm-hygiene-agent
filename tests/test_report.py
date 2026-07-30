"""The assembled report: punch list, totals, and the score-gain claim."""

from __future__ import annotations

import pytest

from engine.report import build_report
from engine.scoring import ImpactAssumptions, compute_score


@pytest.fixture(scope="module")
def report(sample_data, config):
    return build_report(sample_data, config)


def test_report_covers_every_finding(report):
    assert len(report.findings) == sum(i.count for i in report.items)


def test_punch_list_is_ranked_by_value_not_volume(report):
    """A small, expensive category must be able to outrank a large cheap one."""
    values = [i.at_risk_usd + i.direct_cost_usd for i in report.items]
    assert values == sorted(values, reverse=True)

    top = report.items[0]
    biggest = max(report.items, key=lambda i: i.count)
    assert top.count < biggest.count, "expected value-ranking to differ from count-ranking"


def test_score_gain_is_measured_not_guessed(report, sample_data):
    """
    Removing a category should recover exactly the points the item claims.

    Per-record penalty capping makes this non-linear, so the only trustworthy
    way to state it is to recompute — this test pins that.
    """
    for item in report.items:
        remaining = [f for f in report.findings if f.check_id != item.check_id]
        recomputed = compute_score(remaining, sample_data.counts).overall
        assert round(recomputed - report.score.overall, 1) == item.score_gain


def test_fixing_everything_restores_a_perfect_score(sample_data):
    assert compute_score([], sample_data.counts).overall == 100.0


def test_totals_are_internally_consistent(report):
    assert round(sum(i.direct_cost_usd for i in report.items), 2) == pytest.approx(
        report.cost.direct_cost_usd, abs=0.05
    )
    assert round(sum(i.at_risk_usd for i in report.items), 2) == pytest.approx(
        report.cost.at_risk_pipeline_usd, abs=0.05
    )


def test_at_risk_never_exceeds_open_pipeline(report):
    """The cap below face value makes this structurally impossible to violate."""
    assert 0 < report.cost.at_risk_pipeline_usd < report.cost.total_open_pipeline_usd
    assert report.cost.at_risk_share < 1.0


def test_headline_states_score_cost_and_risk(report):
    headline = report.headline
    assert f"{report.score.overall:.0f}/100" in headline
    assert report.score.grade in headline
    # The two numbers are named separately, never merged into one figure.
    assert "avoidable work" in headline
    assert "at risk" in headline


def test_severity_counts_match_the_findings(report):
    tallied = sum(report.findings_by_severity.values())
    assert tallied == len(report.findings)


def test_dataframes_render(report):
    findings_df = report.findings_dataframe()
    assert len(findings_df) == len(report.findings)
    assert "at_risk_usd" in findings_df.columns

    punch_df = report.punch_list_dataframe()
    assert len(punch_df) == len(report.items)
    assert "score_gain" in punch_df.columns


def test_report_serializes_with_its_assumptions(report):
    payload = report.to_dict()
    assert payload["score"]["overall"] == report.score.overall
    assert payload["findings_total"] == len(report.findings)
    # The assumptions travel with the numbers so the arithmetic stays checkable.
    assert payload["assumptions"]["loaded_rep_hourly_cost"] == report.assumptions.loaded_rep_hourly_cost
    assert payload["assumptions"]["forecast_risk_bands"]


def test_assumptions_flow_through_to_the_report(sample_data, config):
    doubled = build_report(sample_data, config, ImpactAssumptions(loaded_rep_hourly_cost=150.0))
    baseline = build_report(sample_data, config, ImpactAssumptions(loaded_rep_hourly_cost=75.0))
    assert doubled.cost.direct_cost_usd == pytest.approx(2 * baseline.cost.direct_cost_usd, rel=1e-6)
    # Rate changes cost, not data quality.
    assert doubled.score.overall == baseline.score.overall


def test_clean_crm_reports_no_work(sample_data, config):
    from engine.loader import load_crm_data

    clean = load_crm_data(
        "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region,Last Activity Date\n"
        "1,ada@x.com,Sarah Chen,lead,United States,2026-07-20\n",
        "Record ID,Company name,Domain,Company owner,Industry\n"
        "10,Acme Inc,acme.com,Sarah Chen,Software\n",
        "Record ID,Deal Name,Deal Stage,Amount,Deal owner,Close Date,Last Activity Date\n"
        "100,Acme - New,qualifiedtobuy,25000,Sarah Chen,2026-12-01,2026-07-25\n",
        as_of="2026-07-30",
    )
    report = build_report(clean, config)
    assert report.findings == []
    assert report.items == []
    assert report.score.overall == 100.0
    assert report.score.grade == "A"
    assert report.cost.direct_cost_usd == 0.0
    assert report.cost.at_risk_pipeline_usd == 0.0
