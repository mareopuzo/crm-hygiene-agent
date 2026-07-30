"""
The check registry.

Every check the engine knows about is listed here, and nothing else needs to
change to add one: implement the `Check` protocol in a new module, add an
instance to `build_checks()`, and the runner, the report, and the app pick it up
automatically.

Several checks are parameterized by object type rather than duplicated per
object — `MissingFieldsCheck(CONTACTS/COMPANIES/DEALS)` is one class doing three
jobs, driven by the policy table in config.
"""

from __future__ import annotations

from engine.checks.decay import EmailDeliverabilityCheck, InactiveContactsCheck
from engine.checks.duplicates import DuplicateCompaniesCheck, DuplicateContactsCheck
from engine.checks.missing_fields import MissingFieldsCheck
from engine.checks.routing import TerritoryRoutingCheck
from engine.checks.stale_deals import PastCloseDateCheck, StaleDealsCheck
from engine.models import COMPANIES, CONTACTS, CRMData, DEALS, Check, Finding


def build_checks() -> list[Check]:
    """A fresh list of every registered check instance."""
    return [
        # Duplicates
        DuplicateContactsCheck(),
        DuplicateCompaniesCheck(),
        # Required fields (policy-driven, one instance per object)
        MissingFieldsCheck(CONTACTS),
        MissingFieldsCheck(COMPANIES),
        MissingFieldsCheck(DEALS),
        # Contact decay
        InactiveContactsCheck(),
        EmailDeliverabilityCheck(),
        # Routing
        TerritoryRoutingCheck(CONTACTS),
        TerritoryRoutingCheck(COMPANIES),
        # Pipeline hygiene
        StaleDealsCheck(),
        PastCloseDateCheck(),
    ]


def run_all_checks(data: CRMData, config, checks: list[Check] | None = None) -> list[Finding]:
    """
    Run every check and return the combined findings.

    Sorted most-severe-first so any consumer that truncates the list (a report
    summary, a UI table) surfaces what matters most.
    """
    selected = checks if checks is not None else build_checks()
    findings: list[Finding] = []
    for check in selected:
        findings.extend(check.run(data, config))
    findings.sort(key=lambda f: (-int(f.severity), f.check_id, f.record_id))
    return findings


__all__ = [
    "build_checks",
    "run_all_checks",
    "DuplicateContactsCheck",
    "DuplicateCompaniesCheck",
    "MissingFieldsCheck",
    "InactiveContactsCheck",
    "EmailDeliverabilityCheck",
    "TerritoryRoutingCheck",
    "StaleDealsCheck",
    "PastCloseDateCheck",
]
