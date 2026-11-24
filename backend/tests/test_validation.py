"""Shared free-text validators (requirement D-9). Pure: no database or app needed."""
import pytest

from app.validation import (
    MAX_LABEL, ValidationFailure, clean_text, has_control_chars, validate_host, validate_ipv4,
    validate_label, validate_port, validate_upload_name,
)

SUFFIXES = frozenset({".csv", ".zip", ".log", ".txt"})


def test_clean_text_trims_and_returns_none_when_optional_and_empty():
    assert clean_text("  abc  ", "name", max_length=10) == "abc"
    assert clean_text("   ", "name", max_length=10) is None
    assert clean_text(None, "name", max_length=10) is None


def test_clean_text_required_empty_asks_for_a_value():
    with pytest.raises(ValidationFailure, match="Enter your username"):
        clean_text(" ", "your username", max_length=10, required=True)


def test_clean_text_length_boundary():
    assert clean_text("x" * 10, "name", max_length=10)
    with pytest.raises(ValidationFailure, match="limited to 10"):
        clean_text("x" * 11, "name", max_length=10)


@pytest.mark.parametrize("bad", ["a\nb", "a\tb", "a\x00b", "a​b", "a‮b"])
def test_control_and_invisible_characters_are_rejected(bad):
    assert has_control_chars(bad)
    with pytest.raises(ValidationFailure, match="control or invisible"):
        clean_text(bad, "name", max_length=20)


def test_fold_whitespace_turns_tabs_and_line_breaks_into_spaces():
    assert clean_text("nak\treply\r\nnext", "search text", max_length=50, fold_whitespace=True) == "nak reply  next"
    assert clean_text("\t\n ", "search text", max_length=50, fold_whitespace=True) is None


def test_fold_whitespace_still_refuses_other_control_and_invisible_characters():
    for bad in ["a\x00b", "a\x1bb", "a\u200bb", "a\u202eb"]:
        with pytest.raises(ValidationFailure, match="control or invisible"):
            clean_text(bad, "search text", max_length=50, fold_whitespace=True)


def test_without_folding_a_tab_is_refused():
    with pytest.raises(ValidationFailure):
        clean_text("a\tb", "name", max_length=50)


def test_ordinary_unicode_is_allowed():
    assert clean_text("Zoë – Röntgen 3", "name", max_length=20)


@pytest.mark.parametrize("ip", ["192.168.0.3", "0.0.0.0", "255.255.255.255", " 10.0.0.1 "])
def test_ipv4_accepts_valid_addresses(ip):
    assert validate_ipv4(ip) == ip.strip()


@pytest.mark.parametrize("ip", ["", "  ", "256.1.1.1", "1.2.3", "1.2.3.4.5", "010.0.0.1", "a.b.c.d", "1.2.3.4/24", "::1", "1.2.3.4 5"])
def test_ipv4_rejects_invalid_addresses(ip):
    with pytest.raises(ValidationFailure):
        validate_ipv4(ip)


@pytest.mark.parametrize("host", ["lab-pc", "lab-pc.local", "a.b.c", "10.1.2.3", "host.example.com."])
def test_host_accepts_names_and_ips(host):
    assert validate_host(host)


@pytest.mark.parametrize("host", ["", "-bad.local", "bad-.local", "bad_name", "a..b", "has space", "300.1.1.1", "x" * 254])
def test_host_rejects_bad_names(host):
    with pytest.raises(ValidationFailure):
        validate_host(host)


@pytest.mark.parametrize("port,expected", [(1, 1), ("22", 22), (" 65535 ", 65535)])
def test_port_accepts_range(port, expected):
    assert validate_port(port) == expected


@pytest.mark.parametrize("port", [0, 65536, -1, "", None, "80a", "1.5"])
def test_port_rejects_out_of_range_and_non_numbers(port):
    with pytest.raises(ValidationFailure, match="between 1 and 65535"):
        validate_port(port)


def test_label_is_optional_and_capped():
    assert validate_label("") is None
    assert validate_label("Room 4 cart") == "Room 4 cart"
    with pytest.raises(ValidationFailure):
        validate_label("x" * (MAX_LABEL + 1))


@pytest.mark.parametrize("name", ["a.csv", "A.LOG", "export.v2.txt", "bundle.zip"])
def test_upload_name_accepts_supported_types(name):
    assert validate_upload_name(name, SUFFIXES) == name


def test_upload_name_message_names_the_kind_of_file():
    with pytest.raises(ValidationFailure, match=r"'a.exe' is not a supported log file \(\.csv, \.log, \.txt, \.zip\)"):
        validate_upload_name("a.exe", SUFFIXES, kind="log file")


@pytest.mark.parametrize("name", ["", "noext", ".csv", "a.exe", "../a.csv", "dir\\a.csv", "a\nb.csv", "x" * 256 + ".csv"])
def test_upload_name_rejects_bad_names(name):
    with pytest.raises(ValidationFailure):
        validate_upload_name(name, SUFFIXES)
