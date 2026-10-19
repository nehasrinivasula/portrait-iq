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
  2. run(target_ip)      deploy (if needed) and run the tool.
  3. cleanup()           tear down whatever run() created. Always called,
                         also after a failure in this or another plugin.

To add a plugin, write a Collector subclass in a new module in this package
and add one line to PLUGINS in registry.py. Nothing else changes.

Nothing here actually reaches a device yet: every built-in run() is a stub.
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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

    def configure(self, options: dict[str, Any]) -> None:
        """Validate and store options; raise PluginConfigError on anything
        unexpected. The default accepts no options, so a plugin that takes
        none does not need to override this."""
        if options:
            raise PluginConfigError(
                f"{self.name} takes no options; got {sorted(options)}"
            )
        self.options = {}

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
