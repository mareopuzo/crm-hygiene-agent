"""
The built-in country → region atlas, and the name-matching that makes it usable
on real CRM exports.

Why this exists: territory routing needs two mappings — which region each
country belongs to, and which region each owner covers. Only the second is
specific to a company. Asking a user to type out every country in EMEA before
they can run a single check would guarantee nobody ever turns the check on, so
the atlas ships with the tool and the user supplies only their own team.

Real exports spell countries inconsistently — "USA", "U.S.", "United States",
"us" are one country typed four ways. Matching is done on a normalized key
(casefolded, punctuation stripped) with an alias table on top, so all four land
in the same place instead of quietly falling through as "unknown".

The mapping for the ten countries used by the sample generator deliberately
matches the generator's own assignment; changing one without the other would
make the demo contradict itself.
"""

from __future__ import annotations

import re

# The regions offered by default. Companies with a different cut (by product,
# by segment) can supply their own map entirely — see Config.region_countries.
DEFAULT_REGIONS = ("North America", "LATAM", "EMEA", "APAC")

DEFAULT_REGION_COUNTRIES: dict[str, list[str]] = {
    "North America": [
        "United States", "Canada", "Mexico",
    ],
    "LATAM": [
        "Brazil", "Argentina", "Chile", "Colombia", "Peru", "Uruguay",
        "Paraguay", "Bolivia", "Ecuador", "Venezuela", "Costa Rica", "Panama",
        "Guatemala", "Honduras", "El Salvador", "Nicaragua", "Dominican Republic",
        "Puerto Rico", "Jamaica", "Trinidad and Tobago",
    ],
    "EMEA": [
        # Europe
        "United Kingdom", "Ireland", "France", "Germany", "Spain", "Portugal",
        "Italy", "Netherlands", "Belgium", "Luxembourg", "Switzerland",
        "Austria", "Denmark", "Sweden", "Norway", "Finland", "Iceland",
        "Poland", "Czechia", "Slovakia", "Hungary", "Romania", "Bulgaria",
        "Greece", "Croatia", "Slovenia", "Serbia", "Estonia", "Latvia",
        "Lithuania", "Ukraine", "Malta", "Cyprus",
        # Middle East
        "Turkey", "Israel", "United Arab Emirates", "Saudi Arabia", "Qatar",
        "Kuwait", "Bahrain", "Oman", "Jordan", "Lebanon",
        # Africa
        "Egypt", "Morocco", "Tunisia", "Algeria", "Nigeria", "Ghana", "Kenya",
        "South Africa", "Tanzania", "Uganda", "Ethiopia", "Rwanda", "Senegal",
        "Ivory Coast", "Cameroon", "Zambia", "Zimbabwe", "Botswana", "Mauritius",
    ],
    "APAC": [
        "Australia", "New Zealand", "Japan", "China", "Hong Kong", "Taiwan",
        "South Korea", "Singapore", "Malaysia", "Indonesia", "Thailand",
        "Vietnam", "Philippines", "India", "Pakistan", "Bangladesh",
        "Sri Lanka", "Nepal", "Cambodia", "Laos", "Myanmar", "Mongolia", "Fiji",
    ],
}

# Spellings that should resolve to a canonical country above. Keys are already
# normalized, so add new entries in normalized form.
_COUNTRY_ALIASES: dict[str, str] = {
    "us": "United States",
    "usa": "United States",
    "u s": "United States",
    "u s a": "United States",
    "america": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "u k": "United Kingdom",
    "great britain": "United Kingdom",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
    "uae": "United Arab Emirates",
    "u a e": "United Arab Emirates",
    "emirates": "United Arab Emirates",
    "holland": "Netherlands",
    "the netherlands": "Netherlands",
    "czech republic": "Czechia",
    "korea": "South Korea",
    "republic of korea": "South Korea",
    "cote d ivoire": "Ivory Coast",
    "cote divoire": "Ivory Coast",
    "deutschland": "Germany",
    "espana": "Spain",
    "brasil": "Brazil",
    "russia": "EMEA_UNMAPPED",  # deliberately unmapped: sanctions/territory varies
}


def normalize_country_key(value) -> str | None:
    """
    Collapse a country to a match key: casefolded, punctuation stripped.

    "U.S.A." and "usa" both become "u s a", which the alias table resolves to
    United States.
    """
    if value is None:
        return None
    text = re.sub(r"[^a-z0-9\s]", " ", str(value).casefold())
    text = " ".join(text.split())
    return text or None


def canonical_country(value) -> str | None:
    """The atlas's spelling of a country, or None if we don't recognize it."""
    key = normalize_country_key(value)
    if not key:
        return None
    alias = _COUNTRY_ALIASES.get(key)
    if alias:
        return None if alias == "EMEA_UNMAPPED" else alias
    for countries in DEFAULT_REGION_COUNTRIES.values():
        for country in countries:
            if normalize_country_key(country) == key:
                return country
    return None


def build_country_region_index(region_countries: dict[str, list[str]]) -> dict[str, str]:
    """
    Invert a region → countries map into a lookup keyed by normalized country.

    Aliases are folded in for any canonical country the caller's map contains,
    so a custom map still benefits from "USA" resolving to "United States".
    """
    index: dict[str, str] = {}
    for region, countries in region_countries.items():
        for country in countries:
            key = normalize_country_key(country)
            if key:
                index.setdefault(key, region)

    for alias_key, canonical in _COUNTRY_ALIASES.items():
        if canonical == "EMEA_UNMAPPED":
            continue
        canonical_key = normalize_country_key(canonical)
        if canonical_key in index:
            index.setdefault(alias_key, index[canonical_key])
    return index


def normalize_person_key(value) -> str | None:
    """
    Match key for an owner name: casefolded, whitespace collapsed.

    HubSpot exports the same rep as "Sarah Chen" and occasionally " sarah chen",
    and a mismatch here silently disables routing for that rep — so names are
    compared on this key rather than raw text.
    """
    if value is None:
        return None
    text = " ".join(str(value).split()).casefold()
    return text or None


def build_owner_region_index(owner_regions: dict[str, str]) -> dict[str, str]:
    """Owner → region, keyed for tolerant matching."""
    index: dict[str, str] = {}
    for owner, region in owner_regions.items():
        key = normalize_person_key(owner)
        if key and region:
            index[key] = region
    return index
