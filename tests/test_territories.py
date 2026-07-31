"""
The territory map: the built-in country atlas, and the rep map a user supplies.

The failure this guards against is silence. If an owner name or a country
spelling doesn't match, the routing check skips that record — it doesn't raise
anything. So a portal that exports "USA" instead of "United States" would get a
clean routing report for entirely the wrong reason.
"""

from __future__ import annotations

import pytest

from engine.checks.routing import TerritoryRoutingCheck
from engine.config import SAMPLE_REGION_COUNTRIES, Config, build_config, default_config
from engine.loader import load_crm_data
from engine.models import CONTACTS
from engine.territories import (
    DEFAULT_REGION_COUNTRIES,
    DEFAULT_REGIONS,
    build_country_region_index,
    canonical_country,
    normalize_country_key,
    normalize_person_key,
)

EMPTY_COMPANIES = "Record ID,Company name\n"
EMPTY_DEALS = "Record ID,Deal Name\n"


def _contacts(*rows: str) -> str:
    header = "Record ID,Email,Contact owner,Lifecycle Stage,Country/Region\n"
    return header + "".join(r + "\n" for r in rows)


# --------------------------------------------------------------------------- #
# The atlas
# --------------------------------------------------------------------------- #

def test_atlas_agrees_with_the_sample_generator():
    """
    The bundled sample assigns ten countries to regions. The shipped atlas must
    agree, or the demo would contradict its own ground truth.
    """
    index = build_country_region_index(DEFAULT_REGION_COUNTRIES)
    for region, countries in SAMPLE_REGION_COUNTRIES.items():
        for country in countries:
            assert index[normalize_country_key(country)] == region


def test_every_atlas_country_is_unique_to_one_region():
    seen: dict[str, str] = {}
    for region, countries in DEFAULT_REGION_COUNTRIES.items():
        for country in countries:
            key = normalize_country_key(country)
            assert key not in seen, f"{country} in both {seen.get(key)} and {region}"
            seen[key] = region


def test_regions_offered_to_the_user_all_exist_in_the_atlas():
    assert set(DEFAULT_REGIONS) == set(DEFAULT_REGION_COUNTRIES)


@pytest.mark.parametrize("spelling", ["USA", "usa", "U.S.A.", "U.S.", "united states", "America"])
def test_common_country_spellings_resolve(spelling):
    assert canonical_country(spelling) == "United States"


@pytest.mark.parametrize("spelling", ["UK", "u.k.", "Great Britain", "England", "united kingdom"])
def test_uk_spellings_resolve(spelling):
    assert canonical_country(spelling) == "United Kingdom"


def test_unknown_country_resolves_to_nothing_rather_than_guessing():
    assert canonical_country("Atlantis") is None
    assert canonical_country("") is None
    assert canonical_country(None) is None


def test_owner_names_match_despite_casing_and_padding():
    assert normalize_person_key("  Sarah   Chen ") == normalize_person_key("sarah chen")


# --------------------------------------------------------------------------- #
# Routing against a user-supplied rep map
# --------------------------------------------------------------------------- #

def test_a_users_own_reps_are_routed_once_mapped():
    """The whole point: a portal whose owners the tool has never seen."""
    data = load_crm_data(
        _contacts(
            "1,a@x.com,John Smith,lead,Germany",       # NA rep, EMEA country -> flagged
            "2,b@x.com,Aisha Bello,lead,Nigeria",      # EMEA rep, EMEA country -> fine
        ),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    config = build_config(owner_regions={"John Smith": "North America", "Aisha Bello": "EMEA"})
    findings = TerritoryRoutingCheck(CONTACTS).run(data, config)
    assert {f.record_id for f in findings} == {"1"}


def test_unmapped_reps_are_skipped_not_guessed():
    data = load_crm_data(
        _contacts("1,a@x.com,John Smith,lead,Germany"),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    # Routing is on, but this rep has no region — silence, not a false positive.
    assert TerritoryRoutingCheck(CONTACTS).run(data, build_config(owner_regions={})) == []


def test_messy_country_spellings_still_route():
    data = load_crm_data(
        _contacts(
            "1,a@x.com,John Smith,lead,USA",     # rep covers EMEA -> mismatch
            "2,b@x.com,John Smith,lead,U.K.",    # rep covers EMEA -> fine
        ),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    config = build_config(owner_regions={"John Smith": "EMEA"})
    findings = TerritoryRoutingCheck(CONTACTS).run(data, config)
    assert {f.record_id for f in findings} == {"1"}


def test_owner_name_casing_in_the_map_does_not_break_matching():
    data = load_crm_data(
        _contacts("1,a@x.com,John Smith,lead,Germany"),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    config = build_config(owner_regions={"  john   smith  ": "North America"})
    assert len(TerritoryRoutingCheck(CONTACTS).run(data, config)) == 1


def test_countries_outside_the_atlas_are_skipped():
    data = load_crm_data(
        _contacts("1,a@x.com,John Smith,lead,Atlantis"),
        EMPTY_COMPANIES, EMPTY_DEALS, as_of="2026-07-30",
    )
    config = build_config(owner_regions={"John Smith": "EMEA"})
    assert TerritoryRoutingCheck(CONTACTS).run(data, config) == []


# --------------------------------------------------------------------------- #
# Config wiring
# --------------------------------------------------------------------------- #

def test_atlas_ships_populated_but_rep_map_does_not():
    config = Config()
    assert config.region_countries, "countries should be built in"
    assert config.owner_regions == {}, "reps cannot be known in advance"
    assert not config.territory_routing_enabled


def test_blank_region_assignments_are_dropped():
    config = build_config(owner_regions={"John Smith": "", "Aisha Bello": "EMEA"})
    assert config.owner_regions == {"Aisha Bello": "EMEA"}


def test_switching_routing_off_beats_a_supplied_map():
    config = build_config(territory_routing=False, owner_regions={"John Smith": "EMEA"})
    assert not config.territory_routing_enabled


def test_sample_config_still_routes_out_of_the_box(sample_data, planted, flagged):
    findings = TerritoryRoutingCheck(CONTACTS).run(sample_data, default_config())
    assert flagged(findings) == planted("contact_bad_routing")
