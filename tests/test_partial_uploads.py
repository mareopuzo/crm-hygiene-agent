"""
Uploading only some of the three objects must work.

The app tells users "any subset works", so an absent object type arrives as a
header-only CSV and every check has to cope with an empty frame. This was a
real crash: `blank()` returned an object-dtype mask on an empty frame, and
pandas reads an object-dtype key as column labels rather than a row filter, so
the missing-fields check raised KeyError('record_id') instead of simply finding
nothing.
"""

from __future__ import annotations

import pandas as pd
import pytest

from engine.checks import build_checks, run_all_checks
from engine.checks.base import blank
from engine.config import default_config
from engine.loader import load_crm_data
from engine.report import build_report

EMPTY_CONTACTS = "Record ID,Email\n"
EMPTY_COMPANIES = "Record ID,Company name\n"
EMPTY_DEALS = "Record ID,Deal Name\n"

CONTACTS = (
    "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region,Last Activity Date\n"
    "1,ada@acme.com,Sarah Chen,lead,United States,2026-07-20\n"
    "2,,Sarah Chen,lead,United States,2026-07-21\n"
)
DEALS = (
    "Record ID,Deal Name,Deal Stage,Amount,Deal owner,Close Date,Last Activity Date\n"
    "101,Acme - New,qualifiedtobuy,50000,Sarah Chen,2026-12-01,2026-07-20\n"
)


def test_blank_returns_a_boolean_mask_even_when_empty():
    empty = pd.Series([], dtype=object)
    assert blank(empty).dtype == bool
    assert blank(pd.Series([None, "x", "  "])).dtype == bool


def test_blank_mask_filters_an_empty_frame_without_raising():
    frame = pd.DataFrame({"record_id": pd.Series([], dtype=object),
                          "domain": pd.Series([], dtype=object)})
    assert frame[blank(frame["domain"])].empty


@pytest.mark.parametrize(
    "contacts,companies,deals",
    [
        (CONTACTS, EMPTY_COMPANIES, DEALS),      # no companies — the crash case
        (CONTACTS, EMPTY_COMPANIES, EMPTY_DEALS),  # contacts only
        (EMPTY_CONTACTS, EMPTY_COMPANIES, DEALS),  # deals only
        (EMPTY_CONTACTS, EMPTY_COMPANIES, EMPTY_DEALS),  # nothing at all
    ],
)
def test_every_check_survives_missing_objects(contacts, companies, deals):
    data = load_crm_data(contacts, companies, deals, as_of="2026-07-25")
    findings = run_all_checks(data, default_config())
    known = {c.id for c in build_checks()}
    assert all(f.check_id in known for f in findings)


def test_report_builds_from_a_partial_upload():
    data = load_crm_data(CONTACTS, EMPTY_COMPANIES, DEALS, as_of="2026-07-25")
    report = build_report(data, default_config())
    # The one planted problem (contact 2 has no email) is still found.
    assert {f.record_id for f in report.findings if f.check_id == "missing_fields_contacts"} == {"2"}
    assert report.score.total_records == 3
    assert report.headline


def test_totally_empty_upload_scores_100_rather_than_crashing():
    data = load_crm_data(EMPTY_CONTACTS, EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-25")
    report = build_report(data, default_config())
    assert report.findings == []
    assert report.score.overall == 100.0
    assert report.cost.direct_cost_usd == 0.0
