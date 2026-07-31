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
from engine.territories import DEFAULT_REGION_COUNTRIES


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
    # Two halves, and only one of them is company-specific:
    #
    #   region_countries — which countries make up each region. Ships populated
    #     from the built-in atlas, because nobody should have to type out EMEA
    #     before they can run a check. Replace it to use a different cut.
    #
    #   owner_regions — which region each rep covers. Necessarily empty: the
    #     tool cannot know a company's team. Until it's supplied the routing
    #     check stays silent rather than guessing.
    owner_regions: dict[str, str] = field(default_factory=dict)
    region_countries: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_REGION_COUNTRIES.items()}
    )

    @property
    def territory_routing_enabled(self) -> bool:
        return bool(self.owner_regions and self.region_countries)


# The rep→region map for the bundled sample data, so the demo exercises the
# routing check out of the box. A real portal's owners are supplied by the user.
SAMPLE_OWNER_REGIONS = {
    "Sarah Chen": "North America",
    "Tom Becker": "North America",
    "Ingrid Larsen": "EMEA",
    "Omar Haddad": "EMEA",
    "Mei Tan": "APAC",
}
# Kept for reference and tests: the exact regions the sample generator uses.
# The shipped atlas assigns these same ten countries the same way.
SAMPLE_REGION_COUNTRIES = {
    "North America": ["United States", "Canada", "Mexico"],
    "EMEA": ["United Kingdom", "Germany", "France", "United Arab Emirates"],
    "APAC": ["Australia", "Singapore", "Japan", "India"],
}


def default_config() -> Config:
    """The locked defaults, with the sample's rep map pre-wired."""
    return Config(owner_regions=dict(SAMPLE_OWNER_REGIONS))


def build_config(
    *,
    stale_deal_days: int = Config.stale_deal_days,
    decayed_contact_days: int = Config.decayed_contact_days,
    territory_routing: bool = True,
    owner_regions: dict[str, str] | None = None,
) -> Config:
    """
    Assemble a Config from the handful of knobs a UI exposes.

    Lives here rather than in the app so the wiring between a control and the
    policy it drives is testable — a slider that silently fails to change
    anything is the kind of bug a demo doesn't reveal.

    `owner_regions` is the one piece the tool can't supply for itself. Passing
    an empty map leaves routing inactive, which is the honest default for a
    portal whose team we've never seen.
    """
    config = Config()
    config.stale_deal_days = stale_deal_days
    config.decayed_contact_days = decayed_contact_days

    if not territory_routing:
        config.owner_regions = {}
    elif owner_regions is not None:
        config.owner_regions = {o: r for o, r in owner_regions.items() if o and r}
    else:
        config.owner_regions = dict(SAMPLE_OWNER_REGIONS)

    return config
