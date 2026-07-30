"""The registry and runner: every check honours the same contract."""

from __future__ import annotations

from engine.checks import build_checks, run_all_checks
from engine.models import Check, Finding, OBJECT_TYPES


def test_every_registered_check_satisfies_the_protocol():
    for check in build_checks():
        assert isinstance(check, Check)
        assert check.id and check.name
        assert check.object_type in OBJECT_TYPES


def test_check_ids_are_unique():
    ids = [c.id for c in build_checks()]
    assert len(ids) == len(set(ids))


def test_runner_returns_findings_sorted_most_severe_first(sample_data, config):
    findings = run_all_checks(sample_data, config)
    assert findings
    severities = [int(f.severity) for f in findings]
    assert severities == sorted(severities, reverse=True)


def test_every_finding_is_well_formed(sample_data, config):
    known_ids = {c.id for c in build_checks()}
    for finding in run_all_checks(sample_data, config):
        assert isinstance(finding, Finding)
        assert finding.check_id in known_ids
        assert finding.record_id
        assert finding.message
        assert finding.object_type in OBJECT_TYPES


def test_findings_reference_real_records(sample_data, config):
    """No finding may point at a record that isn't in the export."""
    ids = {ot: set(sample_data.frame(ot)["record_id"]) for ot in OBJECT_TYPES}
    for finding in run_all_checks(sample_data, config):
        assert finding.record_id in ids[finding.object_type]


def test_checks_do_not_mutate_the_input(sample_data, config):
    before = {ot: sample_data.frame(ot).copy(deep=True) for ot in OBJECT_TYPES}
    run_all_checks(sample_data, config)
    for object_type, original in before.items():
        after = sample_data.frame(object_type)
        assert original.equals(after), f"{object_type} frame was mutated by a check"


def test_runner_accepts_a_subset_of_checks(sample_data, config):
    single = [build_checks()[0]]
    findings = run_all_checks(sample_data, config, checks=single)
    assert {f.check_id for f in findings} == {single[0].id}


def test_findings_are_flattenable_for_display(sample_data, config):
    row = run_all_checks(sample_data, config)[0].to_row()
    for column in ("check_id", "object_type", "record_id", "severity", "message"):
        assert column in row
