"""Search filter rules and paging cursors. No database needed."""
from datetime import datetime, timedelta, timezone

import pytest

from app.storage.filters import (
    MAX_TEXT_LENGTH, FilterError, build_filter, decode_cursor, encode_cursor, to_naive_utc,
)


def test_naive_times_are_taken_as_utc_and_aware_ones_converted():
    naive = datetime(2024, 6, 2, 4, 0, 0)
    assert to_naive_utc(naive) == naive
    plus_two = datetime(2024, 6, 2, 4, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert to_naive_utc(plus_two) == datetime(2024, 6, 2, 2, 0, 0)
    assert to_naive_utc(plus_two).tzinfo is None


def test_since_must_be_before_until():
    t = datetime(2024, 6, 2)
    with pytest.raises(FilterError, match="earlier"):
        build_filter(since=t, until=t)
    with pytest.raises(FilterError):
        build_filter(since=t + timedelta(hours=1), until=t)


def test_since_and_until_are_normalised_before_comparing():
    # 04:00+02:00 is 02:00 UTC, which is before 03:00 UTC.
    f = build_filter(
        since=datetime(2024, 6, 2, 4, tzinfo=timezone(timedelta(hours=2))),
        until=datetime(2024, 6, 2, 3),
    )
    assert f.since == datetime(2024, 6, 2, 2) and f.until == datetime(2024, 6, 2, 3)


def test_severity_names_are_case_insensitive_and_validated():
    assert build_filter(severities=["warn", "ERROR", "warn"]).severities == ("WARN", "ERROR")
    with pytest.raises(FilterError, match="Unknown severity 'loud'"):
        build_filter(severities=["loud"])
    with pytest.raises(FilterError):
        build_filter(min_severity="loud")


@pytest.mark.parametrize(
    "severities, min_severity, expected",
    [
        ((), None, None),                                  # no restriction
        ((), "WARN", ("WARN", "ERROR")),
        ((), "debug", ("DEBUG", "INFO", "WARN", "ERROR")),
        (("INFO", "ERROR"), "WARN", ("ERROR",)),          # both apply
        (("INFO",), "ERROR", ()),                          # contradiction matches nothing
        (("ERROR", "INFO"), None, ("INFO", "ERROR")),
    ],
)
def test_effective_severities(severities, min_severity, expected):
    assert build_filter(severities=severities, min_severity=min_severity).effective_severities == expected


def test_blank_values_are_dropped_and_lists_deduplicated():
    f = build_filter(devices=["a", " a ", "", "b"], sources=[""], text="   ", process="  ", job_id=" ")
    assert f.devices == ("a", "b")
    assert f.sources == () and f.text is None and f.process is None and f.job_id is None


def test_text_length_is_limited():
    build_filter(text="x" * MAX_TEXT_LENGTH)
    with pytest.raises(FilterError, match="limited"):
        build_filter(text="x" * (MAX_TEXT_LENGTH + 1))


def test_used_fields_names_filters_but_never_their_values():
    f = build_filter(text="patient 12345", devices=["hub-1"], min_severity="warn")
    fields = f.used_fields()
    assert set(fields) == {"text", "device", "min_severity"}
    assert "12345" not in " ".join(fields) and "hub-1" not in " ".join(fields)


@pytest.mark.parametrize("ts", [datetime(2024, 6, 2, 2, 0, 0), datetime(2024, 6, 2, 2, 0, 0, 123456)])
def test_cursor_round_trips(ts):
    assert decode_cursor(encode_cursor(ts, 42)) == (ts, 42)


@pytest.mark.parametrize("bad", ["", "not-a-cursor", "!!!!", "MjAyNHxhYmM", encode_cursor(datetime(2024, 1, 1), 1)[:-3]])
def test_garbage_cursors_are_rejected_not_crashed_on(bad):
    with pytest.raises(FilterError, match="Invalid cursor"):
        decode_cursor(bad)
