"""
Synthetic HubSpot-shaped CRM data generator for the CRM Hygiene Agent.

Why this exists
---------------
The whole point of a hygiene agent is to *find problems*. To prove it works,
we need data where we know exactly what's wrong. This module generates three
CSVs that mimic a real HubSpot export (Contacts, Companies, Deals) and
deliberately plants a controlled set of hygiene issues into them.

Three design decisions worth calling out:

1. Dependency-free. Uses only the standard library so anyone can run it with a
   bare Python install. pandas is reserved for the engine, not the fixtures.

2. Deterministic. A fixed seed means the same messy dataset every run, so the
   demo and the tests are reproducible. Change the seed (or --seed) for variety.

3. The ground truth is exact, not approximate. Two properties make it so:

   - Base records are clean *by construction*: emails and company domains are
     de-duplicated as they're generated, so the only duplicates in the output
     are the ones we planted. (Without this, random name collisions produce
     accidental duplicates the manifest doesn't know about — real findings that
     would look like false positives.)

   - Plants can't clobber each other. A `Planter` tracks which columns of which
     records are already spoken for, so a later plant never overwrites an
     earlier one (e.g. blanking the email of a record already planted as a
     duplicate, which would silently destroy that duplicate). Records can still
     carry multiple issues, as long as those issues touch different columns.

Alongside the CSVs it writes `ground_truth.json`: a manifest of every issue we
planted, keyed by record ID. That file is the oracle the test suite uses to
assert the engine catches exactly what we planted — no misses, no invention.

Usage
-----
    python data/generate_sample.py                 # default demo set
    python data/generate_sample.py --contacts 400  # bigger set
    python data/generate_sample.py --seed 7        # different (but reproducible) mess
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# --------------------------------------------------------------------------- #
# Reference data. Small curated lists keep the generator dependency-free and
# the output readable (real-looking names/domains instead of random noise).
# --------------------------------------------------------------------------- #

FIRST_NAMES = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael",
    "Linda", "David", "Elizabeth", "William", "Barbara", "Priya", "Wei",
    "Amara", "Diego", "Yuki", "Fatima", "Lars", "Sofia", "Omar", "Chloe",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Nguyen", "Patel", "Kim", "Okafor",
    "Andersson", "Rossi", "Haddad", "Silva", "Chen", "Novak",
]
JOB_TITLES = [
    "VP of Sales", "Head of Marketing", "CTO", "Account Executive",
    "Operations Manager", "Founder", "Procurement Lead", "RevOps Manager",
    "CFO", "Product Manager", "Demand Gen Lead", "IT Director",
]
INDUSTRIES = [
    "Software", "Financial Services", "Healthcare", "Manufacturing",
    "Retail", "Logistics", "Education", "Telecommunications",
]
# Owners paired with the region their book covers. Routing checks later compare
# a record's country against the assigned owner's region.
OWNERS = {
    "Sarah Chen": "North America",
    "Tom Becker": "North America",
    "Ingrid Larsen": "EMEA",
    "Omar Haddad": "EMEA",
    "Mei Tan": "APAC",
}
REGION_COUNTRIES = {
    "North America": ["United States", "Canada", "Mexico"],
    "EMEA": ["United Kingdom", "Germany", "France", "United Arab Emirates"],
    "APAC": ["Australia", "Singapore", "Japan", "India"],
}

LIFECYCLE_STAGES = [
    "subscriber", "lead", "marketingqualifiedlead",
    "salesqualifiedlead", "opportunity", "customer",
]
LEAD_STATUSES = ["NEW", "OPEN", "IN_PROGRESS", "CONNECTED", "UNQUALIFIED"]

DEAL_STAGES_OPEN = ["appointmentscheduled", "qualifiedtobuy", "presentationscheduled", "contractsent"]
DEAL_STAGES_CLOSED = ["closedwon", "closedlost"]
PIPELINES = ["Sales Pipeline"]

# Role-based / non-personal email local-parts. Marketing to these tanks
# deliverability, so the decay check flags them.
ROLE_LOCALPARTS = ["info", "sales", "admin", "support", "contact", "hello", "office"]
COMPANY_STEMS = [
    "acme", "globex", "initech", "umbrella", "hooli", "stark", "wayne",
    "wonka", "cyberdyne", "soylent", "vandelay", "gekko", "prestige",
]
# Only legitimate TLDs here. Typo'd variants are introduced solely by the
# planted "invalid email" issue, so clean records never look suspicious.
TLDS = ["com", "io", "co", "net"]

# HubSpot column names, kept as constants because the Planter claims columns by
# name and a typo would silently disable a collision guard.
COL_EMAIL = "Email"
COL_CONTACT_OWNER = "Contact owner"
COL_COUNTRY = "Country/Region"
COL_LIFECYCLE = "Lifecycle Stage"
COL_LAST_ACTIVITY = "Last Activity Date"
COL_COMPANY_NAME = "Company name"
COL_DOMAIN = "Company Domain Name"
COL_COMPANY_OWNER = "Company owner"
COL_INDUSTRY = "Industry"
COL_DEAL_STAGE = "Deal Stage"
COL_AMOUNT = "Amount"
COL_DEAL_OWNER = "Deal owner"
COL_CLOSE_DATE = "Close Date"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _rand_date(rng: random.Random, start_days_ago: int, end_days_ago: int, today: date) -> date:
    """A random date between end_days_ago and start_days_ago (inclusive)."""
    lo, hi = sorted((start_days_ago, end_days_ago))
    return today - timedelta(days=rng.randint(lo, hi))


def _fmt(d: date | None) -> str:
    return d.isoformat() if d else ""


class GroundTruth:
    """Accumulates the issues we plant, so tests can assert against them."""

    def __init__(self) -> None:
        self._issues: dict[str, list] = defaultdict(list)

    def add(self, issue_type: str, record_id, detail: str = "") -> None:
        self._issues[issue_type].append({"record_id": str(record_id), "detail": detail})

    def to_dict(self) -> dict:
        return {
            "issue_counts": {k: len(v) for k, v in sorted(self._issues.items())},
            "issues": {k: v for k, v in sorted(self._issues.items())},
        }


class Planter:
    """
    Plants issues into rows without letting one plant destroy another.

    Each plant declares the columns it writes. A record is only eligible if none
    of those columns are already claimed on it, so (for example) the
    "blank the email" plant can never be handed a record whose email is load-
    bearing for a planted duplicate. Different columns on the same record are
    fair game, which keeps the data realistic — a contact can be both decayed
    and missing an industry.
    """

    def __init__(self, rng: random.Random, gt: GroundTruth) -> None:
        self.rng = rng
        self.gt = gt
        self._claims: dict[str, set[str]] = defaultdict(set)

    def pick(self, rows: list[dict], columns: set[str]) -> dict | None:
        """A random row with none of `columns` claimed, or None if exhausted."""
        available = [r for r in rows if not (self._claims[str(r["Record ID"])] & columns)]
        return self.rng.choice(available) if available else None

    def claim(self, record: dict, columns: set[str]) -> None:
        self._claims[str(record["Record ID"])].update(columns)

    def plant(self, rows, count, columns, mutate, issue_type, detail=None) -> int:
        """
        Apply `mutate(row)` to `count` eligible rows, claiming `columns` on each
        and recording the issue. Returns how many were actually planted (fewer
        than requested only if the pool ran dry).
        """
        planted = 0
        for _ in range(count):
            row = self.pick(rows, columns)
            if row is None:
                break
            mutate(row)
            self.claim(row, columns)
            self.gt.add(issue_type, row["Record ID"], detail(row) if detail else "")
            planted += 1
        return planted


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #

def generate_companies(rng: random.Random, n: int, today: date, gt: GroundTruth, planter: Planter):
    """
    Companies with planted issues: duplicate domain, name variant of the same
    company, and missing domain / owner / industry.
    """
    rows: list[dict] = []
    next_id = 1000
    used_stems: set[str] = set()

    def new_id() -> int:
        nonlocal next_id
        next_id += 1
        return next_id

    def unique_stem() -> str:
        """Guarantees distinct domains, so the only dup domains are planted."""
        while True:
            stem = rng.choice(COMPANY_STEMS) + str(rng.randint(1, 999))
            if stem not in used_stems:
                used_stems.add(stem)
                return stem

    for _ in range(n):
        stem = unique_stem()
        owner = rng.choice(list(OWNERS))
        region = OWNERS[owner]
        rows.append({
            "Record ID": new_id(),
            COL_COMPANY_NAME: stem.capitalize() + " " + rng.choice(["Inc.", "LLC", "Ltd", "GmbH", "Group"]),
            COL_DOMAIN: f"{stem}.{rng.choice(TLDS)}",
            COL_COMPANY_OWNER: owner,
            COL_INDUSTRY: rng.choice(INDUSTRIES),
            "Number of Employees": rng.choice([12, 45, 130, 500, 2500, 8000]),
            COL_COUNTRY: rng.choice(REGION_COUNTRIES[region]),
            "Create Date": _fmt(_rand_date(rng, 30, 1200, today)),
            COL_LAST_ACTIVITY: _fmt(_rand_date(rng, 1, 400, today)),
        })

    # --- Planted: exact duplicate domains ---
    # Claims the domain on both halves so neither can be blanked or reused.
    for _ in range(max(2, n // 12)):
        src = planter.pick(rows, {COL_DOMAIN, COL_COMPANY_NAME})
        if src is None:
            break
        dup = dict(src)
        dup["Record ID"] = new_id()
        dup[COL_COMPANY_NAME] = src[COL_COMPANY_NAME].replace("Inc.", "Incorporated")
        rows.append(dup)
        planter.claim(src, {COL_DOMAIN, COL_COMPANY_NAME})
        planter.claim(dup, {COL_DOMAIN, COL_COMPANY_NAME})
        gt.add("company_duplicate", dup["Record ID"], f"same domain as {src['Record ID']}: {src[COL_DOMAIN]}")

    # --- Planted: name variants (same company, different domain spelling) ---
    for _ in range(max(1, n // 20)):
        src = planter.pick(rows, {COL_DOMAIN, COL_COMPANY_NAME})
        if src is None:
            break
        variant = dict(src)
        variant["Record ID"] = new_id()
        base = src[COL_COMPANY_NAME].split()[0]
        variant[COL_COMPANY_NAME] = f"{base}, Inc."
        variant[COL_DOMAIN] = src[COL_DOMAIN].replace(".", "-hq.", 1)
        rows.append(variant)
        planter.claim(src, {COL_DOMAIN, COL_COMPANY_NAME})
        planter.claim(variant, {COL_DOMAIN, COL_COMPANY_NAME})
        gt.add("company_name_variant", variant["Record ID"], f"name variant of {src['Record ID']} ({base})")

    # --- Planted: missing fields ---
    planter.plant(rows, max(2, n // 15), {COL_DOMAIN},
                  lambda r: r.__setitem__(COL_DOMAIN, ""), "company_missing_domain")
    planter.plant(rows, max(2, n // 15), {COL_COMPANY_OWNER},
                  lambda r: r.__setitem__(COL_COMPANY_OWNER, ""), "company_missing_owner")
    planter.plant(rows, max(2, n // 18), {COL_INDUSTRY},
                  lambda r: r.__setitem__(COL_INDUSTRY, ""), "company_missing_industry")

    return rows


def generate_contacts(rng: random.Random, n: int, today: date, companies: list[dict],
                      gt: GroundTruth, planter: Planter):
    """
    Contacts with planted issues: duplicate emails (exact and case/whitespace
    variants), role-based and typo'd emails, decayed activity, missing required
    fields, and territory routing mismatches.
    """
    rows: list[dict] = []
    next_id = 5000
    used_emails: set[str] = set()

    def new_id() -> int:
        nonlocal next_id
        next_id += 1
        return next_id

    def unique_email(first: str, last: str, domain: str) -> str:
        """Disambiguates collisions (john.smith2@) so dups are only planted."""
        base = f"{first.lower()}.{last.lower()}"
        candidate = f"{base}@{domain}"
        suffix = 2
        while candidate.lower() in used_emails:
            candidate = f"{base}{suffix}@{domain}"
            suffix += 1
        used_emails.add(candidate.lower())
        return candidate

    company_by_id = {c["Record ID"]: c for c in companies}
    company_ids = list(company_by_id)

    for _ in range(n):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        comp_row = company_by_id[rng.choice(company_ids)]
        domain = comp_row[COL_DOMAIN] or f"{last.lower()}.com"
        # Contacts inherit the company's owner, and a country inside that
        # owner's region — so clean records are correctly routed by construction.
        owner = comp_row[COL_COMPANY_OWNER] or rng.choice(list(OWNERS))
        region = OWNERS.get(owner, "North America")
        rows.append({
            "Record ID": new_id(),
            "First Name": first,
            "Last Name": last,
            COL_EMAIL: unique_email(first, last, domain),
            "Phone Number": f"+1{rng.randint(2000000000, 9999999999)}",
            "Job Title": rng.choice(JOB_TITLES),
            COL_CONTACT_OWNER: owner,
            COL_LIFECYCLE: rng.choice(LIFECYCLE_STAGES),
            "Lead Status": rng.choice(LEAD_STATUSES),
            COL_COUNTRY: rng.choice(REGION_COUNTRIES[region]),
            "Associated Company ID": comp_row["Record ID"],
            "Create Date": _fmt(_rand_date(rng, 30, 1200, today)),
            COL_LAST_ACTIVITY: _fmt(_rand_date(rng, 1, 170, today)),
        })

    # --- Planted: exact duplicate emails ---
    for _ in range(max(3, n // 12)):
        src = planter.pick(rows, {COL_EMAIL})
        if src is None:
            break
        dup = dict(src)
        dup["Record ID"] = new_id()
        dup["Job Title"] = rng.choice(JOB_TITLES)  # same person, re-entered
        rows.append(dup)
        planter.claim(src, {COL_EMAIL})
        planter.claim(dup, {COL_EMAIL})
        gt.add("contact_duplicate_email", dup["Record ID"], f"same email as {src['Record ID']}: {src[COL_EMAIL]}")

    # --- Planted: case/whitespace email variants (still the same mailbox) ---
    for _ in range(max(2, n // 18)):
        src = planter.pick(rows, {COL_EMAIL})
        if src is None:
            break
        variant = dict(src)
        variant["Record ID"] = new_id()
        variant[COL_EMAIL] = f"  {src[COL_EMAIL].upper()} "  # padded + upper-cased
        rows.append(variant)
        planter.claim(src, {COL_EMAIL})
        planter.claim(variant, {COL_EMAIL})
        gt.add("contact_duplicate_email", variant["Record ID"], f"normalized-equal email to {src['Record ID']}")

    # --- Planted: role-based emails (deliverability risk) ---
    def make_role_based(r: dict) -> None:
        domain = r[COL_EMAIL].split("@")[-1].strip()
        r[COL_EMAIL] = f"{rng.choice(ROLE_LOCALPARTS)}@{domain}"

    planter.plant(rows, max(2, n // 15), {COL_EMAIL}, make_role_based,
                  "contact_role_based_email", detail=lambda r: r[COL_EMAIL])

    # --- Planted: typo'd TLDs (undeliverable) ---
    def make_typo(r: dict) -> None:
        local, _, domain = r[COL_EMAIL].partition("@")
        stem, _, tld = domain.rpartition(".")
        typo = {"com": "cim", "io": "ii", "co": "cp", "net": "nte"}.get(tld, "cim")
        r[COL_EMAIL] = f"{local}@{stem}.{typo}"

    planter.plant(rows, max(2, n // 18), {COL_EMAIL}, make_typo,
                  "contact_invalid_email", detail=lambda r: r[COL_EMAIL])

    # --- Planted: decayed (no activity in well over 6 months) ---
    planter.plant(rows, max(3, n // 10), {COL_LAST_ACTIVITY},
                  lambda r: r.__setitem__(COL_LAST_ACTIVITY, _fmt(_rand_date(rng, 200, 900, today))),
                  "contact_decayed", detail=lambda r: f"last activity {r[COL_LAST_ACTIVITY]}")

    # --- Planted: missing fields ---
    planter.plant(rows, max(3, n // 12), {COL_EMAIL},
                  lambda r: r.__setitem__(COL_EMAIL, ""), "contact_missing_email")
    planter.plant(rows, max(2, n // 15), {COL_CONTACT_OWNER},
                  lambda r: r.__setitem__(COL_CONTACT_OWNER, ""), "contact_missing_owner")
    planter.plant(rows, max(2, n // 18), {COL_LIFECYCLE},
                  lambda r: r.__setitem__(COL_LIFECYCLE, ""), "contact_missing_lifecycle")
    planter.plant(rows, max(2, n // 18), {COL_COUNTRY},
                  lambda r: r.__setitem__(COL_COUNTRY, ""), "contact_missing_country")

    # --- Planted: bad routing (country outside the owner's region) ---
    # Claims both columns: routing is only meaningful when owner AND country are
    # present, so neither may be blanked by a later plant.
    def misroute(r: dict) -> None:
        owner = r[COL_CONTACT_OWNER] or rng.choice(list(OWNERS))
        r[COL_CONTACT_OWNER] = owner
        owner_region = OWNERS.get(owner, "North America")
        wrong = [reg for reg in REGION_COUNTRIES if reg != owner_region]
        r[COL_COUNTRY] = rng.choice(REGION_COUNTRIES[rng.choice(wrong)])

    planter.plant(rows, max(2, n // 12), {COL_CONTACT_OWNER, COL_COUNTRY}, misroute,
                  "contact_bad_routing",
                  detail=lambda r: f"{r[COL_COUNTRY]} is outside {r[COL_CONTACT_OWNER]}'s region")

    return rows


def generate_deals(rng: random.Random, n: int, today: date, companies: list[dict],
                   gt: GroundTruth, planter: Planter):
    """
    Deals with planted issues: stale open deals, open deals past their close
    date, and missing amount / owner / close date.
    """
    rows: list[dict] = []
    next_id = 9000

    def new_id() -> int:
        nonlocal next_id
        next_id += 1
        return next_id

    company_by_id = {c["Record ID"]: c for c in companies}
    company_ids = list(company_by_id)

    for _ in range(n):
        comp_row = company_by_id[rng.choice(company_ids)]
        owner = comp_row[COL_COMPANY_OWNER] or rng.choice(list(OWNERS))
        is_closed = rng.random() < 0.35
        create = _rand_date(rng, 20, 500, today)
        rows.append({
            "Record ID": new_id(),
            "Deal Name": f"{comp_row[COL_COMPANY_NAME].split()[0]} - {rng.choice(['Expansion', 'New Business', 'Renewal', 'Upsell'])}",
            COL_DEAL_STAGE: rng.choice(DEAL_STAGES_CLOSED if is_closed else DEAL_STAGES_OPEN),
            COL_AMOUNT: rng.choice([5000, 12000, 25000, 48000, 90000, 150000]),
            COL_DEAL_OWNER: owner,
            "Pipeline": rng.choice(PIPELINES),
            # Clean open deals forecast into the future; clean closed deals are
            # dated in the past. Only planted rows break that.
            COL_CLOSE_DATE: _fmt(create + timedelta(days=rng.randint(20, 120)))
            if is_closed else _fmt(today + timedelta(days=rng.randint(10, 150))),
            "Create Date": _fmt(create),
            COL_LAST_ACTIVITY: _fmt(_rand_date(rng, 1, 25, today)),
            "Associated Company ID": comp_row["Record ID"],
        })

    # --- Planted: stale open deals (no activity well past 30 days) ---
    # Also forward-dates the close date, and claims that column. Reopening a
    # previously-closed row would otherwise leave its past close date in place,
    # planting an unrecorded "past close date" issue as a side effect — a real
    # finding the manifest wouldn't know about, which reads as a false positive.
    # Keeping the two issues on disjoint rows is what makes the oracle exact.
    def make_stale(r: dict) -> None:
        r[COL_DEAL_STAGE] = rng.choice(DEAL_STAGES_OPEN)
        r[COL_LAST_ACTIVITY] = _fmt(_rand_date(rng, 45, 300, today))
        r[COL_CLOSE_DATE] = _fmt(today + timedelta(days=rng.randint(10, 150)))

    planter.plant(rows, max(2, n // 8), {COL_DEAL_STAGE, COL_LAST_ACTIVITY, COL_CLOSE_DATE},
                  make_stale, "deal_stale",
                  detail=lambda r: f"open, last activity {r[COL_LAST_ACTIVITY]}")

    # --- Planted: open deals whose close date has already passed ---
    def make_past_close(r: dict) -> None:
        r[COL_DEAL_STAGE] = rng.choice(DEAL_STAGES_OPEN)
        r[COL_CLOSE_DATE] = _fmt(_rand_date(rng, 10, 180, today))

    planter.plant(rows, max(2, n // 10), {COL_DEAL_STAGE, COL_CLOSE_DATE}, make_past_close,
                  "deal_past_close_date", detail=lambda r: f"open, close date {r[COL_CLOSE_DATE]} in past")

    # --- Planted: missing fields ---
    planter.plant(rows, max(2, n // 12), {COL_AMOUNT},
                  lambda r: r.__setitem__(COL_AMOUNT, ""), "deal_missing_amount")
    planter.plant(rows, max(1, n // 15), {COL_DEAL_OWNER},
                  lambda r: r.__setitem__(COL_DEAL_OWNER, ""), "deal_missing_owner")
    planter.plant(rows, max(1, n // 15), {COL_CLOSE_DATE},
                  lambda r: r.__setitem__(COL_CLOSE_DATE, ""), "deal_missing_close_date")

    return rows


# --------------------------------------------------------------------------- #
# CSV writing + CLI
# --------------------------------------------------------------------------- #

def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic HubSpot-shaped CRM data with planted hygiene issues.")
    parser.add_argument("--contacts", type=int, default=250, help="clean contacts before planting (default 250)")
    parser.add_argument("--companies", type=int, default=80, help="clean companies before planting (default 80)")
    parser.add_argument("--deals", type=int, default=120, help="clean deals before planting (default 120)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility (default 42)")
    parser.add_argument("--out", type=str, default=str(Path(__file__).parent / "samples"), help="output directory")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    today = date(2026, 7, 30)  # fixed 'today' so date-based checks are reproducible
    gt = GroundTruth()
    planter = Planter(rng, gt)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    companies = generate_companies(rng, args.companies, today, gt, planter)
    contacts = generate_contacts(rng, args.contacts, today, companies, gt, planter)
    deals = generate_deals(rng, args.deals, today, companies, gt, planter)

    _write_csv(out / "companies.csv", companies)
    _write_csv(out / "contacts.csv", contacts)
    _write_csv(out / "deals.csv", deals)

    manifest = gt.to_dict()
    manifest["meta"] = {
        "seed": args.seed,
        "today": today.isoformat(),
        "row_counts": {
            "companies": len(companies),
            "contacts": len(contacts),
            "deals": len(deals),
        },
    }
    (out / "ground_truth.json").write_text(json.dumps(manifest, indent=2))

    print(f"Wrote {len(companies)} companies, {len(contacts)} contacts, {len(deals)} deals to {out}")
    print("\nPlanted issues (ground truth):")
    for issue, count in manifest["issue_counts"].items():
        print(f"  {issue:32s} {count}")
    print(f"\nManifest: {out / 'ground_truth.json'}")


if __name__ == "__main__":
    main()
