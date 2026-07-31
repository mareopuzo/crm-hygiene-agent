"""
Contact decay: the two ways a contact record quietly stops being worth anything.

1. Inactivity — nobody has touched them in months. The record still counts
   toward "database size" but won't convert, and marketing to it is what drives
   bounce rates up.

2. Deliverability — the address itself is a liability: a shared role mailbox
   (info@, sales@) that no individual owns, or an address with a typo'd TLD that
   will hard-bounce.

Both are split into separate checks so the report can price them differently:
inactivity is a re-engagement problem, deliverability is a suppression problem.
"""

from __future__ import annotations

from engine.checks.base import (
    blank,
    days_since,
    email_parts,
    is_syntactically_valid_email,
    suspicious_tld,
)
from engine.models import CONTACTS, CRMData, Finding, Severity


class InactiveContactsCheck:
    """Contacts with no recorded activity inside the configured window."""

    id = "decayed_contacts"
    name = "Decayed Contacts"
    object_type = CONTACTS

    def run(self, data: CRMData, config) -> list[Finding]:
        df = data.contacts
        threshold = config.decayed_contact_days
        idle = days_since(df["last_activity_date"], data.as_of)

        findings: list[Finding] = []
        for record_id, days in zip(df["record_id"], idle):
            # NaN means no activity date at all. That's a gap in the record, not
            # evidence of decay, so it's left to the missing-fields policy.
            if days is None or days != days or days <= threshold:
                continue
            months = int(days // 30)
            findings.append(Finding(
                check_id=self.id,
                object_type=self.object_type,
                record_id=record_id,
                severity=Severity.MEDIUM,
                message=(
                    f"No activity in {int(days)} days (~{months} months) — "
                    f"exceeds the {threshold}-day decay threshold. Re-engage or suppress."
                ),
                field_name="last_activity_date",
                meta={"days_inactive": int(days), "threshold_days": threshold},
            ))
        return findings


class EmailDeliverabilityCheck:
    """
    Addresses that will hurt you if you mail them.

    Severity splits by whether the address is *wrong* or merely *weak*: a typo'd
    domain hard-bounces and damages sender reputation (HIGH), while a role
    mailbox is deliverable but unattributable and low-converting (MEDIUM).
    """

    id = "email_deliverability"
    name = "Email Deliverability Risk"
    object_type = CONTACTS

    def run(self, data: CRMData, config) -> list[Finding]:
        df = data.contacts
        present = df[~blank(df["email"])]

        findings: list[Finding] = []
        for record_id, raw_email in zip(present["record_id"], present["email"]):
            # Malformed beyond repair.
            if not is_syntactically_valid_email(raw_email):
                findings.append(Finding(
                    check_id=self.id,
                    object_type=self.object_type,
                    record_id=record_id,
                    severity=Severity.HIGH,
                    message=f"Email '{str(raw_email).strip()}' is not a valid address — it cannot be delivered.",
                    field_name="email",
                    meta={"reason": "invalid_syntax", "email": str(raw_email).strip()},
                ))
                continue

            # Valid syntax, implausible domain — the classic fat-finger.
            suspect = suspicious_tld(raw_email)
            if suspect is not None:
                tld, likely = suspect
                hint = f" — likely a typo of '.{likely}'" if likely else ""
                findings.append(Finding(
                    check_id=self.id,
                    object_type=self.object_type,
                    record_id=record_id,
                    severity=Severity.HIGH,
                    message=f"Email domain ends in unrecognized '.{tld}'{hint}. Expect a hard bounce.",
                    field_name="email",
                    # The address travels with the finding so a downstream
                    # consumer can propose the correction without re-reading
                    # the source frame.
                    meta={
                        "reason": "suspicious_tld",
                        "tld": tld,
                        "likely_tld": likely,
                        "email": str(raw_email).strip(),
                    },
                ))
                continue

            # Deliverable, but not a person.
            parts = email_parts(raw_email)
            if parts and parts[0] in config.role_based_localparts:
                findings.append(Finding(
                    check_id=self.id,
                    object_type=self.object_type,
                    record_id=record_id,
                    severity=Severity.MEDIUM,
                    message=(
                        f"'{parts[0]}@' is a shared role mailbox, not an individual — "
                        "low conversion and higher spam-complaint risk."
                    ),
                    field_name="email",
                    meta={"reason": "role_based", "local_part": parts[0]},
                ))
        return findings
