"""
The collector plugin interface. Every tool (Netshoot, RTI, tshark, hub log
import, and whatever the "research and propose additional diagnostic tools"
requirement adds later) implements this one contract, so the orchestrator
never needs a special case per tool.

Nothing here actually reaches a device yet — every concrete collector's
run() is a stub. This is the first thing to make real against your lab's
MiniEdge P360 / Virtual Hub Host, per "this week's feature #2".
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ResourceLimits:
    """Ceilings, not targets — from the requirements doc's Reliability
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
    name: str = "unnamed"

    def __init__(self, limits: ResourceLimits):
        self.limits = limits

    @abstractmethod
    def run(self, target_ip: str) -> CollectorResult:
        """Deploy (if needed) and run the tool against target_ip.
        Must respect self.limits. Must be safe to call again after a
        partial failure (idempotent enough not to double-deploy)."""
        raise NotImplementedError

    @abstractmethod
    def cleanup(self) -> None:
        """Tear down anything run() created, unconditionally — this is what
        makes the 'customer environment left in a known clean state'
        requirement true even after a crash. Call this in a finally block,
        never only on the happy path."""
        raise NotImplementedError
