"""
CRM Hygiene Agent — Streamlit front end.

This file is deliberately thin. It collects inputs, calls `build_report`, and
renders the result; every decision about what counts as an issue and what it
costs lives in the engine. That separation is the point — the same engine backs
this app, the test suite, and any future CLI or API without modification.

Presentation notes:
  - The score is a hero number, not a chart. The punch list is a table with
    magnitude bars rather than a bar chart, because the reader needs the exact
    figures alongside the ranking.
  - Status colour never carries meaning on its own: the grade chip always spells
    out the letter and a word, so the traffic-light is reinforcement rather than
    the message. Chip text is near-black on every status colour, which keeps it
    legible in both Streamlit themes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from engine.config import build_config
from engine.loader import load_crm_data
from engine.report import build_report
from engine.scoring import ImpactAssumptions

SAMPLES = Path(__file__).parent / "data" / "samples"

# Status palette, paired with a word so colour is never the only signal.
GRADE_STATUS = {
    "A": ("#0ca30c", "Healthy"),
    "B": ("#0ca30c", "Minor cleanup"),
    "C": ("#fab219", "Needs attention"),
    "D": ("#ec835a", "At risk"),
    "F": ("#d03b3b", "Critical"),
}

SEVERITY_STATUS = {
    "High": "#d03b3b",
    "Medium": "#fab219",
    "Low": "#0ca30c",
}

st.set_page_config(
    page_title="CRM Hygiene Agent",
    page_icon="🧹",
    layout="wide",
)

st.markdown(
    """
    <style>
      .tile {
        padding: 0.9rem 1.1rem;
        border-radius: 10px;
        border: 1px solid rgba(128,128,128,0.25);
        height: 100%;
      }
      .tile-label {
        font-size: 0.72rem;
        letter-spacing: 0.09em;
        text-transform: uppercase;
        opacity: 0.65;
        margin-bottom: 0.15rem;
      }
      .tile-value { font-size: 2.6rem; font-weight: 700; line-height: 1.05; }
      .tile-max { font-size: 1.1rem; font-weight: 500; opacity: 0.55; }
      .chip {
        display: inline-block;
        margin-top: 0.5rem;
        padding: 0.16rem 0.6rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 700;
        color: #111111;
      }
      .headline {
        font-size: 1.05rem;
        line-height: 1.55;
        padding: 0.85rem 1.1rem;
        border-radius: 10px;
        border: 1px solid rgba(128,128,128,0.25);
        margin-bottom: 1.1rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner=False)
def load_sample() -> tuple[bytes, bytes, bytes, str | None]:
    contacts = (SAMPLES / "contacts.csv").read_bytes()
    companies = (SAMPLES / "companies.csv").read_bytes()
    deals = (SAMPLES / "deals.csv").read_bytes()
    manifest = SAMPLES / "ground_truth.json"
    # Pin the reference date to the generator's "today" so the demo matches the
    # documented figures exactly.
    as_of = json.loads(manifest.read_text())["meta"]["today"] if manifest.exists() else None
    return contacts, companies, deals, as_of


@st.cache_data(show_spinner=False)
def build(contacts, companies, deals, as_of, stale_days, decay_days, hourly_rate, routing):
    """Cached end-to-end run. Keyed on the raw bytes plus every tunable."""
    data = load_crm_data(contacts, companies, deals, as_of=as_of)
    config = build_config(
        stale_deal_days=stale_days,
        decayed_contact_days=decay_days,
        territory_routing=routing,
    )
    return build_report(data, config, ImpactAssumptions(loaded_rep_hourly_cost=hourly_rate))


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

st.sidebar.title("🧹 CRM Hygiene Agent")

source = st.sidebar.radio(
    "Data source",
    ["Sample CRM", "Upload your own export"],
    help="The sample is synthetic HubSpot-shaped data with a known set of planted issues.",
)

EMPTY = {
    "contacts": b"Record ID,Email\n",
    "companies": b"Record ID,Company name\n",
    "deals": b"Record ID,Deal Name\n",
}

if source == "Sample CRM":
    contacts_bytes, companies_bytes, deals_bytes, as_of = load_sample()
else:
    st.sidebar.caption("Export Contacts, Companies and Deals from HubSpot as CSV. Any subset works.")
    up_contacts = st.sidebar.file_uploader("Contacts CSV", type="csv")
    up_companies = st.sidebar.file_uploader("Companies CSV", type="csv")
    up_deals = st.sidebar.file_uploader("Deals CSV", type="csv")

    if not any([up_contacts, up_companies, up_deals]):
        st.title("CRM Hygiene Agent")
        st.info("Upload at least one CSV in the sidebar, or switch to **Sample CRM** to see a full report immediately.")
        st.stop()

    contacts_bytes = up_contacts.getvalue() if up_contacts else EMPTY["contacts"]
    companies_bytes = up_companies.getvalue() if up_companies else EMPTY["companies"]
    deals_bytes = up_deals.getvalue() if up_deals else EMPTY["deals"]
    as_of = None  # inferred from the data

st.sidebar.divider()
st.sidebar.subheader("Hygiene thresholds")
stale_days = st.sidebar.slider(
    "Stale deal after (days without activity)", 7, 180, 30,
    help="Open deals untouched for longer than this are inflating the forecast.",
)
decay_months = st.sidebar.slider(
    "Contact decays after (months without activity)", 1, 24, 6,
    help="Contacts with no activity in this long are re-engage-or-suppress candidates.",
)
routing_enabled = st.sidebar.checkbox(
    "Check territory routing", value=True,
    help="Turn off if you don't route leads geographically — otherwise every owner looks mis-assigned.",
)

st.sidebar.divider()
st.sidebar.subheader("Cost assumptions")
hourly_rate = st.sidebar.number_input(
    "Loaded rep cost ($/hour)", min_value=10.0, max_value=500.0, value=75.0, step=5.0,
    help="Fully loaded, not salary: roughly a $120k all-in rep across ~1,600 productive hours.",
)

# A public demo must never answer a bad file with a stack trace. Anything the
# engine can't handle becomes an explanation of what to check, with the
# technical detail tucked behind a disclosure for whoever is debugging it.
try:
    report = build(
        contacts_bytes, companies_bytes, deals_bytes, as_of,
        stale_days, round(decay_months * 30.44), hourly_rate, routing_enabled,
    )
except Exception as exc:  # noqa: BLE001 — the UI boundary catches everything
    st.title("CRM Hygiene Agent")
    st.error("I couldn't read that data. The audit didn't run.")
    st.markdown(
        """
**Worth checking:**

- The files are **CSV**, exported from HubSpot (not Excel `.xlsx`).
- Each file still has its **header row** — that's how columns are recognized.
- There's a **Record ID** column. Every finding is reported against it.
- The right file went in the right slot (contacts in Contacts, deals in Deals).

Switch the sidebar to **Sample CRM** to confirm the app itself is working.
        """
    )
    with st.expander("Technical details"):
        st.exception(exc)
    st.stop()


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #

st.title("CRM Hygiene Agent")
st.caption(
    f"Audited {report.score.total_records:,} records with {report.checks_run} checks · "
    f"reference date {report.as_of.date()}"
)

if source == "Sample CRM":
    st.caption("Showing synthetic HubSpot-shaped sample data with a known set of planted issues.")

st.markdown(f'<div class="headline">{report.headline}</div>', unsafe_allow_html=True)

color, status_label = GRADE_STATUS[report.score.grade]

top = st.columns([1.4, 1, 1, 1])
with top[0]:
    st.markdown(
        f"""
        <div class="tile" style="border-left:6px solid {color}">
          <div class="tile-label">CRM Health Score</div>
          <div class="tile-value">{report.score.overall:.0f}<span class="tile-max">/100</span></div>
          <span class="chip" style="background:{color}">Grade {report.score.grade} · {status_label}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
top[1].metric("Records audited", f"{report.score.total_records:,}")
top[2].metric("Issues found", f"{len(report.findings):,}")
top[3].metric(
    "Clean records",
    f"{report.score.clean_rate:.0%}",
    help=f"{report.score.clean_records:,} records with no findings at all.",
)

# Same column ratio as the row above so the two metric rows line up as a grid,
# with the score tile occupying the leading cell.
cost = st.columns([1.4, 1, 1, 1])[1:]
cost[0].metric(
    "Avoidable work", f"{report.cost.remediation_hours:,.0f} hrs",
    help="Estimated effort to remediate every finding.",
)
cost[1].metric(
    "Direct cost", f"${report.cost.direct_cost_usd:,.0f}",
    help=f"Those hours at ${hourly_rate:,.0f}/hour loaded.",
)
cost[2].metric(
    "Pipeline at risk", f"${report.cost.at_risk_pipeline_usd:,.0f}",
    help=(
        f"{report.cost.at_risk_share:.0%} of ${report.cost.total_open_pipeline_usd:,.0f} open pipeline. "
        "Risk-adjusted exposure, not forecast loss."
    ),
)

st.divider()


# --------------------------------------------------------------------------- #
# Punch list
# --------------------------------------------------------------------------- #

st.subheader("Prioritized punch list")
st.caption(
    "Ranked by what fixing it is worth, not by how many rows it produced. "
    "**Score gain** is measured — the scorer re-runs with that category resolved."
)

if not report.items:
    st.success("No issues found. Every record passed every check.")
else:
    punch = report.punch_list_dataframe().copy()
    punch["value_at_stake"] = punch["cost_usd"] + punch["at_risk_usd"]
    display = punch[[
        "issue", "object", "records", "severity", "value_at_stake",
        "hours_to_fix", "cost_usd", "at_risk_usd", "score_gain",
    ]]

    st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        column_config={
            "issue": st.column_config.TextColumn("Issue", width="medium"),
            "object": st.column_config.TextColumn("Object", width="small"),
            "records": st.column_config.NumberColumn("Records", format="%d"),
            "severity": st.column_config.TextColumn("Severity", width="small"),
            "value_at_stake": st.column_config.ProgressColumn(
                "Value at stake",
                help="Direct cost plus risk-adjusted pipeline exposure.",
                format="dollar",
                min_value=0,
                max_value=float(display["value_at_stake"].max()),
            ),
            "hours_to_fix": st.column_config.NumberColumn("Hours", format="%.1f"),
            "cost_usd": st.column_config.NumberColumn("Cost", format="dollar"),
            "at_risk_usd": st.column_config.NumberColumn("At risk", format="dollar"),
            "score_gain": st.column_config.NumberColumn(
                "Score gain", format="+%.1f",
                help="Points the health score would recover if this category were resolved.",
            ),
        },
    )

    top_item = report.items[0]
    st.info(
        f"**Start here:** {top_item.name} — {top_item.count} records, "
        f"~{top_item.remediation_hours:,.1f} hours of work, "
        f"recovering {top_item.score_gain:+.1f} score points."
    )


# --------------------------------------------------------------------------- #
# Breakdown
# --------------------------------------------------------------------------- #

left, right = st.columns(2)

with left:
    st.subheader("Score by object")
    for object_type, object_score in report.score.by_object.items():
        count = report.record_counts.get(object_type, 0)
        st.markdown(f"**{object_type.title()}** · {count:,} records — {object_score:.0f}/100")
        st.progress(min(max(object_score / 100.0, 0.0), 1.0))

with right:
    st.subheader("Findings by severity")
    for label in ("High", "Medium", "Low"):
        count = report.findings_by_severity.get(label, 0)
        if not count and label == "Low":
            continue
        share = count / len(report.findings) if report.findings else 0
        swatch = SEVERITY_STATUS[label]
        st.markdown(
            f'<span class="chip" style="background:{swatch}">{label}</span> '
            f"&nbsp;**{count:,}** findings ({share:.0%})",
            unsafe_allow_html=True,
        )

with st.expander("How these numbers are built"):
    st.markdown(
        f"""
**Two numbers, two methods — never mixed.**

- **Direct cost** — every finding carries an estimate of the minutes to remediate it,
  priced at **${hourly_rate:,.0f}/hour** loaded. One consistent unit, so it's additive.
- **Pipeline at risk** — deal value multiplied by a risk factor banded by neglect
  ({', '.join(f'{d}d → {int(f * 100)}%' for d, f in report.assumptions.forecast_risk_bands)}),
  and **capped below face value**. A stale deal is unreliable, not dead. It's labelled
  *at risk*, never "lost".

**No double-counting.** A deal that is both stale *and* past its close date has one
exposure, not two — it's attributed to the single worst finding and zeroed on the
others, so any subset of these rows sums correctly. Likewise each check owns exactly
one failure mode: a blank owner is reported by the required-fields policy, and the
routing check deliberately skips unowned records rather than flagging them twice.

**The health score** starts every record at full health and deducts severity-weighted
points per finding, capped at two critical issues so a few catastrophic records can't
dominate. Scores pool across all records, which makes the score volume-normalized —
a bigger CRM isn't automatically a worse one.

Every assumption above is a control in the sidebar. Disagree with a number and change it.
        """
    )


# --------------------------------------------------------------------------- #
# Findings explorer
# --------------------------------------------------------------------------- #

st.divider()
st.subheader("All findings")

findings_df = report.findings_dataframe()

if findings_df.empty:
    st.success("Nothing to show — no findings.")
else:
    # Filters default to empty meaning "everything" — pre-selecting every option
    # renders a wall of chips that pushes the table off screen.
    filters = st.columns(3)
    objects = filters[0].multiselect(
        "Object", sorted(findings_df["object_type"].unique()), placeholder="All objects",
    )
    severities = filters[1].multiselect(
        "Severity", [s for s in ("High", "Medium", "Low") if s in set(findings_df["severity"])],
        placeholder="All severities",
    )
    issues = filters[2].multiselect(
        "Issue type", sorted(findings_df["check_id"].unique()), placeholder="All issue types",
    )

    def _keep(column: str, selected: list[str]) -> pd.Series:
        if not selected:
            return pd.Series(True, index=findings_df.index)
        return findings_df[column].isin(selected)

    filtered = findings_df[
        _keep("object_type", objects) & _keep("severity", severities) & _keep("check_id", issues)
    ]

    st.caption(f"{len(filtered):,} of {len(findings_df):,} findings")
    st.dataframe(
        filtered[[
            "severity", "object_type", "record_id", "check_id",
            "message", "revenue_impact_usd", "at_risk_usd",
        ]],
        hide_index=True,
        width="stretch",
        height=420,
        column_config={
            "severity": st.column_config.TextColumn("Severity", width="small"),
            "object_type": st.column_config.TextColumn("Object", width="small"),
            "record_id": st.column_config.TextColumn("Record ID", width="small"),
            "check_id": st.column_config.TextColumn("Check", width="medium"),
            "message": st.column_config.TextColumn("What's wrong", width="large"),
            "revenue_impact_usd": st.column_config.NumberColumn("Fix cost", format="$%.2f"),
            "at_risk_usd": st.column_config.NumberColumn("At risk", format="dollar"),
        },
    )

    downloads = st.columns(2)
    downloads[0].download_button(
        "⬇ Download findings (CSV)",
        data=filtered.to_csv(index=False).encode("utf-8"),
        file_name="crm_hygiene_findings.csv",
        mime="text/csv",
        width="stretch",
    )
    downloads[1].download_button(
        "⬇ Download report summary (JSON)",
        data=json.dumps(report.to_dict(), indent=2).encode("utf-8"),
        file_name="crm_hygiene_report.json",
        mime="application/json",
        width="stretch",
    )
