"""Duplicate detection: contacts by email, companies by domain then name."""

from __future__ import annotations

from engine.checks.duplicates import DuplicateCompaniesCheck, DuplicateContactsCheck
from engine.loader import load_crm_data
from engine.models import Severity


def test_contact_duplicates_match_ground_truth_exactly(sample_data, config, planted, flagged):
    findings = DuplicateContactsCheck().run(sample_data, config)
    assert flagged(findings) == planted("contact_duplicate_email")


def test_contact_duplicates_point_at_the_older_master(sample_data, config):
    findings = DuplicateContactsCheck().run(sample_data, config)
    assert findings, "expected planted duplicates"
    for finding in findings:
        master = finding.meta["master_record_id"]
        # The master is kept, so it must never itself be flagged, and it must be
        # the older (lower-ID) record of the pair.
        assert int(master) < int(finding.record_id)
        assert finding.related_record_ids == (master,)
        assert finding.severity is Severity.HIGH


def test_case_and_whitespace_variants_are_caught():
    """The classic CRM duplicate: same mailbox, different string."""
    contacts = (
        "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region\n"
        "1,ada@x.com,Sarah Chen,lead,United States\n"
        "2,  ADA@X.COM ,Sarah Chen,lead,United States\n"
        "3,grace@x.com,Sarah Chen,lead,United States\n"
    )
    empty_companies = "Record ID,Company name,Domain,Company owner,Industry\n"
    empty_deals = "Record ID,Deal Name,Deal Stage,Amount,Deal owner,Close Date\n"
    data = load_crm_data(contacts, empty_companies, empty_deals, as_of="2026-07-30")

    findings = DuplicateContactsCheck().run(data, None)
    assert {f.record_id for f in findings} == {"2"}


def test_blank_emails_are_not_duplicates_of_each_other():
    """Many records share 'no email'. That's a completeness gap, not a dup."""
    contacts = (
        "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region\n"
        "1,,Sarah Chen,lead,United States\n"
        "2,,Sarah Chen,lead,United States\n"
        "3,,Sarah Chen,lead,United States\n"
    )
    data = load_crm_data(contacts, "Record ID,Company name\n", "Record ID,Deal Name\n", as_of="2026-07-30")
    assert DuplicateContactsCheck().run(data, None) == []


def test_company_duplicates_match_ground_truth_exactly(sample_data, config, planted, flagged):
    findings = DuplicateCompaniesCheck().run(sample_data, config)
    assert flagged(findings) == planted("company_duplicate", "company_name_variant")


def test_company_match_confidence_drives_severity(sample_data, config):
    findings = DuplicateCompaniesCheck().run(sample_data, config)
    by_type = {f.record_id: f for f in findings}
    assert by_type, "expected planted company duplicates"

    for finding in findings:
        if finding.meta["match_type"] == "domain":
            # An identical domain is proof.
            assert finding.severity is Severity.HIGH
        else:
            # A name match after stripping legal suffixes is a strong hint.
            assert finding.severity is Severity.MEDIUM


def test_each_company_reported_once(sample_data, config):
    """A record caught by domain must not be reported again by name."""
    findings = DuplicateCompaniesCheck().run(sample_data, config)
    ids = [f.record_id for f in findings]
    assert len(ids) == len(set(ids))
