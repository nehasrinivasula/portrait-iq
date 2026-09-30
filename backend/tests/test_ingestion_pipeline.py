"""
Proves the one real piece of the skeleton — the Portrait CSV parser — works
end to end: sniff detects the format, parse produces the right count and
severities. Rows below are shaped like the real sample data, values changed.
"""
from pathlib import Path

from app.ingestion.parsers import portrait_csv

SAMPLE_CSV = """timeStampAndOffset,processCounter,concentrationCounter,logLevel,hostName,sourceUUID,friendlyName,processInfo,sourceInfo,eventCategory,description
"2024-06-02T02:00:00.393+0000","0","20744","INFO","44-4b-5d-01-04-19","ffa5984a-f560-4183-86c1-cd672d96c946","unsetFriendlyName","axone_logger[777:0]",":0","PerfData","{}"
"2024-06-02T02:00:01.500+0000","0","20745","ERROR","um-pm-0.novalocal","c0757714-67fd-4edc-90ec-88c095bac7e2","unsetFriendlyName","SvcAdmDiagUI[1:0]",":0","General","Request/Response Timeout observed during the ping scheduler execution"
"2024-06-02T02:00:02.100+0000","0","20746","WARN","44-4b-5d-01-04-49","aaaa","H50","axonerouter[2:0]",":0","General","!DDS DISCSimpleParticipantDiscoveryPluginReaderListener_onSampleLost: ff0101c7"
"""


def test_sniff_recognizes_real_header(tmp_path: Path):
    p = tmp_path / "sample.csv"
    p.write_text(SAMPLE_CSV)
    assert portrait_csv.sniff(p) is True


def test_sniff_rejects_unrelated_csv(tmp_path: Path):
    p = tmp_path / "other.csv"
    p.write_text("a,b,c\n1,2,3\n")
    assert portrait_csv.sniff(p) is False


def test_parse_produces_expected_events(tmp_path: Path):
    p = tmp_path / "sample.csv"
    p.write_text(SAMPLE_CSV)

    events = list(portrait_csv.parse(p))
    assert len(events) == 3

    severities = [e.severity for e in events]
    assert severities == ["INFO", "ERROR", "WARN"]

    error_event = events[1]
    assert error_event.process == "SvcAdmDiagUI"
    assert "ping scheduler" in error_event.message
    assert error_event.device_id == "um-pm-0.novalocal"  # falls back to hostName

    warn_event = events[2]
    assert warn_event.device_id == "H50"  # real friendlyName, not the fallback
