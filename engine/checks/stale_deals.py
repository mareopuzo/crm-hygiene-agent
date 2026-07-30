"""
Pipeline hygiene: open deals that are lying to the forecast.

Two distinct failure modes, deliberately separated because they call for
different actions:

  - Stale: an open deal nobody has touched in longer than the configured
    window. It's probably dead but still counted in pipeline.

  - Past close date: an open deal whose close date has already come and gone.
    Even if it's alive, the forecast's timing is wrong, and every week it sits
    there it silently pushes the number.

Both only apply to *open* deals. A closed-won deal with no recent activity is
just a finished deal, not a hygiene problem.
"""

from __future__ import annotations

import pandas as pd

from engine.checks.base import blank, days_since
from engine.models import CRMData, DEALS, Finding, Severity


def _open_mask(df: pd.DataFrame) -> pd.Series:
    """
    HubSpot marks terminal stages with a 'closed' prefix (closedwon/closedlost).

    A blank stage is treated as open: it's sitting in a pipeline with no
    resolution recorded, which is exactly the kind of record that needs looking
    at rather than excusing.
    """
    stage = df["deal_stage"].astype("object").where(df["deal_stage"].notna(), "")
    normalized = stage.map(lambda s: str(s).strip().lower().replace(" ", "").replace("_", ""))
    return ~normalized.str.startswith("closed")


class StaleDealsCheck:
    """Open deals with no activity inside the configured window."""

    id = "stale_deals"
    name = "Stale Deals"
    object_type = DEALS

    def run(self, data: CRMData, config) -> list[Finding]:
        df = data.deals[_open_mask(data.deals)]
        threshold = config.stale_deal_days
        idle = days_since(df["last_activity_date"], data.as_of)

        findings: list[Finding] = []
        for record_id, days, amount in zip(df["record_id"], idle, df["amount"]):
            if days is None or days != days or days <= threshold:
                continue
            value = "" if pd.isna(amount) else f" (${amount:,.0f})"
            findings.append(Finding(
                check_id=self.id,
                object_type=self.object_type,
                record_id=record_id,
                severity=Severity.HIGH,
                message=(
                    f"Open deal{value} with no activity in {int(days)} days — "
                    f"past the {threshold}-day threshold. Inflating the forecast."
                ),
                field_name="last_activity_date",
                meta={
                    "days_inactive": int(days),
                    "threshold_days": threshold,
                    "amount": None if pd.isna(amount) else float(amount),
                },
            ))
        return findings


class PastCloseDateCheck:
    """Open deals whose forecast close date is already in the past."""

    id = "past_close_date"
    name = "Deals Past Close Date"
    object_type = DEALS

    def run(self, data: CRMData, config) -> list[Finding]:
        df = data.deals[_open_mask(data.deals)]
        # A missing close date is the missing-fields check's problem, not ours.
        df = df[~blank(df["close_date"])]
        overdue = days_since(df["close_date"], data.as_of)

        findings: list[Finding] = []
        for record_id, days, amount in zip(df["record_id"], overdue, df["amount"]):
            if days is None or days != days or days <= 0:
                continue
            value = "" if pd.isna(amount) else f" (${amount:,.0f})"
            findings.append(Finding(
                check_id=self.id,
                object_type=self.object_type,
                record_id=record_id,
                severity=Severity.HIGH,
                message=(
                    f"Open deal{value} is {int(days)} days past its close date. "
                    "Re-date it or close it — the forecast is wrong either way."
                ),
                field_name="close_date",
                meta={
                    "days_overdue": int(days),
                    "amount": None if pd.isna(amount) else float(amount),
                },
            ))
        return findings
