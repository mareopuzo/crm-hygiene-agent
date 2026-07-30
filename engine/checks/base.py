"""
Shared helpers for checks.

Checks stay small and declarative by pushing the fiddly bits — email
normalization, company-name canonicalization, date math — down here where they
can be tested once and reused. Nothing in this module touches config or emits
Findings; it's pure data manipulation.

Note on `revenue_impact_usd`: checks deliberately leave it at 0.0. Detection and
costing are separate concerns — the scoring layer owns the dollar model so it
lives in one auditable place rather than being scattered across ten checks.
"""

from __future__ import annotations

import re

import pandas as pd

# A pragmatic syntax check. Not RFC-complete on purpose: the goal is catching
# obviously broken addresses, not litigating the standard's exotic corners.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")

# TLDs we accept without comment. Anything outside this set is treated as
# suspect — which is why the sample generator only ever uses legitimate TLDs
# for clean records.
KNOWN_TLDS = frozenset({
    "com", "org", "net", "io", "co", "ai", "dev", "app", "biz", "info", "me",
    "xyz", "cloud", "tech", "us", "uk", "ca", "de", "fr", "es", "it", "nl",
    "se", "no", "fi", "dk", "ie", "ch", "at", "be", "pt", "pl", "cz", "gr",
    "au", "nz", "sg", "jp", "cn", "in", "hk", "kr", "my", "ph", "th", "id",
    "br", "mx", "ar", "cl", "ae", "sa", "za", "ng", "ke", "eu", "edu", "gov",
})

# The TLDs people actually fat-finger. Used to phrase a helpful message
# ("likely typo of .com") when an unknown TLD is one edit away from a common one.
_COMMON_TLDS = ("com", "net", "org", "io", "co")

# Legal-entity noise stripped before comparing company names, so
# "Acme Inc." and "Acme, Incorporated" collapse to the same key.
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "llp", "ltd", "limited", "gmbh", "ag", "bv",
    "nv", "sa", "sas", "srl", "spa", "plc", "pty", "corp", "corporation",
    "company", "co", "group", "holdings", "holding", "partners", "the",
})


def normalize_email(value) -> str | None:
    """
    Canonical form used for duplicate matching: trimmed and lower-cased.

    This is what makes "  ADA@X.COM " and "ada@x.com" collide — the single most
    common real-world duplicate pattern, since CRMs treat them as distinct
    strings but they're the same mailbox.
    """
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().lower()
    return text or None


def email_parts(value) -> tuple[str, str] | None:
    """(local_part, domain) from an email, or None if it isn't split-able."""
    email = normalize_email(value)
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    if not local or not domain:
        return None
    return local, domain


def is_syntactically_valid_email(value) -> bool:
    email = normalize_email(value)
    return bool(email and _EMAIL_RE.match(email))


def email_tld(value) -> str | None:
    parts = email_parts(value)
    if not parts:
        return None
    _, domain = parts
    _, _, tld = domain.rpartition(".")
    return tld or None


def suspicious_tld(value) -> tuple[str, str | None] | None:
    """
    Flag an unrecognized TLD, with the common TLD it was probably meant to be.

    Returns (tld, likely_intended) or None if the TLD looks fine. `.co` is a
    real TLD one edit from `.com`, which is exactly why the allowlist is checked
    first — otherwise every Colombian domain would be a false positive.
    """
    tld = email_tld(value)
    if not tld or tld in KNOWN_TLDS:
        return None
    for candidate in _COMMON_TLDS:
        if _edit_distance(tld, candidate) == 1:
            return tld, candidate
    return tld, None


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance. Inputs are TLDs, so the naive DP is plenty fast."""
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,          # deletion
                current[j - 1] + 1,       # insertion
                previous[j - 1] + (ca != cb),  # substitution
            ))
        previous = current
    return previous[-1]


def normalize_company_name(value) -> str | None:
    """
    Canonical company key: lower-cased, punctuation-free, legal suffixes removed.

    "Vandelay115 LLC" and "Vandelay115, Inc." both reduce to "vandelay115",
    which is how the same company entered twice under different legal styling
    gets caught even when the domains differ.
    """
    if value is None or pd.isna(value):
        return None
    text = re.sub(r"[^a-z0-9\s]", " ", str(value).lower())
    tokens = [t for t in text.split() if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens) or None


def normalize_domain(value) -> str | None:
    """Strip scheme, www., path and casing so domains compare cleanly."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    text = text.split("/")[0].strip()
    return text or None


def days_since(series: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    """Whole days between each date and `as_of`. NaT stays NaN."""
    return (as_of - pd.to_datetime(series, errors="coerce")).dt.days


def blank(series: pd.Series) -> pd.Series:
    """
    Boolean mask of 'effectively empty' values.

    The loader already turns "" into NA, so this is mostly isna() — but it also
    catches whitespace-only strings that slipped through a non-loader path.
    """
    filled = series.astype("object").where(series.notna(), None)
    return filled.map(lambda v: v is None or str(v).strip() == "")
