"""
The contract every registered parser must honour. Adding a parser to
registry.PARSERS and a matching entry in FIXTURES below is how a new source
proves it produces the common format; a parser with no fixture fails
test_every_parser_has_a_contract_fixture, so nobody can skip it.

A fixture is a small, VALID file in that parser's format plus a bad row that
must be rejected, not lost.
"""
from datetime import timedelta

import pytest

from app.ingestion import registry
from app.ingestion.parsers.base import ParseReport, UnrecognizedLogFormat
from app.ingestion.schema import SEVERITIES

PORTRAIT_HEADER = (
    "timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,"
    "sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description"
)

FIXTURES = {
    "portrait_csv": (
        "sample.csv",
        PORTRAIT_HEADER + "\n"
        '"2024-06-02T02:00:00.393+0000","0","1","INFO","h1","u","unsetFriendlyName","p[1:0]",":0","General","one"\n'
        '"2024-06-02T02:00:01.000+0000","0","2","WARN","h2","u","H50","p[1:0]",":0","PerfData","two"\n'
        '"NOT-A-TIME","0","3","ERROR","h3","u","x","p[1:0]",":0","General","bad row"\n',
        2, 1,   # expected good events, expected rejected rows
    ),
    # 10 good lines + 1 stray line: real kubectl output is overwhelmingly
    # well-formed, and the parser's sniff() requires >=90% of lines to be.
    "k8s_log": (
        "pod-a.log",
        "".join(f"2024-06-02T02:00:{i:02d}.123456789Z line {i}\n" for i in range(9))
        + "2024-06-02T02:00:09.000000000Z [WARN] tenth line\n"
        + "line with no timestamp\n",
        10, 1,
    ),
}

IMPLEMENTED = [p for p in registry.PARSERS if p.name != "compute_csv"]


def test_every_parser_has_a_contract_fixture():
    missing = [p.name for p in IMPLEMENTED if p.name not in FIXTURES]
    assert not missing, f"add a FIXTURES entry for: {missing}"


def test_parser_names_and_sources_are_unique_and_well_formed():
    names = [p.name for p in registry.PARSERS]
    assert len(names) == len(set(names))
    for p in registry.PARSERS:
        assert p.source == p.source.lower() and p.source.isidentifier()


@pytest.mark.parametrize("parser", IMPLEMENTED, ids=lambda p: p.name)
def test_parser_produces_valid_common_format(parser, tmp_path):
    filename, content, good, bad = FIXTURES[parser.name]
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    assert registry.detect_parser(path).name == parser.name       # routed to the right parser

    report = ParseReport()
    events = list(registry.iter_events(path, report=report))

    assert len(events) == good
    for e in events:
        e.validate()                                              # satisfies the schema
        assert e.timestamp.utcoffset() == timedelta(0)             # UTC
        assert e.severity in SEVERITIES
        assert e.source == parser.source
        assert e.source_file == filename and e.source_line >= 1     # provenance present
        assert isinstance(e.message, str)

    # nothing is silently lost: every row is an event or a counted rejection
    assert report.rows_rejected == bad
    assert report.accounted_for
    assert report.events_parsed == good


def test_all_sources_converge_to_one_shape(tmp_path):
    """Events from different source types are interchangeable: they can be
    merged into one timeline and read by code that knows nothing about
    where they came from."""
    events = []
    for name, (filename, content, _, _) in FIXTURES.items():
        path = tmp_path / filename
        path.write_text(content, encoding="utf-8")
        events += list(registry.iter_events(path))

    assert {e.source for e in events} >= {"portrait", "kubernetes"}
    assert len({type(e) for e in events}) == 1                    # one type
    assert len({tuple(f for f in e.__slots__) for e in events}) == 1   # one field set
    timeline = sorted(events, key=lambda e: e.timestamp)           # directly comparable
    assert [e.timestamp for e in timeline] == sorted(e.timestamp for e in events)


def test_unrecognised_file_raises_loudly(tmp_path):
    p = tmp_path / "mystery.csv"
    p.write_text("x,y\n1,2\n")
    assert registry.detect_parser(p) is None
    with pytest.raises(UnrecognizedLogFormat):
        list(registry.iter_events(p))


def test_compute_placeholder_never_claims_files(tmp_path):
    """Until a real compute sample exists, that parser must not grab anything."""
    for name, (filename, content, _, _) in FIXTURES.items():
        p = tmp_path / filename
        p.write_text(content)
        assert not registry.get_parser("compute_csv").sniff(p)
