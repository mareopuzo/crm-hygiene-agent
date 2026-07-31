"""
Turn findings into files you can act on.

The audit tells you what's wrong; this turns that into the actual artefacts a
RevOps person needs on a Monday. Three kinds, and the distinction between them
is the whole safety model:

  **import**   — a mechanical, information-preserving correction. Trimming
                 whitespace off an email or writing "United States" where the
                 record said "USA" changes the spelling, never the meaning.
                 Ready to feed straight to HubSpot's importer.

  **review**   — a *suggested* correction the tool inferred but cannot be sure
                 of, like reading `@acme.cim` as a typo of `@acme.com`. Ships
                 with the current and proposed value side by side so a human
                 decides. Never pre-applied.

  **worklist** — no mechanical fix exists. Merging duplicates, reassigning an
                 account, deciding whether a silent deal is dead: these need
                 judgment, so the file is a checklist, not an import.

Every import file contains **only the Record ID and the columns being changed**.
Including anything else risks overwriting a field the audit never looked at —
an import is a write, and a wide file is a wide blast radius.

Column headers match HubSpot's own export names so the importer's field mapping
lands on the right property without hand-mapping every column.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

import pandas as pd

from engine.checks.base import normalize_email
from engine.models import COMPANIES, CONTACTS, CRMData, DEALS, Finding
from engine.territories import canonical_country

# Internal column name -> the header HubSpot exports it under, per object.
_HUBSPOT_HEADERS: dict[str, dict[str, str]] = {
    CONTACTS: {
        "record_id": "Record ID",
        "email": "Email",
        "owner": "Contact owner",
        "lifecycle_stage": "Lifecycle Stage",
        "country": "Country/Region",
    },
    COMPANIES: {
        "record_id": "Record ID",
        "domain": "Company Domain Name",
        "owner": "Company owner",
        "industry": "Industry",
        "country": "Country/Region",
    },
    DEALS: {
        "record_id": "Record ID",
        "amount": "Amount",
        "owner": "Deal owner",
        "close_date": "Close Date",
    },
}

_OBJECT_LABEL = {CONTACTS: "Contacts", COMPANIES: "Companies", DEALS: "Deals"}


@dataclass
class FixFile:
    """One downloadable file, with the instructions for using it."""

    filename: str
    kind: str            # "import" | "review" | "worklist"
    title: str
    object_type: str
    summary: str         # what it contains
    instructions: str    # what to do with it
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def row_count(self) -> int:
        return len(self.frame)

    def to_csv_bytes(self) -> bytes:
        return self.frame.to_csv(index=False).encode("utf-8")


def _header(object_type: str, column: str) -> str:
    return _HUBSPOT_HEADERS.get(object_type, {}).get(column, column)


def _findings_for(findings: list[Finding], check_id: str) -> list[Finding]:
    return [f for f in findings if f.check_id == check_id]


def _duplicate_record_ids(findings: list[Finding]) -> set[tuple[str, str]]:
    """(object_type, record_id) for everything queued to be merged away."""
    return {
        (f.object_type, f.record_id)
        for f in findings
        if f.check_id in ("duplicate_contacts", "duplicate_companies")
    }


# --------------------------------------------------------------------------- #
# import — mechanical corrections
# --------------------------------------------------------------------------- #

def _email_normalization(data: CRMData, findings: list[Finding]) -> FixFile:
    """
    Trim and lowercase emails that are stored with stray case or whitespace.

    Records already queued for merging are deliberately excluded: writing the
    canonical form onto a duplicate would make it collide with the master it's
    about to be merged into, and an importer faced with two identical emails
    does something you didn't ask for. Merge first, normalize after.
    """
    queued = _duplicate_record_ids(findings)
    rows = []
    for record_id, raw in zip(data.contacts["record_id"], data.contacts["email"]):
        if (CONTACTS, record_id) in queued:
            continue
        clean = normalize_email(raw)
        if clean and str(raw) != clean:
            rows.append({_header(CONTACTS, "record_id"): record_id,
                         _header(CONTACTS, "email"): clean})

    return FixFile(
        filename="import_contacts_email_normalized.csv",
        kind="import",
        title="Normalized email addresses",
        object_type=CONTACTS,
        summary="Emails stored with stray capitals or leading/trailing spaces, rewritten to their canonical form.",
        instructions=(
            "Import into HubSpot as Contacts → update existing records, matched on Record ID. "
            "Same mailbox, tidier spelling — nothing is lost. Records awaiting a merge are "
            "excluded on purpose; run the merge worklist first."
        ),
        frame=pd.DataFrame(rows),
    )


def _country_standardization(data: CRMData, object_type: str) -> FixFile:
    """Rewrite country spellings to the atlas's canonical name."""
    frame = data.frame(object_type)
    rows = []
    for record_id, raw in zip(frame["record_id"], frame["country"]):
        canonical = canonical_country(raw)
        if canonical and str(raw).strip() != canonical:
            rows.append({_header(object_type, "record_id"): record_id,
                         _header(object_type, "country"): canonical})

    label = _OBJECT_LABEL[object_type]
    return FixFile(
        filename=f"import_{object_type}_country_standardized.csv",
        kind="import",
        title=f"Standardized country names — {label.lower()}",
        object_type=object_type,
        summary='Country values written four different ways ("USA", "U.S.", "America") rewritten to one spelling.',
        instructions=(
            f"Import as {label} → update existing records, matched on Record ID. "
            "Consistent country values are what make territory routing and regional "
            "reporting work at all."
        ),
        frame=pd.DataFrame(rows),
    )


# --------------------------------------------------------------------------- #
# review — suggested, never applied
# --------------------------------------------------------------------------- #

def _email_typo_suggestions(findings: list[Finding]) -> FixFile:
    """Proposed corrections for domains that look mistyped."""
    rows = []
    for finding in _findings_for(findings, "email_deliverability"):
        if finding.meta.get("reason") != "suspicious_tld":
            continue
        likely = finding.meta.get("likely_tld")
        current = finding.meta.get("email") or ""
        if not likely or not current:
            continue
        tld = finding.meta.get("tld") or ""
        suggested = current[: -len(tld)] + likely if tld and current.endswith(tld) else ""
        rows.append({
            "Record ID": finding.record_id,
            "Current Email": current,
            "Suggested Email": suggested,
            "Confirmed? (yes/no)": "",
        })

    return FixFile(
        filename="review_email_typos.csv",
        kind="review",
        title="Suggested email corrections",
        object_type=CONTACTS,
        summary="Addresses whose domain looks mistyped, with the correction the tool would propose.",
        instructions=(
            "This is a guess, so it is NOT ready to import. Check each suggestion, delete the "
            "rows you disagree with, then keep only the Record ID and Suggested Email columns "
            "(renaming Suggested Email to Email) before importing."
        ),
        frame=pd.DataFrame(rows),
    )


# --------------------------------------------------------------------------- #
# worklist — judgment required
# --------------------------------------------------------------------------- #

def _merge_worklist(findings: list[Finding]) -> FixFile:
    rows = []
    for finding in findings:
        if finding.check_id not in ("duplicate_contacts", "duplicate_companies"):
            continue
        rows.append({
            "Object": _OBJECT_LABEL[finding.object_type],
            "Merge This Record ID": finding.record_id,
            "Into This Record ID": finding.meta.get("master_record_id", ""),
            "Matched On": finding.meta.get("match_type", ""),
            "Matched Value": finding.meta.get("match_key", ""),
            "Done? (yes/no)": "",
        })

    return FixFile(
        filename="worklist_merge_duplicates.csv",
        kind="worklist",
        title="Duplicate merge worklist",
        object_type=CONTACTS,
        summary="Each duplicate paired with the record it should be merged into (the older one, so history is kept).",
        instructions=(
            "Do NOT import this. Merging in HubSpot cannot be undone, so work through it by hand: "
            "open the record in the first column, Actions → Merge, and merge it into the second. "
            "Roughly a minute each."
        ),
        frame=pd.DataFrame(rows),
    )


def _routing_worklist(findings: list[Finding]) -> FixFile:
    rows = []
    for finding in findings:
        if not finding.check_id.startswith("routing_"):
            continue
        rows.append({
            "Object": _OBJECT_LABEL[finding.object_type],
            "Record ID": finding.record_id,
            "Current Owner": finding.meta.get("owner", ""),
            "Owner Covers": finding.meta.get("owner_region", ""),
            "Record Country": finding.meta.get("record_country", ""),
            "Should Be Owned In": finding.meta.get("record_region", ""),
            "New Owner (fill in)": "",
        })

    return FixFile(
        filename="worklist_reassign_owners.csv",
        kind="worklist",
        title="Territory reassignment worklist",
        object_type=CONTACTS,
        summary="Records whose owner doesn't cover their region, with the region they belong to.",
        instructions=(
            "Fill in the New Owner column, then keep only Record ID and New Owner (renamed to "
            "the owner property, e.g. 'Contact owner') and import as an update. Reassignment is "
            "a management decision, so the tool won't pick the new owner for you — and the new "
            "owner needs the context handed over, not just the record."
        ),
        frame=pd.DataFrame(rows),
    )


def _pipeline_worklist(data: CRMData, findings: list[Finding]) -> FixFile:
    names = dict(zip(data.deals["record_id"], data.deals["deal_name"]))
    rows = []
    for finding in findings:
        if finding.check_id not in ("stale_deals", "past_close_date"):
            continue
        amount = finding.meta.get("amount")
        rows.append({
            "Record ID": finding.record_id,
            "Deal Name": names.get(finding.record_id, ""),
            "Issue": "Stale" if finding.check_id == "stale_deals" else "Past close date",
            "Days": finding.meta.get("days_inactive") or finding.meta.get("days_overdue") or "",
            "Amount": "" if amount is None else amount,
            "At Risk (USD)": finding.meta.get("at_risk_usd", 0),
            "Decision (chase / re-date / close)": "",
        })

    return FixFile(
        filename="worklist_pipeline_review.csv",
        kind="worklist",
        title="Pipeline review worklist",
        object_type=DEALS,
        summary="Open deals that are stale or past their close date, with the value each puts at risk.",
        instructions=(
            "Take this into your pipeline review. Every row needs a rep to decide: chase it, "
            "push the close date with a reason, or mark it Closed Lost. No import fixes a stale "
            "deal — the forecast only gets honest when someone answers for each one."
        ),
        frame=pd.DataFrame(rows),
    )


def _missing_fields_template(findings: list[Finding], object_type: str) -> FixFile:
    """One row per record, one column per missing field, ready to fill in."""
    per_record: dict[str, set[str]] = {}
    for finding in findings:
        if finding.check_id != f"missing_fields_{object_type}" or not finding.field_name:
            continue
        per_record.setdefault(finding.record_id, set()).add(finding.field_name)

    columns = sorted({c for cols in per_record.values() for c in cols})
    rows = []
    for record_id, missing in per_record.items():
        row = {_header(object_type, "record_id"): record_id}
        # A blank means "needs a value"; n/a means this record already has one,
        # so an importer won't wipe a field that was never the problem.
        for column in columns:
            row[_header(object_type, column)] = "" if column in missing else "n/a"
        rows.append(row)

    label = _OBJECT_LABEL[object_type]
    return FixFile(
        filename=f"template_{object_type}_missing_fields.csv",
        kind="worklist",
        title=f"Missing fields to fill — {label.lower()}",
        object_type=object_type,
        summary="Records with a required field empty, one column per field, blank where a value is needed.",
        instructions=(
            "The tool can't invent data, so this is a template rather than a fix. Fill the blanks "
            "from whatever source you have, DELETE every cell still reading 'n/a' and any column "
            f"you didn't fill, then import as {label} → update existing records. "
            "Importing an empty cell can clear a value, so remove what you didn't complete."
        ),
        frame=pd.DataFrame(rows),
    )


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def build_fix_files(data: CRMData, findings: list[Finding]) -> list[FixFile]:
    """Every fix file that has at least one row, in the order to work through them."""
    candidates = [
        _pipeline_worklist(data, findings),
        _merge_worklist(findings),
        _routing_worklist(findings),
        _email_normalization(data, findings),
        _country_standardization(data, CONTACTS),
        _country_standardization(data, COMPANIES),
        _email_typo_suggestions(findings),
        _missing_fields_template(findings, CONTACTS),
        _missing_fields_template(findings, COMPANIES),
        _missing_fields_template(findings, DEALS),
    ]
    return [f for f in candidates if f.row_count]


_KIND_BLURB = {
    "import": "READY TO IMPORT — mechanical corrections, nothing is lost.",
    "review": "REVIEW FIRST — a suggestion the tool cannot be certain of.",
    "worklist": "NOT AN IMPORT — needs a human decision per row.",
}


def bundle_readme(files: list[FixFile]) -> str:
    lines = [
        "CRM HYGIENE AGENT — FIX FILES",
        "=" * 60,
        "",
        "Before importing anything:",
        "  1. Export a full backup of the object you're about to change.",
        "     Your backup is the only undo an import has.",
        "  2. Try five rows first. Check them in HubSpot, then do the rest.",
        "  3. Never bulk-merge duplicates. Merging cannot be undone.",
        "",
        "Import files contain ONLY the Record ID and the columns being changed,",
        "so nothing outside the audit's scope gets overwritten. In HubSpot choose",
        "Import > Update existing records, and match on Record ID.",
        "",
        "=" * 60,
        "",
    ]
    for index, fix in enumerate(files, start=1):
        lines += [
            f"{index}. {fix.filename}  ({fix.row_count} rows)",
            f"   {fix.title}",
            f"   {_KIND_BLURB.get(fix.kind, '')}",
            f"   {fix.summary}",
            f"   HOW: {fix.instructions}",
            "",
        ]
    return "\n".join(lines)


def bundle_zip(files: list[FixFile]) -> bytes:
    """All the files plus a README, as one download."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", bundle_readme(files))
        for fix in files:
            archive.writestr(fix.filename, fix.to_csv_bytes())
    return buffer.getvalue()
