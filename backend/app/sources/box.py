"""
Box (optional). Some teams drop log exports in a Box folder; this pulls the
ones modified in the requested time range. Box is not part of the standard
service process (the platform's /portrait/diags export is), and it needs
outbound internet, which the lab switch and many hospital networks do not
have. So with no credentials or no route to Box, this source shows as
unavailable with a reason and everything else keeps working.

Auth: Client Credentials Grant (client ID + secret + enterprise or user ID).
The Box auth type is still to be confirmed with the Box admin (SECRETS.md);
if it turns out to be JWT or OAuth, only _build_api() changes.

A common setup gotcha: a CCG app acts as its own service account, which can
only see folders it has been added to as a collaborator. A 404 on the folder
almost always means that, so the error says so.
"""
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Protocol

from app.config import settings
from app.secrets_loader import SecretError, load_secret, register_for_redaction
from app.sources.base import ALLOWED_SUFFIXES, LogSource, RemoteFile, SourceError, SourceStatus

log = logging.getLogger(__name__)

MAX_DEPTH = 3      # subfolders to walk below BOX_FOLDER_ID
PAGE_SIZE = 1000   # Box's maximum for folder listings


@dataclass
class BoxEntry:
    type: str            # "file" | "folder" | "web_link"
    id: str
    name: str
    size: int | None = None
    modified_at: datetime | None = None
    sha1: str | None = None


class BoxApi(Protocol):
    """The three calls this source needs. The real one wraps box-sdk-gen;
    tests pass a fake, so nothing in the test suite touches the network."""
    def get_folder_name(self, folder_id: str) -> str: ...
    def list_folder(self, folder_id: str, marker: str | None) -> tuple[list[BoxEntry], str | None]: ...
    def download(self, file_id: str, out: BinaryIO) -> None: ...


# --- real API (box-sdk-gen) -------------------------------------------------

def _explain_box_error(e: Exception, folder_id: str | None) -> SourceError:
    """Turn an SDK exception into a message a field engineer can act on.
    Never includes str(e): it can carry request/response details."""
    from box_sdk_gen import BoxAPIError, BoxSDKError

    if isinstance(e, BoxAPIError):
        status = e.response_info.status_code
        url = e.request_info.url or ""
        log.warning("Box API error: status=%s code=%s request_id=%s",
                    status, e.response_info.code, e.response_info.request_id)
        if "oauth2/token" in url or status == 401:
            return SourceError(
                "Box rejected the app credentials. Check BOX_CLIENT_ID, BOX_CLIENT_SECRET "
                "and BOX_ENTERPRISE_ID / BOX_USER_ID, and that the Box admin has authorized the app."
            )
        if status in (403, 404):
            return SourceError(
                f"The Box app cannot see folder {folder_id}. Check BOX_FOLDER_ID, and ask the "
                "Box admin to add the app's service account to that folder as a collaborator."
            )
        if status == 429:
            return SourceError("Box is rate limiting requests. Wait a minute and try again.")
        if status >= 500:
            return SourceError(f"Box is having problems right now (HTTP {status}). Try again later.")
        return SourceError(f"Box returned HTTP {status}.")
    if isinstance(e, BoxSDKError):
        log.warning("Box SDK error: %s", type(e.error).__name__ if e.error else "unknown")
        return SourceError(
            "Could not reach Box. This machine may have no internet connection "
            "(the lab switch usually has none)."
        )
    return SourceError(f"Unexpected Box error ({type(e).__name__}).")


class _SdkBoxApi:
    def __init__(self, client, folder_id: str | None):
        self._client = client
        self._folder_id = folder_id

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # SDK raises a few different types; map them all
            raise _explain_box_error(e, self._folder_id) from None

    def get_folder_name(self, folder_id: str) -> str:
        folder = self._call(self._client.folders.get_folder_by_id, folder_id, fields=["name"])
        return folder.name or folder_id

    def list_folder(self, folder_id: str, marker: str | None):
        page = self._call(
            self._client.folders.get_folder_items,
            folder_id,
            fields=["type", "id", "name", "size", "modified_at", "sha1"],
            usemarker=True,
            marker=marker,
            limit=PAGE_SIZE,
        )
        entries = [
            BoxEntry(
                type=str(getattr(item.type, "value", item.type)),
                id=item.id,
                name=getattr(item, "name", None) or item.id,
                size=getattr(item, "size", None),
                modified_at=getattr(item, "modified_at", None),
                sha1=getattr(item, "sha_1", None),
            )
            for item in (page.entries or [])
        ]
        return entries, page.next_marker or None

    def download(self, file_id: str, out: BinaryIO) -> None:
        self._call(self._client.downloads.download_file_to_output_stream, file_id, out)


def _build_api(client_id: str, client_secret: str, enterprise_id: str | None,
               user_id: str | None, folder_id: str | None) -> BoxApi:
    from box_sdk_gen import BoxCCGAuth, BoxClient, CCGConfig
    from box_sdk_gen.box.token_storage import InMemoryTokenStorage
    from box_sdk_gen.networking.network import NetworkSession
    from box_sdk_gen.networking.retries import BoxRetryStrategy

    class _RedactingTokenStorage(InMemoryTokenStorage):
        # Every access token Box hands back, including refreshes, gets masked
        # in logs and caught by the export check (SCRUM-72).
        def store(self, token):
            if token is not None and token.access_token:
                register_for_redaction(token.access_token, "BOX_ACCESS_TOKEN")
            super().store(token)

    config = CCGConfig(
        client_id=client_id,
        client_secret=client_secret,
        enterprise_id=enterprise_id,
        user_id=user_id,
        token_storage=_RedactingTokenStorage(),
    )
    # Fail fast when offline instead of retrying for minutes.
    session = NetworkSession(retry_strategy=BoxRetryStrategy(max_attempts=2, max_retries_on_exception=1))
    client = BoxClient(BoxCCGAuth(config), network_session=session)
    return _SdkBoxApi(client, folder_id)


# --- the source -------------------------------------------------------------

class BoxSource(LogSource):
    id = "box"
    display_name = "Box folder"
    needs_network = True

    def __init__(self, api_factory: Callable[[], BoxApi] | None = None):
        # Settings and secrets are read at call time, not import time, so the
        # tool starts with none of them set and picks them up from .env/env.
        self._api_factory = api_factory
        self._api: BoxApi | None = None

    def _missing_settings(self) -> list[str]:
        missing = []
        if not settings.box_folder_id:
            missing.append("BOX_FOLDER_ID")
        if bool(settings.box_enterprise_id) == bool(settings.box_user_id):
            missing.append("exactly one of BOX_ENTERPRISE_ID / BOX_USER_ID")
        return missing

    def is_configured(self) -> SourceStatus:
        if self._api_factory is None:
            try:
                ids = [load_secret("BOX_CLIENT_ID", required=False),
                       load_secret("BOX_CLIENT_SECRET", required=False)]
            except SecretError as e:  # messages name the secret, never its value
                return SourceStatus(ok=False, reason=str(e))
            if not all(ids):
                return SourceStatus(ok=False, reason="Box is not set up: BOX_CLIENT_ID and BOX_CLIENT_SECRET are needed.")
        missing = self._missing_settings()
        if missing:
            return SourceStatus(ok=False, reason=f"Box is not set up: set {', '.join(missing)}.")
        return SourceStatus(ok=True)

    def _get_api(self) -> BoxApi:
        if self._api is not None:
            return self._api
        status = self.is_configured()
        if not status.ok:
            raise SourceError(status.reason)
        if self._api_factory is not None:
            self._api = self._api_factory()
        else:
            client_id = load_secret("BOX_CLIENT_ID")
            client_secret = load_secret("BOX_CLIENT_SECRET")
            self._api = _build_api(
                client_id.reveal(), client_secret.reveal(),
                settings.box_enterprise_id, settings.box_user_id, settings.box_folder_id,
            )
        return self._api

    def test_connection(self) -> SourceStatus:
        try:
            name = self._get_api().get_folder_name(settings.box_folder_id)
        except SourceError as e:
            self._api = None  # rebuild next time, e.g. after creds are fixed
            return SourceStatus(ok=False, reason=str(e))
        return SourceStatus(ok=True, reason=f"Connected to Box folder '{name}'.")

    def list_available(self, since: datetime, until: datetime, options: dict[str, str]) -> list[RemoteFile]:
        api = self._get_api()
        files: list[RemoteFile] = []
        stack = [(settings.box_folder_id, 0)]
        while stack:
            folder_id, depth = stack.pop()
            marker = None
            while True:
                entries, marker = api.list_folder(folder_id, marker)
                for e in entries:
                    if e.type == "folder":
                        if depth < MAX_DEPTH:
                            stack.append((e.id, depth + 1))
                        continue
                    if e.type != "file" or Path(e.name).suffix.lower() not in ALLOWED_SUFFIXES:
                        continue
                    modified = _as_utc(e.modified_at)
                    if modified is None or not (since <= modified <= until):
                        continue
                    files.append(RemoteFile(
                        source=self.id, remote_id=e.id, name=e.name, size=e.size,
                        modified_at=modified, checksum=e.sha1,
                    ))
                if not marker:
                    break
        return files

    def fetch(self, remote: RemoteFile, dest_dir: Path) -> Path:
        api = self._get_api()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(remote.name).name
        tmp = dest.with_name(dest.name + ".part")
        try:
            with tmp.open("wb") as out:
                api.download(remote.remote_id, out)
            if remote.checksum:
                actual = _sha1_of(tmp)
                if actual != remote.checksum.lower():
                    raise SourceError(f"{remote.name} was corrupted in transit (checksum mismatch). Try again.")
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return dest


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
