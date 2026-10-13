"""Portrait CSV parser: conversion rules, and exact accounting of bad rows."""
from pathlib import Path

import pytest

from app.ingestion.parsers.base import ParseReport, UnrecognizedLogFormat
from app.ingestion.parsers.portrait_csv import PortraitCsvParser

HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)


def row(ts="2024-06-02T02:00:00.393+0000", level="INFO", host="44-4b-5d-01-04-19",
        friendly="unsetFriendlyName", proc="axone_logger[777:0]", cat="General", desc="hello"):
    return f'"{ts}","0","1","{level}","{host}","uuid","{friendly}","{proc}",":0","{cat}","{desc}"'


def write(tmp_path: Path, *lines: str, name="log.csv", header=HEADER) -> Path:
    p = tmp_path / name
    p.write_text("\n".join([header, *lines]) + "\n", encoding="utf-8")
    return p


def run(path):
    report = ParseReport()
    events = list(PortraitCsvParser().parse(path, report))
    return events, report


def test_sniff_accepts_real_header_and_rejects_others(tmp_path):
    parser = PortraitCsvParser()
    assert parser.sniff(write(tmp_path, row()))
    other = tmp_path / "other.csv"
    other.write_text("a,b,c\n1,2,3\n")
    assert not parser.sniff(other)
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    assert not parser.sniff(empty)


def test_field_mapping(tmp_path):
    events, report = run(write(tmp_path, row(level="ERROR", host="um-pm-0.novalocal",
                                              proc="SvcAdmDiagUI[1:0]", cat="PerfData", desc="boom")))
    (e,) = events
    assert e.source == "portrait" and e.severity == "ERROR"
    assert e.device_id == "um-pm-0.novalocal"      # placeholder friendlyName -> hostName
    assert e.process == "SvcAdmDiagUI"              # [pid:n] suffix stripped
    assert e.category == "PerfData" and e.message == "boom"
    assert e.source_file == "log.csv" and e.source_line == 2   # line 1 is the header
    assert report.rows_read == 1 and report.rows_rejected == 0


def test_real_friendly_name_wins_over_hostname(tmp_path):
    (e,), _ = run(write(tmp_path, row(friendly="H50")))
    assert e.device_id == "H50"


def test_timestamp_converted_to_utc(tmp_path):
    (e,), _ = run(write(tmp_path, row(ts="2024-06-02T04:00:00.000+0200")))
    assert e.timestamp.isoformat() == "2024-06-02T02:00:00+00:00"


def test_every_row_is_accounted_for_including_bad_ones(tmp_path):
    p = write(
        tmp_path,
        row(desc="good 1"),
        row(ts="garbage"),                       # bad timestamp
        '"too","few","fields"',                   # wrong field count
        row(desc="good 2"),
    )
    events, report = run(p)
    assert [e.message for e in events] == ["good 1", "good 2"]
    # parse() called directly doesn't fill events_parsed (registry.iter_events does),
    # so check the accounting identity using the events we actually got back.
    assert report.rows_read == 4
    assert report.rows_read == len(events) + report.rows_rejected
    assert report.rows_rejected == 2
    assert report.reject_reasons == {"bad_timestamp": 1, "wrong_field_count": 1}
    assert {s.line for s in report.reject_samples} == {3, 4}


def test_unknown_level_defaults_to_info_with_a_warning(tmp_path):
    (e,), report = run(write(tmp_path, row(level="LOUD")))
    assert e.severity == "INFO"
    assert report.rows_rejected == 0
    assert any("unknown_severity" in k for k in report.warnings)


def test_multiline_quoted_description_is_one_event(tmp_path):
    p = tmp_path / "ml.csv"
    p.write_text(HEADER + "\n" + row(desc="line one\nline two") + "\n" + row(desc="next") + "\n")
    events, report = run(p)
    assert [e.message for e in events] == ["line one\nline two", "next"]
    assert [e.source_line for e in events] == [2, 4]   # second record starts after the 2-line one


def test_utf8_bom_is_tolerated(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(b"\xef\xbb\xbf" + (HEADER + "\n" + row() + "\n").encode())
    assert PortraitCsvParser().sniff(p)
    events, _ = run(p)
    assert len(events) == 1


def test_wrong_format_raises_instead_of_returning_nothing(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(UnrecognizedLogFormat):
        list(PortraitCsvParser().parse(p))
