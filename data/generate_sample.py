"""
Synthetic HubSpot-shaped CRM data generator for the CRM Hygiene Agent.

Why this exists
---------------
The whole point of a hygiene agent is to *find problems*. To prove it works,
we need data where we know exactly what's wrong. This module generates three
CSVs that mimic a real HubSpot export (Contacts, Companies, Deals) and
deliberately plants a controlled set of hygiene issues into them.

Two design decisions worth calling out:

1. Dependency-free. Uses only the standard library so anyone can run it with a
   bare Python install. pandas is reserved for the engine, not the fixtures.

2. Deterministic. A fixed seed means the same messy dataset every run, so the
   demo and the tests are reproducible. Change the seed (or --seed) for variety.

Alongside the CSVs it writes `ground_truth.json`: a manifest of every issue we
planted, keyed by record ID. That file is the oracle the test suite uses to
assert the engine catches what we planted (and doesn't invent extra findings).

Usage
-----
    python data/generate_sample.py                 # default: healthy-ish demo set
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
# Company owners paired with the region their book covers. Routing checks later
# compare a record's country against the assigned owner's region.
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
ALL_COUNTRIES = [c for cs in REGION_COUNTRIES.values() for c in cs]

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
# Domains we intentionally typo to simulate likely-invalid / bounced addresses.
COMPANY_STEMS = [
    "acme", "globex", "initech", "umbrella", "hooli", "stark", "wayne",
    "wonka", "cyberdyne", "soylent", "vandelay", "gekko", "prestige",
]
TLDS = ["com", "io", "co", "net"]


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
        # issue_type -> list of record ids (or id-pairs for duplicates)
        self._issues: dict[str, list] = defaultdict(list)

    def add(self, issue_type: str, record_id, detail: str = "") -> None:
        self._issues[issue_type].append({"record_id": record_id, "detail": detail})

    def to_dict(self) -> dict:
        return {
            "issue_counts": {k: len(v) for k, v in sorted(self._issues.items())},
            "issues": {k: v for k, v in sorted(self._issues.items())},
        }


# --------------------------------------------------------------------------- #
# Generators. Each returns (rows, ...) and records what it planted.
# --------------------------------------------------------------------------- #

def generate_companies(rng: random.Random, n: int, today: date, gt: GroundTruth):
    """
    Companies with planted issues:
      - duplicate domain (two records, same domain)
      - name variants of the same company (Acme Inc / Acme, Inc.)
      - missing domain
      - missing owner
      - missing industry
    Returns (rows, index) where index maps company_id -> row for later linking.
    """
    rows: list[dict] = []
    next_id = 1000

    def new_id() -> int:
        nonlocal next_id
        next_id += 1
        return next_id

    # A pool of clean base companies first.
    for _ in range(n):
        stem = rng.choice(COMPANY_STEMS) + str(rng.randint(1, 999))
        tld = rng.choice(TLDS)
        owner = rng.choice(list(OWNERS))
        region = OWNERS[owner]
        rows.append({
            "Record ID": new_id(),
            "Company name": stem.capitalize() + " " + rng.choice(["Inc.", "LLC", "Ltd", "GmbH", "Group"]),
            "Company Domain Name": f"{stem}.{tld}",
            "Company owner": owner,
            "Industry": rng.choice(INDUSTRIES),
            "Number of Employees": rng.choice([12, 45, 130, 500, 2500, 8000]),
            "Country/Region": rng.choice(REGION_COUNTRIES[region]),
            "Create Date": _fmt(_rand_date(rng, 30, 1200, today)),
            "Last Activity Date": _fmt(_rand_date(rng, 1, 400, today)),
        })

    # --- Planted: duplicate domains (exact) ---
    for _ in range(max(2, n // 12)):
        src = rng.choice(rows)
        dup = dict(src)
        dup["Record ID"] = new_id()
        dup["Company name"] = src["Company name"].replace("Inc.", "Incorporated")
        rows.append(dup)
        gt.add("company_duplicate", dup["Record ID"], f"same domain as {src['Record ID']}: {src['Company Domain Name']}")

    # --- Planted: name variants (dedup by fuzzy name, domain may differ) ---
    for _ in range(max(1, n // 20)):
        src = rng.choice(rows)
        variant = dict(src)
        variant["Record ID"] = new_id()
        base = src["Company name"].split()[0]
        variant["Company name"] = f"{base}, Inc."
        variant["Company Domain Name"] = src["Company Domain Name"].replace(".", "-hq.", 1)
        rows.append(variant)
        gt.add("company_name_variant", variant["Record ID"], f"name variant of {src['Record ID']} ({base})")

    # --- Planted: missing fields ---
    for _ in range(max(2, n // 15)):
        r = rng.choice(rows)
        r["Company Domain Name"] = ""
        gt.add("company_missing_domain", r["Record ID"])
    for _ in range(max(2, n // 15)):
        r = rng.choice(rows)
        r["Company owner"] = ""
        gt.add("company_missing_owner", r["Record ID"])
    for _ in range(max(2, n // 18)):
        r = rng.choice(rows)
        r["Industry"] = ""
        gt.add("company_missing_industry", r["Record ID"])

    index = {r["Record ID"]: r for r in rows}
    return rows, index


def generate_contacts(rng: random.Random, n: int, today: date, companies: list[dict], gt: GroundTruth):
    """
    Contacts with planted issues:
      - exact duplicate email (two records)
      - case/whitespace email variant (Jane@X.com vs  jane@x.com )
      - role-based email (info@, sales@)
      - likely-invalid email (typo'd domain)
      - decayed: no activity in > 9 months
      - missing email / owner / lifecycle stage / country
      - bad routing: country doesn't match owner's region
    """
    rows: list[dict] = []
    next_id = 5000

    def new_id() -> int:
        nonlocal next_id
        next_id += 1
        return next_id

    company_ids = [c["Record ID"] for c in companies]
    company_by_id = {c["Record ID"]: c for c in companies}

    for _ in range(n):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        comp = rng.choice(company_ids)
        comp_row = company_by_id[comp]
        domain = comp_row["Company Domain Name"] or f"{last.lower()}.com"
        owner = comp_row["Company owner"] or rng.choice(list(OWNERS))
        region = OWNERS.get(owner, "North America")
        rows.append({
            "Record ID": new_id(),
            "First Name": first,
            "Last Name": last,
            "Email": f"{first.lower()}.{last.lower()}@{domain}",
            "Phone Number": f"+1{rng.randint(2000000000, 9999999999)}",
            "Job Title": rng.choice(JOB_TITLES),
            "Contact owner": owner,
            "Lifecycle Stage": rng.choice(LIFECYCLE_STAGES),
            "Lead Status": rng.choice(LEAD_STATUSES),
            "Country/Region": rng.choice(REGION_COUNTRIES[region]),
            "Associated Company ID": comp,
            "Create Date": _fmt(_rand_date(rng, 30, 1200, today)),
            "Last Activity Date": _fmt(_rand_date(rng, 1, 240, today)),
        })

    # --- Planted: exact duplicate emails ---
    for _ in range(max(3, n // 12)):
        src = rng.choice(rows)
        dup = dict(src)
        dup["Record ID"] = new_id()
        dup["Job Title"] = rng.choice(JOB_TITLES)  # slightly different, same person
        rows.append(dup)
        gt.add("contact_duplicate_email", dup["Record ID"], f"same email as {src['Record ID']}: {src['Email']}")

    # --- Planted: case/whitespace email variants (still the same address) ---
    for _ in range(max(2, n // 18)):
        src = rng.choice(rows)
        variant = dict(src)
        variant["Record ID"] = new_id()
        variant["Email"] = f"  {src['Email'].upper()} "  # padded + upper-cased
        rows.append(variant)
        gt.add("contact_duplicate_email", variant["Record ID"], f"normalized-equal email to {src['Record ID']}")

    # --- Planted: role-based emails ---
    for _ in range(max(2, n // 15)):
        r = rng.choice(rows)
        role = rng.choice(ROLE_LOCALPARTS)
        domain = r["Email"].split("@")[-1].strip()
        r["Email"] = f"{role}@{domain}"
        gt.add("contact_role_based_email", r["Record ID"], r["Email"])

    # --- Planted: likely-invalid emails (typo'd TLD/domain) ---
    for _ in range(max(2, n // 18)):
        r = rng.choice(rows)
        r["Email"] = r["Email"].replace(".com", ".cim").replace(".io", ".ii")
        if "@" in r["Email"] and "example" not in r["Email"]:
            gt.add("contact_invalid_email", r["Record ID"], r["Email"])

    # --- Planted: decayed (no activity in > 9 months) ---
    for _ in range(max(3, n // 10)):
        r = rng.choice(rows)
        r["Last Activity Date"] = _fmt(_rand_date(rng, 280, 900, today))
        gt.add("contact_decayed", r["Record ID"], f"last activity {r['Last Activity Date']}")

    # --- Planted: missing fields ---
    for _ in range(max(3, n // 12)):
        r = rng.choice(rows)
        r["Email"] = ""
        gt.add("contact_missing_email", r["Record ID"])
    for _ in range(max(2, n // 15)):
        r = rng.choice(rows)
        r["Contact owner"] = ""
        gt.add("contact_missing_owner", r["Record ID"])
    for _ in range(max(2, n // 18)):
        r = rng.choice(rows)
        r["Lifecycle Stage"] = ""
        gt.add("contact_missing_lifecycle", r["Record ID"])
    for _ in range(max(2, n // 18)):
        r = rng.choice(rows)
        r["Country/Region"] = ""
        gt.add("contact_missing_country", r["Record ID"])

    # --- Planted: bad routing (country not in owner's region) ---
    for _ in range(max(2, n // 12)):
        r = rng.choice(rows)
        owner = r["Contact owner"] or rng.choice(list(OWNERS))
        r["Contact owner"] = owner
        owner_region = OWNERS.get(owner, "North America")
        wrong_regions = [reg for reg in REGION_COUNTRIES if reg != owner_region]
        r["Country/Region"] = rng.choice(REGION_COUNTRIES[rng.choice(wrong_regions)])
        gt.add("contact_bad_routing", r["Record ID"],
               f"country {r['Country/Region']} outside {owner}'s region ({owner_region})")

    return rows


def generate_deals(rng: random.Random, n: int, today: date, companies: list[dict], gt: GroundTruth):
    """
    Deals with planted issues:
      - stale: open deal, no activity in > 60 days
      - past close date: close date in the past but still open
      - missing amount / owner / close date
    """
    rows: list[dict] = []
    next_id = 9000

    def new_id() -> int:
        nonlocal next_id
        next_id += 1
        return next_id

    company_ids = [c["Record ID"] for c in companies]
    company_by_id = {c["Record ID"]: c for c in companies}

    for _ in range(n):
        comp = rng.choice(company_ids)
        comp_row = company_by_id[comp]
        owner = comp_row["Company owner"] or rng.choice(list(OWNERS))
        is_closed = rng.random() < 0.35
        stage = rng.choice(DEAL_STAGES_CLOSED if is_closed else DEAL_STAGES_OPEN)
        create = _rand_date(rng, 20, 500, today)
        close = create + timedelta(days=rng.randint(20, 120))
        rows.append({
            "Record ID": new_id(),
            "Deal Name": f"{comp_row['Company name'].split()[0]} - {rng.choice(['Expansion', 'New Business', 'Renewal', 'Upsell'])}",
            "Deal Stage": stage,
            "Amount": rng.choice([5000, 12000, 25000, 48000, 90000, 150000]),
            "Deal owner": owner,
            "Pipeline": rng.choice(PIPELINES),
            "Close Date": _fmt(close),
            "Create Date": _fmt(create),
            "Last Activity Date": _fmt(_rand_date(rng, 1, 45, today)),
            "Associated Company ID": comp,
        })

    open_rows = [r for r in rows if r["Deal Stage"] in DEAL_STAGES_OPEN]

    # --- Planted: stale open deals (no activity > 60 days) ---
    for _ in range(max(2, n // 8)):
        r = rng.choice(open_rows) if open_rows else rng.choice(rows)
        r["Deal Stage"] = rng.choice(DEAL_STAGES_OPEN)
        r["Last Activity Date"] = _fmt(_rand_date(rng, 75, 300, today))
        gt.add("deal_stale", r["Record ID"], f"open, last activity {r['Last Activity Date']}")

    # --- Planted: close date in the past but still open ---
    for _ in range(max(2, n // 10)):
        r = rng.choice(open_rows) if open_rows else rng.choice(rows)
        r["Deal Stage"] = rng.choice(DEAL_STAGES_OPEN)
        r["Close Date"] = _fmt(_rand_date(rng, 10, 180, today))
        gt.add("deal_past_close_date", r["Record ID"], f"open, close date {r['Close Date']} in past")

    # --- Planted: missing fields ---
    for _ in range(max(2, n // 12)):
        r = rng.choice(rows)
        r["Amount"] = ""
        gt.add("deal_missing_amount", r["Record ID"])
    for _ in range(max(1, n // 15)):
        r = rng.choice(rows)
        r["Deal owner"] = ""
        gt.add("deal_missing_owner", r["Record ID"])
    for _ in range(max(1, n // 15)):
        r = rng.choice(rows)
        r["Close Date"] = ""
        gt.add("deal_missing_close_date", r["Record ID"])

    return rows


# --------------------------------------------------------------------------- #
# CSV writing + CLI
# --------------------------------------------------------------------------- #

def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    # Union of keys preserves every column even if some rows were blanked out.
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic HubSpot-shaped CRM data with planted hygiene issues.")
    parser.add_argument("--contacts", type=int, default=250, help="approx. number of clean contacts before planting (default 250)")
    parser.add_argument("--companies", type=int, default=80, help="approx. number of clean companies before planting (default 80)")
    parser.add_argument("--deals", type=int, default=120, help="approx. number of clean deals before planting (default 120)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility (default 42)")
    parser.add_argument("--out", type=str, default=str(Path(__file__).parent / "samples"), help="output directory")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    today = date(2026, 7, 30)  # fixed 'today' so date-based checks are reproducible
    gt = GroundTruth()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    companies, _ = generate_companies(rng, args.companies, today, gt)
    contacts = generate_contacts(rng, args.contacts, today, companies, gt)
    deals = generate_deals(rng, args.deals, today, companies, gt)

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
