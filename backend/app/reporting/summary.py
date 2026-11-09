"""
The diagnostic summary report: one readable page per run listing the key
errors, when they happened, which devices and components they touched, and
what to do next, so a field engineer does not have to open the log files.

Sources, all already in the database or the run's folder:
  run                 the Job row
  findings            every rule in analysis/rules.RULES applied to the run's
                      events in one pass, for count, first/last seen, devices
                      and components. Applied here even when pattern detection
                      never ran for the run (an uploaded collection) or ran
                      before more logs arrived, so the report never says
                      "none found" just because nobody looked. A saved Signal
                      still supplies its manual match status and actions.
  other key errors    ERROR/WARN events no rule matched, grouped by process
                      and message shape (numbers, ids and quoted values blanked)
  diagnostics         each tool's result (orchestrator/jobs.read_results)
  data completeness   storage.events.collection_summary and the PHI/PII scan

Suggested next steps from findings are drafts until knowledge/lookup.py can
match them against service manuals, and the report says so. With `mask` on
(the default), anything the privacy scanner flags in a message shape is
redacted, the same rule the Logs page and the export use.

build_summary() gathers everything; render_markdown() and summary_to_json()
turn it into SUMMARY.md / summary.json.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.rules import RULES, Rule
from app.knowledge.lookup import lookup_signal
from app.models import Job, Signal
from app.models import NormalizedEvent as E
from app.orchestrator.jobs import read_results
from app.privacy.scanner import category_group, mask_text, scan_text
from app.storage import events as store
from app.storage.completeness import PROBLEM

TOP_ERRORS = 10
TOP_DEVICES = 5
# Distinct message shapes kept while grouping "other" errors. A noisy run can
# have a huge number; lines past the cap are counted, not grouped.
MAX_SHAPES = 2000
RUNNING = ("queued", "running")
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
DRAFT_NOTE = "draft, not yet confirmed against service manuals"


@dataclass
class Spread:
    """When and where something happened."""
    count: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    devices: Counter = field(default_factory=Counter)
    processes: Counter = field(default_factory=Counter)

    def add(self, ts: datetime, device: Optional[str], process: Optional[str]) -> None:
        self.count += 1
        if self.first_seen is None or ts < self.first_seen:
            self.first_seen = ts
        if self.last_seen is None or ts > self.last_seen:
            self.last_seen = ts
        if device:
            self.devices[device] += 1
        if process:
            self.processes[process] += 1


@dataclass
class FindingItem:
    rule_id: str
    title: str
    severity: str                 # error | warning | info
    count: int
    status: str                   # unconfirmed | confirmed | no_match
    manual_ref: Optional[str]
    actions: list[str]
    spread: Spread
    saved: bool = True            # a Signal from pattern detection exists for it


@dataclass
class ErrorGroup:
    severity: str                 # ERROR | WARN
    process: Optional[str]
    message: str                  # shape: numbers, ids and quoted values blanked; masked if needed
    spread: Spread


@dataclass
class ToolOutcome:
    tool: str
    ok: bool
    detail: str
    warnings: list[str]


@dataclass
class Summary:
    job_id: str
    mode: Optional[str]
    status: Optional[str]
    tools: list[str]
    requested_by: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    generated_at: datetime
    masked: bool
    events: int
    first_event: Optional[datetime]
    last_event: Optional[datetime]
    findings: list[FindingItem]
    other_errors: list[ErrorGroup]
    other_error_total: int        # ERROR/WARN lines no rule matched, all of them (not just the top)
    other_error_ungrouped: int    # of those, lines past MAX_SHAPES distinct messages
    analysis: str                 # current | not_run | out_of_date: the saved pattern detection vs. the logs
    diagnostics: list[ToolOutcome]
    logs_complete: Optional[bool]
    completeness_issues: list[str]
    next_steps: list[str]
    notes: list[str]

    @property
    def verdict(self) -> str:
        if self.status in RUNNING:
            return "The run has not finished; this summary covers what is stored so far."
        problems = sum(f.severity == "error" for f in self.findings) + sum(not t.ok for t in self.diagnostics)
        problems += 0 if self.logs_complete in (None, True) else 1
        if problems:
            return f"Finished with {problems} problem{'s' if problems != 1 else ''} needing attention."
        review = sum(f.severity == "warning" for f in self.findings) + len(self.other_errors)
        if review:
            return f"Finished; no known problems, {review} item{'s' if review != 1 else ''} to review."
        return "Finished with nothing that needs attention."


# --- message shapes -------------------------------------------------------------------

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{12,}\b")
_QUOTED = re.compile(r"(['\"])[^'\"]{1,80}\1")
_NUMBER = re.compile(r"\d+(?:[.:]\d+)*")
_SPACES = re.compile(r"\s+")


def message_shape(message: Optional[str]) -> str:
    """'Timeout after 3021 ms for 1f0e...' -> 'Timeout after <n> ms for <id>', so
    lines that differ only in values group together."""
    text = _UUID.sub("<id>", message or "")
    text = _HEX.sub("<id>", text)
    text = _QUOTED.sub(r"\1…\1", text)
    text = _NUMBER.sub("<n>", text)
    return _SPACES.sub(" ", text).strip()


def _shorten(text: str, limit: int = 160) -> str:
    return text[:limit] + ("…" if len(text) > limit else "")


def _safe(text: Optional[str], mask: bool, field: str = "message") -> Optional[str]:
    """Mask personal data the privacy scanner flags in one value, as the Logs
    page does. IP and MAC addresses identify equipment, not people, so they
    stay readable (a device name is often a MAC address)."""
    if not mask or not text:
        return text
    flags = [f for f in scan_text(text, field) if category_group(f.category) == "personal"]
    return mask_text(text, flags, field) if flags else text


# --- building ------------------------------------------------------------------------

def _matches(rule: Rule, row) -> bool:
    # The one place a rule is applied. Pattern detection (#35) changes this to
    # rule.matches(row.severity, row.message).
    return rule.match(row)


def _scan_events(db: Session, job_id: str, mask: bool):
    """One pass over the run's events (columns only): every rule's spread and
    groups of unmatched ERROR/WARN lines. Device and component names are
    masked here, once per distinct value."""
    by_rule = {r.id: Spread() for r in RULES}
    groups: dict[tuple, Spread] = {}
    total = unmatched = ungrouped = 0
    first = last = None
    names: dict[tuple, Optional[str]] = {}

    def name(value: Optional[str], fld: str) -> Optional[str]:
        if (value, fld) not in names:
            names[(value, fld)] = _safe(value, mask, fld)
        return names[(value, fld)]

    q = select(E.timestamp, E.device_id, E.process, E.severity, E.message).where(E.job_id == job_id)
    for row in db.execute(q).yield_per(5000):
        total += 1
        first = row.timestamp if first is None or row.timestamp < first else first
        last = row.timestamp if last is None or row.timestamp > last else last
        matched = False
        for rule in RULES:
            if _matches(rule, row):
                by_rule[rule.id].add(row.timestamp, name(row.device_id, "device_id"), name(row.process, "process"))
                matched = True
        if not matched and row.severity in ("ERROR", "WARN"):
            unmatched += 1
            process = name(row.process, "process")
            key = (row.severity, process, message_shape(row.message))
            group = groups.get(key)
            if group is None:
                if len(groups) >= MAX_SHAPES:
                    ungrouped += 1
                    continue
                group = groups[key] = Spread()
            group.add(row.timestamp, name(row.device_id, "device_id"), process)
    return total, first, last, by_rule, groups, unmatched, ungrouped


def _findings(signals, by_rule: dict[str, Spread]) -> tuple[list[FindingItem], str]:
    """One finding per rule that matched, and whether the saved Signals agree
    with what the rules find in the stored logs now."""
    saved = {s.rule_id: s for s in signals}
    findings, missing, differs = [], False, False
    for rule in RULES:
        spread, sig = by_rule[rule.id], saved.get(rule.id)
        if sig is not None and sig.count != spread.count:
            differs = True
        if not spread.count:
            continue
        if sig is None:
            missing = True
            match = lookup_signal(rule.id)
            status, ref, actions = match.status, match.manual_ref, match.actions or list(rule.draft_actions)
        else:
            status, ref, actions = sig.status, sig.manual_ref, json.loads(sig.actions_json or "[]")
        findings.append(FindingItem(
            rule_id=rule.id, title=rule.title_template.format(n=spread.count), severity=rule.severity,
            count=spread.count, status=status, manual_ref=ref, actions=actions, spread=spread,
            saved=sig is not None,
        ))
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), -f.count))
    if missing and not signals:
        return findings, "not_run"
    return findings, "out_of_date" if (missing or differs) else "current"


def _tool_outcomes(job_id: str) -> list[ToolOutcome]:
    out = []
    for r in read_results(job_id):
        tool = str(r.get("tool", "unknown"))
        if "error" in r:
            out.append(ToolOutcome(tool, False, str(r["error"]), []))
        else:
            out.append(ToolOutcome(tool, True, str(r.get("summary") or "Ran."), [str(w) for w in r.get("warnings", [])]))
    return out


def _next_steps(findings: list[FindingItem], diagnostics: list[ToolOutcome],
                logs_complete: Optional[bool], issues: list[str]) -> list[str]:
    steps: list[str] = []

    def add(text: str) -> None:
        if text not in steps:
            steps.append(text)

    for t in diagnostics:
        if not t.ok:
            add(f"Re-run the {t.tool} diagnostic ({t.detail}).")
    if logs_complete is False:
        add("Check the data completeness section: some logs are not in this report " +
            ("(" + issues[0] + ")" if issues else "") + ".")
    for f in sorted(findings, key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), -f.count)):
        draft = f.status != "confirmed"
        for a in f.actions:
            add(f"{a} [{f.title}]" + (" (draft)" if draft else ""))
    return steps


def build_summary(db: Session, job_id: str, *, mask: bool = True,
                  now: Optional[datetime] = None) -> Optional[Summary]:
    """Everything the report needs for one run. None for an unknown job."""
    job = db.get(Job, job_id)
    if job is None:
        return None
    signals = db.scalars(select(Signal).where(Signal.job_id == job_id)).all()
    total, first, last, by_rule, groups, unmatched, ungrouped = _scan_events(db, job_id, mask)
    findings, analysis = _findings(signals, by_rule)

    ranked = sorted(groups.items(), key=lambda kv: (kv[0][0] != "ERROR", -kv[1].count))[:TOP_ERRORS]
    other = [ErrorGroup(severity=sev, process=proc, message=_shorten(_safe(shape, mask)), spread=spread)
             for (sev, proc, shape), spread in ranked]

    diagnostics = _tool_outcomes(job_id)
    col = store.collection_summary(db, job_id)
    issues = [i.message for i in col.issues if i.severity == PROBLEM] if col else []
    if col is None or (not total and not issues):
        logs_complete = None
    else:
        logs_complete = col.complete

    notes = []
    if not total:
        notes.append("This run has no stored log entries, so there are no errors to list.")
    if job.status in RUNNING:
        notes.append("The run has not finished; more logs and findings may still arrive.")
    if analysis == "not_run":
        notes.append("Pattern detection has not run for this run, so the findings below come from "
                     "applying the rules to the stored logs now.")
    elif analysis == "out_of_date":
        notes.append("The saved pattern detection for this run is out of date (the logs changed after it "
                     "ran), so the findings below come from applying the rules to the stored logs now.")

    return Summary(
        job_id=job.id, mode=job.mode, status=job.status, tools=json.loads(job.tools or "[]"),
        requested_by=job.requested_by, started_at=job.created_at, finished_at=job.finished_at,
        generated_at=now or datetime.now(timezone.utc).replace(tzinfo=None), masked=mask,
        events=total, first_event=first, last_event=last,
        findings=findings, other_errors=other, other_error_total=unmatched,
        other_error_ungrouped=ungrouped, analysis=analysis,
        diagnostics=diagnostics, logs_complete=logs_complete, completeness_issues=issues,
        next_steps=_next_steps(findings, diagnostics, logs_complete, issues), notes=notes,
    )


# --- output --------------------------------------------------------------------------

def _t(ts: Optional[datetime]) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC") if ts else "-"


def _top(counter: Counter, n: int = TOP_DEVICES) -> str:
    if not counter:
        return "-"
    names = [name for name, _ in counter.most_common(n)]
    more = len(counter) - len(names)
    return ", ".join(names) + (f" (+{more} more)" if more > 0 else "")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(s: Summary) -> str:
    lines = [
        "# Diagnostic summary",
        "",
        f"**{s.verdict}**",
        "",
        f"- Run: `{s.job_id}` ({s.mode or '-'}), status **{s.status or '-'}**",
        f"- Started {_t(s.started_at)}, finished {_t(s.finished_at)}",
        f"- Diagnostics: {', '.join(s.tools) or 'none'}; requested by {s.requested_by or '-'}",
        f"- Logs: {s.events:,} entries from {_t(s.first_event)} to {_t(s.last_event)}",
        f"- Generated {_t(s.generated_at)}; personal data in messages is {'masked' if s.masked else 'NOT masked'}",
    ]
    for n in s.notes:
        lines.append(f"- Note: {n}")

    lines += ["", "## Suggested next steps", ""]
    if any(step.endswith("(draft)") for step in s.next_steps):
        lines += [f"Steps marked (draft) come from the analysis rules and are {DRAFT_NOTE}.", ""]
    lines += [f"{i}. {_cell(step)}" for i, step in enumerate(s.next_steps, 1)] or ["Nothing to do."]

    lines += ["", "## Key errors found by the analysis", ""]
    if not s.findings:
        lines.append("No known problem patterns were found in this run.")
    for f in s.findings:
        sp = f.spread
        lines += [
            f"### {f.title} ({f.severity})",
            "",
            f"- Seen {sp.count or f.count:,} times, first {_t(sp.first_seen)}, last {_t(sp.last_seen)}",
            f"- Devices ({len(sp.devices)}): {_top(sp.devices)}",
            f"- Components: {_top(sp.processes)}",
            f"- Manual match: {f.manual_ref or f.status}",
            "",
        ]

    lines += ["## Other errors and warnings", ""]
    if not s.other_errors:
        lines.append("None beyond the findings above.")
    else:
        lines += [
            f"The most frequent of {s.other_error_total:,} ERROR/WARN lines no rule covers, grouped by component "
            "and message (numbers and ids shown as <n> / <id>):",
            "",
            "| Level | Component | Message | Count | First seen | Last seen | Devices |",
            "|---|---|---|---|---|---|---|",
        ]
        for g in s.other_errors:
            sp = g.spread
            lines.append(f"| {g.severity} | {_cell(g.process or '-')} | {_cell(g.message)} | {sp.count:,} | "
                         f"{_t(sp.first_seen)} | {_t(sp.last_seen)} | {_cell(_top(sp.devices, 3))} |")
        if s.other_error_ungrouped:
            lines += ["", f"{s.other_error_ungrouped:,} more lines had too many different messages to group "
                          f"(over {MAX_SHAPES:,}); search the logs for them."]

    lines += ["", "## Diagnostics", ""]
    if not s.diagnostics:
        lines.append("No diagnostic results were recorded for this run." if s.tools else "No diagnostics were requested.")
    for t in s.diagnostics:
        lines.append(f"- **{t.tool}**: " + (f"ok. {_cell(t.detail)}" if t.ok else f"FAILED, did not finish: {_cell(t.detail)}"))
        lines += [f"  - warning: {_cell(w)}" for w in t.warnings]

    lines += ["", "## Data completeness", ""]
    if s.logs_complete is None:
        lines.append("No logs were stored for this run.")
    elif s.logs_complete:
        lines.append("Every log row read was stored or accounted for.")
    else:
        lines.append("Some logs are missing or were rejected:")
        lines += [f"- {_cell(i)}" for i in s.completeness_issues]
    lines.append("")
    return "\n".join(lines)


def summary_to_json(s: Summary) -> dict:
    def spread(sp: Spread) -> dict:
        return {"count": sp.count, "first_seen": _iso(sp.first_seen), "last_seen": _iso(sp.last_seen),
                "devices": dict(sp.devices.most_common()), "components": dict(sp.processes.most_common())}

    return {
        "job_id": s.job_id, "verdict": s.verdict, "status": s.status, "mode": s.mode, "tools": s.tools,
        "requested_by": s.requested_by, "started_at": _iso(s.started_at), "finished_at": _iso(s.finished_at),
        "generated_at": _iso(s.generated_at), "masked": s.masked,
        "events": s.events, "first_event": _iso(s.first_event), "last_event": _iso(s.last_event),
        "next_steps": s.next_steps,
        "findings": [{**{k: v for k, v in asdict(f).items() if k != "spread"}, **spread(f.spread)} for f in s.findings],
        "other_errors": [{"severity": g.severity, "component": g.process, "message": g.message, **spread(g.spread)}
                         for g in s.other_errors],
        "other_error_total": s.other_error_total, "other_error_ungrouped": s.other_error_ungrouped,
        "analysis": s.analysis,
        "diagnostics": [asdict(t) for t in s.diagnostics],
        "logs_complete": s.logs_complete, "completeness_issues": s.completeness_issues, "notes": s.notes,
    }


def _iso(ts: Optional[datetime]) -> Optional[str]:
    return ts.replace(tzinfo=timezone.utc).isoformat() if ts else None
