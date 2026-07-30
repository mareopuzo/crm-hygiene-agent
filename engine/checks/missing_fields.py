"""
Required-field completeness.

The policy lives entirely in `Config.required_fields` — this check just applies
it. That separation is deliberate: which fields are mandatory is a RevOps
judgment call that differs per org, so it belongs in config where it can be
changed without touching detection logic.

Severity comes from the field's tier (see engine/config.py):
  HIGH   = a core revenue process cannot run without it
  MEDIUM = the process runs but quality degrades
"""

from __future__ import annotations

from engine.checks.base import blank
from engine.models import CRMData, Finding

# Human-readable labels for the internal column names, so findings read like
# English rather than like a schema dump.
_FIELD_LABELS = {
    "email": "Email",
    "owner": "Owner",
    "lifecycle_stage": "Lifecycle Stage",
    "country": "Country/Region",
    "domain": "Domain",
    "industry": "Industry",
    "amount": "Amount",
    "close_date": "Close Date",
}

_OBJECT_LABELS = {"contacts": "Contact", "companies": "Company", "deals": "Deal"}


class MissingFieldsCheck:
    """
    One instance per object type — the registry creates three.

    Parameterizing by object type rather than writing three near-identical
    classes keeps the policy table the single source of truth.
    """

    def __init__(self, object_type: str) -> None:
        self.object_type = object_type
        self.id = f"missing_fields_{object_type}"
        self.name = f"Missing Required Fields — {_OBJECT_LABELS.get(object_type, object_type).title()}"

    def run(self, data: CRMData, config) -> list[Finding]:
        df = data.frame(self.object_type)
        policies = config.required_fields.get(self.object_type, [])
        object_label = _OBJECT_LABELS.get(self.object_type, self.object_type)

        findings: list[Finding] = []
        for policy in policies:
            if policy.column not in df.columns:
                continue
            missing = df[blank(df[policy.column])]
            label = _FIELD_LABELS.get(policy.column, policy.column.replace("_", " ").title())
            for record_id in missing["record_id"]:
                findings.append(Finding(
                    check_id=self.id,
                    object_type=self.object_type,
                    record_id=record_id,
                    severity=policy.severity,
                    message=f"{object_label} is missing {label} — {policy.reason}.",
                    field_name=policy.column,
                    meta={"required_field": policy.column, "reason": policy.reason},
                ))
        return findings
