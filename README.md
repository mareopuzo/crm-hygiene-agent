# CRM Hygiene Agent

**Upload a HubSpot CRM export → get a CRM Health Score, a dollar-quantified estimate of what the mess is costing you, and a prioritized remediation punch list.** No paid services, runs anywhere, reproducible every time.

> Built as a RevOps engineering case study: it demonstrates domain fluency (knowing *what* good CRM hygiene is and *why* it maps to revenue), engineering (a clean, tested data pipeline), and communication (a decision-ready report, not a wall of errors).

---

## The problem

Every B2B company's CRM rots over time. Reps create duplicate contacts, deals sit untouched for months, emails go stale and bounce, required fields get skipped, and leads land on the wrong owner. Nobody's job is to clean it, so it compounds silently until:

- **Forecasts lie** — stale/ghost deals inflate the pipeline number leadership plans around.
- **Reps waste hours** — chasing dead contacts and re-entering duplicates.
- **Marketing burns sender reputation** — emailing decayed/role-based contacts drives bounces and spam flags.
- **Routing leaks revenue** — a hot lead with the wrong (or no) owner just sits there.

The killer detail: most teams can't even *measure* how bad it is, so it never gets prioritized. This tool turns invisible data debt into a number an ops leader can act on.

## What it checks

| Check | Flags | Revenue consequence |
|---|---|---|
| **Duplicates** | Same contact/company via email, domain, or fuzzy name match | Wasted rep effort, split activity history |
| **Decayed contacts** | Role-based emails, likely-invalid addresses, no activity in N months | Sender-reputation risk, dead outreach |
| **Missing fields** | Blank required fields (email, owner, lifecycle, country…) | Broken routing, un-scorable leads |
| **Bad routing** | Owner missing, or territory ≠ assigned owner's region | Leads rotting unassigned, SLA breaches |
| **Stale deals** | Open deals past age / close date with no activity | Inflated forecast, pipeline hygiene |
| **Report** | Health score + \$ impact + prioritized punch list | Makes all of the above actionable |

## Architecture

The rule **engine knows nothing about the web app**. It takes DataFrames in and returns structured findings out, so the same engine can be driven by the web app, a CLI, or a test suite with zero changes.

```
crm-hygiene-agent/
├── app.py                 # Streamlit UI (thin — upload, call engine, render)
├── engine/                # the brain (pure Python, no UI deps)
│   ├── loader.py          # read + normalize HubSpot CSV schema
│   ├── models.py          # Finding / Check / Severity types
│   ├── scoring.py         # health score (0–100) + $ impact model
│   ├── report.py          # findings → structured report
│   └── checks/            # one file per check, all sharing one contract
├── data/
│   ├── generate_sample.py # synthetic HubSpot-shaped data w/ planted issues
│   └── samples/           # ready-to-audit demo CSVs + ground-truth manifest
└── tests/                 # each check proven against the ground truth
```

## Quick start

```bash
# 1. (optional) regenerate the sample data — needs no dependencies
python data/generate_sample.py

# 2. install engine + app deps
pip install -r requirements.txt

# 3. run the web app  (coming in a later build step)
streamlit run app.py
```

### The sample data

`data/generate_sample.py` builds three CSVs shaped like a real HubSpot export (Contacts, Companies, Deals) with a **controlled set of planted hygiene issues**, plus a `ground_truth.json` manifest listing every issue by record ID. That manifest is the oracle the test suite checks the engine against — so we can prove the agent catches what's actually wrong without over-flagging clean records.

It's **deterministic** (seeded), so the demo and tests are reproducible:

```bash
python data/generate_sample.py --seed 42 --contacts 250 --companies 80 --deals 120
```

The manifest is *exact*, not approximate — two properties make it so. Base records are clean by construction (emails and domains are de-duplicated as they're generated, so random name collisions can't create accidental duplicates), and a `Planter` tracks which columns of which records are already spoken for, so no plant can silently clobber an earlier one. That's what lets the tests assert **set equality** between what each check flags and what was planted — proving no false negatives *and* no false positives at once, rather than "it found most of them."

## Testing

```bash
python -m pytest -q      # 55 tests
```

Every check is verified against the ground-truth manifest, plus hand-built edge cases: threshold boundaries, legitimate-but-unusual TLDs (`.co` is one edit from `.com` and must not be flagged), blank-vs-wrong distinctions, and a guarantee that no check mutates its input.

**No double-counting.** Each check owns exactly one failure mode. A blank owner is reported once by the missing-fields policy — the routing check deliberately skips unowned records rather than flagging them again, so one underlying problem never inflates the score twice.

## Build status

- [x] **Step 1 — Sample data generator** (HubSpot-shaped CSVs + ground-truth manifest)
- [x] **Step 2 — Loader + core models** (`Finding`, `Check` contract, `Config`, HubSpot schema normalization; 8 tests)
- [x] **Step 3 — All 11 checks** (duplicates, missing fields, decay, routing, stale deals; 55 tests)
- [ ] Step 4 — Scoring + \$ impact model + report
- [ ] Step 5 — Streamlit app
- [ ] Step 6 — Deploy (live link)
