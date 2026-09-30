"""
This is the seam Marco Van Brenk's feedback was specifically about: he
asked whether the DDS finding's suggested action was a real, confirmed fix
or just a guess. Today it's a guess. This file exists so that's always
labeled honestly, and so replacing the guess with a real manual lookup
later doesn't require touching the analysis engine, the API, or the
frontend — only this function's body.

TODO once manuals/documentation are available:
  - Ingest the service manuals (start with the ones referencing error
    codes and alert notifications, per Michael Parrish's interview).
  - Build a real index (even a keyword index over the PDFs is a fine
    start; a proper search/embedding index is the natural next step).
  - Replace the body below with a real lookup, and start returning
    status="confirmed" with a manual_ref when there's an actual match,
    and status="no_match" when the manual was searched but nothing fit
    (as opposed to "unconfirmed", meaning it hasn't been searched at all).
"""
from dataclasses import dataclass


@dataclass
class KnowledgeMatch:
    status: str                 # "unconfirmed" | "confirmed" | "no_match"
    manual_ref: str | None
    actions: list[str]           # empty means "use the rule's draft_actions instead"


def lookup_signal(rule_id: str) -> KnowledgeMatch:
    """Always returns unconfirmed for now — there is no manual corpus to
    search yet. Never invent a confirmed-looking match here; an honest
    'we don't know' is worth more than a plausible-sounding guess."""
    return KnowledgeMatch(status="unconfirmed", manual_ref=None, actions=[])
