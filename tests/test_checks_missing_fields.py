"""Required-field completeness, driven by the config policy table."""

from __future__ import annotations

from engine.checks.missing_fields import MissingFieldsCheck
from engine.config import Config, RequiredField
from engine.loader import load_crm_data
from engine.models import COMPANIES, CONTACTS, DEALS, Severity


def _for_field(findings, column):
    return {f.record_id for f in findings if f.field_name == column}


def test_contact_missing_fields_match_ground_truth(sample_data, config, planted):
    findings = MissingFieldsCheck(CONTACTS).run(sample_data, config)
    assert _for_field(findings, "email") == planted("contact_missing_email")
    assert _for_field(findings, "owner") == planted("contact_missing_owner")
    assert _for_field(findings, "lifecycle_stage") == planted("contact_missing_lifecycle")
    assert _for_field(findings, "country") == planted("contact_missing_country")


def test_company_missing_fields_match_ground_truth(sample_data, config, planted):
    findings = MissingFieldsCheck(COMPANIES).run(sample_data, config)
    assert _for_field(findings, "domain") == planted("company_missing_domain")
    assert _for_field(findings, "owner") == planted("company_missing_owner")
    assert _for_field(findings, "industry") == planted("company_missing_industry")


def test_deal_missing_fields_match_ground_truth(sample_data, config, planted):
    findings = MissingFieldsCheck(DEALS).run(sample_data, config)
    assert _for_field(findings, "amount") == planted("deal_missing_amount")
    assert _for_field(findings, "owner") == planted("deal_missing_owner")
    assert _for_field(findings, "close_date") == planted("deal_missing_close_date")


def test_severity_follows_the_field_tier(sample_data, config):
    """Critical fields are HIGH; recommended ones are MEDIUM."""
    findings = MissingFieldsCheck(CONTACTS).run(sample_data, config)
    by_field = {f.field_name: f.severity for f in findings}
    assert by_field["email"] is Severity.HIGH
    assert by_field["owner"] is Severity.HIGH
    assert by_field["lifecycle_stage"] is Severity.HIGH
    assert by_field["country"] is Severity.MEDIUM  # recommended, not critical


def test_findings_explain_what_breaks(sample_data, config):
    findings = MissingFieldsCheck(CONTACTS).run(sample_data, config)
    email_finding = next(f for f in findings if f.field_name == "email")
    # The message carries the business reason, not just "field is empty".
    assert "dedup key" in email_finding.message
    assert email_finding.meta["reason"]


def test_policy_is_configurable_not_hardcoded():
    """Swapping the policy changes what's reported, with no logic change."""
    contacts = (
        "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region,Job Title\n"
        "1,ada@x.com,Sarah Chen,lead,United States,\n"
    )
    data = load_crm_data(contacts, "Record ID,Company name\n", "Record ID,Deal Name\n", as_of="2026-07-30")

    # Default policy doesn't care about job title.
    assert MissingFieldsCheck(CONTACTS).run(data, Config()) == []

    custom = Config(required_fields={CONTACTS: [
        RequiredField("job_title", Severity.LOW, "personalization quality suffers"),
    ]})
    findings = MissingFieldsCheck(CONTACTS).run(data, custom)
    assert [f.record_id for f in findings] == ["1"]
    assert findings[0].severity is Severity.LOW


def test_whitespace_only_counts_as_missing():
    contacts = (
        "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region\n"
        "1,ada@x.com,   ,lead,United States\n"
    )
    data = load_crm_data(contacts, "Record ID,Company name\n", "Record ID,Deal Name\n", as_of="2026-07-30")
    findings = MissingFieldsCheck(CONTACTS).run(data, Config())
    assert _for_field(findings, "owner") == {"1"}
