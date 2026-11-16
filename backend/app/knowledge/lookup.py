"""
Finding -> manual. This is the seam Marco Van Brenk's feedback was about: he
asked whether the DDS finding's suggested action was a real, confirmed fix or
a guess. Every finding carries one of three labels, from the actual outcome:

  confirmed     Confirmed against manual. A person matched the rule to a manual
                section (knowledge/mapping.py); always has a manual_ref.
  no_match      No match found. A person searched the manuals and nothing fit.
  not_checked   Not checked against a manual. No mapping is configured, the
                rule has not been reviewed yet, or the lookup failed.

"Not checked" is not "no match": no_match means a search happened. The older
value "unconfirmed" meant not checked and is read as such.

Nothing is ever confirmed without a reference: every result goes through
_checked(), and safe_lookup() turns any failure into not_checked, never into
confirmed or no_match. Call safe_lookup(); lookup_signal() is the part that a
richer lookup (search, embeddings) would replace later, behind the same guard.
"""
import logging
from dataclasses import dataclass, field, replace
from typing import Optional

from app.knowledge.mapping import load_mapping

log = logging.getLogger(__name__)

CONFIRMED, NO_MATCH, NOT_CHECKED = "confirmed", "no_match", "not_checked"
STATUSES = (CONFIRMED, NO_MATCH, NOT_CHECKED)
STATUS_LABELS = {
    CONFIRMED: "Confirmed against manual",
    NO_MATCH: "No match found",
    NOT_CHECKED: "Not checked against a manual",
}
_LEGACY = {"unconfirmed": NOT_CHECKED}

NOTE_NOT_CONFIGURED = "No manual mapping is configured."
NOTE_UNREADABLE = "The manual mapping could not be read."
NOTE_NOT_REVIEWED = "Not reviewed against the manuals yet."
NOTE_NO_MATCH = "The manuals were searched; none covers this."


@dataclass
class KnowledgeMatch:
    status: str                          # confirmed | no_match | not_checked
    manual_ref: Optional[str] = None     # "Document, section X, p. N"; required for confirmed
    actions: list[str] = field(default_factory=list)   # from the manual; empty means the rule's drafts
    note: Optional[str] = None           # short reason, safe to show; never manual text
    version: Optional[str] = None        # mapping version it was checked against


def normalize_status(status: Optional[str]) -> str:
    """Any stored status as one of STATUSES (old and unknown values -> not_checked)."""
    status = _LEGACY.get(status or "", status)
    return status if status in STATUSES else NOT_CHECKED


def status_label(status: Optional[str]) -> str:
    return STATUS_LABELS[normalize_status(status)]


def stored_outcome(status: Optional[str], manual_ref: Optional[str]) -> tuple[str, Optional[str]]:
    """A saved finding's (status, manual_ref) as it may be shown: old values
    normalized, and a confirmed row without a reference shown as not checked."""
    status = normalize_status(status)
    if status != CONFIRMED:
        return status, None
    return (CONFIRMED, manual_ref) if (manual_ref or "").strip() else (NOT_CHECKED, None)


def _checked(rule_id: str, match: KnowledgeMatch) -> KnowledgeMatch:
    """The one guard: confirmed needs a reference; only known statuses pass."""
    if match.status == CONFIRMED and not (match.manual_ref or "").strip():
        log.warning("lookup for %s said confirmed without a manual reference; labelled not checked", rule_id)
        return KnowledgeMatch(NOT_CHECKED, note=NOTE_NOT_REVIEWED, version=match.version)
    if match.status not in STATUSES:
        log.warning("lookup for %s returned an unknown status; labelled not checked", rule_id)
        return KnowledgeMatch(NOT_CHECKED, note=NOTE_NOT_REVIEWED, version=match.version)
    if match.status != CONFIRMED:
        match = replace(match, manual_ref=None, actions=[])   # references and manual steps only when confirmed
    return match


def lookup_signal(rule_id: str) -> KnowledgeMatch:
    """What the reviewed mapping says about one rule. Use safe_lookup()."""
    mapping = load_mapping()
    if not mapping.configured:
        return KnowledgeMatch(NOT_CHECKED, note=NOTE_NOT_CONFIGURED)
    if not mapping.readable:
        return KnowledgeMatch(NOT_CHECKED, note=NOTE_UNREADABLE)
    entry = mapping.entries.get(rule_id)
    if entry is None:
        return KnowledgeMatch(NOT_CHECKED, note=NOTE_NOT_REVIEWED, version=mapping.version)
    if entry.status == CONFIRMED:
        return KnowledgeMatch(CONFIRMED, manual_ref=entry.manual_ref, actions=list(entry.actions),
                              version=mapping.version)
    return KnowledgeMatch(NO_MATCH, note=NOTE_NO_MATCH, version=mapping.version)


def safe_lookup(rule_id: str) -> KnowledgeMatch:
    """lookup_signal() behind the guard. A failure is not checked, never confirmed or no match."""
    try:
        match = lookup_signal(rule_id)
    except Exception as e:  # noqa: BLE001 - the label must stay honest whatever went wrong
        log.exception("manual lookup for %s failed", rule_id)
        return KnowledgeMatch(NOT_CHECKED, note=f"Lookup failed ({type(e).__name__}).")
    return _checked(rule_id, match)


def current_version() -> Optional[str]:
    """The mapping version a fresh lookup would use (None when none is readable)."""
    try:
        mapping = load_mapping()
    except Exception:  # noqa: BLE001
        return None
    return mapping.version if mapping.readable else None
