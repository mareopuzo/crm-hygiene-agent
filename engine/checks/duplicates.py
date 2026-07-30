"""
Duplicate detection for contacts and companies.

Merge policy: within a duplicate cluster the *oldest* record (lowest Record ID)
is treated as the master and left unflagged; every other member is reported as
"merge into <master>". Flagging the whole cluster would double-count the same
underlying problem and overstate the damage — one duplicate pair is one thing
to fix, not two.

Matching is tiered by confidence, and severity follows:
  - contacts, same normalized email        -> HIGH   (certain: same mailbox)
  - companies, same normalized domain      -> HIGH   (certain: same web domain)
  - companies, same canonical name         -> MEDIUM (probable: needs a human's eye)
"""

from __future__ import annotations

import pandas as pd

from engine.checks.base import normalize_company_name, normalize_domain, normalize_email
from engine.models import COMPANIES, CONTACTS, CRMData, Finding, Severity


def _cluster_findings(
    df: pd.DataFrame,
    key: pd.Series,
    *,
    check_id: str,
    object_type: str,
    severity: Severity,
    field_name: str,
    describe: str,
    already_flagged: set[str] | None = None,
) -> list[Finding]:
    """
    Turn a grouping key into findings, one per non-master cluster member.

    `already_flagged` lets a caller run a second, weaker matching pass without
    reporting the same record twice (a record caught by domain shouldn't also be
    reported by name).
    """
    findings: list[Finding] = []
    seen = already_flagged if already_flagged is not None else set()

    working = df.assign(_key=key)
    working = working[working["_key"].notna()]

    for key_value, group in working.groupby("_key", sort=False):
        if len(group) < 2:
            continue
        # Oldest record wins. IDs are numeric strings in HubSpot exports; sort
        # numerically when possible so "9" doesn't sort after "10".
        ordered = sorted(group["record_id"], key=lambda r: (len(str(r)), str(r)))
        master, duplicates = ordered[0], ordered[1:]
        for record_id in duplicates:
            if record_id in seen:
                continue
            seen.add(record_id)
            findings.append(Finding(
                check_id=check_id,
                object_type=object_type,
                record_id=record_id,
                severity=severity,
                message=f"Duplicate of record {master} — {describe} ({key_value}). Merge into {master}.",
                field_name=field_name,
                related_record_ids=(master,),
                meta={"master_record_id": master, "match_key": str(key_value), "match_type": field_name},
            ))
    return findings


class DuplicateContactsCheck:
    """Contacts sharing a mailbox once case and stray whitespace are ignored."""

    id = "duplicate_contacts"
    name = "Duplicate Contacts"
    object_type = CONTACTS

    def run(self, data: CRMData, config) -> list[Finding]:
        df = data.contacts
        key = df["email"].map(normalize_email)
        return _cluster_findings(
            df, key,
            check_id=self.id,
            object_type=self.object_type,
            severity=Severity.HIGH,
            field_name="email",
            describe="same email",
        )


class DuplicateCompaniesCheck:
    """
    Companies matched on domain first, then on canonical name.

    Two passes because they carry different confidence: an identical domain is
    proof, a matching name after stripping "Inc./LLC/Ltd" is a strong hint that
    still deserves human review before merging.
    """

    id = "duplicate_companies"
    name = "Duplicate Companies"
    object_type = COMPANIES

    def run(self, data: CRMData, config) -> list[Finding]:
        df = data.companies
        flagged: set[str] = set()

        findings = _cluster_findings(
            df, df["domain"].map(normalize_domain),
            check_id=self.id,
            object_type=self.object_type,
            severity=Severity.HIGH,
            field_name="domain",
            describe="same domain",
            already_flagged=flagged,
        )
        findings += _cluster_findings(
            df, df["company_name"].map(normalize_company_name),
            check_id=self.id,
            object_type=self.object_type,
            severity=Severity.MEDIUM,
            field_name="company_name",
            describe="same company name once legal suffixes are ignored",
            already_flagged=flagged,
        )
        return findings
