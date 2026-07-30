"""
Load raw HubSpot CSV exports into a normalized `CRMData` object.

The rest of the engine must never see a raw HubSpot header. HubSpot column
names are verbose, punctuation-heavy, and vary a little between portals and
export configs ("Contact owner" vs "HubSpot Owner", "Company Domain Name" vs
"Domain"). This module is the single place that absorbs that variation: it maps
whatever headers arrive onto a small, stable internal vocabulary and coerces
dates/numbers, so every check downstream can rely on `df["owner"]`,
`df["last_activity_date"]`, etc.

Design notes:
  - Header matching is fuzzy-but-safe: we compare on a normalized key
    (lowercased, alphanumerics only), so "Last Activity Date",
    "last_activity_date", and "LastActivityDate" all resolve the same.
  - Missing optional columns are created as empty, so checks can assume the
    column exists and only test for blank values.
  - `record_id` is coerced to string — IDs are identifiers, not integers to do
    math on, and keeping them as strings avoids float-ification of blanks.
"""

from __future__ import annotations

import io
import re
from typing import Mapping

import pandas as pd

from engine.models import COMPANIES, CONTACTS, CRMData, DEALS

# --------------------------------------------------------------------------- #
# Header alias maps: normalized_internal_name -> list of accepted raw headers.
# The first form in each list is the canonical HubSpot export header.
# --------------------------------------------------------------------------- #

_CONTACT_ALIASES: dict[str, list[str]] = {
    "record_id": ["Record ID", "Contact ID", "id"],
    "first_name": ["First Name"],
    "last_name": ["Last Name"],
    "email": ["Email", "Email Address"],
    "phone": ["Phone Number", "Phone"],
    "job_title": ["Job Title"],
    "owner": ["Contact owner", "Contact Owner", "HubSpot Owner", "Owner"],
    "lifecycle_stage": ["Lifecycle Stage", "Lifecycle"],
    "lead_status": ["Lead Status"],
    "country": ["Country/Region", "Country"],
    "associated_company_id": ["Associated Company ID", "Primary Associated Company ID"],
    "create_date": ["Create Date", "Created At"],
    "last_activity_date": ["Last Activity Date", "Last Activity"],
}

_COMPANY_ALIASES: dict[str, list[str]] = {
    "record_id": ["Record ID", "Company ID", "id"],
    "company_name": ["Company name", "Company Name", "Name"],
    "domain": ["Company Domain Name", "Domain", "Website"],
    "owner": ["Company owner", "Company Owner", "HubSpot Owner", "Owner"],
    "industry": ["Industry"],
    "num_employees": ["Number of Employees", "Employees"],
    "country": ["Country/Region", "Country"],
    "create_date": ["Create Date", "Created At"],
    "last_activity_date": ["Last Activity Date", "Last Activity"],
}

_DEAL_ALIASES: dict[str, list[str]] = {
    "record_id": ["Record ID", "Deal ID", "id"],
    "deal_name": ["Deal Name", "Name"],
    "deal_stage": ["Deal Stage", "Stage"],
    "amount": ["Amount", "Deal Amount"],
    "owner": ["Deal owner", "Deal Owner", "HubSpot Owner", "Owner"],
    "pipeline": ["Pipeline"],
    "close_date": ["Close Date"],
    "create_date": ["Create Date", "Created At"],
    "last_activity_date": ["Last Activity Date", "Last Activity"],
    "associated_company_id": ["Associated Company ID", "Primary Associated Company ID"],
}

# Which normalized columns are dates / numbers, for type coercion.
_DATE_COLUMNS = {"create_date", "last_activity_date", "close_date"}
_NUMERIC_COLUMNS = {"amount", "num_employees"}

_ALIAS_MAPS = {
    CONTACTS: _CONTACT_ALIASES,
    COMPANIES: _COMPANY_ALIASES,
    DEALS: _DEAL_ALIASES,
}


def _norm_key(header: str) -> str:
    """Collapse a header to lowercase alphanumerics for tolerant matching."""
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def _build_rename_map(columns: list[str], aliases: Mapping[str, list[str]]) -> dict[str, str]:
    """
    Map raw headers present in `columns` to internal names using the alias table.

    Matching is done on normalized keys so spacing/casing/punctuation don't
    matter. If two raw columns map to the same internal name, the first wins.
    """
    # normalized raw header -> raw header (as it appears in the DataFrame)
    present = {_norm_key(c): c for c in columns}
    rename: dict[str, str] = {}
    for internal, raw_options in aliases.items():
        for raw in raw_options:
            hit = present.get(_norm_key(raw))
            if hit is not None and hit not in rename:
                rename[hit] = internal
                break
    return rename


def _normalize_frame(df: pd.DataFrame, object_type: str, as_of: pd.Timestamp) -> pd.DataFrame:
    aliases = _ALIAS_MAPS[object_type]
    rename = _build_rename_map(list(df.columns), aliases)
    out = df.rename(columns=rename)

    # Keep only recognized internal columns; ignore extra HubSpot columns.
    known = [c for c in out.columns if c in aliases]
    out = out[known].copy()

    # Ensure every expected column exists, even if the export omitted it.
    for internal in aliases:
        if internal not in out.columns:
            out[internal] = pd.NA

    # --- Type coercion ---
    out["record_id"] = out["record_id"].apply(_clean_id)

    for col in _DATE_COLUMNS & set(out.columns):
        out[col] = pd.to_datetime(out[col], errors="coerce")

    for col in _NUMERIC_COLUMNS & set(out.columns):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    # Normalize text columns: strip whitespace, turn empty strings into NA so
    # "missing field" checks can test isna() uniformly.
    text_cols = [c for c in out.columns if c not in _DATE_COLUMNS | _NUMERIC_COLUMNS | {"record_id"}]
    for col in text_cols:
        out[col] = out[col].apply(_clean_text)

    # Stash the reference date on each frame's attrs for convenience.
    out.attrs["as_of"] = as_of
    out.attrs["object_type"] = object_type
    return out.reset_index(drop=True)


def _clean_id(value) -> str:
    """IDs to clean strings; drop trailing '.0' from int-parsed ids and blanks."""
    if pd.isna(value):
        return ""
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _clean_text(value):
    """Strip surrounding whitespace; empty → NA so blank checks are uniform."""
    if pd.isna(value):
        return pd.NA
    s = str(value).strip()
    return s if s else pd.NA


def _read_csv(source) -> pd.DataFrame:
    """Read a CSV from a path, file-like object, or raw bytes/str of CSV text."""
    if isinstance(source, (bytes, bytearray)):
        return pd.read_csv(io.BytesIO(source), dtype=str, keep_default_na=False, na_values=[""])
    if isinstance(source, str) and "\n" in source:  # raw CSV text, not a path
        return pd.read_csv(io.StringIO(source), dtype=str, keep_default_na=False, na_values=[""])
    return pd.read_csv(source, dtype=str, keep_default_na=False, na_values=[""])


def load_crm_data(
    contacts_source,
    companies_source,
    deals_source,
    as_of: str | pd.Timestamp | None = None,
) -> CRMData:
    """
    Load and normalize the three HubSpot exports into a `CRMData`.

    Each *_source may be a file path, a file-like object (e.g. a Streamlit
    upload), raw CSV text, or bytes.

    `as_of` fixes the reference "today" for time-based checks. Defaults to the
    latest *backward-looking* date seen (last activity / create date), which
    makes an old exported file audit sensibly instead of looking entirely stale
    against wall-clock time. Pass an explicit date to override.
    """
    raw = {
        CONTACTS: _read_csv(contacts_source),
        COMPANIES: _read_csv(companies_source),
        DEALS: _read_csv(deals_source),
    }

    resolved_as_of = _resolve_as_of(as_of, raw)

    frames = {ot: _normalize_frame(df, ot, resolved_as_of) for ot, df in raw.items()}

    return CRMData(
        contacts=frames[CONTACTS],
        companies=frames[COMPANIES],
        deals=frames[DEALS],
        as_of=resolved_as_of,
    )


def _resolve_as_of(as_of, raw: dict[str, pd.DataFrame]) -> pd.Timestamp:
    if as_of is not None:
        return pd.Timestamp(as_of)

    # Infer from the latest *backward-looking* date present anywhere; fall back
    # to now. Close Date is deliberately excluded: for open deals it's a future
    # forecast date, and using it would push the reference "today" ahead of the
    # real export date, masking stale/decayed records.
    latest = None
    for df in raw.values():
        for col in ("Last Activity Date", "Create Date"):
            if col in df.columns:
                parsed = pd.to_datetime(df[col], errors="coerce")
                col_max = parsed.max()
                if pd.notna(col_max) and (latest is None or col_max > latest):
                    latest = col_max
    return latest if latest is not None else pd.Timestamp.now().normalize()
