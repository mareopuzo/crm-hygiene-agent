"""
The controls the app exposes must actually drive the engine.

A slider that silently fails to change anything is exactly the bug a demo
doesn't reveal, so the wiring gets tested rather than eyeballed.
"""

from __future__ import annotations

from engine.config import Config, build_config, default_config
from engine.report import build_report


def test_defaults_match_the_locked_policy():
    config = build_config()
    assert config.stale_deal_days == 30
    assert config.decayed_contact_days == 183
    assert config.territory_routing_enabled


def test_thresholds_are_carried_through():
    config = build_config(stale_deal_days=90, decayed_contact_days=365)
    assert config.stale_deal_days == 90
    assert config.decayed_contact_days == 365


def test_routing_can_be_switched_off():
    assert not build_config(territory_routing=False).territory_routing_enabled


def test_required_field_policy_survives_disabling_routing():
    """Turning routing off must not quietly drop the rest of the policy."""
    config = build_config(territory_routing=False)
    assert config.required_fields == Config().required_fields
    assert config.role_based_localparts


def test_building_a_config_does_not_mutate_the_shared_default():
    build_config(stale_deal_days=120, decayed_contact_days=999)
    fresh = default_config()
    assert fresh.stale_deal_days == 30
    assert fresh.decayed_contact_days == 183


def test_six_months_of_slider_equals_the_documented_default():
    """The app's months-to-days conversion must land on the locked 183 days."""
    assert round(6 * 30.44) == Config.decayed_contact_days


def test_loosening_thresholds_reduces_findings(sample_data):
    strict = build_report(sample_data, build_config(stale_deal_days=7, decayed_contact_days=30))
    loose = build_report(sample_data, build_config(stale_deal_days=180, decayed_contact_days=730))
    assert len(strict.findings) > len(loose.findings)
    assert strict.score.overall < loose.score.overall


def test_disabling_routing_removes_only_routing_findings(sample_data):
    with_routing = build_report(sample_data, build_config(territory_routing=True))
    without = build_report(sample_data, build_config(territory_routing=False))

    removed = {f.check_id for f in with_routing.findings} - {f.check_id for f in without.findings}
    assert removed == {"routing_contacts"}
    assert len(without.findings) < len(with_routing.findings)
