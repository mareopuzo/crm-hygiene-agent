"""
Core types shared across the whole engine.

Everything the engine produces flows through these dataclasses. Keeping them in
one dependency-light module (only stdlib + a typing import of pandas) means the
checks, the scoring layer, the report, and the web app all speak the same
language — a `Finding` means the same thing everywhere.

The `Check` protocol is the contract every check implements. Because all checks
share one shape, the runner can treat them uniformly and adding a new check is
just dropping one file into `engine/checks/` and registering it — no changes
anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # avoid importing pandas at type-check time only
    import pandas as pd

    from engine.config import Config


class Severity(IntEnum):
    """
    How much a finding hurts. IntEnum so findings sort by severity naturally
    and the scoring layer can weight by the numeric value.
    """

    LOW = 1       # cosmetic / quality nudge
    MEDIUM = 2    # degrades a process (e.g. missing 'Recommended' field)
    HIGH = 3      # breaks a core revenue process (routing/scoring/forecast)

    @property
    def label(self) -> str:
        return self.name.capitalize()


# Which CRM object a check operates on. Plain strings kept as constants so
# they're greppable and typo-safe without the ceremony of another enum.
CONTACTS = "contacts"
COMPANIES = "companies"
DEALS = "deals"
OBJECT_TYPES = (CONTACTS, COMPANIES, DEALS)


@dataclass(frozen=True)
class Finding:
    """
    One thing wrong with one record.

    Findings are the atomic output of the engine. The scoring layer aggregates
    them into a health score and a dollar estimate; the report groups them; the
    web app renders them. A finding is immutable once produced.
    """

    check_id: str            # e.g. "stale_deals" — ties back to the Check that raised it
    object_type: str         # CONTACTS | COMPANIES | DEALS
    record_id: str           # the CRM Record ID this is about
    severity: Severity
    message: str             # human-readable, specific ("Open deal, no activity in 87 days")
    # Optional structured extras for the report / $-impact model.
    field_name: str | None = None            # which column, when relevant
    revenue_impact_usd: float = 0.0          # estimated $ cost attributed to this finding
    related_record_ids: tuple[str, ...] = () # e.g. the other half of a duplicate pair
    meta: dict = field(default_factory=dict) # anything a specific check wants to attach

    def to_row(self) -> dict:
        """Flatten to a plain dict for building a findings DataFrame / table."""
        return {
            "check_id": self.check_id,
            "object_type": self.object_type,
            "record_id": self.record_id,
            "severity": self.severity.label,
            "severity_rank": int(self.severity),
            "message": self.message,
            "field": self.field_name or "",
            "revenue_impact_usd": round(self.revenue_impact_usd, 2),
            "related_record_ids": ", ".join(self.related_record_ids),
        }


@dataclass
class CRMData:
    """
    The normalized CRM export the engine operates on.

    The loader turns raw HubSpot CSVs into this: three DataFrames with a
    consistent, engine-internal column vocabulary (see engine/loader.py), plus
    a fixed `as_of` date so every time-based check ("30 days stale") measures
    against the same reference point instead of wall-clock time.
    """

    contacts: "pd.DataFrame"
    companies: "pd.DataFrame"
    deals: "pd.DataFrame"
    as_of: "pd.Timestamp"

    def frame(self, object_type: str) -> "pd.DataFrame":
        """Fetch the DataFrame for an object type by its constant name."""
        mapping = {
            CONTACTS: self.contacts,
            COMPANIES: self.companies,
            DEALS: self.deals,
        }
        try:
            return mapping[object_type]
        except KeyError:
            raise ValueError(f"Unknown object_type {object_type!r}; expected one of {OBJECT_TYPES}")

    @property
    def counts(self) -> dict[str, int]:
        return {
            CONTACTS: len(self.contacts),
            COMPANIES: len(self.companies),
            DEALS: len(self.deals),
        }


@runtime_checkable
class Check(Protocol):
    """
    The contract every hygiene check implements.

    A check is a small object with identity metadata and a single `run` method
    that inspects the CRM data and returns a list of Findings. It must be pure:
    no mutation of the input, no I/O — just data in, findings out. That purity
    is what makes the checks trivially unit-testable against the generator's
    ground-truth manifest.
    """

    id: str            # stable machine id, e.g. "duplicate_contacts"
    name: str          # human label, e.g. "Duplicate Contacts"
    object_type: str   # which frame it primarily inspects

    def run(self, data: CRMData, config: "Config") -> list[Finding]:
        ...
