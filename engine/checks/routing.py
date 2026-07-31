"""
Territory routing: records assigned to an owner who doesn't cover their region.

Scope note — this check reports *mismatches only*, not unowned records. A blank
owner is already a Critical finding from the missing-fields policy, and counting
it twice would inflate both the issue count and the score penalty for a single
underlying problem. Every check owning exactly one failure mode is what keeps
the health score honest.

The territory map lives in config and is opt-in: orgs that route by product,
segment, or round-robin rather than geography simply leave it empty and this
check no-ops instead of producing nonsense.
"""

from __future__ import annotations

from engine.checks.base import blank
from engine.models import COMPANIES, CONTACTS, CRMData, Finding, Severity
from engine.territories import (
    build_country_region_index,
    build_owner_region_index,
    normalize_country_key,
    normalize_person_key,
)

_OBJECT_LABELS = {CONTACTS: "Contact", COMPANIES: "Company"}


class TerritoryRoutingCheck:
    """One instance per routable object type (contacts, companies)."""

    def __init__(self, object_type: str) -> None:
        self.object_type = object_type
        self.id = f"routing_{object_type}"
        self.name = f"Territory Routing — {_OBJECT_LABELS.get(object_type, object_type).title()}"

    def run(self, data: CRMData, config) -> list[Finding]:
        if not config.territory_routing_enabled:
            return []

        df = data.frame(self.object_type)
        label = _OBJECT_LABELS.get(self.object_type, self.object_type)

        # Both sides are matched on normalized keys rather than raw text, so a
        # portal that exports "USA" and another that exports "United States"
        # resolve identically. A silent match failure here doesn't raise an
        # error — it just quietly disables the check for that record — so the
        # tolerance is doing real work.
        country_to_region = build_country_region_index(config.region_countries)
        owner_to_region = build_owner_region_index(config.owner_regions)

        # Only rows where both sides of the comparison are present are
        # evaluable; the rest belong to the missing-fields check.
        evaluable = df[~blank(df["owner"]) & ~blank(df["country"])]

        findings: list[Finding] = []
        for record_id, owner, country in zip(
            evaluable["record_id"], evaluable["owner"], evaluable["country"]
        ):
            owner_region = owner_to_region.get(normalize_person_key(owner))
            record_region = country_to_region.get(normalize_country_key(country))

            # An owner or country we have no mapping for isn't a mismatch —
            # it's an unknown, and guessing would manufacture false positives.
            if owner_region is None or record_region is None:
                continue

            if owner_region != record_region:
                findings.append(Finding(
                    check_id=self.id,
                    object_type=self.object_type,
                    record_id=record_id,
                    severity=Severity.HIGH,
                    message=(
                        f"{label} is in {country} ({record_region}) but owned by {owner}, "
                        f"who covers {owner_region}. Reassign to a {record_region} owner."
                    ),
                    field_name="owner",
                    meta={
                        "owner": str(owner),
                        "owner_region": owner_region,
                        "record_country": str(country),
                        "record_region": record_region,
                    },
                ))
        return findings
