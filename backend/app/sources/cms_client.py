"""Client for the CMS log-snap API (/cms/sprsnap/*). Read-only on purpose: it lists
snaps and downloads one. It never generates or deletes a snap on the live system.

Unverified against the live system: the shape of the list response (assumed a JSON
list of filenames, or of objects with "filename"/"name") and any authentication."""
from pathlib import Path

import httpx

from app.sources.base import SourceError


class CmsClient:
    def __init__(self, base_url: str, *, timeout: float = 60, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def list_snaps(self) -> list[str]:
        try:
            resp = self._http.get("/cms/sprsnap/list")
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise SourceError(f"The CMS snap list could not be read ({type(e).__name__}).") from None
        names = [x if isinstance(x, str) else x.get("filename") or x.get("name") for x in data] \
            if isinstance(data, list) else []
        return sorted(n for n in names if isinstance(n, str) and n and "/" not in n and ".." not in n)

    def download(self, filename: str, out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        try:
            with self._http.stream("GET", f"/cms/sprsnap/download/{filename}") as resp:
                resp.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes(1 << 20):
                        f.write(chunk)
            tmp.replace(out)
        except httpx.HTTPError as e:
            tmp.unlink(missing_ok=True)
            raise SourceError(f"The CMS snap could not be downloaded ({type(e).__name__}).") from None
        return out
