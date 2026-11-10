"""The pattern rules themselves: every rule proves itself on its own examples, and the known issues
(ping time-outs, DDS errors, ...) are caught in every spelling we know of. No database needed."""
from app.analysis.rules import ERROR, INFO, RULES, RULES_BY_ID, WARN


def _hits(level, message):
    return sorted(r.id for r in RULES if r.matches(level, message))


def test_rule_ids_are_unique_and_every_rule_has_examples():
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids)) and set(ids) == set(RULES_BY_ID)
    for r in RULES:
        assert r.examples, f"{r.id} has no example it is required to catch"
        assert r.matchers and r.draft_actions and r.note and "{n}" in r.title_template


def test_every_example_is_caught_by_its_own_rule():
    for r in RULES:
        for level, message in r.examples:
            assert r.matches(level, message), (r.id, level, message)


def test_no_near_miss_is_caught():
    for r in RULES:
        for level, message in r.near_misses:
            assert not r.matches(level, message), (r.id, level, message)


def test_an_example_is_never_also_counted_by_another_rule():
    """One log line should not inflate two findings unless that is meant. Today none is."""
    for r in RULES:
        for level, message in r.examples:
            assert _hits(level, message) == [r.id], (r.id, level, message, _hits(level, message))


def test_ping_timeouts_in_every_known_spelling():
    for message in (
        "Request/Response Timeout observed during the ping scheduler execution",
        "ping scheduler: time-out",
        "Ping scheduler timed out",
        "PING SCHEDULER TIMEOUT",
        "ping_scheduler execution failed",
        "Ping to 10.1.2.3 time out",
        "timed-out waiting for ping",
        "ping: no response from hub",
    ):
        for level in (ERROR, WARN):
            assert "ping_timeout" in _hits(level, message), (level, message)
    assert "ping_timeout" not in _hits(INFO, "Ping scheduler timed out")       # only problems count


def test_dds_errors_in_every_known_spelling():
    for message in (
        "!DDS DISCSimpleParticipantDiscoveryPluginReaderListener_onSampleLost: ff0101c7",
        "!dds authentication timeout",
        "DDS participant removed after authentication time-out",
        "dds: security handshake failed",
        "DDS discovery rejected remote participant",
        "on_sample_lost: total_count=4",
        "ON SAMPLE LOST reported by reader",
        "DataReaderListener::on_sample_lost called",
    ):
        for level in (ERROR, WARN):
            assert "dds_security" in _hits(level, message), (level, message)


def test_the_two_timeout_wordings_are_counted_once_each():
    ping = "Request/Response Timeout observed during the ping scheduler execution"
    plain = "Request/Response Timeout observed"
    assert _hits(ERROR, ping) == ["ping_timeout"]
    assert _hits(ERROR, plain) == ["request_timeout"]


def test_unrelated_messages_match_nothing():
    for level, message in (
        (INFO, "axone_logger started"), (ERROR, "Disk is full"), (WARN, "Address book updated"),
        (ERROR, ""), (WARN, "mapping complete"),
    ):
        assert _hits(level, message) == [], (level, message)


def test_a_missing_message_is_just_no_match():
    assert all(not r.matches(ERROR, None) for r in RULES)
