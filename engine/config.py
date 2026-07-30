"""
Engine configuration — every threshold and policy lives here, nothing is
hardcoded inside a check.

Why a config object instead of magic numbers? Two reasons:

1. These are RevOps *judgment calls*, not universal truths. "Stale = 30 days"
   is right for a fast transactional motion and wrong for enterprise. Making
   them configurable is the honest way to model that, and it lets someone
   playing with the tool tune it to their own motion without touching logic.

2. It keeps the checks pure and declarative — a check reads `config.stale_deal_days`
   rather than owning the policy, so the policy is auditable in one place.

The defaults below are the ones locked for this build:
  - Stale deal      : 30 days with no activity
  - Decayed contact : 6 months (183 days) with no activity
  - Required fields : tiered Critical (hard) vs Recommended (soft)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.models import COMPANIES, CONTACTS, DEALS, Severity


@dataclass
class RequiredField:
    """
    One required-field policy: which column, and how bad a blank is.

    Critical (HIGH) = a core revenue process cannot run without it.
    Recommended (MEDIUM/LOW) = the process runs but quality degrades.
    """

    column: str          # engine-internal normalized column name
    severity: Severity
    reason: str          # what breaks — surfaced in the finding message


# --------------------------------------------------------------------------- #
# Required-field policy per object (the tiers agreed in the case study).
# Column names are the engine's *normalized* names (see engine/loader.py),
# not the raw HubSpot headers.
# --------------------------------------------------------------------------- #

DEFAULT_REQUIRED_FIELDS: dict[str, list[RequiredField]] = {
    CONTACTS: [
        RequiredField("email", Severity.HIGH, "no email means no outreach and no dedup key"),
        RequiredField("owner", Severity.HIGH, "unowned contacts are accountable to no one and rot"),
        RequiredField("lifecycle_stage", Severity.HIGH, "can't score, report the funnel, or nurture correctly"),
        RequiredField("country", Severity.MEDIUM, "territory routing can't place this contact"),
    ],
    COMPANIES: [
        RequiredField("domain", Severity.HIGH, "domain is the dedup and enrichment key"),
        RequiredField("owner", Severity.HIGH, "unowned companies are accountable to no one"),
        RequiredField("industry", Severity.MEDIUM, "segmentation and scoring degrade without industry"),
    ],
    DEALS: [
        RequiredField("amount", Severity.HIGH, "a deal with no amount is invisible to the forecast"),
        RequiredField("close_date", Severity.HIGH, "no close date means the forecast can't be time-phased"),
        RequiredField("owner", Severity.HIGH, "unowned deals break accountability and routing"),
    ],
}


@dataclass
class Config:
    """All tunable policy for one engine run."""

    # --- Time-based thresholds ---
    stale_deal_days: int = 30          # open deal with no activity for this long → stale
    decayed_contact_days: int = 183    # ~6 months no activity → decayed

    # --- Required fields ---
    required_fields: dict[str, list[RequiredField]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_REQUIRED_FIELDS.items()}
    )

    # --- Decay / deliverability policy ---
    # Local-parts that indicate a non-personal, deliverability-risky mailbox.
    role_based_localparts: frozenset[str] = frozenset(
        {"info", "sales", "admin", "support", "contact", "hello", "office", "team", "help", "no-reply", "noreply"}
    )

    # --- Routing policy ---
    # Owner -> the region their book covers. Used by the routing check to spot
    # contacts/companies whose country sits outside their owner's region.
    # Empty by default: routing-by-territory is opt-in, because not every org
    # routes geographically. The sample data uses the map below.
    owner_regions: dict[str, str] = field(default_factory=dict)
    region_countries: dict[str, list[str]] = field(default_factory=dict)

    @property
    def territory_routing_enabled(self) -> bool:
        return bool(self.owner_regions and self.region_countries)


# The territory map that matches data/generate_sample.py, provided as a
# convenience so the demo exercises the routing check out of the box.
SAMPLE_OWNER_REGIONS = {
    "Sarah Chen": "North America",
    "Tom Becker": "North America",
    "Ingrid Larsen": "EMEA",
    "Omar Haddad": "EMEA",
    "Mei Tan": "APAC",
}
SAMPLE_REGION_COUNTRIES = {
    "North America": ["United States", "Canada", "Mexico"],
    "EMEA": ["United Kingdom", "Germany", "France", "United Arab Emirates"],
    "APAC": ["Australia", "Singapore", "Japan", "India"],
}


def default_config() -> Config:
    """The locked defaults, with sample territory routing pre-wired."""
    return Config(
        owner_regions=dict(SAMPLE_OWNER_REGIONS),
        region_countries={k: list(v) for k, v in SAMPLE_REGION_COUNTRIES.items()},
    )
