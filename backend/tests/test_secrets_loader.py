"""Tests for secrets_loader (SCRUM-72). All secret values here are fake."""

import io
import logging
import os
import pickle
import zipfile

import pytest

from app import secrets_loader as sl

FAKE = "fake-box-secret-9f8e7d6c5b4a"  # clearly fake, long enough to be registered


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Every test starts with no registered secrets and the original log record factory."""
    sl.clear_registered_secrets()
    previous_factory = logging.getLogRecordFactory()
    previous_make_record = logging.Logger.makeRecord
    for var in ("TEST_SECRET", "TEST_SECRET_FILE", sl.TRUSTED_GID_VAR):
        monkeypatch.delenv(var, raising=False)
    yield
    logging.setLogRecordFactory(previous_factory)
    logging.Logger.makeRecord = previous_make_record
    sl.clear_registered_secrets()


def write_secret_file(tmp_path, content=FAKE, mode=0o600, name="TEST_SECRET"):
    path = tmp_path / name
    path.write_text(content)
    os.chmod(path, mode)
    return path


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def test_load_from_env(monkeypatch):
    monkeypatch.setenv("TEST_SECRET", FAKE)
    secret = sl.load_secret("TEST_SECRET")
    assert secret.reveal() == FAKE
    assert secret.name == "TEST_SECRET"


def test_load_from_file_strips_trailing_newline(monkeypatch, tmp_path):
    path = write_secret_file(tmp_path, content=FAKE + "\n")
    monkeypatch.setenv("TEST_SECRET_FILE", str(path))
    assert sl.load_secret("TEST_SECRET").reveal() == FAKE


def test_both_env_and_file_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_SECRET", FAKE)
    monkeypatch.setenv("TEST_SECRET_FILE", str(write_secret_file(tmp_path)))
    with pytest.raises(sl.AmbiguousSecretError):
        sl.load_secret("TEST_SECRET")


def test_missing_required_secret_names_it_but_shows_no_value():
    with pytest.raises(sl.MissingSecretError) as err:
        sl.load_secret("TEST_SECRET")
    message = str(err.value)
    assert "TEST_SECRET" in message and "TEST_SECRET_FILE" in message


def test_missing_optional_secret_returns_none():
    assert sl.load_secret("TEST_SECRET", required=False) is None


def test_file_that_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_SECRET_FILE", str(tmp_path / "nope"))
    with pytest.raises(sl.MissingSecretError):
        sl.load_secret("TEST_SECRET")


def test_empty_file_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_SECRET_FILE", str(write_secret_file(tmp_path, content="\n")))
    with pytest.raises(sl.MissingSecretError):
        sl.load_secret("TEST_SECRET")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
@pytest.mark.parametrize("mode", [0o644, 0o666, 0o660, 0o604, 0o440, 0o640])
def test_loose_file_permissions_are_rejected(monkeypatch, tmp_path, mode):
    # Group read (0440/0640) counts as loose unless the group is trusted.
    monkeypatch.setenv("TEST_SECRET_FILE", str(write_secret_file(tmp_path, mode=mode)))
    with pytest.raises(sl.InsecureSecretFileError) as err:
        sl.load_secret("TEST_SECRET")
    assert FAKE not in str(err.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_safe_file_permissions_are_accepted(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("TEST_SECRET_FILE", str(write_secret_file(tmp_path, mode=mode)))
    assert sl.load_secret("TEST_SECRET").reveal() == FAKE


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
@pytest.mark.parametrize("mode", [0o440, 0o640])
def test_group_read_is_accepted_only_for_the_trusted_group(monkeypatch, tmp_path, mode):
    path = write_secret_file(tmp_path, mode=mode)
    monkeypatch.setenv("TEST_SECRET_FILE", str(path))
    file_gid = path.stat().st_gid

    monkeypatch.setenv(sl.TRUSTED_GID_VAR, str(file_gid))  # e.g. the pod's fsGroup
    assert sl.load_secret("TEST_SECRET").reveal() == FAKE

    monkeypatch.setenv(sl.TRUSTED_GID_VAR, str(file_gid + 4242))  # some unrelated group
    with pytest.raises(sl.InsecureSecretFileError, match="readable by its group"):
        sl.load_secret("TEST_SECRET")


def test_trusted_group_must_be_numeric(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_SECRET_FILE", str(write_secret_file(tmp_path, mode=0o440)))
    monkeypatch.setenv(sl.TRUSTED_GID_VAR, "secrets")
    with pytest.raises(sl.SecretError, match="numeric"):
        sl.load_secret("TEST_SECRET")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_file_owned_by_another_user_is_rejected(monkeypatch, tmp_path):
    path = write_secret_file(tmp_path)
    monkeypatch.setenv("TEST_SECRET_FILE", str(path))
    monkeypatch.setattr(sl.os, "geteuid", lambda: path.stat().st_uid + 4242)
    if path.stat().st_uid == 0:
        pytest.skip("root-owned files are always allowed")
    with pytest.raises(sl.InsecureSecretFileError, match="owned by another user"):
        sl.load_secret("TEST_SECRET")


# --------------------------------------------------------------------------- #
# Secret wrapper
# --------------------------------------------------------------------------- #

def test_secret_never_reveals_itself_when_printed():
    secret = sl.Secret("BOX_CLIENT_SECRET", FAKE)
    for rendered in (repr(secret), str(secret), f"{secret}", "%s" % secret, "{}".format(secret)):
        assert FAKE not in rendered
        assert "BOX_CLIENT_SECRET" in rendered


def test_secret_cannot_be_pickled():
    with pytest.raises(TypeError):
        pickle.dumps(sl.Secret("X", FAKE))


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #

def test_redact_masks_registered_values():
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")
    assert sl.redact(f"sent {FAKE} to box") == "sent *** to box"


def test_redact_masks_common_token_patterns():
    assert "abc.def-123" not in sl.redact("Authorization: Bearer abc.def-123")
    assert "s3cr3tvalue" not in sl.redact("client_secret=s3cr3tvalue&x=1")
    assert "s3cr3tvalue" not in sl.redact('{"password": "s3cr3tvalue"}')
    assert "s3cr3tvalue" not in sl.redact("{'access_token': 's3cr3tvalue'}")
    assert "s3cr3tvalue" not in sl.redact("password = s3cr3tvalue")
    assert sl.redact("password reset requested") == "password reset requested"  # no false positive
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----"
    assert "MIIBOgIBAAJBAK" not in sl.redact(f"key: {pem} done")


def test_redaction_scope_masks_only_while_open():
    with sl.redaction_scope(FAKE, "CONNECT_PASSWORD"):
        assert sl.redact(FAKE) == "***"
        with sl.redaction_scope(FAKE, "CONNECT_PASSWORD"):  # a second request, same password
            pass
        assert sl.redact(FAKE) == "***"  # still open in the outer scope
    assert sl.redact(f"pw {FAKE}") == f"pw {FAKE}"
    assert sl._snapshot() == {}


def test_redaction_scope_keeps_long_lived_secrets():
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")
    with sl.redaction_scope(FAKE, "CONNECT_PASSWORD"):
        pass
    assert sl.redact(FAKE) == "***"


def test_redaction_scope_closes_on_error():
    with pytest.raises(RuntimeError):
        with sl.redaction_scope(FAKE, "CONNECT_PASSWORD"):
            raise RuntimeError("request failed")
    assert sl._snapshot() == {}


def test_very_short_values_are_not_registered():
    sl.register_for_redaction("abc", "TINY")
    assert sl.redact("abc def") == "abc def"


def make_logger(name):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger(name)
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_log_messages_and_arguments_are_masked():
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")
    sl.install_log_redaction()
    logger, stream = make_logger("test.redaction.msg")

    logger.info("plain %s", FAKE)
    logger.info(f"fstring {FAKE}")
    logger.info("wrapped %s", sl.Secret("BOX_CLIENT_SECRET", FAKE))

    output = stream.getvalue()
    assert FAKE not in output
    assert output.count("***") >= 3


def test_tracebacks_are_masked():
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")
    sl.install_log_redaction()
    logger, stream = make_logger("test.redaction.exc")

    try:
        raise ValueError(f"auth failed for {FAKE}")
    except ValueError:
        logger.exception("could not authenticate")

    output = stream.getvalue()
    assert "Traceback" in output  # the traceback is still useful ...
    assert FAKE not in output  # ... but the secret is gone


def test_extra_fields_are_masked():
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")
    logger, stream = make_logger("test.redaction.extra")  # created before install
    sl.install_log_redaction()
    for handler in logger.handlers:
        handler.setFormatter(logging.Formatter("%(message)s cred=%(credential)s ctx=%(ctx)s n=%(n)d"))

    logger.info("safe", extra={"credential": FAKE, "ctx": {"token": FAKE}, "n": 3})

    output = stream.getvalue()
    assert FAKE not in output
    assert "cred=*** " in output and "n=3" in output


def test_install_is_idempotent_and_covers_loggers_added_later():
    sl.register_for_redaction(FAKE, "X")
    sl.install_log_redaction()
    factory = logging.getLogRecordFactory()
    make_record = logging.Logger.makeRecord
    sl.install_log_redaction()
    assert logging.getLogRecordFactory() is factory
    assert logging.Logger.makeRecord is make_record

    logger, stream = make_logger("test.redaction.later")
    logger.warning("late %s", FAKE)
    assert FAKE not in stream.getvalue()


def test_unformattable_log_message_does_not_leak_or_crash():
    sl.register_for_redaction(FAKE, "X")
    sl.install_log_redaction()
    logger, stream = make_logger("test.redaction.bad")
    logger.info("%d items", FAKE)  # wrong type on purpose
    output = stream.getvalue()
    assert FAKE not in output
    assert "suppressed" in output


def test_secret_loaded_with_load_secret_is_redacted_automatically(monkeypatch):
    monkeypatch.setenv("TEST_SECRET", FAKE)
    sl.load_secret("TEST_SECRET")
    assert FAKE not in sl.redact(f"value is {FAKE}")


# --------------------------------------------------------------------------- #
# Export guard
# --------------------------------------------------------------------------- #

def test_clean_directory_passes(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "device1.log").write_text("cpu 12%\nmemory 40%\n")
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")
    sl.assert_no_secrets([tmp_path])  # does not raise


def test_planted_secret_blocks_export_and_error_names_it_without_the_value(tmp_path):
    (tmp_path / "logs").mkdir()
    planted = tmp_path / "logs" / "debug.txt"
    planted.write_text(f"line 1\nbox said: {FAKE}\nline 3\n")
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")

    with pytest.raises(sl.ExportGuardError) as err:
        sl.assert_no_secrets([tmp_path])

    message = str(err.value)
    assert "BOX_CLIENT_SECRET" in message
    assert "debug.txt" in message
    assert FAKE not in message


def test_secret_split_across_read_chunks_is_still_found(tmp_path):
    path = tmp_path / "big.log"
    path.write_text("A" * 5 + FAKE + "B" * 5)
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")
    findings = sl.scan_for_secrets([path], chunk_size=8)
    assert [f.what for f in findings] == ["registered secret BOX_CLIENT_SECRET"]


def test_secret_inside_a_zip_is_found(tmp_path):
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("logs/ok.log", "nothing here")
        zf.writestr("logs/leak.log", f"token={FAKE}")
    sl.register_for_redaction(FAKE, "BOX_CLIENT_SECRET")

    findings = sl.scan_for_secrets([archive])
    assert len(findings) == 1
    assert findings[0].location.endswith("export.zip::logs/leak.log")
    assert FAKE not in str(findings[0])


def test_private_key_and_bearer_token_are_flagged(tmp_path):
    (tmp_path / "key.txt").write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----")
    (tmp_path / "hdr.txt").write_text("Authorization: Bearer abcdefghij0123456789abcdefghij")
    whats = sorted(f.what for f in sl.scan_for_secrets([tmp_path]))
    assert whats == ["bearer token", "private key"]


def test_missing_path_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        sl.scan_for_secrets([tmp_path / "does-not-exist"])


def test_secret_loaded_at_startup_is_caught_by_the_export_scan(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_SECRET", FAKE)
    sl.load_secret("TEST_SECRET")
    (tmp_path / "out.txt").write_text(f"oops {FAKE}")
    with pytest.raises(sl.ExportGuardError):
        sl.assert_no_secrets([tmp_path])
