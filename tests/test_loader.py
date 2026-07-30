"""
Tests for the loader: header normalization, type coercion, blank handling,
and as_of inference. These guard the contract every downstream check relies on
— that it can trust internal column names, real dtypes, and NA for blanks.
"""

from __future__ import annotations

import pandas as pd
import pytest

from engine.loader import load_crm_data
from engine.models import COMPANIES, CONTACTS, DEALS

CONTACTS_CSV = """Record ID,First Name,Last Name,Email,Contact owner,Lifecycle Stage,Country/Region,Create Date,Last Activity Date
5001,Ada,Lovelace,ada@x.com,Sarah Chen,lead,United States,2024-01-01,2026-07-01
5002,Grace,Hopper,,Tom Becker,,United States,2024-02-01,2026-06-01
"""

# Deliberately awkward headers to prove tolerant matching: different casing,
# "Domain" instead of "Company Domain Name", "Company Owner" capitalization.
COMPANIES_CSV = """Record ID,Company Name,Domain,Company Owner,Industry,Create Date,Last Activity Date
1001,Acme Inc,acme.com,Sarah Chen,Software,2023-01-01,2026-05-01
1002,Globex,,Ingrid Larsen,,2023-02-01,2026-04-01
"""

DEALS_CSV = """Record ID,Deal Name,Deal Stage,Amount,Deal owner,Close Date,Create Date,Last Activity Date
9001,Acme - New Business,qualifiedtobuy,25000,Sarah Chen,2026-12-01,2025-01-01,2026-05-15
9002,Globex - Renewal,contractsent,,Ingrid Larsen,2026-11-01,2025-02-01,2026-03-01
"""


@pytest.fixture
def data():
    return load_crm_data(CONTACTS_CSV, COMPANIES_CSV, DEALS_CSV)


def test_columns_are_normalized(data):
    assert "owner" in data.contacts.columns
    assert "lifecycle_stage" in data.contacts.columns
    assert "domain" in data.companies.columns          # from raw "Domain"
    assert "amount" in data.deals.columns


def test_record_ids_are_clean_strings(data):
    assert list(data.contacts["record_id"]) == ["5001", "5002"]
    # Values must be plain strings (identifiers), not ints/floats — regardless
    # of whether pandas backs the column with object or its newer StringDtype.
    assert all(isinstance(v, str) for v in data.contacts["record_id"])


def test_blanks_become_na(data):
    # Contact 5002 has no email and no lifecycle stage.
    row = data.contacts.set_index("record_id").loc["5002"]
    assert pd.isna(row["email"])
    assert pd.isna(row["lifecycle_stage"])
    # Company 1002 has no domain and no industry.
    crow = data.companies.set_index("record_id").loc["1002"]
    assert pd.isna(crow["domain"])
    assert pd.isna(crow["industry"])


def test_dates_and_numbers_are_typed(data):
    assert pd.api.types.is_datetime64_any_dtype(data.contacts["last_activity_date"])
    assert pd.api.types.is_datetime64_any_dtype(data.deals["close_date"])
    assert pd.api.types.is_numeric_dtype(data.deals["amount"])
    # Missing amount coerces to NaN, present one is the real number.
    amounts = data.deals.set_index("record_id")["amount"]
    assert amounts["9001"] == 25000
    assert pd.isna(amounts["9002"])


def test_missing_optional_columns_are_created(data):
    # None of the CSVs include phone/job_title/lead_status — loader adds them.
    for col in ("phone", "job_title", "lead_status"):
        assert col in data.contacts.columns


def test_as_of_ignores_future_close_dates(data):
    # Latest backward-looking date is contact 5001's activity 2026-07-01.
    # Deal close dates in Dec 2026 must NOT push as_of forward.
    assert data.as_of == pd.Timestamp("2026-07-01")


def test_explicit_as_of_overrides(data):
    override = load_crm_data(CONTACTS_CSV, COMPANIES_CSV, DEALS_CSV, as_of="2026-07-30")
    assert override.as_of == pd.Timestamp("2026-07-30")


def test_frame_lookup_by_object_type(data):
    assert data.frame(CONTACTS) is data.contacts
    assert data.frame(COMPANIES) is data.companies
    assert data.frame(DEALS) is data.deals
    with pytest.raises(ValueError):
        data.frame("widgets")
