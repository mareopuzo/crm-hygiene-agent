"""Pipeline hygiene: stale open deals and deals past their close date."""

from __future__ import annotations

from engine.checks.stale_deals import PastCloseDateCheck, StaleDealsCheck
from engine.config import Config
from engine.loader import load_crm_data
from engine.models import Severity

EMPTY_CONTACTS = "Record ID,Email\n"
EMPTY_COMPANIES = "Record ID,Company name\n"


def _deals(*rows: str) -> str:
    header = "Record ID,Deal Name,Deal Stage,Amount,Deal owner,Close Date,Last Activity Date\n"
    return header + "".join(r + "\n" for r in rows)


def test_stale_deals_match_ground_truth(sample_data, config, planted, flagged):
    findings = StaleDealsCheck().run(sample_data, config)
    assert flagged(findings) == planted("deal_stale")


def test_past_close_date_matches_ground_truth(sample_data, config, planted, flagged):
    findings = PastCloseDateCheck().run(sample_data, config)
    assert flagged(findings) == planted("deal_past_close_date")


def test_closed_deals_are_never_stale():
    """A finished deal with no recent activity is finished, not neglected."""
    data = load_crm_data(
        EMPTY_CONTACTS, EMPTY_COMPANIES,
        _deals(
            "1,Won,closedwon,25000,Sarah Chen,2025-01-01,2025-01-01",
            "2,Lost,closedlost,25000,Sarah Chen,2025-01-01,2025-01-01",
            "3,Open,qualifiedtobuy,25000,Sarah Chen,2026-12-01,2025-01-01",
        ),
        as_of="2026-07-30",
    )
    assert {f.record_id for f in StaleDealsCheck().run(data, Config())} == {"3"}


def test_closed_deals_are_never_past_close_date():
    data = load_crm_data(
        EMPTY_CONTACTS, EMPTY_COMPANIES,
        _deals(
            "1,Won,closedwon,25000,Sarah Chen,2025-01-01,2026-07-01",
            "2,Open,contractsent,25000,Sarah Chen,2025-01-01,2026-07-01",
        ),
        as_of="2026-07-30",
    )
    assert {f.record_id for f in PastCloseDateCheck().run(data, Config())} == {"2"}


def test_stale_threshold_boundary_is_exclusive():
    data = load_crm_data(
        EMPTY_CONTACTS, EMPTY_COMPANIES,
        _deals(
            "1,A,qualifiedtobuy,25000,Sarah Chen,2026-12-01,2026-06-30",  # exactly 30 days
            "2,B,qualifiedtobuy,25000,Sarah Chen,2026-12-01,2026-06-29",  # 31 days
        ),
        as_of="2026-07-30",
    )
    assert {f.record_id for f in StaleDealsCheck().run(data, Config())} == {"2"}


def test_stale_threshold_is_configurable():
    data = load_crm_data(
        EMPTY_CONTACTS, EMPTY_COMPANIES,
        _deals("1,A,qualifiedtobuy,25000,Sarah Chen,2026-12-01,2026-06-15"),  # 45 days
        as_of="2026-07-30",
    )
    assert len(StaleDealsCheck().run(data, Config(stale_deal_days=30))) == 1
    assert StaleDealsCheck().run(data, Config(stale_deal_days=90)) == []


def test_blank_stage_is_treated_as_open():
    """A deal sitting in a pipeline with no resolution recorded needs a look."""
    data = load_crm_data(
        EMPTY_CONTACTS, EMPTY_COMPANIES,
        _deals("1,A,,25000,Sarah Chen,2026-12-01,2025-01-01"),
        as_of="2026-07-30",
    )
    assert len(StaleDealsCheck().run(data, Config())) == 1


def test_missing_close_date_is_left_to_missing_fields():
    data = load_crm_data(
        EMPTY_CONTACTS, EMPTY_COMPANIES,
        _deals("1,A,qualifiedtobuy,25000,Sarah Chen,,2026-07-20"),
        as_of="2026-07-30",
    )
    assert PastCloseDateCheck().run(data, Config()) == []


def test_findings_carry_deal_value_for_the_impact_model(sample_data, config):
    """The $ model downstream needs the amount, including when it's absent."""
    findings = StaleDealsCheck().run(sample_data, config)
    assert findings
    for finding in findings:
        assert "amount" in finding.meta
        assert finding.severity is Severity.HIGH
