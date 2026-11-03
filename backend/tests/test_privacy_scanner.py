"""PHI/PII scanner rules. Pure Python: no database, no web app."""
import json

from app.privacy.scanner import (
    CATEGORY_LABELS, NETWORK_CATEGORIES, SCANNER_VERSION, Flag, category_group, decode_flags, encode_flags,
    mask_text, scan_columns, scan_event_fields, scan_text, split_flags,
)


def found(text):
    """[(category, matched text)] for one message."""
    return [(f.category, text[f.start:f.end]) for f in scan_text(text)]


def test_every_rule_detects_its_pattern():
    cases = {
        "ssn": ("applicant 123-45-6789 on file", "123-45-6789"),
        "email": ("mail jane.doe@hospital.org please", "jane.doe@hospital.org"),
        "phone": ("call (608) 555-0142 today", "(608) 555-0142"),
        "credit_card": ("paid with 4111 1111 1111 1111", "4111 1111 1111 1111"),
        "street_address": ("lives at 1600 Pennsylvania Avenue", "1600 Pennsylvania Avenue"),
        "ip_address": ("peer 192.168.0.5 timed out", "192.168.0.5"),
        "mac_address": ("nic 00:1A:2B:3C:4D:5E up", "00:1A:2B:3C:4D:5E"),
        "person_name": ("patientName=John Smith admitted", "John Smith"),
        "record_identifier": ("mrn: 00482913 matched", "00482913"),
        "date_of_birth": ("dob=1980-01-02", "1980-01-02"),
    }
    for category, (text, expected) in cases.items():
        assert (category, expected) in found(text), category
    assert set(cases) <= set(CATEGORY_LABELS)


def test_every_category_has_a_description():
    assert all(CATEGORY_LABELS.values())


def test_labelled_values_cover_quoted_and_json_forms():
    for text, value in [
        ('{"patientName": "Mary Ann Jones", "bed": 4}', "Mary Ann Jones"),
        ("patient_id='A-1234'", "A-1234"),
        ("PatientName => Li Wei", "Li Wei"),
    ]:
        assert value in [v for _, v in found(text)], text


def test_a_name_label_does_not_swallow_the_rest_of_the_sentence():
    text = "patientName=John Smith disconnected from bed 4"
    assert ("person_name", "John Smith") in found(text)
    assert all("disconnected" not in v for _, v in found(text))


def test_two_labels_in_one_message_are_both_flagged():
    text = "patientName=John Smith mrn=55 dob=1980-01-02"
    assert [c for c, _ in found(text)] == ["person_name", "record_identifier", "date_of_birth"]


def test_empty_and_placeholder_values_are_not_flagged():
    for text in ["patientName=null", "mrn=", "dob=unknown", "patient_id: none", "ssn=N/A", 'patientName=""']:
        assert found(text) == [], text


def test_hl7_patient_segments_are_flagged_whole():
    msg = "recv MSH|^~\\&|ADT|H\\rPID|1||12345^^^MR||DOE^JOHN||19800101|M\\rOBX|1|NM|HR||72"
    hits = found(msg)
    assert hits == [("hl7_patient_segment", "PID|1||12345^^^MR||DOE^JOHN||19800101|M")]
    real_newlines = "MSH|^~\\&|ADT\nPID|1||99\nNK1|1|DOE^JANE\nOBX|1"
    assert [c for c, _ in found(real_newlines)] == ["hl7_patient_segment"] * 2


def test_non_patient_hl7_segments_are_left_alone():
    assert found("MSH|^~\\&|ADT|H\\rOBX|1|NM|HR||72") == []


def test_invalid_ssns_are_not_flagged():
    for text in ["000-12-3456", "666-12-3456", "900-12-3456", "123-00-4567", "123-45-0000"]:
        assert all(c != "ssn" for c, _ in found(text)), text


def test_ssn_inside_a_longer_identifier_is_not_flagged():
    assert all(c != "ssn" for c, _ in found("id 7a1b-123-45-6789-x"))


def test_card_numbers_need_a_valid_luhn_checksum():
    assert ("credit_card", "4111111111111111") in found("card 4111111111111111")
    assert all(c != "credit_card" for c, _ in found("counter 1234567890123456"))


def test_card_numbers_must_look_like_a_real_card_network():
    # Both pass the Luhn check; only the first starts like a card network.
    assert ("credit_card", "4111111111111111") in found("card 4111111111111111")
    assert all(c != "credit_card" for c, _ in found("id 9000000000000001"))
    assert all(c != "credit_card" for c, _ in found("value 4111111111111111.5 ms"))
    assert ("credit_card", "378282246310005") in found("amex 378282246310005")


def test_bare_digit_runs_and_timestamps_are_not_phone_numbers():
    for text in ["counter 6085550142", "2024-06-02T02:00:00.393+0000", "pid 12345 tid 6789"]:
        assert all(c != "phone" for c, _ in found(text)), text


def test_ip_addresses_are_validated():
    assert found("to 10.0.0.5:8080") == [("ip_address", "10.0.0.5")]
    assert found("bad 999.1.1.1 and 01.2.3.4") == []


def test_ip_flags_are_low_confidence_and_patterns_are_high():
    ip, = scan_text("peer 10.0.0.5")
    assert ip.confidence == "low"
    ssn, = scan_text("123-45-6789")
    assert ssn.confidence == "high"


def test_ordinary_log_lines_have_no_flags():
    for text in [
        "Cleanup of Patient Data Recording files failed",
        "ping scheduler timed out after 5000 ms",
        "!DDS participant removed after authentication time-out",
        "Healthcheck succeeded in 12 ms",
        "",
    ]:
        assert scan_text(text) == (), text


def test_overlapping_matches_keep_the_stronger_one():
    # A labelled phone number is both a labelled value and a phone number: one flag, not two.
    flags = scan_text("patient_phone=(608) 555-0142")
    assert len(flags) == 1 and flags[0].confidence == "high"


def test_other_fields_are_scanned_and_named():
    flags = scan_event_fields(message="ok", device_id="jane.doe@x.org", process="svc", category="General")
    assert [(f.field, f.category) for f in flags] == [("device_id", "email")]


def test_flags_never_contain_the_matched_text():
    text = "patientName=John Smith ssn=123-45-6789 jane@x.org"
    stored = encode_flags(list(scan_text(text)))
    for secret in ("John", "Smith", "123-45-6789", "jane@x.org"):
        assert secret not in stored


def test_encode_decode_roundtrip_and_none_when_empty():
    flags = list(scan_text("mail a@b.org"))
    assert decode_flags(encode_flags(flags)) == flags
    assert encode_flags([]) is None
    assert decode_flags(None) == []


def test_damaged_stored_flags_decode_to_nothing_instead_of_raising():
    for raw in ["not json", "[1,2]", '[["message","email"]]']:
        assert decode_flags(raw) == []


def test_mask_replaces_only_flagged_spans():
    text = "patientName=John Smith mrn=55 bed 4"
    assert mask_text(text, list(scan_text(text))) == \
        "patientName=[REDACTED:person_name] mrn=[REDACTED:record_identifier] bed 4"


def test_mask_leaves_other_fields_and_bad_ranges_alone():
    flags = [Flag("device_id", "email", "high", 0, 5), Flag("message", "ssn", "high", 50, 60)]
    assert mask_text("hello world", flags) == "hello world"
    assert mask_text(None, flags) is None


def test_scanner_version_is_a_positive_int():
    assert isinstance(SCANNER_VERSION, int) and SCANNER_VERSION >= 1
    json.dumps({"v": SCANNER_VERSION})


def test_ip_and_mac_addresses_are_network_identifiers_not_personal_data():
    assert NETWORK_CATEGORIES == {"ip_address", "mac_address"}
    assert category_group("mac_address") == "network" and category_group("ssn") == "personal"
    personal, network = split_flags(list(scan_text("peer 10.0.0.5 ssn 123-45-6789")))
    assert [f.category for f in personal] == ["ssn"] and [f.category for f in network] == ["ip_address"]


def test_scan_columns_keeps_network_addresses_out_of_the_personal_flags():
    cols = scan_columns(message="timeout to 10.0.0.5", device_id="00:1A:2B:3C:4D:5E", process="svc", category="General")
    assert cols["sensitive_flags"] is None
    assert {f.category for f in decode_flags(cols["network_flags"])} == {"ip_address", "mac_address"}
    assert cols["privacy_scan_version"] == SCANNER_VERSION
    both = scan_columns(message="ssn 123-45-6789 via 10.0.0.5")
    assert [f.category for f in decode_flags(both["sensitive_flags"])] == ["ssn"]


def test_long_counters_and_epoch_times_are_not_cards():
    for text in ["t 1700000000000", "count 4111111111111111111111", "id 9000000000000001"]:
        assert all(c != "credit_card" for c, _ in found(text)), text


# --- review round: values that were only partly masked, and shapes that were missed ---------------------------

def _masked(text):
    """The text as a masked view would show it (personal kinds only)."""
    personal, _ = split_flags(list(scan_text(text)))
    return mask_text(text, personal)


def test_labelled_values_are_masked_whole_not_cut_at_the_first_space():
    assert _masked("home_address=123 Main St, Springfield") == "home_address=[REDACTED:street_address]"
    assert _masked('dob="January 5, 1980"') == 'dob="[REDACTED:date_of_birth]"'
    assert _masked("ssn: 123 45 6789") == "ssn: [REDACTED:ssn]"
    assert _masked("phone number: (555) 123-4567 ok") == "phone number: [REDACTED:phone] ok"
    assert _masked("dob=5 January 1980 end") == "dob=[REDACTED:date_of_birth] end"


def test_a_quoted_value_with_a_leading_space_is_masked_to_its_last_character():
    assert _masked('ssn=" 123-45-6789"') == 'ssn=" [REDACTED:ssn]"'


def test_overlapping_hits_merge_into_one_span_instead_of_leaving_a_piece_behind():
    flags = scan_text("home_address=123 Main St, Springfield")
    assert len(flags) == 1 and flags[0].start == len("home_address=")


def test_names_with_apostrophes_commas_and_lower_case_are_masked_whole():
    assert _masked("patientName=O'Brien") == "patientName=[REDACTED:person_name]"
    assert _masked("patientName=Smith, John") == "patientName=[REDACTED:person_name]"
    assert _masked("patientName: john smith") == "patientName: [REDACTED:person_name]"


def test_a_name_does_not_swallow_the_rest_of_the_sentence_or_the_next_field():
    assert _masked("patientName=John Smith was admitted") == "patientName=[REDACTED:person_name] was admitted"
    assert _masked("patientName=John, age=30") == "patientName=[REDACTED:person_name], age=30"
    assert _masked("patientName=John Smith admitted") == "patientName=[REDACTED:person_name] admitted"


def test_a_card_followed_by_another_number_is_still_found():
    assert _masked("card 4111 1111 1111 1111 123 ok") == "card [REDACTED:credit_card] 123 ok"
    assert _masked("card 4111 1111 1111 1111 12/26") == "card [REDACTED:credit_card] 12/26"


def test_mastercard_2_series_is_found_with_and_without_separators():
    assert _masked("paid 2223000048400011 today") == "paid [REDACTED:credit_card] today"
    assert _masked("paid 2223 0000 4840 0011 today") == "paid [REDACTED:credit_card] today"


def test_hl7_segments_are_found_after_equals_and_colon():
    for prefix in ("msg=", "hl7:", "payload: "):
        flags = scan_text(prefix + "PID|1||12345^^^MRN||DOE^JOHN")
        assert [f.category for f in flags] == ["hl7_patient_segment"], prefix


def test_common_label_spellings_are_found():
    cases = {
        r'{\"patientName\":\"John Smith\"}': "person_name",
        "patientFirstName=John": "person_name",
        "patientDOB=01/02/1980": "date_of_birth",
        "patient.name=John": "person_name",
        "patient-id=12345": "record_identifier",
        "MRN#: 123456": "record_identifier",
        "DOB 01/02/1980": "date_of_birth",
    }
    for text, category in cases.items():
        assert [f.category for f in scan_text(text)] == [category], text


def test_more_street_address_forms_are_found():
    for text in ("lives at 123 N Main St today", "456 W 5th Ave", "123 MAIN ST", "9 Oak Street."):
        assert [f.category for f in scan_text(text)] == ["street_address"], text


def test_the_wider_rules_do_not_flag_ordinary_log_text():
    for text in ("processed 1234 items in 56 ms", "retry 3 of 5 for Main St service", "request id 4111111111111112 rejected",
                 "patientName= null", "name=Widget status=ok", "dob unknown today", "count 2223000048400012 total"):
        assert scan_text(text) == (), text


def test_a_change_to_the_rules_bumps_the_scanner_version():
    assert SCANNER_VERSION >= 2
