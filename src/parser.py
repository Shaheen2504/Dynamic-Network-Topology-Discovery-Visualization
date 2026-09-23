"""Parse LLDP/CDP neighbor output into a common Neighbor record.

One function per vendor/protocol dialect. Each takes the raw CLI text plus the
name of the device the text came from, and returns a list of Neighbor.

rdx: hand-written regex keeps this dependency-free so the pipeline runs on a
laptop with no lab. In production, Cisco dialects are better served by
ntc-templates (TextFSM); Comware/AOS-CX templates still have to be written here.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Neighbor:
    local_device: str
    local_port: str
    remote_device: str
    remote_port: str
    remote_chassis: str = ""
    capabilities: tuple = field(default_factory=tuple)
    protocol: str = ""


# Longest-first so "Te" does not shadow "TwentyFiveGigE".
_PORT_NAMES = [
    "TwentyFiveGigE",
    "TenGigabitEthernet",
    "FortyGigabitEthernet",
    "HundredGigE",
    "GigabitEthernet",
    "FastEthernet",
    "Port-channel",
    "Bridge-Aggregation",
    "Ethernet",
]


def normalize_port(port):
    """Gi1/0/24 and GigabitEthernet1/0/24 are the same port.

    CDP prints the long form, LLDP the short form. Without this the same cable
    produces two different edges.
    """
    port = port.strip()
    m = re.match(r"^([A-Za-z][A-Za-z-]*?)\s*([\d/.:]+)$", port)
    if not m:
        return port
    name, number = m.groups()
    key = name.lower().replace("-", "")
    for full in _PORT_NAMES:
        if full.lower().replace("-", "").startswith(key):
            return full + number
    return name + number


def normalize_chassis(value):
    """00e1.6d2a.1b00, 00e1-6d2a-1b00 and 00:e1:6d:2a:1b:00 are one identity."""
    hex_only = re.sub(r"[^0-9a-f]", "", (value or "").lower())
    if len(hex_only) != 12:
        return ""
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def _capabilities(value):
    return tuple(c.strip() for c in re.split(r"[,\s]+", value.strip()) if c.strip())


def _field(block, label):
    m = re.search(rf"^\s*{label}\s*:\s*(.+?)\s*$", block, re.MULTILINE)
    return m.group(1) if m else ""


def parse_cisco_lldp(text, local_device):
    """`show lldp neighbors detail` on Cisco IOS / IOS-XE."""
    neighbors = []
    for block in re.split(r"^-{10,}\s*$", text, flags=re.MULTILINE):
        local_port = _field(block, "Local Intf")
        remote_port = _field(block, "Port id")
        if not local_port or not remote_port:
            continue
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=normalize_port(local_port),
            remote_device=_field(block, "System Name"),
            remote_port=normalize_port(remote_port),
            remote_chassis=normalize_chassis(_field(block, "Chassis id")),
            capabilities=_capabilities(_field(block, "Enabled Capabilities")),
            protocol="lldp",
        ))
    return neighbors


def parse_cisco_cdp(text, local_device):
    """`show cdp neighbors detail` on Cisco IOS / IOS-XE."""
    neighbors = []
    for block in re.split(r"^-{10,}\s*$", text, flags=re.MULTILINE):
        m = re.search(
            r"^Interface:\s*(.+?),\s*Port ID \(outgoing port\):\s*(.+?)\s*$",
            block, re.MULTILINE)
        if not m:
            continue
        local_port, remote_port = m.groups()
        caps = re.search(r"Capabilities:\s*(.+?)\s*$", block, re.MULTILINE)
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=normalize_port(local_port),
            remote_device=_field(block, "Device ID"),
            remote_port=normalize_port(remote_port),
            # CDP does not advertise a chassis MAC; identity falls back to name.
            remote_chassis="",
            capabilities=_capabilities(caps.group(1)) if caps else (),
            protocol="cdp",
        ))
    return neighbors


def parse_comware_lldp(text, local_device):
    """`display lldp neighbor-information verbose` on HPE/H3C Comware."""
    neighbors = []
    blocks = re.split(r"^LLDP neighbor-information of port ", text, flags=re.MULTILINE)
    for block in blocks[1:]:
        header = re.match(r"\d+\[(.+?)\]", block)
        if not header:
            continue
        remote_port = _field(block, "Port ID")
        if not remote_port:
            continue
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=normalize_port(header.group(1)),
            remote_device=_field(block, "System name"),
            remote_port=normalize_port(remote_port),
            remote_chassis=normalize_chassis(_field(block, "Chassis ID")),
            capabilities=_capabilities(_field(block, "System capabilities enabled")),
            protocol="lldp",
        ))
    return neighbors


PARSERS = {
    "cisco_lldp": parse_cisco_lldp,
    "cisco_cdp": parse_cisco_cdp,
    "comware_lldp": parse_comware_lldp,
}


def parse(dialect, text, local_device):
    if dialect not in PARSERS:
        raise ValueError(f"no parser for dialect {dialect!r}; have {sorted(PARSERS)}")
    return PARSERS[dialect](text, local_device)
