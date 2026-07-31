# CRM Hygiene Agent

### ▶ [**Try the live demo →**](https://crm-hygiene-agent.streamlit.app/)

**Upload a HubSpot CRM export → get a CRM Health Score, a dollar-quantified estimate of what the mess is costing you, and a prioritized remediation punch list.** No paid services, runs anywhere, reproducible every time.

> Built as a RevOps engineering case study: it demonstrates domain fluency (knowing *what* good CRM hygiene is and *why* it maps to revenue), engineering (a clean, tested data pipeline), and communication (a decision-ready report, not a wall of errors).

[![CRM Hygiene Agent](docs/screenshot.png)](https://crm-hygiene-agent.streamlit.app/)

*The demo opens with sample data already loaded — it produces a full report on first click, no file needed.*

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

### Territory routing needs to learn your team

Routing is the one check that can't work out of the box, because it depends on something no tool can know: **which region each of your reps covers.**

So it's split in two. **Countries → regions** ships built in — a world atlas covering ~120 countries, with the spellings real exports actually contain (`USA`, `U.S.`, `United States` and `America` all resolve to the same place). **Reps → regions** is yours: the app reads the owner names straight out of your upload and asks you to assign each one from a dropdown.

Reading the names from the data rather than having you type them removes the failure mode that matters here — a name that doesn't quite match the export would silently disable the check for that rep, and a check reporting zero because it was never configured looks exactly like a check reporting zero because everything is fine. Unassigned owners are skipped rather than guessed at, and the app says so on the report instead of implying your routing is clean.

## Why not just use HubSpot's built-in tools?

Fair question, and part of the answer is: if you have them, use them.

HubSpot ships real data-quality tooling — a Data Quality Command Center, duplicate management, formatting automations, deal-rot settings on pipelines. Where it's stronger than this, it's genuinely stronger: it works on live data instead of an export, it can write changes back, and its duplicate matching is more sophisticated than the exact-and-normalized matching here. Anyone already on Operations Hub Professional should be using it.

Three gaps this fills:

**1. It quantifies.** HubSpot shows you a list of issues. It doesn't tell you that list represents ~42 hours of avoidable work and $924,750 of pipeline whose timing can't be trusted. Data hygiene competes for attention against every other item on a RevOps roadmap, and without a number attached it loses that fight every quarter.

**2. It prioritizes.** "Here is everything wrong" is not a plan. This ranks categories by what fixing them is worth and states what each recovers on the score, so the first hour of cleanup goes to the right place — and the score gives you something to trend in a QBR.

**3. It travels.** Most of that tooling sits in paid Operations Hub tiers, and all of it only works inside HubSpot. This runs on a CSV: it works on a free portal, works for a consultant with no portal access, works before a migration, and works on any export with a similar shape.

The rules are also legible. Every threshold is a value in `engine/config.py` and every dollar figure is arithmetic you can follow, so when someone challenges a finding you can show them the line rather than shrugging at a vendor's black box.

**The honest framing:** this isn't a replacement for HubSpot's tooling. It's the quantification layer nobody ships — turning *"our CRM is messy"* into *"here's the number, here's what to fix first, and here's what it recovers."*

## The output

```
CRM Health Score 78/100 (grade C) — 246 issues across 213 of 493 records,
representing ~42 hours (≈$3,182) of avoidable work and $924,750 of pipeline at risk.

#  Issue                              Recs     Sev   Hours       Cost     At risk  +Score
1  Stale Deals                          15    High     3.8       $281    $556,000     1.5
2  Deals Past Close Date                12    High     2.0       $150    $368,750     1.2
3  Missing Required Fields — Contact    62    High     9.0       $678          $0     5.5
4  Duplicate Contacts                   33    High     8.2       $619          $0     3.4
5  Territory Routing — Contact          20    High     6.7       $500          $0     1.8
```

The punch list is ranked by **what fixing it is worth**, not by row count — so a small, expensive category outranks a large cosmetic one. The `+Score` column is *measured*, not estimated: the scorer re-runs with that category removed and takes the difference, which matters because per-record penalty capping makes the relationship non-linear. That turns the report from a complaint into a plan: *"merge these 33 duplicates, recover 3.4 points and 8 hours."*

## From findings to fixes

A diagnosis you can't act on is just a complaint, so the app also generates the files you work from — downloadable individually or as one ZIP with a README. They come in three kinds, and the distinction between them *is* the safety model:

| | Kind | What it is |
|---|---|---|
| 🟢 | **Ready to import** | Mechanical, information-preserving corrections — trimming whitespace off an email, writing `United States` where the record said `USA`. The spelling changes, the meaning never does. Feed straight to HubSpot's importer. |
| 🟡 | **Review first** | A correction the tool *inferred* but can't be sure of, like reading `@acme.cim` as a typo of `@acme.com`. Ships with current and proposed values side by side. Never pre-applied. |
| 🔵 | **Worklist** | No mechanical fix exists. Merging duplicates, reassigning an account, deciding whether a silent deal is dead — these need judgment, so the file is a checklist, not an import. |

Four rules keep this from being dangerous:

- **Import files carry only the Record ID and the columns being changed.** An import is a write, and a wide file is a wide blast radius.
- **Records queued for merging are excluded from email normalization.** Writing the canonical form onto a duplicate would make it collide with the master it's about to be merged into.
- **A suggestion is only offered when it's confident.** `.cim` is one edit from `.com` and gets a proposal; `.nte` is two from `.net` and doesn't. Guessing wrong here corrupts a real address.
- **The missing-fields template marks untouched fields `n/a` rather than leaving them blank.** An empty cell in a HubSpot import can *clear* a value — so cells that were never the problem are impossible to submit by accident.

The duplicate worklist is deliberately **not** importable. Merging can't be undone in HubSpot, so it's a one-at-a-time job with the master record named for you.

## How the numbers are built

A single figure a sharp CRO can poke a hole in discredits the whole report, so the model follows three rules:

**1. Two numbers, two methods, never mixed.**
- *Direct cost* — every finding carries an estimate of the minutes to remediate it, priced at a loaded hourly rate. One consistent unit, additive, easy to defend.
- *At-risk pipeline* — deal value × a risk factor, labelled **at risk**, never "lost."

**2. Risk-adjusted, never face value.** A 90-day-silent $150k deal is not a $150k loss. Exposure is banded by neglect (30d → 25%, 60d → 50%, 90d+ → 75%) and **capped below 100%**, because even a badly neglected deal isn't certainly dead.

**3. Additive by construction.** A deal that is both stale *and* past its close date has one exposure, not two. The at-risk amount is attributed to the single worst finding and zeroed on the others, so summing any subset of findings is always correct.

Every assumption lives in `ImpactAssumptions` and is printed with the report, so a reader who disagrees with a number can change it rather than disbelieving the output.

### The health score

Each record starts at full health and loses points per finding, weighted by severity, **capped at two critical issues** so a few catastrophic records can't dominate. Scores are pooled across all records, which makes the score volume-normalized — a bigger CRM isn't automatically a worse one.

## Architecture

The rule **engine knows nothing about the web app**. It takes DataFrames in and returns structured findings out, so the same engine can be driven by the web app, a CLI, or a test suite with zero changes.

```
crm-hygiene-agent/
├── app.py                 # Streamlit UI (thin — collect inputs, call engine, render)
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
pip install -r requirements.txt
streamlit run app.py
```

The app opens with the sample CRM already loaded, so it produces a full report on first click — no file hunting required. Switch the sidebar to **Upload your own export** to audit real HubSpot CSVs (Contacts, Companies, Deals — any subset works).

Every threshold and cost assumption is a sidebar control, so you can retune the policy to your own motion and watch the score and the punch list move.

```bash
# optional: regenerate the sample data — needs no dependencies
python data/generate_sample.py
```

Tested on Python 3.11 with the versions pinned in `requirements.txt`.

## Deploying

The app runs free on [Streamlit Community Cloud](https://share.streamlit.io):

| Field | Value |
|---|---|
| Repository | `mareopuzo/project` |
| Branch | `main` |
| **Main file path** | **`app.py`** |
| Python version | 3.11 (under *Advanced settings*) |

The deploy form defaults the main file to `streamlit_app.py` — that's a placeholder, not this repo's entry point. Set it to `app.py`.

If the branch dropdown claims a branch doesn't exist, reload the page: the branch list is fetched once when the form opens, so it goes stale after a push.

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
- [x] **Step 4 — Scoring + \$ impact model + report** (health score, risk-adjusted exposure, ranked punch list; 84 tests)
- [x] **Step 5 — Streamlit app** (upload or sample, live-tunable policy, filterable findings, CSV/JSON export; 92 tests)
- [x] **Step 6 — Deployed** — [live at crm-hygiene-agent.streamlit.app](https://crm-hygiene-agent.streamlit.app/)
