"""
Shared fixtures.

The sample data and its ground-truth manifest are loaded once per session and
used as an oracle: because the generator guarantees clean records are clean by
construction, tests can assert *exact* set equality between what a check flags
and what was planted. That's a stronger claim than "it found most of them" — it
proves no false negatives and no false positives simultaneously.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.config import default_config
from engine.loader import load_crm_data

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"


@pytest.fixture(scope="session")
def ground_truth() -> dict:
    return json.loads((SAMPLES / "ground_truth.json").read_text())


@pytest.fixture(scope="session")
def sample_data(ground_truth):
    # Pin as_of to the generator's "today" so date-based checks measure against
    # the same reference the manifest was built with.
    return load_crm_data(
        SAMPLES / "contacts.csv",
        SAMPLES / "companies.csv",
        SAMPLES / "deals.csv",
        as_of=ground_truth["meta"]["today"],
    )


@pytest.fixture
def config():
    return default_config()


@pytest.fixture
def planted(ground_truth):
    """planted("contact_decayed") -> set of record IDs planted with that issue."""
    def _planted(*issue_types: str) -> set[str]:
        ids: set[str] = set()
        for issue_type in issue_types:
            ids |= {str(i["record_id"]) for i in ground_truth["issues"].get(issue_type, [])}
        return ids
    return _planted


@pytest.fixture
def flagged():
    """flagged(findings) -> set of record IDs, optionally filtered by predicate."""
    def _flagged(findings, predicate=None) -> set[str]:
        return {f.record_id for f in findings if predicate is None or predicate(f)}
    return _flagged
