"""Territory routing: owner's region vs the record's country."""

from __future__ import annotations

from engine.checks.routing import TerritoryRoutingCheck
from engine.config import Config, default_config
from engine.loader import load_crm_data
from engine.models import COMPANIES, CONTACTS, Severity

EMPTY_COMPANIES = "Record ID,Company name\n"
EMPTY_DEALS = "Record ID,Deal Name\n"


def _contacts(*rows: str) -> str:
    header = "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region\n"
    return header + "".join(r + "\n" for r in rows)


def test_contact_routing_matches_ground_truth(sample_data, config, planted, flagged):
    findings = TerritoryRoutingCheck(CONTACTS).run(sample_data, config)
    assert flagged(findings) == planted("contact_bad_routing")


def test_correctly_routed_companies_produce_nothing(sample_data, config):
    """Sample companies are routed correctly by construction — proves no noise."""
    assert TerritoryRoutingCheck(COMPANIES).run(sample_data, config) == []


def test_check_is_opt_in():
    """Orgs that don't route geographically get silence, not nonsense."""
    data = load_crm_data(
        _contacts("1,a@x.com,Mei Tan,lead,Germany"),  # APAC owner, EMEA country
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    assert TerritoryRoutingCheck(CONTACTS).run(data, Config()) == []       # no map configured
    assert len(TerritoryRoutingCheck(CONTACTS).run(data, default_config())) == 1


def test_unmapped_owner_or_country_is_not_a_mismatch():
    """An unknown is an unknown — guessing would manufacture false positives."""
    data = load_crm_data(
        _contacts(
            "1,a@x.com,Unknown Person,lead,Germany",    # owner not in the map
            "2,b@x.com,Mei Tan,lead,Atlantis",          # country not in the map
        ),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    assert TerritoryRoutingCheck(CONTACTS).run(data, default_config()) == []


def test_blank_owner_is_left_to_the_missing_fields_check():
    """No double-counting: one underlying problem, one finding."""
    data = load_crm_data(
        _contacts("1,a@x.com,,lead,Germany", "2,b@x.com,Mei Tan,lead,"),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    assert TerritoryRoutingCheck(CONTACTS).run(data, default_config()) == []


def test_finding_names_both_regions_and_the_fix(sample_data, config):
    findings = TerritoryRoutingCheck(CONTACTS).run(sample_data, config)
    finding = findings[0]
    assert finding.severity is Severity.HIGH
    assert finding.meta["owner_region"] != finding.meta["record_region"]
    assert "Reassign" in finding.message
