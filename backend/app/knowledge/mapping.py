"""
The reviewed manual mapping: the only source of "confirmed against manual".

A person reads the service manuals and writes down, per pattern rule, where
the manual covers it (or that they searched and it does not). This file loads
that record. It never searches manual text itself: on a medical device, a
wrong "confirmed" is worse than none, so a match has to be one a person made.

The file is JSON, kept outside the repo like the manuals it points into
(settings.manual_mapping_file / MANUAL_MAPPING_FILE). backend/manual_mapping.example.json
shows the shape:

    {"version": "2026-11-20",
     "rules": {
       "ping_timeout": {"status": "confirmed", "document": "...", "section": "7.4 ...",
                        "page": 112, "actions": ["..."]},
       "hidepid":      {"status": "no_match", "searched": ["Service manual 5.2"]}}}

Rules for an entry:
  confirmed   needs "document" and "section"; "page" and "actions" are optional.
  no_match    needs a non-empty "searched" list: the manuals that were checked.
Anything else, a rule that is not listed, or an unreadable file, comes back as
"not checked". Extra fields (e.g. who reviewed it) may be in the file for the
team's records; only the fields above are ever read, so they are never copied
into the database, the API or an export.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config import settings

log = logging.getLogger(__name__)

TEXT_LIMIT = 200          # longest document / section / action kept; references, not manual text


@dataclass(frozen=True)
class Entry:
    status: str                          # confirmed | no_match (only valid entries are kept)
    manual_ref: Optional[str] = None     # "Document, section X, p. N" for confirmed
    actions: tuple = ()                  # from the manual, confirmed only


@dataclass
class Mapping:
    configured: bool                     # a mapping file is set
    readable: bool                       # ... and it could be read
    version: Optional[str] = None
    entries: dict = field(default_factory=dict)   # rule id -> Entry


_cache: dict = {}                        # path -> (mtime_ns, size, Mapping)


def _text(value) -> Optional[str]:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    out = str(value).strip()
    return out[:TEXT_LIMIT] or None


def _entry(rule_id: str, raw) -> Optional[Entry]:
    """A valid entry, or None (logged by rule id only, never the content)."""
    if not isinstance(raw, dict):
        log.warning("manual mapping: entry for %s is not an object; treated as not checked", rule_id)
        return None
    status = raw.get("status")
    if status == "confirmed":
        document, section, page = _text(raw.get("document")), _text(raw.get("section")), _text(raw.get("page"))
        if not document or not section:
            log.warning("manual mapping: %s is confirmed without a document and section; treated as not checked",
                        rule_id)
            return None
        ref = f"{document}, section {section}" + (f", p. {page}" if page else "")
        actions = raw.get("actions") or []
        actions = tuple(a for a in (_text(x) for x in actions) if a) if isinstance(actions, list) else ()
        return Entry("confirmed", ref, actions)
    if status == "no_match":
        searched = raw.get("searched")
        if not isinstance(searched, list) or not any(_text(s) for s in searched):
            log.warning("manual mapping: %s is no_match without a 'searched' list; treated as not checked", rule_id)
            return None
        return Entry("no_match")
    log.warning("manual mapping: %s has an unknown status; treated as not checked", rule_id)
    return None


def _parse(raw_bytes: bytes) -> Mapping:
    data = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), dict):
        raise ValueError("expected an object with a 'rules' object")
    version = _text(data.get("version")) or "sha256:" + hashlib.sha256(raw_bytes).hexdigest()[:12]
    entries = {}
    for rule_id, raw in data["rules"].items():
        entry = _entry(str(rule_id), raw)
        if entry is not None:
            entries[str(rule_id)] = entry
    return Mapping(configured=True, readable=True, version=version, entries=entries)


def load_mapping() -> Mapping:
    """The current mapping. Re-read when the file changes; never raises."""
    if not settings.manual_mapping_file:
        return Mapping(configured=False, readable=False)
    path = Path(settings.manual_mapping_file).expanduser()
    try:
        st = path.stat()
        cached = _cache.get(str(path))
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return cached[2]
        mapping = _parse(path.read_bytes())
    except (OSError, ValueError, UnicodeDecodeError) as e:
        # Type only: the path is the team's own setting, the content is not ours to log.
        log.warning("manual mapping could not be read (%s); every finding is not checked", type(e).__name__)
        return Mapping(configured=True, readable=False)
    _cache[str(path)] = (st.st_mtime_ns, st.st_size, mapping)
    return mapping
