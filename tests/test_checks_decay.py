"""Contact decay: inactivity, plus email deliverability risk."""

from __future__ import annotations

from engine.checks.decay import EmailDeliverabilityCheck, InactiveContactsCheck
from engine.config import Config, default_config
from engine.loader import load_crm_data
from engine.models import Severity

EMPTY_COMPANIES = "Record ID,Company name\n"
EMPTY_DEALS = "Record ID,Deal Name\n"


def _contacts(*rows: str) -> str:
    header = "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region,Last Activity Date\n"
    return header + "".join(r + "\n" for r in rows)


def test_decayed_contacts_match_ground_truth(sample_data, config, planted, flagged):
    findings = InactiveContactsCheck().run(sample_data, config)
    assert flagged(findings) == planted("contact_decayed")


def test_threshold_boundary_is_exclusive():
    """A contact exactly at the threshold is not yet decayed; one day past is."""
    data = load_crm_data(
        _contacts(
            "1,a@x.com,Sarah Chen,lead,United States,2026-01-28",  # exactly 183 days
            "2,b@x.com,Sarah Chen,lead,United States,2026-01-27",  # 184 days
        ),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    findings = InactiveContactsCheck().run(data, Config())
    assert {f.record_id for f in findings} == {"2"}


def test_missing_activity_date_is_not_decay():
    """No activity date is a completeness gap, not evidence of decay."""
    data = load_crm_data(
        _contacts("1,a@x.com,Sarah Chen,lead,United States,"),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    assert InactiveContactsCheck().run(data, Config()) == []


def test_decay_threshold_is_configurable():
    data = load_crm_data(
        _contacts("1,a@x.com,Sarah Chen,lead,United States,2026-06-01"),  # 59 days
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    assert InactiveContactsCheck().run(data, Config()) == []                       # 183-day default
    assert len(InactiveContactsCheck().run(data, Config(decayed_contact_days=30))) == 1


def test_role_based_emails_match_ground_truth(sample_data, config, planted, flagged):
    findings = EmailDeliverabilityCheck().run(sample_data, config)
    role_based = flagged(findings, lambda f: f.meta.get("reason") == "role_based")
    assert role_based == planted("contact_role_based_email")


def test_typo_domains_match_ground_truth(sample_data, config, planted, flagged):
    findings = EmailDeliverabilityCheck().run(sample_data, config)
    invalid = flagged(
        findings,
        lambda f: f.meta.get("reason") in {"suspicious_tld", "invalid_syntax"},
    )
    assert invalid == planted("contact_invalid_email")


def test_legitimate_tlds_are_not_flagged():
    """'.co' is one edit from '.com' — the allowlist must protect it."""
    data = load_crm_data(
        _contacts(
            "1,a@x.co,Sarah Chen,lead,United States,2026-07-01",
            "2,b@x.io,Sarah Chen,lead,United States,2026-07-01",
            "3,c@x.com,Sarah Chen,lead,United States,2026-07-01",
            "4,d@x.de,Sarah Chen,lead,United States,2026-07-01",
        ),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    assert EmailDeliverabilityCheck().run(data, default_config()) == []


def test_typo_message_names_the_intended_tld():
    data = load_crm_data(
        _contacts("1,a@x.cim,Sarah Chen,lead,United States,2026-07-01"),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    finding = EmailDeliverabilityCheck().run(data, default_config())[0]
    assert finding.meta["likely_tld"] == "com"
    assert "typo of '.com'" in finding.message
    assert finding.severity is Severity.HIGH


def test_deliverability_severity_splits_by_consequence(sample_data, config):
    """Bouncing addresses outrank merely-weak ones."""
    findings = EmailDeliverabilityCheck().run(sample_data, config)
    for finding in findings:
        if finding.meta["reason"] == "role_based":
            assert finding.severity is Severity.MEDIUM
        else:
            assert finding.severity is Severity.HIGH


def test_blank_email_is_not_a_deliverability_finding(sample_data, config):
    """Missing emails belong to the missing-fields check, not this one."""
    findings = EmailDeliverabilityCheck().run(sample_data, config)
    contacts = sample_data.contacts.set_index("record_id")
    for finding in findings:
        assert contacts.loc[finding.record_id, "email"] is not None
