"""Kubernetes (kubectl logs --timestamps) parser."""
import pytest

from app.ingestion.parsers.base import ParseReport
from app.ingestion.parsers.k8s_log import KubernetesLogParser, infer_severity

SAMPLE = """\
2024-06-02T02:00:01.123456789Z E0602 02:00:01.123456 1 reflector.go:138] failed to list *v1.Pod
2024-06-02T02:00:02.000000000Z level=warn msg="retrying connection" attempt=3
2024-06-02T02:00:03.500000000Z {"level":"error","msg":"disk full"}
2024-06-02T02:00:04.000000000Z [ERROR] could not bind port 7400
2024-06-02T02:00:05.000000000Z Error handling is enabled
2024-06-02T02:00:06.000000000Z plain informational line
"""


@pytest.mark.parametrize(
    "message,expected",
    [
        ("E0602 02:00:01.1 1 file.go:1] boom", "ERROR"),
        ("W0602 02:00:01.1 1 file.go:1] hmm", "WARN"),
        ("I0602 02:00:01.1 1 file.go:1] ok", "INFO"),
        ("F0602 02:00:01.1 1 file.go:1] dead", "ERROR"),
        ('level=warn msg="x"', "WARN"),
        ('{"level":"error","msg":"x"}', "ERROR"),
        ('{"severity": "CRITICAL"}', "ERROR"),
        ("[ERROR] x", "ERROR"),
        ("WARN: x", "WARN"),
        ("Error handling is enabled", None),     # not a level: lower-case 'Error' as a word
        ("plain line", None),
        ("level=banana", None),
    ],
)
def test_infer_severity(message, expected):
    assert infer_severity(message) == expected


def test_sniff(tmp_path):
    good = tmp_path / "um-pm-0.log"
    good.write_text(SAMPLE)
    assert KubernetesLogParser().sniff(good)
    csv_like = tmp_path / "a.csv"
    csv_like.write_text("a,b,c\n1,2,3\n")
    assert not KubernetesLogParser().sniff(csv_like)
    empty = tmp_path / "e.log"
    empty.write_text("")
    assert not KubernetesLogParser().sniff(empty)


def test_parse_sample(tmp_path):
    p = tmp_path / "um-pm-0.log"
    p.write_text(SAMPLE)
    report = ParseReport()
    events = list(KubernetesLogParser().parse(p, report))
    assert [e.severity for e in events] == ["ERROR", "WARN", "ERROR", "ERROR", "INFO", "INFO"]
    assert {e.device_id for e in events} == {"um-pm-0"}      # from file name
    assert {e.source for e in events} == {"kubernetes"}
    assert events[0].timestamp.isoformat() == "2024-06-02T02:00:01.123456+00:00"
    assert events[0].source_line == 1
    assert report.rows_read == 6 and report.rows_rejected == 0


def test_lines_without_timestamp_are_rejected_and_counted(tmp_path):
    p = tmp_path / "pod.log"
    p.write_text("2024-06-02T02:00:01Z ok\nno timestamp here\n2024-06-02T02:00:02Z ok\n")
    report = ParseReport()
    events = list(KubernetesLogParser().parse(p, report))
    assert len(events) == 2
    assert report.rows_read == 3 and report.reject_reasons == {"no_timestamp_prefix": 1}


def test_device_override(tmp_path):
    p = tmp_path / "whatever.log"
    p.write_text("2024-06-02T02:00:01Z ok\n")
    (e,) = KubernetesLogParser(device_id="rti-pod").parse(p)
    assert e.device_id == "rti-pod"
