"""
Fix files: the artefacts you actually act on.

The safety properties matter more than the contents here. An import file is a
*write* to someone's CRM, so the tests pin the rules that keep the blast radius
small: only the columns being changed, only mechanical corrections, and nothing
pre-filled that the tool had to guess at.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from engine.config import default_config
from engine.fixes import build_fix_files, bundle_readme, bundle_zip
from engine.loader import load_crm_data
from engine.report import build_report


@pytest.fixture(scope="module")
def fixes(sample_data, config):
    report = build_report(sample_data, config)
    return build_fix_files(sample_data, report.findings)


def _named(fixes, fragment):
    return next(f for f in fixes if fragment in f.filename)


# --------------------------------------------------------------------------- #
# Safety rules
# --------------------------------------------------------------------------- #

def test_import_files_carry_only_the_id_and_the_changed_column(fixes):
    """A wide import file is a wide blast radius."""
    for fix in fixes:
        if fix.kind != "import":
            continue
        assert "Record ID" in fix.frame.columns
        assert len(fix.frame.columns) == 2, f"{fix.filename} would write extra columns"


def test_records_queued_for_merging_are_left_out_of_email_normalization(sample_data, config):
    """
    Normalizing a duplicate's email would make it collide with the master it's
    about to be merged into. Merge first, normalize after.
    """
    report = build_report(sample_data, config)
    fixes = build_fix_files(sample_data, report.findings)
    duplicates = {f.record_id for f in report.findings if f.check_id == "duplicate_contacts"}
    normalized = set(_named(fixes, "email_normalized").frame["Record ID"])
    assert not (normalized & duplicates)


def test_normalized_emails_are_genuinely_canonical(fixes):
    emails = _named(fixes, "email_normalized").frame["Email"]
    assert all(e == e.strip().lower() for e in emails)


def test_country_standardization_only_lists_rows_that_change(sample_data, fixes):
    fix = _named(fixes, "country_standardized")
    current = dict(zip(sample_data.contacts["record_id"], sample_data.contacts["country"]))
    for _, row in fix.frame.iterrows():
        assert str(current[row["Record ID"]]).strip() != row["Country/Region"]


def test_typo_suggestions_are_review_only_and_never_pre_confirmed(fixes):
    fix = _named(fixes, "review_email_typos")
    assert fix.kind == "review"
    assert (fix.frame["Confirmed? (yes/no)"] == "").all()
    assert "NOT ready to import" in fix.instructions


def test_a_suggestion_is_only_offered_when_it_is_confident(sample_data, config):
    """
    '.cim' is one edit from '.com' so it gets a suggestion; '.nte' is two from
    '.net' and must not. Guessing wrong here would corrupt a real address.
    """
    report = build_report(sample_data, config)
    fixes = build_fix_files(sample_data, report.findings)
    suggested = _named(fixes, "review_email_typos").frame
    assert len(suggested) > 0
    assert all(s for s in suggested["Suggested Email"]), "a blank suggestion is not a suggestion"

    invalid = [f for f in report.findings
               if f.meta.get("reason") == "suspicious_tld"]
    unconfident = [f for f in invalid if not f.meta.get("likely_tld")]
    offered = set(suggested["Record ID"])
    assert not ({f.record_id for f in unconfident} & offered)


def test_merge_worklist_is_not_an_import(fixes):
    fix = _named(fixes, "merge_duplicates")
    assert fix.kind == "worklist"
    assert "Do NOT import" in fix.instructions


def test_merge_worklist_keeps_the_older_record(fixes):
    frame = _named(fixes, "merge_duplicates").frame
    assert len(frame) > 0
    for _, row in frame.iterrows():
        assert int(row["Into This Record ID"]) < int(row["Merge This Record ID"])


def test_missing_field_template_marks_untouched_fields_rather_than_blanking_them(fixes):
    """
    An empty cell in a HubSpot import can clear a value. Fields that were never
    the problem are marked 'n/a' so a careless import can't wipe them.
    """
    frame = _named(fixes, "template_contacts_missing_fields").frame
    values = frame.drop(columns=["Record ID"]).to_numpy().ravel()
    assert set(values) <= {"", "n/a"}
    assert "n/a" in set(values)
    assert "DELETE every cell still reading 'n/a'" in _named(
        fixes, "template_contacts_missing_fields").instructions


def test_pipeline_worklist_asks_for_a_decision_per_row(fixes):
    frame = _named(fixes, "pipeline_review").frame
    assert (frame["Decision (chase / re-date / close)"] == "").all()
    assert set(frame["Issue"]) <= {"Stale", "Past close date"}


def test_routing_worklist_does_not_pick_the_new_owner(fixes):
    frame = _named(fixes, "reassign_owners").frame
    assert (frame["New Owner (fill in)"] == "").all()
    assert (frame["Owner Covers"] != frame["Should Be Owned In"]).all()


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def test_every_generated_file_has_rows(fixes):
    assert fixes
    assert all(f.row_count > 0 for f in fixes)


def test_every_file_explains_what_to_do_with_it(fixes):
    for fix in fixes:
        assert fix.kind in {"import", "review", "worklist"}
        assert fix.title and fix.summary and fix.instructions


def test_bundle_contains_a_readme_and_every_file(fixes):
    archive = zipfile.ZipFile(io.BytesIO(bundle_zip(fixes)))
    names = set(archive.namelist())
    assert "README.txt" in names
    assert {f.filename for f in fixes} <= names
    readme = archive.read("README.txt").decode()
    assert "backup" in readme.lower()
    assert "cannot be undone" in readme.lower()


def test_readme_lists_every_file(fixes):
    readme = bundle_readme(fixes)
    for fix in fixes:
        assert fix.filename in readme


def test_a_clean_crm_produces_no_fix_files(config):
    clean = load_crm_data(
        "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region,Last Activity Date\n"
        "1,ada@acme.com,Sarah Chen,lead,United States,2026-07-20\n",
        "Record ID,Company name,Domain,Company owner,Industry,Country/Region\n"
        "10,Acme Inc,acme.com,Sarah Chen,Software,United States\n",
        "Record ID,Deal Name,Deal Stage,Amount,Deal owner,Close Date,Last Activity Date\n"
        "100,Acme - New,qualifiedtobuy,25000,Sarah Chen,2026-12-01,2026-07-25\n",
        as_of="2026-07-30",
    )
    report = build_report(clean, config)
    assert build_fix_files(clean, report.findings) == []
