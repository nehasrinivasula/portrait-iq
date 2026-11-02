"""
Device discovery (D-3): which hub, viewers and other devices are on the
Portrait Mobile system's network, so the engineer picks them instead of
typing addresses.

One short-lived pod on the node, from the tshark image, with host networking:

  1. The node's ARP table (/proc/net/arp): every LAN address the node has
     talked to recently. Finds devices, but not what they are.
  2. A short capture of the node's own traffic (host <node IP>), when live
     capture is on for this site. A device sending RTI (DDS) traffic shows
     its domain in its port number (7400 + 250 * domain): domain 12 is the
     source pod (hub), 13 the view pod (viewer). Anything else is listed
     with the ports it used, for the engineer to label (D-7).

Nothing leaves the pod but addresses and ports: tshark summarizes the
capture inside it, and the capture is deleted with the pod. Packets are
cut at the header size anyway.

A later source can add RTI's own participant discovery (rtiddsspy) once
GE's rtitools image is on the bench; it would replace the port guess with
the topics each device publishes.
"""
import ipaddress
import re
import threading
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.collectors import k8s
from app.collectors.base import PluginError, ResourceLimits
from app.collectors.rti import DOMAINS, rtps_port_range
from app.config import settings
from app.models import Device

DEFAULT_DURATION_S = 20
MAX_DURATION_S = 60
HEADER_SNAPLEN = 128
LIMITS = ResourceLimits(cpu_pct=25, mem_mb=256, max_output_mb=64)

# Which RTI domain says which role (GE's rtitools README).
DOMAIN_ROLES = {12: "hub", 13: "viewer"}
DOMAIN_NAMES = {7: "logging", 12: "source pod", 13: "view pod"}

# Interfaces that are Kubernetes plumbing, not the site's network.
_CLUSTER_IFACES = re.compile(r"^(cni|flannel|veth|cali|vxlan|docker|kube|tunl|lo$)")
# k3s/flannel pod and service ranges, plus GE's 100.64/10 pod network.
_CLUSTER_NETS = [ipaddress.ip_network(n) for n in ("100.64.0.0/10", "10.42.0.0/16", "10.43.0.0/16")]
# 192.0.2.10:10535    <-> 192.0.2.3:32013   (tshark -z conv,udp / conv,tcp)
_CONV = re.compile(r"^(?P<a>[0-9.]+):(?P<pa>\d+)\s+<->\s+(?P<b>[0-9.]+):(?P<pb>\d+)\b")

_lock = threading.Lock()


class DiscoveryBusy(Exception):
    pass


@dataclass
class Found:
    ip: str
    kind: str = "unknown"            # "hub" | "viewer" | "server" | "unknown"
    evidence: list[str] = field(default_factory=list)
    mac: str | None = None


@dataclass
class Discovery:
    node_ip: str
    devices: list[Found]
    notes: list[str]
    captured: bool


def rtps_domain(port: int) -> int | None:
    for d in DOMAINS:
        first, last = rtps_port_range(d)
        if first <= port <= last:
            return d
    return None


def _lan_address(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (addr.version == 4 and not addr.is_loopback and not addr.is_link_local
            and not addr.is_multicast and not any(addr in n for n in _CLUSTER_NETS))


def parse_arp(text: str) -> dict[str, str]:
    """IP -> MAC for complete entries on the site's network (/proc/net/arp)."""
    out = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        ip, flags, mac, dev = parts[0], parts[2], parts[3], parts[5]
        if flags == "0x0" or mac == "00:00:00:00:00:00" or _CLUSTER_IFACES.match(dev):
            continue
        if _lan_address(ip):
            out[ip] = mac
    return out


@dataclass
class _Peer:
    udp: set[int] = field(default_factory=set)      # the peer's own ports
    to_node: set[str] = field(default_factory=set)  # "udp 32013" on the node's side


def parse_conversations(text: str, node_ip: str) -> dict[str, _Peer]:
    """Peers that talked to the node, from tshark's conv,udp and conv,tcp tables."""
    peers: dict[str, _Peer] = {}
    proto = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("UDP Conversations"):
            proto = "udp"
        elif line.startswith("TCP Conversations"):
            proto = "tcp"
        m = _CONV.match(line)
        if not m or proto is None:
            continue
        a, pa, b, pb = m["a"], int(m["pa"]), m["b"], int(m["pb"])
        if a == node_ip:
            a, pa, b, pb = b, pb, a, pa
        if b != node_ip or not _lan_address(a):
            continue
        peer = peers.setdefault(a, _Peer())
        if proto == "udp":
            peer.udp.add(pa)
        peer.to_node.add(f"{proto} {pb}")
    return peers


def classify(ip: str, peer: _Peer | None, mac: str | None) -> Found:
    found = Found(ip=ip, mac=mac)
    if peer is not None:
        domains = sorted({d for p in peer.udp if (d := rtps_domain(p)) is not None})
        for d in domains:
            found.evidence.append(f"RTI domain {d} ({DOMAIN_NAMES[d]})")
        roles = [DOMAIN_ROLES[d] for d in domains if d in DOMAIN_ROLES]
        if len(set(roles)) == 1:
            found.kind = roles[0]
        ports = sorted(peer.to_node, key=lambda s: (s.split()[0], int(s.split()[1])))
        found.evidence.append("talks to the system on " + ", ".join(ports[:6]) + ("…" if len(ports) > 6 else ""))
    if mac:
        found.evidence.append("in the system's ARP table")
    return found


def run_discovery(duration_s: int = DEFAULT_DURATION_S) -> Discovery:
    """One discovery pass from a pod on the node. Raises DiscoveryBusy if one
    is already running, PluginError if the pod can't do its work."""
    if not _lock.acquire(blocking=False):
        raise DiscoveryBusy("A device search is already running. Wait for it to finish.")
    try:
        return _discover(duration_s)
    finally:
        _lock.release()


def _discover(duration_s: int) -> Discovery:
    capture = settings.tshark_live_capture_enabled
    runner = k8s.get_pod_runner()
    name = k8s.pod_name("discovery", None)
    start_s = settings.tool_pod_start_timeout_seconds
    deadline = start_s + duration_s + 120
    notes = []
    runner.create(
        name=name, tool="discovery", job_id=None, image=settings.tshark_image,
        command=["sleep", str(deadline)], deadline_s=deadline, limits=LIMITS, host_network=True,
        # Raw sockets only when capture is allowed here; the ARP table needs none.
        add_capabilities=("NET_RAW", "NET_ADMIN") if capture else (),
    )
    try:
        node_ip = runner.wait_running(name, start_s)
        arp = runner.exec(name, ["cat", "/proc/net/arp"], 30)
        if arp.exit_code != 0:
            raise PluginError("could not read the system's ARP table")
        macs = parse_arp(arp.stdout)

        peers: dict[str, _Peer] = {}
        if capture and node_ip:
            res = runner.exec(name, [
                "tshark", "-i", "any", "-n", "-q", "-s", str(HEADER_SNAPLEN),
                "-a", f"duration:{duration_s}", "-f", f"host {node_ip}", "-w", "/tmp/d.pcapng",
            ], duration_s + 60)
            if res.exit_code != 0:
                raise PluginError("the traffic capture for device discovery failed")
            conv = runner.exec(name, ["tshark", "-n", "-r", "/tmp/d.pcapng", "-q",
                                      "-z", "conv,udp", "-z", "conv,tcp"], 120)
            if conv.exit_code != 0:
                raise PluginError("could not summarize the discovery capture")
            peers = parse_conversations(conv.stdout, node_ip)
        elif not capture:
            notes.append("Live capture is off here, so devices come from the ARP table only and "
                         "their roles are unknown. Set each one's role by hand.")
        else:
            notes.append("The system's IP wasn't reported, so traffic wasn't checked.")
    finally:
        runner.delete(name)

    ips = sorted((set(macs) | set(peers)) - {node_ip}, key=ipaddress.ip_address)
    devices = [classify(ip, peers.get(ip), macs.get(ip)) for ip in ips]
    if node_ip:
        devices.insert(0, Found(ip=node_ip, kind="server", evidence=["the system this tool runs on"]))
    if not [d for d in devices if d.kind in ("hub", "viewer")] and capture:
        notes.append("No hub or viewer traffic was seen. Devices may be idle or off; try again "
                     "with a longer search, or set roles by hand.")
    return Discovery(node_ip=node_ip, devices=devices, notes=notes, captured=capture and bool(node_ip))


AUTO_NAMES = {"hub": "Hub", "viewer": "Viewer", "server": "System (MiniEdge)", "unknown": "Device"}


def auto_name(kind: str, ip: str) -> str:
    """The name a search gives a device; the UI shows the IP next to it."""
    return AUTO_NAMES.get(kind, "Device")


def save(db: Session, result: Discovery) -> list[tuple[Device, Found]]:
    """Upsert by IP. A device the engineer edited keeps their name and role
    (D-7); every device not seen this time is marked not reachable."""
    by_ip = {d.ip_address: d for d in db.query(Device).all()}
    out = []
    seen = set()
    for f in result.devices:
        row = by_ip.get(f.ip)
        if row is None:
            row = Device(ip_address=f.ip, name=auto_name(f.kind, f.ip), kind=f.kind)
            db.add(row)
        elif not row.overridden:
            row.kind = f.kind
            row.name = auto_name(f.kind, f.ip)
        row.reachable = 1
        seen.add(f.ip)
        out.append((row, f))
    for ip, row in by_ip.items():
        if ip not in seen:
            row.reachable = 0
    db.commit()
    return out
