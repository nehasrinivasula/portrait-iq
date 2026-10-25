"""The common event format: normalization rules and what it refuses to accept."""
from datetime import datetime, timedelta, timezone

import pytest

from app.ingestion.schema import (
    SEVERITIES,
    EventValidationError,
    NormalizedEvent,
    make_event,
    normalize_severity,
    parse_timestamp,
)

UTC = timezone.utc


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("INFO", "INFO"), ("info", "INFO"), (" Warn ", "WARN"), ("WARNING", "WARN"),
        ("ERROR", "ERROR"), ("err", "ERROR"), ("CRITICAL", "ERROR"), ("fatal", "ERROR"),
        ("TRACE", "DEBUG"), ("debug", "DEBUG"), ("NOTICE", "INFO"),
    ],
)
def test_severity_aliases(raw, expected):
    assert normalize_severity(raw) == expected


@pytest.mark.parametrize("raw", ["", "banana", None, "5"])
def test_unknown_severity_is_none_not_a_guess(raw):
    assert normalize_severity(raw) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2024-06-02T02:00:00.393+0000", datetime(2024, 6, 2, 2, 0, 0, 393000, tzinfo=UTC)),
        ("2024-06-02T02:00:01.123456789Z", datetime(2024, 6, 2, 2, 0, 1, 123456, tzinfo=UTC)),
        ("2024-06-02 02:00:01,500+01:00", datetime(2024, 6, 2, 1, 0, 1, 500000, tzinfo=UTC)),
        ("2024-06-02T02:00:00Z", datetime(2024, 6, 2, 2, 0, 0, tzinfo=UTC)),
        ("2024-06-02T02:00:00-0530", datetime(2024, 6, 2, 7, 30, 0, tzinfo=UTC)),
    ],
)
def test_parse_timestamp_converts_to_utc(text, expected):
    result = parse_timestamp(text)
    assert result == expected
    assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "text",
    [
        "2024-06-02T02:00:00",          # no timezone: refuse to guess
        "2024-06-02 02:00:00.5",         # no timezone
        "not a timestamp",
        "",
        "2024-13-02T02:00:00Z",           # impossible month
        "2024-06-02T02:00:00+2500",        # impossible offset
    ],
)
def test_parse_timestamp_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_timestamp(text)


def _good(**overrides):
    base = dict(
        timestamp=datetime(2024, 6, 2, 2, 0, tzinfo=UTC), source="portrait",
        severity="WARN", message="hello",
    )
    base.update(overrides)
    return make_event(**base)


def test_make_event_normalizes_everything():
    event = _good(
        timestamp=datetime(2024, 6, 2, 4, 0, tzinfo=timezone(timedelta(hours=2))),
        source="  Portrait ", severity="warning", device_id="  H50 ", category=" ", process="",
        message=None,
    )
    assert event.timestamp == datetime(2024, 6, 2, 2, 0, tzinfo=UTC)
    assert event.timestamp.utcoffset() == timedelta(0)
    assert event.source == "portrait"
    assert event.severity == "WARN"
    assert event.device_id == "H50"
    assert event.category is None and event.process is None   # blank -> None
    assert event.message == ""                                  # never None
    assert event.severity in SEVERITIES


def test_make_event_rejects_naive_timestamp():
    with pytest.raises(EventValidationError):
        _good(timestamp=datetime(2024, 6, 2, 2, 0))


def test_make_event_rejects_unknown_severity():
    with pytest.raises(EventValidationError):
        _good(severity="LOUD")


@pytest.mark.parametrize("source", ["", "Has Space", "9lives", "a-b"])
def test_make_event_rejects_bad_source(source):
    with pytest.raises(EventValidationError):
        _good(source=source)


def test_validate_catches_hand_built_bad_events():
    ok = _good()
    ok.validate()
    ok.severity = "LOUD"
    with pytest.raises(EventValidationError):
        ok.validate()

    non_utc = NormalizedEvent(
        timestamp=datetime(2024, 6, 2, 2, 0, tzinfo=timezone(timedelta(hours=1))),
        device_id=None, source="portrait", category=None, severity="INFO", process=None, message="x",
    )
    with pytest.raises(EventValidationError):
        non_utc.validate()
