"""Matching over a stream of events, with no database: counts, evidence, and the "none missed" check,
which plants known issues among ordinary lines and requires every one to be found and nothing else."""
import random
from datetime import datetime, timedelta

from app.analysis.rules import ERROR, INFO, RULES, WARN
from app.analysis.scan import EVIDENCE_EACH_END, EventRow, scan
from app.analysis.text import shape

T0 = datetime(2024, 6, 2, 0, 0, 0)

NOISE = [
    (INFO, "axone_logger started"), (INFO, "Heartbeat ok"), (WARN, "Battery at 80%"),
    (ERROR, "Disk is full"), (INFO, "Healthcheck succeeded in 12 ms"), (WARN, "Address book updated"),
]


def row(i, level, message, device="hub-1"):
    return EventRow(id=i, timestamp=T0 + timedelta(seconds=i), device_id=device, severity=level, message=message)


def test_counts_devices_and_time_range_per_rule():
    rows = [
        row(1, ERROR, "ping scheduler timed out", "hub-a"),
        row(2, WARN, "!DDS onSampleLost", "hub-b"),
        row(3, ERROR, "ping scheduler timed out", "hub-a"),
        row(4, ERROR, "ping scheduler timed out", "hub-b"),
        row(5, INFO, "all fine", "hub-b"),
    ]
    found = scan(rows)
    assert set(found) == {"ping_timeout", "dds_security"}
    ping = found["ping_timeout"]
    assert ping.count == 3 and ping.devices == {"hub-a": 2, "hub-b": 1}
    assert [eid for _, eid in ping.evidence()] == [1, 3, 4]


def test_evidence_is_the_earliest_and_the_latest_matches_in_time_order():
    n = 100
    rows = [row(i, ERROR, "ping scheduler timed out") for i in range(1, n + 1)]
    ids = [eid for _, eid in scan(rows)["ping_timeout"].evidence()]
    assert ids == list(range(1, EVIDENCE_EACH_END + 1)) + list(range(n - EVIDENCE_EACH_END + 1, n + 1))


def test_evidence_follows_time_not_id_order():
    rows = [row(i, ERROR, "ping scheduler timed out") for i in range(1, 60)]
    rows[0], rows[-1] = (EventRow(1, T0 + timedelta(days=9), "h", ERROR, "ping scheduler timed out"),
                         EventRow(59, T0 - timedelta(days=9), "h", ERROR, "ping scheduler timed out"))
    ids = [eid for _, eid in scan(rows)["ping_timeout"].evidence()]
    assert ids[0] == 59 and ids[-1] == 1


def test_a_few_matches_are_all_evidence_and_none_twice():
    rows = [row(i, WARN, "!DDS timeout") for i in range(1, 4)]
    assert [eid for _, eid in scan(rows)["dds_security"].evidence()] == [1, 2, 3]


def test_one_event_can_support_two_rules():
    both = "Cleanup of Patient Data Recording files failed, hidepid"
    assert set(scan([row(1, ERROR, both)])) == {"cleanup_failed", "hidepid"}


def test_nothing_found_is_an_empty_result():
    assert scan([row(1, INFO, "axone_logger started")]) == {}
    assert scan([]) == {}


def test_every_planted_issue_is_found_and_nothing_else():
    """The 'none missed' check: plant every example of every rule among noise, in random order, and
    require each one to come back as evidence-eligible with exact counts."""
    rng = random.Random(7)
    planted = [(r.id, level, message) for r in RULES for level, message in r.examples for _ in range(3)]
    lines = [(None, level, message) for level, message in NOISE for _ in range(40)] + planted
    rng.shuffle(lines)
    rows = [row(i, level, message, device=f"hub-{i % 4}") for i, (_, level, message) in enumerate(lines, 1)]
    found = scan(rows)
    expected = {}
    for rid, _, _ in planted:
        expected[rid] = expected.get(rid, 0) + 1
    assert {rid: acc.count for rid, acc in found.items()} == expected
    # and the noise produced none of it: the planted rows alone account for every match
    assert sum(acc.count for acc in found.values()) == len(planted)


def test_shape_folds_what_varies():
    assert shape("Timeout to 10.0.0.5 after 5000 ms") == shape("Timeout to 10.0.0.9 after 250 ms")
    assert shape("participant ff0101c7 removed") == shape("participant ab12cd34 removed")
    assert shape("id 123e4567-e89b-12d3-a456-426614174000 gone") == "id <id> gone"
    assert shape("Disk full") != shape("Disk empty")
