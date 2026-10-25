"""
The plugin contract (SCRUM-38). Every diagnostic tool (Netshoot, UMF RTI,
tshark, hub log import, and whatever the "research and propose additional
diagnostic tools" requirement adds later) implements this one interface, so
the orchestrator never needs a special case per tool.

Lifecycle, enforced by orchestrator/jobs.py:

  1. configure(options)  validate and store options. Must not touch the
                         network or a device: the orchestrator configures
                         every selected plugin before running any of them,
                         so a bad option fails the job before anything is
                         deployed.
  2. run(target_ip)      deploy (if needed) and run the tool. Before this,
                         the orchestrator calls bind_job() with the job id
                         and the folder run() writes its files to.
  3. cleanup()           tear down whatever run() created. Always called,
                         also after a failure in this or another plugin.
                         If it raises, the job ends as "cleanup_failed" and
                         the plugin is named in the log and the audit log.

To add a plugin, write a Collector subclass in a new module in this package
and add one line to PLUGINS in registry.py. Nothing else changes.

Plugins that deploy a pod do it through collectors/k8s.py (SCRUM-39).
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class PluginError(Exception):
    """A plugin failure with a message safe to show the field engineer:
    it says what went wrong and never contains a secret value."""


class PluginConfigError(PluginError):
    """configure() was given options the plugin does not accept."""


@dataclass
class ResourceLimits:
    """Ceilings, not targets, from the requirements doc's Reliability
    section (configurable CPU/memory/throttling/file-size limits)."""
    cpu_pct: int
    mem_mb: int
    max_output_mb: int


@dataclass
class CollectorResult:
    tool: str
    artifact_paths: list[str] = field(default_factory=list)   # raw files produced
    summary: str = ""
    warnings: list[str] = field(default_factory=list)


class Collector(ABC):
    # Unique registry key, lowercase letters/digits/underscores, e.g. "netshoot".
    name: ClassVar[str] = ""
    # Shown in the tool-selection UI (SCRUM-40).
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def __init__(self, limits: ResourceLimits):
        self.limits = limits
        self.options: dict[str, Any] = {}
        self.job_id: str | None = None
        self.output_dir: Path | None = None

    def bind_job(self, job_id: str, output_dir: Path) -> None:
        """Called by the orchestrator before run(): the job this run is part
        of (tool pods are labelled with it) and the folder for its files."""
        self.job_id = job_id
        self.output_dir = output_dir

    def require_output_dir(self) -> Path:
        if self.output_dir is None:
            raise PluginError(f"{self.name} was run without a job folder (bind_job not called)")
        return self.output_dir

    def configure(self, options: dict[str, Any]) -> None:
        """Validate and store options; raise PluginConfigError on anything
        unexpected. The default accepts no options, so a plugin that takes
        none does not need to override this."""
        if options:
            raise PluginConfigError(
                f"{self.name} takes no options; got {sorted(options)}"
            )
        self.options = {}

    def audit_fields(self) -> dict[str, Any]:
        """Configured choices to record in the job_start audit entry, e.g.
        a privacy-relevant switch and the reason given for it. Only values
        safe to store: never a credential. The default records nothing."""
        return {}

    @abstractmethod
    def run(self, target_ip: str) -> CollectorResult:
        """Deploy (if needed) and run the tool against target_ip.
        Must respect self.limits. Must be safe to call again after a
        partial failure (idempotent enough not to double-deploy)."""
        raise NotImplementedError

    @abstractmethod
    def cleanup(self) -> None:
        """Tear down anything run() created, unconditionally. This is what
        makes the 'customer environment left in a known clean state'
        requirement true even after a crash. Must be safe to call when
        run() never started or failed halfway."""
        raise NotImplementedError
