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

        # country -> region, inverted from the config's region -> [countries].
        country_to_region = {
            country: region
            for region, countries in config.region_countries.items()
            for country in countries
        }

        # Only rows where both sides of the comparison are present are
        # evaluable; the rest belong to the missing-fields check.
        evaluable = df[~blank(df["owner"]) & ~blank(df["country"])]

        findings: list[Finding] = []
        for record_id, owner, country in zip(
            evaluable["record_id"], evaluable["owner"], evaluable["country"]
        ):
            owner_region = config.owner_regions.get(str(owner).strip())
            record_region = country_to_region.get(str(country).strip())

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
